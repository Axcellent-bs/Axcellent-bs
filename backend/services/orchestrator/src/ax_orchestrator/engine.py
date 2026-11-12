"""plan 실행 엔진 (ORC-05·06·07·08·09·10).

엔진은 시나리오를 모른다. plan(단계·선행·skill)을 받아 순차·병렬로 부서를 부르고,
`input-required`를 중계하고, 규칙으로 이관하고, 실패를 재시도하고, 이벤트를 닫는다.
분석 문장·금액·등급은 만들지 않는다. 모든 값은 부서 결과에서 온 것이다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from ax_agent_template import routing
from ax_agent_template.services import route_target
from ax_contracts.schemas import (
    HANDED_TO_HUMAN,
    AgentRequest,
    AgentResult,
    ErrorCode,
    InputReason,
    RequestMeta,
    TaskStatus,
)
from ax_orchestrator.models import (
    NO_RESPONSE,
    NOT_INTERVENTION,
    TERMINAL,
    WAITING_RECOVERY,
    Incident,
    Plan,
    Step,
    StepRun,
    iso,
    now,
)
from ax_orchestrator.naming import DEPT_KO, RESOLUTION_KO
from ax_orchestrator.registry import Registry
from ax_orchestrator.resolved_log import log_resolved
from ax_orchestrator.timeline import prior_case_of, step_timings, times_of

Caller = Callable[[str, AgentRequest], Awaitable[AgentResult]]
Publisher = Callable[[str, Incident], Awaitable[None]]
# 이벤트를 저장한다(실패해도 이벤트 처리는 멈추지 않는다): 이벤트 -> 없음
Persister = Callable[[Incident], None]
# 안전 관문의 보류 건 상태를 되물어 본다: review_id -> {"status", "decided_by", ...}
# 못 읽으면 None
ReviewChecker = Callable[[str], Awaitable[dict[str, Any] | None]]
# 규칙으로 안 풀릴 때만 쓰는 LLM 선택기: (skill, 후보 부서들) -> 부서 이름 또는 None
# 확장점이다. 서버(main.py)는 연결하지 않아 지금은 규칙으로 못 찾으면 이관 큐로 넘긴다(README 6장)
LlmPicker = Callable[[str, list[str]], Awaitable[str | None]]

log = logging.getLogger("orchestrator.engine")

TRANSFER_CODES = {ErrorCode.FORBIDDEN_SKILL.value, ErrorCode.HOP_LIMIT.value, "not_my_scope"}
MAX_FAILED_RETRIES = 2
MAX_CANCELED_RETRIES = 1
MAX_EARLY_REVIEWS = 200  # 대기로 오르기 전에 먼저 온 안전 관문 결정을 담는 상한
DEADLINE_FACTOR = 2.0  # 약속 시간(`Step.expected_seconds`)의 몇 배까지 기다릴지
MAX_DEADLINE_SECONDS = 300.0  # 약속으로 늘리는 호출 마감의 상한(초)
ASYNC_HEARTBEAT_SECONDS = 300.0  # 긴 작업: 마지막 응답 뒤 이만큼 소식이 없으면 응답 끊김
ASYNC_TOTAL_SECONDS = 1800.0  # 긴 작업: 약속 시간이 없을 때 전체 대기 상한
MAX_ASYNC_SECONDS = 86400.0  # 긴 작업 전체 대기의 절대 상한(사람 할 일함 대기와 같은 24시간)
MAX_EARLY_ASYNC = 200  # 대기에 오르기 전에 먼저 온 긴 작업 알림을 담는 상한


class Hold:
    """사람 승인을 기다리는 단계(C 등급 보류). 총괄은 승인하지 않는다.

    `review_id`는 부서가 돌려준 안전 관문 보류 건 번호이고, 안전 관문의 결정 이벤트와 맞춰 본다.
    """

    def __init__(self, review_id: str | None = None, wait_seconds: float = 0.0) -> None:
        self.pending = True
        self.approved: bool | None = None
        self.reason = ""
        self.decided_by: str | None = None
        self.review_id = review_id
        self.started_at = now()
        self.expires_at = self.started_at + timedelta(seconds=wait_seconds)
        self.decided_at: datetime | None = None  # 사람이 결정한 시각(승인 대기 시간 계산용)
        self.event = asyncio.Event()

    def decide(self, approved: bool, reason: str, by: str | None = None) -> None:
        if not self.pending:
            return
        self.pending = False
        self.decided_at = now()
        self.approved = approved
        self.reason = reason
        self.decided_by = by
        self.event.set()


@dataclass(frozen=True)
class ReviewOutcome:
    """안전 관문이 확인해 준 사람의 결정. 이벤트가 아니라 안전 관문의 기록이 기준이다."""

    approved: bool
    reason: str  # human | review_timeout
    by: str | None


def _plain(label: str) -> str:
    """단계 이름 끝의 담당 표시 "(infra)"를 뺀다."""
    return re.sub(r"\s*\([^)]*\)$", "", label)


TIME_KO = {
    "detected_at": "감지 시각",
    "cause_candidate_at": "원인 후보가 나온 시각",
    "human_work_started_at": "사람에게 넘긴 시각",
    "human_work_done_at": "사람이 처리를 끝낸 시각",
    "recovered_at": "정상 복귀 시각",
    "step_timings": "단계별 시간",
}


def _record_note(note: object) -> str | None:
    """이벤트에 보관된 설명에서 중복 머리말을 떼고 영어 코드를 한국어로 바꾼다."""
    if not isinstance(note, str) or not note:
        return None
    text = re.sub(r"^문의 #\d+ 처리 결과 기록: ", "", note)
    for code, ko in RESOLUTION_KO.items():
        text = text.replace(code, ko)
    return text


STATUS_KO = {
    "completed": "완료",
    "failed": "실패",
    "skipped": "건너뜀",
    "rejected_by_human": "보류 후 거절",
    "transfer_queued": "이관 큐로 넘김",
    "waiting_human": "사람 처리 대기",
    "waiting_async": "긴 작업 진행 중",
}


class HandoffWait:
    """꺼진 서비스라 사람 할 일함으로 넘어간 단계(`handed_to_human`). C 등급 보류와 다르다.

    사람이 할 일함에서 끝내면 `handoff.completed`가 오고, `IncidentManager`가 `finish`를 부른다.
    한 번 끝난 일은 다시 바뀌지 않는다(같은 이벤트가 두 번 와도 한 번만 반영).
    """

    def __init__(self, task_id: str, assignee_unit: str | None) -> None:
        self.task_id = task_id
        self.assignee_unit = assignee_unit
        self.pending = True
        self.status: str | None = None  # done | rejected | timeout
        self.started_at = now()
        self.finished_at: datetime | None = None
        self.result: str | None = None  # 사람이 적은 결과 (외부 입력처럼 데이터로만 다룬다)
        self.event = asyncio.Event()

    def finish(self, status: str, result: str | None) -> bool:
        if not self.pending:
            return False
        self.pending = False
        self.finished_at = now()
        self.status = status
        self.result = result
        self.event.set()
        return True


class AsyncWait:
    """부서가 접수한 긴 작업(`working` + `task_id`). 진행 알림과 완료 이벤트로 이어진다.

    - 접수한 때부터 응답(진행 알림)이 올 때마다 `last_response_at`이 갱신된다. 응답 사이가 가장
      길게 비었던 시간을 `max_silence`로 남겨 인사 에이전트 평가가 "응답 끊김"을 본다.
    - 끝나는 방법은 넷이다: done·failed(부서가 알림), silent(응답이 끊김), timeout(전체 시간 초과).
      한 번 끝난 일은 다시 바뀌지 않는다(같은 이벤트가 두 번 와도 한 번만 반영).
    """

    def __init__(self, task_id: str, dept: str, skill: str) -> None:
        self.task_id = task_id
        self.dept = dept
        self.skill = skill
        self.pending = True
        self.status: str | None = None  # done | failed | silent | timeout
        self.started_at = now()
        self.last_response_at = self.started_at
        self.heartbeats = 0
        self.max_silence = 0.0
        self.percent: int | None = None
        self.note: str | None = None
        self.output: dict[str, Any] = {}
        self.finished_at: datetime | None = None
        self.wake = asyncio.Event()

    def silent_for(self) -> float:
        """마지막 응답 이후 지금까지 아무 소식이 없던 시간(초)."""
        return (now() - self.last_response_at).total_seconds()

    def beat(self, percent: int | None, note: str | None) -> bool:
        """진행 알림. 끝난 일이면 False."""
        if not self.pending:
            return False
        at = now()
        self.max_silence = max(self.max_silence, (at - self.last_response_at).total_seconds())
        self.last_response_at = at
        self.heartbeats += 1
        self.percent = percent if percent is not None else self.percent
        self.note = note if note is not None else self.note
        self.wake.set()
        return True

    def finish(self, status: str, output: dict[str, Any] | None, note: str | None = None) -> bool:
        if not self.pending:
            return False
        at = now()
        if status in ("done", "failed"):  # 부서의 응답이다. 끊김·시간 초과는 응답이 아니다
            self.max_silence = max(self.max_silence, (at - self.last_response_at).total_seconds())
            self.last_response_at = at
        else:
            self.max_silence = max(self.max_silence, self.silent_for())
        self.pending = False
        self.finished_at = at
        self.status = status
        self.output = dict(output or {})
        self.note = note if note is not None else self.note
        self.wake.set()
        return True


class Engine:
    def __init__(
        self,
        registry: Registry,
        caller: Caller,
        *,
        publish: Publisher | None = None,
        hold_timeout: float = 600.0,
        handoff_timeout: float = 86400.0,
        retry_delay: float = 0.0,
        step_timeout: float = 30.0,
        llm_pick: LlmPicker | None = None,
        author: str = "orchestrator",
        persist: Persister | None = None,
        review_check: ReviewChecker | None = None,
        release_wait: float = 0.0,
        late_retry_delays: tuple[float, ...] = (),
        async_heartbeat_timeout: float = ASYNC_HEARTBEAT_SECONDS,
        async_total_timeout: float = ASYNC_TOTAL_SECONDS,
    ) -> None:
        self.registry = registry
        # 긴 작업(비동기): 부서가 마지막으로 응답한 뒤 이 시간(초)이 지나도록 소식이 없으면 응답이
        # 끊긴 것으로 보고 단계를 실패로 끝낸다. 전체 대기는 약속 시간의 `DEADLINE_FACTOR`배
        # (약속이 없으면 `async_total_timeout`)이고 `MAX_ASYNC_SECONDS`를 넘지 않는다
        self.async_heartbeat_timeout = async_heartbeat_timeout
        self.async_total_timeout = async_total_timeout
        # `Step.late_retry` 단계가 실패하면 이 간격(초)으로 다시 돌린다. 비우면 다시 돌리지 않는다
        self.late_retry_delays = late_retry_delays
        self._late_tasks: set[asyncio.Task[None]] = set()
        # 사람이 장애를 해제하길 기다리는 시간(초, `Step.wait_for_release`). 0이면 기다리지 않는다
        self.release_wait = release_wait
        self._released: dict[str, asyncio.Event] = {}
        self.caller = caller
        self.publish = publish
        self.hold_timeout = hold_timeout
        self.handoff_timeout = handoff_timeout
        self.retry_delay = retry_delay
        self.step_timeout = step_timeout
        self.llm_pick = llm_pick
        self.author = author
        self.persist = persist
        self.review_check = review_check
        self._locks: dict[str, asyncio.Lock] = {}
        self._sequence: dict[str, int] = {}
        # 사람이 대기 상태보다 먼저 끝낸 결과: (context_id, task_id) → (status, 결과)
        self._early_handoffs: dict[tuple[str, str], tuple[str, str | None]] = {}
        # 긴 작업이 대기 상태에 오르기 전에 먼저 온 알림: (context_id, task_id) → 보관한 알림들
        self._early_async: dict[tuple[str, str], list[tuple[str, str, dict[str, Any]]]] = {}
        # 대기에 오르기 전에 먼저 온 안전 관문 결정: review_id → 결정
        self._early_reviews: dict[str, ReviewOutcome] = {}

    # ---------- 실행 ----------
    async def run(self, inc: Incident, plan: Plan) -> Incident:
        for step in plan.steps:
            inc.steps[step.id] = StepRun(step.id, step.skill, step.label)
        inc.log(f"이벤트 시작: {inc.title}")
        await self._publish("incident.opened", inc)
        self._save(inc)

        by_id = {s.id: s for s in plan.steps}
        pending = set(by_id)
        running: dict[asyncio.Task[None], str] = {}
        while pending or running:
            progressed = True
            while progressed:  # 건너뛴 단계가 다른 단계의 선행을 끝내므로 변화가 없을 때까지 반복
                progressed = self._schedule(inc, by_id, pending, running)
            if not running:
                break
            done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                running.pop(task)
                task.result()
                self._save(inc)
        await self._close(inc, plan)
        return inc

    def _schedule(
        self,
        inc: Incident,
        by_id: dict[str, Step],
        pending: set[str],
        running: dict[asyncio.Task[None], str],
    ) -> bool:
        progressed = False
        for sid in sorted(pending):
            step = by_id[sid]
            deps = (*step.after, *step.also_after)
            if not all(inc.steps[d].status in TERMINAL for d in deps):
                continue
            pending.discard(sid)
            progressed = True
            failed = [d for d in step.after if inc.steps[d].status != "completed"]
            if failed:
                run = inc.steps[sid]
                run.status = "skipped"
                run.note = f"선행 단계 미완료({', '.join(failed)})"
                run.finished_at = now()
                inc.log(f"{step.label}: 건너뜀 — {run.note}")
                continue
            running[asyncio.create_task(self._run_step(inc, step))] = sid
        return progressed

    async def _run_step(self, inc: Incident, step: Step) -> None:
        run = inc.steps[step.id]
        run.started_at = now()
        run.status = "running"
        run.promised_seconds = step.expected_seconds
        if step.kind == "record":
            await self._record(inc, step, run)
            return
        blocked = step.gate(inc)
        if (
            blocked is not None
            and step.wait_for_release is not None
            and self.release_wait > 0
            and step.wait_for_release(inc)
        ):
            blocked = await self._wait_release(inc, step)
        if (
            blocked is not None
        ):  # 앞 단계 결과로 보아 지금은 부르지 않는다(지어내지 않고 이유를 남김)
            run.status, run.reason_code, run.note = blocked
            run.finished_at = now()
            inc.log(f"{step.label}: {run.note}")
            return
        entry = self.registry.find(step.skill)
        if entry is None:
            self._skip(inc, run, "담당 부서를 확인할 수 없음 (Agent Card에 skill 없음)")
            return
        run.dept, run.grade = entry.dept, entry.grade
        if not entry.callable_by_orchestrator:
            self._skip(inc, run, "총괄이 요청할 수 없는 skill (allowed_requesters)")
            return
        inc.log(f"{step.label}: {entry.dept}에 요청")
        payload = step.build(inc)
        if step.split and len(payload.get(step.split[0]) or []) > step.split[1]:
            await self._call_in_batches(inc, step, run, entry.dept, payload)
        else:
            await self._call_and_handle(inc, step, run, entry.dept, payload)
        self._judge(inc, step, run)
        self._keep(inc, step, run)
        if step.late_retry and run.status == "failed" and self.late_retry_delays:
            task = asyncio.create_task(self._late_retry(inc, step))
            self._late_tasks.add(task)
            task.add_done_callback(self._late_tasks.discard)

    async def _late_retry(self, inc: Incident, step: Step) -> None:
        """실패한 정리 단계를 `late_retry_delays` 간격으로 다시 돌린다.

        성공하거나 게이트에 막히면 멈춘다.

        예: 판매 서버가 잠깐 멈춘 동안 장애 배너 내리기가 세 번 모두 실패하면, 서버가 돌아온 뒤
        배너가 그대로 남았다(복구 신호 뒤 다시 돌리기는 '기다리느라 건너뛴' 단계만 대상이다).
        게이트를 매번 다시 보므로 그 사이 정상화 공지로 배너가 내려갔으면 부르지 않는다.
        """
        for delay in self.late_retry_delays:
            await asyncio.sleep(delay)
            current = inc.steps.get(step.id)
            if current is None or current.status != "failed":
                return  # 다른 경로(복구 신호 뒤 다시 돌리기 등)로 이미 끝났다
            fresh = StepRun(step.id, step.skill, step.label)
            fresh.attempts = current.attempts
            inc.steps[step.id] = fresh
            inc.log(f"{step.label}: 실패해서 {delay:g}초 뒤 다시 시도")
            try:
                await self._run_step_once(inc, step)
            except Exception as exc:  # noqa: BLE001 - 사후 정리 실패가 다른 처리를 막지 않게 한다
                self._fail(inc, fresh, f"{type(exc).__name__}")
            if inc.closed_at is not None:
                inc.summary = [self._summary_line(r) for r in inc.steps.values()]
                inc.metrics = incident_metrics(inc)
            self._save(inc)
            if fresh.status != "failed":
                return

    async def _run_step_once(self, inc: Incident, step: Step) -> None:
        """나중 재시도용: 다시 실패해도 또 예약하지 않는다(간격 목록이 끝나면 멈춘다)."""
        late, step.late_retry = step.late_retry, False
        try:
            await self._run_step(inc, step)
        finally:
            step.late_retry = late

    async def refresh_impact(self, inc: Incident, plan: Plan) -> bool:
        """복구 신호를 받은 뒤 영향 분석을 같은 구간(복구 시각까지)으로 다시 돌려 결과를 갱신한다.

        첫 분석은 이벤트를 열고 2초 뒤에 한 번만 돌아서 그 뒤 실패가 빠진다. 새 결과로 바꾸고,
        새로 잡힌 고객은 `added_customer_ids`로 남긴다. 보상은 자동으로 다시 보내지 않는다.
        실패하면 이전 결과를 그대로 둔다. 갱신했으면 True.
        """
        run = inc.steps.get("impact")
        step = next((s for s in plan.steps if s.id == "impact"), None)
        if run is None or step is None or run.status != "completed":
            return False
        entry = self.registry.find(step.skill)
        if entry is None or not entry.callable_by_orchestrator:
            return False
        again = replace(step, id="impact_refresh")  # 멱등 키가 첫 분석과 달라야 다시 계산한다
        result = await self._send(inc, again, step.skill, step.build(inc), entry.dept)
        if result.status != TaskStatus.COMPLETED or result.outcome in TRANSFER_CODES:
            inc.log(f"{step.label}: 복구 뒤 다시 계산하지 못해 이전 결과를 유지")
            return False
        old_ids = set(inc.outputs.get("impact", {}).get("affected_customer_ids") or [])
        output = dict(result.output)
        added = [c for c in output.get("affected_customer_ids") or [] if c not in old_ids]
        output["added_customer_ids"] = added
        run.output = output
        run.refreshed_at = (
            now()
        )  # finished_at는 첫 분석이 끝난 시각 그대로 둔다(단계 시간이 부풀지 않게)
        inc.outputs["impact"] = output
        inc.log(f"{step.label}: 복구 시각까지 다시 계산 (새로 잡힌 영향 고객 {len(added)}명)")
        if added:
            await self._queue_late(inc, plan, added)
        return True

    async def _wait_release(self, inc: Incident, step: Step) -> tuple[str, str, str] | None:
        """사람이 장애를 해제했다는 신호를 `release_wait`초까지 기다린 뒤 게이트를 다시 본다.

        신호(`notify_recovered`)가 와도 게이트가 막으면(예: 해제가 아닌 지표 정상 복귀) 다시
        기다리고, 시간이 다 되면 마지막 게이트 결과(건너뜀 이유)를 돌려준다.
        """
        minutes = self.release_wait / 60
        inc.log(f"{step.label}: 사람이 장애를 해제하길 기다림 (최대 {minutes:g}분)")
        event = self._released.setdefault(inc.context_id, asyncio.Event())
        deadline = asyncio.get_running_loop().time() + self.release_wait
        blocked = step.gate(inc)
        while blocked is not None:
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                break
            try:
                await asyncio.wait_for(event.wait(), timeout=left)
            except TimeoutError:
                break
            event.clear()
            blocked = step.gate(inc)
        self._released.pop(inc.context_id, None)
        if blocked is None:
            inc.log(f"{step.label}: 사람이 장애를 해제함 — 이어서 진행")
        return blocked

    def notify_recovered(self, inc: Incident) -> None:
        """복구 신호(shop.recovered)를 받았다. 해제를 기다리는 단계가 있으면 게이트를 다시 본다."""
        event = self._released.get(inc.context_id)
        if event is not None:
            event.set()

    async def rerun_after_recovery(self, inc: Incident, plan: Plan) -> list[str]:
        """복구 신호를 기다리느라 건너뛴 단계(`rerun_on_recovery`)를 신호가 온 뒤 다시 돌린다.

        이벤트가 닫힐 때 복구 신호가 아직 없어서 건너뛴 단계만 대상이다(다른 이유로 건너뛰었거나
        이미 돌았거나 실패한 단계는 건드리지 않는다). 돌린 단계 번호를 돌려준다. 단계가 실패해도
        예외로 올리지 않고 단계 결과에 남긴다(복구 처리를 막지 않는다).
        """
        ran: list[str] = []
        for step in plan.steps:
            run = inc.steps.get(step.id)
            if not step.rerun_on_recovery or run is None:
                continue
            if run.status != "skipped" or run.reason_code != WAITING_RECOVERY:
                continue
            fresh = StepRun(step.id, step.skill, step.label)
            inc.steps[step.id] = fresh
            try:
                await self._run_step(inc, step)
            except Exception as exc:  # noqa: BLE001 - 사후 정리 실패가 복구 처리를 막지 않게 한다
                self._fail(inc, fresh, f"{type(exc).__name__}")
            ran.append(step.id)
        if ran and inc.closed_at is not None:
            inc.summary = [self._summary_line(r) for r in inc.steps.values()]
            inc.metrics = incident_metrics(inc)
        return ran

    async def record_late_resolution(self, inc: Incident, plan: Plan) -> bool:
        """사람에게 넘긴 뒤 풀린 장애의 해결 내용을 인사 카드에 다시 남긴다.

        이벤트가 닫힐 때는 아직 풀리지 않아 "미해결"로 남았다. 쇼핑몰 복구 신호를 받으면 같은
        해결 기록 단계를 한 번 더 보내 해결·조치(풀린 방법)·걸린 시간을 채운다. 그래야 다음에
        같은 유형 장애가 오면 비슷한 과거 이벤트 찾기가 이 조치를 내밀 수 있다.
        사람이 이미 적은 해결 기록은 덮지 않는다(`only_if_unresolved`). 실패해도 이벤트는 그대로다.
        """
        run = inc.steps.get("resolution")
        step = next((s for s in plan.steps if s.id == "resolution"), None)
        recovery = inc.steps.get("recovery")
        if run is None or step is None or run.status != "completed":
            return False
        if recovery is not None and recovery.status == "completed":
            return False  # 자동 복구 확인으로 이미 해결로 남겼다
        entry = self.registry.find(step.skill)
        if entry is None or not entry.callable_by_orchestrator or step.build is None:
            return False
        payload = {**step.build(inc), "only_if_unresolved": True}
        if payload.get("outcome") != "resolved":
            return False
        again = replace(step, id="resolution_recovered")  # 멱등 키가 첫 기록과 달라야 한다
        try:
            result = await self._send(inc, again, step.skill, payload, entry.dept)
        except Exception as exc:  # noqa: BLE001 - 참고 기록 실패가 이벤트 처리를 막지 않게 한다
            inc.log(f"복구 뒤 해결 내용 기록 실패: {type(exc).__name__}")
            return False
        outcome = (result.output or {}).get("outcome")
        if result.status != TaskStatus.COMPLETED:
            inc.log(f"복구 뒤 해결 내용 기록 못 함: {result.outcome or result.status}")
            return False
        inc.shared["late_resolution"] = payload
        inc.log(
            "복구 뒤 해결 내용 기록: "
            + ("이미 해결 기록이 있어 그대로 둠" if outcome == "duplicate" else "해결로 채움")
        )
        return outcome == "recorded"

    async def _queue_late(self, inc: Incident, plan: Plan, added: list[str]) -> None:
        """새로 잡힌 고객을 고객지원 보상 "대기"에 올린다. 쿠폰은 상담원이 승인한 뒤에만 나간다.

        실패해도 영향 분석 갱신은 그대로 둔다(보상을 자동으로 보내지 않는다).
        같은 고객 묶음은 같은 단계 id라 다시 와도 한 번만 처리된다.
        """
        tag = hashlib.sha1("|".join(sorted(added)).encode()).hexdigest()[:8]
        for base in plan.late_steps:
            step = replace(base, id=f"{base.id}_{tag}")
            if step.id in inc.steps:
                continue
            inc.steps[step.id] = StepRun(step.id, step.skill, step.label)
            try:
                await self._run_step(inc, step)
            except Exception as exc:  # noqa: BLE001 - 대기 올리기 실패가 복구 처리를 막지 않게 한다
                self._fail(inc, inc.steps[step.id], f"{type(exc).__name__}")

    async def _call_in_batches(
        self, inc: Incident, step: Step, run: StepRun, dept: str, payload: dict[str, Any]
    ) -> None:
        """부서가 한 번에 받는 개수에 상한이 있으면(`step.split`) 순서대로 나눠 부른다.

        묶음마다 멱등 키가 달라 다시 보내도 한 번만 처리된다. 한 묶음이라도 끝내지 못하면
        거기서 멈추고 단계를 실패로 남긴다(남은 묶음은 보내지 않고, 보낸 만큼만 결과에 남긴다).
        """
        field_name, size = step.split  # type: ignore[misc]
        items = list(payload[field_name])
        chunks = [items[i : i + size] for i in range(0, len(items), size)]
        outputs: list[dict[str, Any]] = []
        extra_attempts = 0  # 묶음 수가 아니라 다시 시도한 횟수만 센다
        for n, chunk in enumerate(chunks, start=1):
            part = replace(step, id=f"{step.id}.{n}")
            run.output = {}
            before = run.attempts
            await self._call_and_handle(inc, part, run, dept, {**payload, field_name: chunk})
            extra_attempts += max(run.attempts - before - 1, 0)
            run.attempts = 1 + extra_attempts
            if run.status != "completed":
                note = f"{n}/{len(chunks)}번째 묶음에서 멈춤 — {run.note}"
                self._finish_batches(inc, step, run, outputs)
                run.note = note
                inc.log(f"{run.label}: {note}")
                return
            outputs.append(run.output)
        self._finish_batches(inc, step, run, outputs)
        inc.log(f"{run.label}: {len(items)}건을 {len(chunks)}묶음으로 나눠 요청")

    @staticmethod
    def _finish_batches(
        inc: Incident, step: Step, run: StepRun, outputs: list[dict[str, Any]]
    ) -> None:
        if not outputs:
            return
        merged = step.merge(outputs) if step.merge else {**outputs[0]}
        merged["batches"] = len(outputs)
        run.output = merged
        inc.outputs[step.id] = merged

    @staticmethod
    def _judge(inc: Incident, step: Step, run: StepRun) -> None:
        """성공했어도 결과가 정상이 아니면(예: 복구 확인 실패) 이관 큐로 돌려 사람이 보게 한다."""
        if run.status != "completed":
            return
        verdict = step.verdict(run.output)
        if verdict is not None:
            run.reason_code, run.note = verdict
            run.status = "transfer_queued"
            inc.log(f"{run.label}: {run.note}")

    @staticmethod
    def _keep(inc: Incident, step: Step, run: StepRun) -> None:
        """성공한 단계의 결과에서 다른 부서에 넘길 값(예: `notice_id`)을 이벤트에 보관한다.

        부서가 값을 주지 않았으면 만들어 내지 않고 비워 둔다.
        """
        if run.status != "completed":
            return
        for field_name, keep_as in step.keep.items():
            value = run.output.get(field_name)
            if value is not None:
                inc.shared[keep_as] = value
                inc.log(f"{run.label}: {keep_as} 보관 ({value})")

    def _skip(self, inc: Incident, run: StepRun, note: str) -> None:
        run.status, run.note, run.finished_at = "skipped", note, now()
        inc.log(f"{run.label}: 건너뜀 — {note}")

    # ---------- 호출·재시도·이관·중계 ----------
    def deadline_seconds(self, step: Step) -> float:
        """부서를 한 번 부를 때 기다리는 시간(초).

        - 단계에 마감(`timeout`)이 따로 있으면 그대로 쓴다.
        - 약속 시간(`expected_seconds`)이 있으면 약속의 `DEADLINE_FACTOR`배로 하되, 기본 마감보다
          짧아지지 않게 하고 `MAX_DEADLINE_SECONDS`를 넘기지 않는다. 일이 큰 단계가 30초에 끊겨
          다시 시도되는 것을 막는다. 부서가 멈췄을 때 기다리는 시간도 늘어나서 상한을 둔다.
        - 둘 다 없으면 기본 마감이다.
        """
        if step.timeout:
            return step.timeout
        if step.expected_seconds:
            wanted = step.expected_seconds * DEADLINE_FACTOR
            return min(max(wanted, self.step_timeout), max(MAX_DEADLINE_SECONDS, self.step_timeout))
        return self.step_timeout

    def _request(
        self, inc: Incident, step: Step, skill: str, payload: dict[str, Any], dept: str
    ) -> AgentRequest:
        sequence = None
        if step.serial_group:
            sequence = self._sequence.get(inc.context_id + step.serial_group, 0) + 1
            self._sequence[inc.context_id + step.serial_group] = sequence
        meta = RequestMeta(
            context_id=inc.context_id,
            token="orchestrator",
            path=["orchestrator"],
            deadline=now() + timedelta(seconds=self.deadline_seconds(step)),
            idempotency_key=f"{inc.context_id}:{skill}:{step.id}",
        )
        return AgentRequest(meta=meta, skill=skill, input=payload, sequence=sequence)

    async def _send(
        self, inc: Incident, step: Step, skill: str, payload: dict[str, Any], dept: str
    ) -> AgentResult:
        lock = self._locks.setdefault(f"{inc.context_id}:{step.serial_group}", asyncio.Lock())
        req = self._request(inc, step, skill, payload, dept)
        if step.serial_group:
            async with lock:
                return await self.caller(dept, req)
        return await self.caller(dept, req)

    async def _call_and_handle(
        self, inc: Incident, step: Step, run: StepRun, dept: str, payload: dict[str, Any]
    ) -> None:
        failed_retries = canceled_retries = 0
        while True:
            run.attempts += 1
            result = await self._send(inc, step, step.skill, payload, dept)
            status, outcome = result.status, result.outcome or ""

            if status == TaskStatus.COMPLETED and outcome not in TRANSFER_CODES:
                self._done(inc, run, result)
                return
            if status == TaskStatus.INPUT_REQUIRED:
                done = await self._relay(inc, step, run, dept, payload, result)
                if done:
                    return
                continue_payload = run.output.pop("_next_payload", None)
                if continue_payload is None:
                    return
                payload = continue_payload
                continue
            if (
                status == TaskStatus.WORKING
            ):  # 긴 작업: 접수만 하고 진행 알림·완료 이벤트로 이어 간다
                await self._wait_async(inc, step, run, dept, result)
                return
            if status == TaskStatus.REJECTED and outcome == ErrorCode.SERVICE_DISABLED.value:
                # 받는 서비스가 꺼져 있고 조회라 사람에게도 넘기지 않았다
                # → 실패가 아니라 "확인할 수 없음"으로 건너뛴다
                service = result.output.get("to_service") or dept
                run.reason_code = outcome
                self._skip(inc, run, f"확인할 수 없음 ({service} 서비스가 꺼져 있음)")
                return
            if outcome in TRANSFER_CODES and status in (TaskStatus.REJECTED, TaskStatus.COMPLETED):
                await self._transfer(inc, step, run, dept, payload, outcome)
                return
            if status == TaskStatus.CANCELED and canceled_retries < MAX_CANCELED_RETRIES:
                canceled_retries += 1
                inc.log(f"{run.label}: 마감 초과, 재시도 {canceled_retries}")
                await asyncio.sleep(self.retry_delay)
                continue
            if status == TaskStatus.FAILED and failed_retries < MAX_FAILED_RETRIES:
                failed_retries += 1
                inc.log(f"{run.label}: 실패({outcome}), 재시도 {failed_retries}")
                await asyncio.sleep(self.retry_delay)
                continue
            self._fail(inc, run, f"{status.value}: {outcome or '사유 없음'}")
            return

    def _done(self, inc: Incident, run: StepRun, result: AgentResult) -> None:
        run.status = "completed"
        run.output = dict(result.output)
        run.note = result.outcome or ""
        run.finished_at = now()
        inc.outputs[run.step_id] = run.output
        inc.log(f"{run.label}: 완료")

    def _fail(self, inc: Incident, run: StepRun, note: str) -> None:
        run.status, run.note, run.finished_at = "failed", note, now()
        inc.log(f"{run.label}: 실패 — {note}")

    async def _transfer(
        self, inc: Incident, step: Step, run: StepRun, dept: str, payload: dict[str, Any], why: str
    ) -> None:
        """이관 큐: 규칙(같은 skill을 가진 다른 부서)으로 먼저, 안 되면 LLM 선택기(`llm_pick`).

        서버는 `llm_pick`을 연결하지 않으므로 규칙으로 못 찾으면 "이관 큐로 넘김"으로 끝난다.
        """
        run.reason_code = why
        candidates = [
            a for a in self.registry.alternatives(step.skill, dept) if a.callable_by_orchestrator
        ]
        target = candidates[0].dept if candidates else None
        used_llm = False
        if target is None and self.llm_pick is not None:
            names = sorted(self.registry.cards)
            target = await self.llm_pick(step.skill, [n for n in names if n != dept])
            used_llm = target is not None
        if target is None:  # 실제에 없는 부서를 지어내지 않고 "이관 큐로 넘김"만 남긴다
            run.status = "transfer_queued"
            run.note = f"소관 부서를 찾지 못해 이관 큐로 넘김({why})"
            run.finished_at = now()
            inc.log(f"{run.label}: {run.note}")
            return
        inc.log(f"{run.label}: {dept} → {target} 이관({'LLM' if used_llm else '규칙'})")
        run.transferred_to = target
        run.dept = target
        result = await self._send(inc, step, step.skill, payload, target)
        if result.status == TaskStatus.COMPLETED and (result.outcome or "") not in TRANSFER_CODES:
            self._done(inc, run, result)
            run.note = f"이관 처리({'LLM' if used_llm else '규칙'})"
        else:
            self._fail(inc, run, f"이관 후에도 실패: {result.status.value}")

    async def _relay(
        self,
        inc: Incident,
        step: Step,
        run: StepRun,
        dept: str,
        payload: dict[str, Any],
        result: AgentResult,
    ) -> bool:
        """input-required 중계. 끝났으면 True, 다시 호출해야 하면 False(+ `_next_payload`)."""
        reason = result.reason_code
        run.reason_code = reason.value if reason else None
        if result.outcome == HANDED_TO_HUMAN:  # 받는 서비스가 꺼져 있어 사람 할 일함으로 감
            return await self._wait_handoff(inc, step, run, result)
        if reason == InputReason.HUMAN_INPUT:
            return await self._wait_human(inc, step, run, dept, payload, result)
        if reason == InputReason.DEPT_INPUT:
            question = result.output.get("question") or {}
            q_skill = question.get("skill")
            entry = self.registry.find(q_skill) if q_skill else None
            if entry is None or not entry.callable_by_orchestrator:
                self._fail(inc, run, "다른 부서의 판단이 필요하지만 물을 부서를 찾지 못함")
                return True
            inc.log(f"{run.label}: {entry.dept}에 질의({q_skill})")
            answer = await self._send(inc, step, q_skill, question.get("input", {}), entry.dept)
            if answer.status != TaskStatus.COMPLETED:
                self._fail(inc, run, f"질의 실패: {answer.status.value}")
                return True
            run.output["_next_payload"] = {**payload, "answers": {q_skill: dict(answer.output)}}
            return False
        run.status = "skipped"
        run.note = "고객 자료 대기 — 이벤트 요약에 남김"
        run.finished_at = now()
        inc.log(f"{run.label}: 고객 입력 대기(보류)")
        return True

    async def _wait_human(
        self,
        inc: Incident,
        step: Step,
        run: StepRun,
        dept: str,
        payload: dict[str, Any],
        result: AgentResult,
    ) -> bool:
        review_id = (result.output or {}).get("review_id")
        hold = Hold(review_id if isinstance(review_id, str) else None, self.hold_timeout)
        inc.holds[step.id] = hold
        run.status = "held"
        run.note = (result.output or {}).get("hold_reason", "") or "사람 승인 대기"
        inc.log(f"{run.label}: C 등급 보류 — 승인 대기열에 올림")
        self._save(inc)
        early = self._early_reviews.pop(hold.review_id, None) if hold.review_id else None
        if early is not None:  # 대기로 올리기 전에 안전 관문이 이미 결정했다
            hold.decide(early.approved, early.reason, early.by)
        try:
            await asyncio.wait_for(hold.event.wait(), timeout=self.hold_timeout)
        except TimeoutError:
            # 결정 이벤트를 놓쳤을 수 있어 마감 때 안전 관문에 한 번 되물어 본다
            outcome = await self.settle_review(hold.review_id, None)
            if outcome is not None:
                hold.decide(outcome.approved, outcome.reason, outcome.by)
            else:
                hold.decide(False, "review_timeout")
        if hold.decided_at is not None:
            run.approval_wait_seconds = max(
                0.0, (hold.decided_at - hold.started_at).total_seconds()
            )
        if hold.approved:
            by = f"({hold.decided_by})" if hold.decided_by else ""
            inc.log(f"{run.label}: 사람이 승인{by} — 재개")
            run.status = "running"
            again = await self._send(
                inc, step, step.skill, {**payload, "review_decision": "approved"}, dept
            )
            if again.status == TaskStatus.COMPLETED:
                self._done(inc, run, again)
            else:
                self._fail(inc, run, f"승인 후 재개 실패: {again.status.value}")
        else:
            run.status = "rejected_by_human"
            run.note = "시간 초과로 자동 거절" if hold.reason == "review_timeout" else "사람이 거절"
            run.finished_at = now()
            inc.log(f"{run.label}: {run.note}")
        return True

    async def _wait_handoff(
        self, inc: Incident, step: Step, run: StepRun, result: AgentResult
    ) -> bool:
        """사람 할 일함 처리를 기다린다. 이벤트는 열린 채로, 이 단계에 기대지 않는 단계는 계속 돈다.

        - done: 사람이 적은 결과를 이 단계의 결과로 넣고 완료 → 뒤 단계가 이어진다
        - rejected·시간 초과: C 등급 보류 거절과 같이 `rejected_by_human`
        """
        output = dict(result.output)
        task_id = output.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            self._fail(inc, run, "사람 할 일함으로 넘겼다는데 일 번호(task_id)가 없음")
            return True
        unit = output.get("assignee_unit")
        wait = HandoffWait(task_id, unit if isinstance(unit, str) else None)
        inc.handoffs[step.id] = wait
        run.status = "waiting_human"
        run.handoff_task_id = task_id
        run.output = output
        run.note = f"{wait.assignee_unit or '맡은 부서'} 할 일함에서 사람 처리 대기 ({task_id})"
        inc.log(f"{run.label}: {run.note}")
        self._save(inc)
        early = self._early_handoffs.pop((inc.context_id, task_id), None)
        if early is not None:  # 대기로 올리기 전에 사람이 이미 끝냈다: 보관한 결과로 바로 이어 간다
            wait.finish(*early)
            inc.log(f"{run.label}: 먼저 끝난 사람 처리 결과를 이어 받음 ({task_id}, {early[0]})")
        try:
            await asyncio.wait_for(wait.event.wait(), timeout=self.handoff_timeout)
        except TimeoutError:
            wait.finish("timeout", None)
        run.finished_at = now()
        if wait.finished_at is not None:
            run.human_wait_seconds = max(0.0, (wait.finished_at - wait.started_at).total_seconds())
        if wait.status == "done":
            run.status = "completed"
            run.output = {
                "handled_by": "human",
                "task_id": task_id,
                "assignee_unit": wait.assignee_unit,
                "to_service": output.get("to_service"),
                "result": wait.result,
            }
            run.note = f"사람이 처리 ({wait.assignee_unit or '맡은 부서'})"
            inc.outputs[run.step_id] = run.output
        else:
            run.status = "rejected_by_human"
            run.note = (
                "사람 처리 시간 초과로 자동 거절"
                if wait.status == "timeout"
                else "사람이 처리하지 않기로 함"
            )
        inc.log(f"{run.label}: {run.note}")
        return True

    # ---------- 긴 작업(비동기) ----------
    def _async_total(self, expected: float | None) -> float:
        """긴 작업을 기다리는 전체 시간(초). 약속이 있으면 약속의 배수, 없으면 기본값."""
        if expected:
            return min(expected * DEADLINE_FACTOR, MAX_ASYNC_SECONDS)
        return min(self.async_total_timeout, MAX_ASYNC_SECONDS)

    async def _wait_async(
        self, inc: Incident, step: Step, run: StepRun, dept: str, result: AgentResult
    ) -> None:
        """부서가 접수한 긴 작업(`working` + `task_id`)을 진행 알림과 완료 이벤트로 이어 간다.

        이벤트는 열린 채로, 이 단계에 기대지 않는 단계는 계속 돈다.
        - done: 부서가 알린 결과를 이 단계의 결과로 넣고 완료 → 뒤 단계가 이어진다
        - failed: 부서가 실패를 알림. 단계 실패
        - 응답 끊김: 마지막 응답 뒤 `async_heartbeat_timeout` 동안 소식이 없음. 단계 실패
          (사유 코드 `no_response`)
        - 시간 초과: 전체 대기 시간(`_async_total`)이 지남. 단계 실패(`async_timeout`)
        """
        output = dict(result.output)
        task_id = output.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            self._fail(inc, run, "긴 작업을 접수했다는데 작업 번호(task_id)가 없음")
            return
        said = output.get("expected_seconds")
        expected = said if isinstance(said, int | float) and said > 0 else step.expected_seconds
        total = self._async_total(expected)
        wait = AsyncWait(task_id, dept, step.skill)
        inc.async_waits[step.id] = wait
        run.status = "waiting_async"
        run.async_task_id = task_id
        run.last_response_at = wait.last_response_at
        run.note = f"{dept}가 긴 작업을 접수해 진행 중 ({task_id})"
        inc.log(f"{run.label}: {run.note}")
        self._save(inc)
        for kind, source, data in self._early_async.pop((inc.context_id, task_id), []):
            self._apply_async(inc, step.id, wait, kind, source, data)
        while wait.pending:
            elapsed = (now() - wait.started_at).total_seconds()
            if elapsed >= total:
                wait.finish("timeout", None)
                break
            silent = wait.silent_for()
            if silent >= self.async_heartbeat_timeout:
                wait.finish("silent", None)
                break
            wait.wake.clear()
            left = min(total - elapsed, self.async_heartbeat_timeout - silent)
            try:
                await asyncio.wait_for(wait.wake.wait(), timeout=max(left, 0.001))
            except TimeoutError:
                pass
        self._finish_async(inc, run, wait)

    def _finish_async(self, inc: Incident, run: StepRun, wait: AsyncWait) -> None:
        run.last_response_at = wait.last_response_at
        run.heartbeats = wait.heartbeats
        run.max_silence_seconds = round(wait.max_silence, 3)
        run.progress_percent, run.progress_note = wait.percent, wait.note
        if wait.status == "done":
            run.status = "completed"
            run.output = dict(wait.output)
            run.note = f"긴 작업 완료 ({wait.task_id}, 진행 알림 {wait.heartbeats}회)"
            run.finished_at = wait.finished_at or now()
            inc.outputs[run.step_id] = run.output
            inc.log(f"{run.label}: 완료 — {run.note}")
            return
        if wait.status == "failed":
            reason, note = (
                "async_failed",
                f"부서가 긴 작업 실패를 알림 ({wait.note or '사유 없음'})",
            )
        elif wait.status == "silent":
            reason = NO_RESPONSE
            note = (
                f"진행 알림이 {self.async_heartbeat_timeout:.0f}초 넘게 없어 응답 끊김으로 끝냄 "
                f"({wait.task_id})"
            )
        else:
            reason, note = (
                "async_timeout",
                f"긴 작업이 정해진 시간 안에 끝나지 않음 ({wait.task_id})",
            )
        self._fail(inc, run, note)
        run.reason_code = reason
        run.finished_at = wait.finished_at or run.finished_at

    def _apply_async(
        self,
        inc: Incident,
        step_id: str,
        wait: AsyncWait,
        kind: str,
        source: str,
        data: dict[str, Any],
    ) -> bool:
        """진행 알림·완료를 대기에 반영한다. 보낸 곳이나 skill이 다르면 무시한다."""
        if source != wait.dept or data.get("skill") != wait.skill:
            return False
        if kind == "progress":
            applied = wait.beat(data.get("percent"), data.get("note"))
        else:
            status = "done" if data.get("status") == "done" else "failed"
            applied = wait.finish(status, data.get("output"), data.get("note"))
        run = inc.steps[step_id]
        run.last_response_at = wait.last_response_at
        run.heartbeats = wait.heartbeats
        run.max_silence_seconds = round(wait.max_silence, 3)
        run.progress_percent, run.progress_note = wait.percent, wait.note
        return applied

    def receive_async(self, inc: Incident, kind: str, source: str, data: dict[str, Any]) -> str:
        """`task.progress`·`task.completed`를 긴 작업 단계에 반영한다. 결과를 문자열로 돌려준다.

        - applied: 반영함 · ignored: 보낸 곳이나 skill이 일을 맡긴 부서와 달라 무시함(위조 방지)
        - kept: 기다리는 단계가 아직 없어 보관함(대기에 오르면 이어 받는다)
        - unmatched: 이미 끝났거나 맞는 단계가 없음
        """
        task_id = data.get("task_id")
        for step_id, wait in inc.async_waits.items():
            if wait.task_id == task_id:
                if source != wait.dept or data.get("skill") != wait.skill:
                    return "ignored"
                return (
                    "applied"
                    if self._apply_async(inc, step_id, wait, kind, source, data)
                    else ("unmatched")
                )
        if not isinstance(task_id, str):
            return "unmatched"
        key = (inc.context_id, task_id)
        kept = self._early_async.setdefault(key, [])
        if len(self._early_async) > MAX_EARLY_ASYNC or len(kept) >= 20:
            return "unmatched"
        kept.append((kind, source, data))
        return "kept"

    def complete_handoff(
        self, inc: Incident, task_id: str, status: str, result: str | None
    ) -> bool:
        """`handoff.completed`를 단계에 반영한다. 기다리는 일이 없거나 이미 끝났으면 False."""
        if status not in ("done", "rejected"):
            return False
        for wait in inc.handoffs.values():
            if wait.task_id == task_id:
                return wait.finish(status, result)
        return False

    def keep_early_handoff(
        self, inc: Incident, task_id: str, status: str, result: str | None
    ) -> bool:
        """기다리는 단계가 아직 없을 때 온 `handoff.completed`를 보관한다.

        그 단계가 대기로 오르면(`_wait_handoff`) 보관한 결과로 바로 이어 간다.

        라우터가 할 일함에 넣은 직후, 총괄이 단계를 대기로 올리기 전에 사람이 끝내면 생긴다.
        이미 그 일로 기다린 적이 있거나(반영됨) 같은 일을 이미 보관했으면 False.
        """
        if status not in ("done", "rejected"):
            return False
        if any(wait.task_id == task_id for wait in inc.handoffs.values()):
            return False
        key = (inc.context_id, task_id)
        if key in self._early_handoffs:
            return False
        self._early_handoffs[key] = (status, result)
        return True

    async def settle_review(
        self, review_id: str | None, claimed: str | None
    ) -> ReviewOutcome | None:
        """안전 관문에 보류 건 상태를 되물어 사람의 결정을 확정한다.

        `claimed`는 이벤트가 말한 결정(approved|rejected|expired)이고 마감 때 확인이면 None이다.
        이벤트는 누가 보냈든 위조될 수 있어서 **승인은 안전 관문이 승인으로 기록했을 때만**
        인정한다.
        안전 관문을 못 읽으면 승인은 인정하지 않고, 거절·만료는 안전한 쪽이라 그대로 따른다.
        아직 결정되지 않았으면 None이다.
        """
        if not review_id:
            return None
        review = await self.review_check(review_id) if self.review_check else None
        if review is None:
            if claimed in ("rejected", "expired"):
                return ReviewOutcome(
                    False, "review_timeout" if claimed == "expired" else "human", None
                )
            return None
        status = review.get("status")
        by = review.get("decided_by")
        by = by if isinstance(by, str) else None
        if status == "approved":
            return ReviewOutcome(True, "human", by)
        if status == "expired":
            return ReviewOutcome(False, "review_timeout", by)
        if status == "rejected":
            return ReviewOutcome(False, "human", by)
        return None  # pending: 아직 사람이 결정하지 않았다

    def keep_early_review(self, review_id: str, outcome: ReviewOutcome) -> bool:
        """승인 대기에 오르기 전에 온 안전 관문 결정을 보관한다. 이미 있거나 너무 많으면 False."""
        if review_id in self._early_reviews or len(self._early_reviews) >= MAX_EARLY_REVIEWS:
            return False
        self._early_reviews[review_id] = outcome
        return True

    def decide_hold(self, inc: Incident, step_id: str, approved: bool) -> bool:
        hold = inc.holds.get(step_id)
        if hold is None or not hold.pending:
            return False
        hold.decide(approved, "human")
        return True

    # ---------- 기록·종료 ----------
    async def _record(self, inc: Incident, step: Step, run: StepRun) -> None:
        entry = self.registry.find(step.skill)
        if entry is None:
            self._skip(inc, run, "담당 부서를 확인할 수 없음 (Agent Card에 skill 없음)")
            return
        run.dept, run.grade = entry.dept, entry.grade
        done = [r for r in inc.steps.values() if r.status == "completed"]
        problems = [
            r for r in inc.steps.values() if r.status in ("failed", "skipped", "rejected_by_human")
        ]
        kind, area = RECORD_BY_PLAN.get(inc.kind, ("incident", "infra"))
        # 재무가 남긴 기록처럼 앞 단계가 이벤트에 보관한 설명을 기록에 붙인다 (없으면 붙이지 않는다)
        note = _record_note(inc.shared.get("record_note"))
        payload = {
            "context_id": inc.context_id,
            "kind": kind,
            "event_type": inc.event_type,
            "summary": inc.title + (f" · {note}" if note else ""),
            "decision_reason": (
                f"끝내지 못한 일: {', '.join(_plain(r.label) for r in problems)}"
                if problems
                else "모든 단계를 끝냈어요"
            ),
            "alternatives_considered": [
                *(f"{_plain(r.label)}: {STATUS_KO.get(r.status, r.status)}" for r in problems),
                *(
                    f"{_plain(r.label)}: {DEPT_KO.get(r.dept or '', r.dept)}에게 넘김"
                    for r in done
                    if r.transferred_to
                ),
            ],
            "source_refs": [f"step:{r.step_id}" for r in done],
            "author": self.author,
            "area": area,
        }
        await self._call_and_handle(inc, step, run, entry.dept, payload)

    async def sync_times(self, inc: Incident) -> None:
        """이벤트에서 알게 된 시각을 인사 카드에 남긴다. 카드가 아직 없으면 다음에 다시 보낸다.

        사람이 적는 값이 아니라 단계 완료 시각·사람 할 일함·쇼핑몰 복귀 신호만 쓴다.
        실패해도 이벤트 처리에는 영향이 없다(참고용 기록).
        """
        entry = self.registry.find("record_event_times")
        if entry is None or not entry.callable_by_orchestrator or inc.times_off:
            return
        if not routing.is_enabled(route_target(entry.dept, "record_event_times")):
            # 인사가 꺼진 회사: 시각 기록은 사람이 할 일이 아니라 참고 기록이라 보내지 않는다.
            # (라우터는 읽기 조회가 아니면 꺼진 서비스 요청을 사람 할 일함에 넣으므로 여기서 멈춘다)
            inc.times_off = True
            inc.log("이벤트 시각 기록 건너뜀: 인사 서비스가 꺼져 있음")
            return
        fresh = {
            name: at.isoformat()
            for name, at in times_of(inc).items()
            if inc.times_sent.get(name) != at.isoformat()
        }
        prior = prior_case_of(inc)
        if prior and inc.times_sent.get("prior_case_id") != prior:
            fresh["prior_case_id"] = prior
        # 단계별 시간은 이벤트가 끝난 뒤에만 보낸다. 사람 처리가 늦게 끝나 값이 바뀌면 다시 보낸다
        timings = step_timings(inc) if inc.status in ("resolved", "degraded") else []
        timings_digest = (
            hashlib.sha256(json.dumps(timings, sort_keys=True).encode()).hexdigest()[:12]
            if timings
            else None
        )
        send_timings = (
            timings_digest is not None and inc.times_sent.get("step_timings") != timings_digest
        )
        if not fresh and not send_timings:
            return
        payload: dict[str, Any] = {"context_id": inc.context_id, **fresh}
        if send_timings:
            payload["step_timings"] = timings
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:10]
        req = AgentRequest(
            meta=RequestMeta(
                context_id=inc.context_id,
                token="orchestrator",
                path=["orchestrator"],
                deadline=now() + timedelta(seconds=self.step_timeout),
                idempotency_key=f"{inc.context_id}:record_event_times:{digest}",
            ),
            skill="record_event_times",
            input=payload,
        )
        try:
            result = await self.caller(entry.dept, req)
            outcome = (result.output or {}).get("outcome")
        except Exception as exc:  # noqa: BLE001 - 시각 기록 실패가 이벤트 처리를 막지 않게 한다
            inc.log(f"이벤트 시각 기록 실패: {type(exc).__name__}")
            return
        if getattr(result.outcome, "value", result.outcome) == ErrorCode.SERVICE_DISABLED.value:
            # 인사가 꺼진 회사: 시각 기록은 사람이 할 일이 아니라 참고 기록이라 넘기지 않고 멈춘다
            inc.times_off = True
            inc.log("이벤트 시각 기록 건너뜀: 인사 서비스가 꺼져 있음")
            return
        if outcome in ("recorded", "duplicate"):
            inc.times_sent.update(fresh)
            sent = sorted(fresh)
            if send_timings and timings_digest is not None:
                inc.times_sent["step_timings"] = timings_digest
                sent.append("step_timings")
            inc.log(f"이벤트 시각 기록: {', '.join(TIME_KO.get(k, k) for k in sent)}")
        else:
            inc.log(f"이벤트 시각 기록 보류: {outcome or '결과 없음'}")

    async def _close(self, inc: Incident, plan: Plan) -> None:
        required = {s.id for s in plan.steps if s.required}
        ok = all(inc.steps[i].status == "completed" for i in required)
        inc.status = "resolved" if ok else "degraded"
        inc.closed_at = now()
        inc.settle_after_recovery()  # 복구 신호를 먼저 받았으면 장애 중으로 남기지 않는다
        inc.summary = [self._summary_line(r) for r in inc.steps.values()]
        inc.metrics = incident_metrics(inc)
        inc.log(f"이벤트 종료: {inc.status}")
        log_resolved(inc)  # 해결로 닫히면 공통 로그에 복구 시각을 남긴다 (degraded는 남기지 않음)
        await self.sync_times(inc)
        self._save(inc)
        await self._publish("incident.resolved", inc)

    @staticmethod
    def _summary_line(run: StepRun) -> str:
        who = run.dept or "미정"
        text = {
            "completed": "완료",
            "failed": f"실패 ({run.note})",
            "skipped": f"건너뜀 ({run.note})",
            "rejected_by_human": f"보류 후 거절 ({run.note})",
            "transfer_queued": f"이관 큐로 넘김 ({run.note})",
            "waiting_human": f"사람 처리 대기 ({run.note})",
            "waiting_async": f"긴 작업 진행 중 ({run.note})",
        }.get(run.status, run.status)
        return f"{run.label} [{who}] {text}"

    async def _publish(self, kind: str, inc: Incident) -> None:
        if self.publish is not None:
            await self.publish(kind, inc)

    def _save(self, inc: Incident) -> None:
        """이벤트를 저장한다. 저장에 실패해도 이벤트 처리는 멈추지 않고 로그만 남긴다."""
        if self.persist is None:
            return
        try:
            self.persist(inc)
        except Exception:  # noqa: BLE001 - 저장 실패가 장애 대응을 막으면 안 된다
            log.exception("이벤트 저장 실패: %s", inc.context_id)


def incident_metrics(inc: Incident) -> dict[str, Any]:
    """닫힌 이벤트의 지표. 이벤트를 닫을 때와 재시작으로 중단된 이벤트를 남길 때 같이 쓴다."""
    assert inc.closed_at is not None
    statuses = [r.status for r in inc.steps.values()]
    retries = sum(max(r.attempts - 1, 0) for r in inc.steps.values())
    return {
        "mttr_seconds": inc.mttr_seconds(),
        # AI 대응이 끝나기까지 걸린 초. 장애가 풀린 시간(mttr_seconds)과 다르다
        "response_seconds": round((inc.closed_at - inc.opened_at).total_seconds(), 2),
        "steps_total": len(statuses),
        "steps_completed": statuses.count("completed"),
        "held": sum(1 for r in inc.steps.values() if r.reason_code == "human_input"),
        "handoffs": sum(1 for r in inc.steps.values() if r.handoff_task_id),
        "transfers": sum(1 for r in inc.steps.values() if r.transferred_to),
        "retries": retries,
        "auto_rate": round(
            # 꺼진 서비스라 건너뛴 조회 등(NOT_INTERVENTION)은 사람이 개입한 일이 아니다
            1
            - sum(
                1
                for r in inc.steps.values()
                if r.reason_code and r.reason_code not in NOT_INTERVENTION
            )
            / max(len(statuses), 1),
            2,
        ),
    }


# 이벤트 종류(plan 이름) → (기록 종류, 담당 영역). 인사 `record_case`로 보내는 값이다.
# 없으면 장애 이벤트로 기록한다.
RECORD_BY_PLAN: dict[str, tuple[str, str]] = {
    "support_resolved": ("case", "support-desk"),
}


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


__all__ = [
    "Engine",
    "HandoffWait",
    "Hold",
    "ReviewOutcome",
    "incident_metrics",
    "iso",
    "new_event_id",
]
