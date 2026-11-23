"""의뢰 실행: 접수된 의뢰의 계획을 첫 작업 부서부터 순서대로 시킨다 (의뢰 자동 넘김 2단계).

장애 이벤트용 엔진(`engine.py`)은 이벤트(Incident) 전제라서 건드리지 않고, 의뢰 기록
(`work_requests.py`) 위에서 도는 작은 실행기를 따로 둔다. 부서를 부르는 길(레지스트리·호출기)은
엔진의 것을 그대로 쓴다.

- 시킬 수 있는 단계(`ready_steps`): 안 시작했고 선행 단계가 모두 성공으로 끝난 것. 흐름의 첫 부서가
  먼저고, 앞 단계가 실패하면 뒤 단계는 시키지 않고 건너뜀으로 닫는다.
- LLM은 부르지 않는다. 순서·부서·skill은 일 목록이 정한 대로다.
- 고객 글은 부서에 `untrusted` 칸(데이터)으로만 간다. 지시문으로 넣지 않는다.
- 읽기(`read`)와 상태를 바꾸는 A·B 단계는 스스로 시킨다. A(자동)·B(교차검증)는 부서가 안전 관문
  (`execute()`)으로 등급대로 처리하므로 총괄이 한 번 더 막지 않는다. 다만 고객 문장에서 입력이
  나오는 상태 변경(`human_gate`: 기능 개발 요청·환불 접수)과 C 등급, 사람 확인 단계는 여기서
  멈추고 `waiting_human`으로 둔다. 분류 LLM이 고객 글에 속아도 사람 승인 없이는 실행되지
  않게 하는 방어다.
- 부서가 받아 간 긴 작업(`working`)은 `waiting_async`로 두고 작업 번호를 적는다. 부서가 보내는
  `task.progress`·`task.completed`를 `receive_async`로 받아 단계를 끝내고 다음 단계를 이어 시킨다.
  알림이 `ASYNC_SILENCE`초 넘게 없으면 응답 끊김으로 그 단계를 실패로 닫는다(`expire_silent`).
- 사람 승인·할 일함 대기는 번호만 적고 `held`·`waiting_human`으로 둔다.
- 앞 단계 결과 중 다음 단계가 통째로 받는 값(홍보 영상의 기획 입력)만 의뢰 기록 안 `carry`에 두고
  쓰고 나면 지운다. 화면·조회에는 내지 않는다.
- 끝나지 않은 일이 있어도 서버는 멈추지 않는다. 실행 중 예외는 그 단계를 실패로 닫고 의뢰를
  사람 확인(`needs_human`)으로 돌린다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from ax_contracts.schemas import (
    HANDED_TO_HUMAN,
    AgentRequest,
    AgentResult,
    ErrorCode,
    InputReason,
    RequestMeta,
    TaskStatus,
)
from ax_orchestrator import request_inputs, request_tasks
from ax_orchestrator.engine import TRANSFER_CODES, Engine
from ax_orchestrator.models import now
from ax_orchestrator.work_requests import OPEN_STATES, STEP_WAITING, WorkRequests, ready_steps

HUMAN_GRADE = request_tasks.HUMAN_GRADE

log = logging.getLogger("orchestrator.request_runner")

MAX_FAILED_RETRIES = 2
MAX_CANCELED_RETRIES = 1
STEP_TIMEOUT = 30.0
RETRY_DELAY = 0.2
WAITING = ("held", "waiting_human", "waiting_async")
NOT_OK = ("failed", "skipped", "rejected_by_human")
HIDE_NOTE = "사람이 의뢰를 삭제함"  # 의뢰 삭제(멈추고 숨김)로 닫은 단계·의뢰의 이유
NEEDS_APPROVAL = "needs_approval"
HUMAN_CHECK = "human_check"
NO_AGENT = "no_agent"
NO_REF = "no_ref"
APPROVED = "approved"
NO_INPUT = "no_input"  # 고객 문장에서 부서가 요구하는 입력을 찾지 못해 부서에 보내지 않음
NO_CARRY = "no_carry"  # 앞 단계가 넘겨야 할 결과가 없어 이 단계를 시키지 않음
NO_INSIGHT = "no_insight"  # 기획에 넘길 근거(facts)가 없어 영상 제작안을 건너뜀
ASYNC_SILENT = "async_silent"
ASYNC_SILENCE = 300.0  # 긴 작업 알림이 이만큼(초) 없으면 응답 끊김 (엔진의 기준과 같다)
WATCH_INTERVAL = 60.0
MAX_EARLY_KEYS = 100
MAX_EARLY_EACH = 20
REF_PATTERN = re.compile(r"[\w:.\-]{1,80}")  # 부서가 정한 결과 번호 모양 (문장은 받지 않는다)
Notifier = Callable[[str, str], None]
Escalator = Callable[[str, str, str | None], None]
ScopeCheck = Callable[[dict[str, Any], dict[str, Any]], bool]
OUT_OF_SCOPE = "out_of_scope"


def _dig(out: dict[str, Any], path: str) -> Any:
    """`items.0.campaign_id`처럼 점으로 이은 길을 따라 결과 안쪽 값을 꺼낸다. 없으면 None."""
    cur: Any = out
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def in_catalog(row: dict[str, Any], spec: dict[str, Any]) -> bool:
    """이 단계가 고른 일의 일 목록에 있는 단계(부서·skill)와 같은가. 다르면 시키지 않는다."""
    task = request_tasks.get(row.get("task"))
    return task is not None and any(
        s.dept == spec.get("dept") and s.skill == spec.get("skill") and s.kind == spec.get("kind")
        for s in task.steps
    )


class RequestRunner:
    def __init__(
        self,
        requests: WorkRequests,
        get_engine: Callable[[], Engine | None],
        *,
        step_timeout: float = STEP_TIMEOUT,
        retry_delay: float = RETRY_DELAY,
        notify: Notifier | None = None,
        escalate: Escalator | None = None,
        scope_check: ScopeCheck = in_catalog,
        clock: Callable[[], datetime] = now,
    ) -> None:
        self._clock = clock
        self._requests = requests
        self._get_engine = get_engine
        self._step_timeout = step_timeout
        self._retry_delay = retry_delay
        # 의뢰자에게 진행을 알린다: (대화 번호, 문장). 정해진 문장 틀만 보낸다. None이면 안 알린다
        self.notify = notify
        # 끝내지 못한 의뢰를 사람 받은 요청에 올린다: (번호, 이유, 실패 단계). None이면 기록만
        self.escalate = escalate
        self._scope_check = scope_check
        self._running: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        # 접수 응답보다 먼저 온 긴 작업 알림: (context_id, 작업 번호) → [(종류, 보낸 곳, 데이터)]
        self._early: dict[tuple[str, str], list[tuple[str, str, dict[str, Any]]]] = {}

    # ---- 시작 ------------------------------------------------------------------
    def start(self, request_id: str) -> None:
        """의뢰를 뒤에서 실행한다 (대화 답은 기다리지 않는다). 이미 실행 중이면 한 번만 돈다."""
        task = asyncio.get_running_loop().create_task(self.run(request_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def resume_open(self) -> int:
        """다시 켠 뒤 시킬 단계가 남은 열린 의뢰를 이어서 시킨다. 이어 받은 수를 돌려준다."""
        count = 0
        for row in self._requests.open_requests():
            for sid, step in row["steps"].items():  # 꺼져 있던 동안의 침묵은 세지 않는다
                if step["status"] == "waiting_async":
                    self._update(row["request_id"], sid, last_signal_at=now().isoformat())
            if row["status"] in ("classified", "running", "waiting") and ready_steps(row):
                self.start(row["request_id"])
                count += 1
        return count

    async def wait_idle(self) -> None:
        """실행 중인 일이 모두 끝날 때까지 기다린다 (시험·종료용)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ---- 실행 ------------------------------------------------------------------
    async def run(self, request_id: str) -> None:
        if request_id in self._running:
            return
        self._running.add(request_id)
        try:
            await self._drive(request_id)
        except Exception as exc:  # noqa: BLE001 - 실행 오류가 서버를 멈추지 않게 사람 확인으로 돌린다
            log.exception("의뢰 실행 실패: %s", request_id)
            self._safe_status(request_id, "needs_human", f"실행 중 오류 ({type(exc).__name__})")
        finally:
            self._running.discard(request_id)

    def _safe_status(self, request_id: str, status: str, message: str) -> None:
        try:
            self._requests.set_status(request_id, status, message)
        except KeyError:
            pass

    async def _drive(self, request_id: str) -> None:
        row = self._requests.get(request_id)
        if row is None or row["status"] not in OPEN_STATES or not row["plan"]:
            return
        task = request_tasks.get(row["task"]) if row.get("task") else None
        if task is not None and self._stale(row, task):  # 일 목록이 바뀐 뒤 시작하는 의뢰
            self._requests.refresh_plan(request_id, request_tasks.plan_of(task))
            row = self._requests.get(request_id) or row
        if task is not None and task.blocked_by:
            self._requests.set_status(
                request_id, "needs_human", f"아직 자동으로 진행할 수 없는 일: {task.blocked_by}"
            )
            return
        self._requests.set_status(request_id, "running", "부서에 시키기 시작했다")
        while True:
            row = self._requests.get(request_id)
            if row is None or row.get("stopped_at"):  # 사람이 멈췄으면 다음 단계로 넘기지 않는다
                return
            ready = ready_steps(row)
            if not ready:
                break
            await asyncio.gather(*(self._step(row, sid) for sid in ready))
        self._finish(request_id)

    @staticmethod
    def _ctx(row: dict[str, Any]) -> dict[str, Any]:
        """입력 규칙이 쓰는 의뢰자 정보: 이름(계정)과 구매자 번호(로그인 계정에서 받은 값)."""
        return {"name": row.get("account"), "buyer_id": row.get("buyer_id")}

    def _stale(self, row: dict[str, Any], task: request_tasks.TaskDef) -> bool:
        """시작 전인데 저장된 계획이 지금 일 목록과 다른가 (옛 계획으로 돌지 않게)."""
        if any(s["status"] != "pending" for s in row["steps"].values()):
            return False
        return self._requests.plan_differs(row["request_id"], request_tasks.plan_of(task))

    async def _step(self, row: dict[str, Any], step_id: str) -> None:
        """단계 하나를 시킨다. 어떤 일이 있어도 이 단계는 `pending`을 벗어난다 (무한 반복 방지)."""
        rid = row["request_id"]
        try:
            await self._run_step(row, step_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("의뢰 단계 실패: %s/%s", rid, step_id)
            self._update(
                rid,
                step_id,
                status="failed",
                note=f"실행 중 오류 ({type(exc).__name__})",
                finished_at=now().isoformat(),
            )

    async def _run_step(self, row: dict[str, Any], step_id: str) -> None:
        rid = row["request_id"]
        spec = next(s for s in row["plan"] if s["step_id"] == step_id)
        stamp = now().isoformat
        if spec.get("kind") == "human":
            self._update(
                rid,
                step_id,
                status="waiting_human",
                reason_code=HUMAN_CHECK,
                note="사람 확인을 기다린다",
                started_at=stamp(),
            )
            return
        if not self._scope_check(row, spec):  # 일 목록에 없는 단계는 어떤 부서에도 보내지 않는다
            self._update(
                rid,
                step_id,
                status="failed",
                reason_code=OUT_OF_SCOPE,
                note="고른 일의 목록에 없는 단계라 시키지 않았다",
                started_at=stamp(),
                finished_at=stamp(),
            )
            return
        engine = self._get_engine()
        entry = engine.registry.find(spec["skill"]) if engine else None
        if engine is not None and entry is not None and entry.dept != spec.get("dept"):
            entry = None  # 계획의 부서와 카드의 부서가 다르면 부르지 않는다
        if engine is None or entry is None or not entry.callable_by_orchestrator:
            self._update(
                rid,
                step_id,
                status="failed",
                reason_code=NO_AGENT,
                note="담당 부서를 확인할 수 없다 (카드에 없거나 총괄이 시킬 수 없는 일)",
                started_at=stamp(),
                finished_at=stamp(),
            )
            return
        carried = None
        if spec.get("takes_carry"):  # 넘길 값이 없으면 승인도 묻지 않고 이 단계를 건너뛴다
            carried = self._carried(row, spec)
            if carried is None and spec.get("on_missing") == "fail":
                self._update(
                    rid,
                    step_id,
                    status="failed",
                    reason_code=NO_CARRY,
                    note="앞 단계가 넘겨야 할 결과가 없어 시킬 수 없다",
                    started_at=stamp(),
                    finished_at=stamp(),
                )
                return
            if carried is None:
                self._update(
                    rid,
                    step_id,
                    status="skipped",
                    reason_code=NO_INSIGHT,
                    note="기획에 넘길 근거(facts)가 없어 이 단계를 건너뛰었다",
                    started_at=stamp(),
                    finished_at=stamp(),
                )
                return
        filled = None
        if spec.get(
            "fill"
        ):  # 고객 문장에서 기간 같은 입력을 채운다. 못 채우면 부서에 보내지 않는다
            filled = request_inputs.fill(
                spec["fill"], row["text"], self._clock(), row["request_id"], self._ctx(row)
            )
            if filled.ask or filled.handoff:
                self._update(
                    rid,
                    step_id,
                    status="failed",
                    reason_code=NO_INPUT,
                    note="필요한 입력(기간 등)을 고객 문장에서 찾지 못해 부서에 보내지 않았다",
                    started_at=stamp(),
                    finished_at=stamp(),
                )
                return
        step = row["steps"][step_id]
        grade = entry.grade or step.get("grade")
        gated = bool(spec.get("human_gate")) or grade == HUMAN_GRADE
        if gated and step.get("reason_code") != APPROVED:  # 사람 승인 없이는 시키지 않는다
            self._update(
                rid,
                step_id,
                status="waiting_human",
                grade=grade,
                reason_code=NEEDS_APPROVAL,
                note="상태를 바꾸는 일이라 사람 승인을 기다린다"
                + (f" ({filled.note})" if filled is not None and filled.note else ""),
                started_at=stamp(),
            )
            return
        payload = carried if carried is not None else self._input_of(row, spec, filled)
        if payload is None:
            self._update(
                rid,
                step_id,
                status="failed",
                reason_code=NO_REF,
                note="앞 단계의 결과 번호가 없어 시킬 수 없다",
                started_at=stamp(),
                finished_at=stamp(),
            )
            return
        self._update(rid, step_id, status="running", grade=grade, started_at=stamp())
        result = await self._call(engine, row, spec, entry.dept, step_id, payload, grade)
        self._apply(rid, step_id, result, spec)

    # ---- 부서 호출 ----------------------------------------------------------------
    @staticmethod
    def _input_of(
        row: dict[str, Any], spec: dict[str, Any], filled: request_inputs.Fill | None = None
    ) -> dict[str, Any] | None:
        """부서에 보낼 입력: 일 목록의 고정 입력, 고객 문장 칸(있으면), 앞 단계 결과 번호.

        앞 단계 결과는 번호(`result_ref`)만 넘긴다. 필요한데 없으면 None.
        """
        payload: dict[str, Any] = dict(spec.get("fixed_input") or {})
        if filled is not None:
            payload.update(filled.payload)
        text_key = spec.get("text_key")
        if text_key:  # 부서가 문장을 `input` 칸으로만 읽는 일이다 (문장은 데이터로만 쓰인다)
            payload[text_key] = row["text"]
        key = spec.get("takes_ref")
        if not key:
            return payload
        for before in spec.get("after") or []:
            ref = row["steps"][before].get("result_ref")
            if ref:
                payload[key] = ref
                return payload
        return None

    async def _call(
        self,
        engine: Engine,
        row: dict[str, Any],
        spec: dict[str, Any],
        dept: str,
        step_id: str,
        payload: dict[str, Any],
        grade: str | None = None,
    ) -> AgentResult | str:
        """부서를 부른다. 못 받으면 이유.

        읽기 단계만 실패·취소를 정해진 횟수 다시 부른다. 상태를 바꾸거나 비용이 드는 단계(A·B·C)는
        한 번만 보낸다: 첫 호출이 이미 일을 시작했을 수 있어 다시 보내면 중복·충돌(예: 영상 제작이
        "이미 진행 중"으로 거절)이 난다. 기다리는 시간은 단계의 `timeout_s`, 없으면 기본값이다.
        """
        rid = row["request_id"]
        retry = grade == request_tasks.READ_GRADE  # 등급을 모르면 다시 보내지 않는다
        timeout = float(spec.get("timeout_s") or self._step_timeout)
        failed = canceled = attempts = 0
        while True:
            attempts += 1
            self._update(rid, step_id, attempts=attempts)
            req = AgentRequest(
                meta=RequestMeta(
                    context_id=row.get("context_id") or f"ctx_{rid}",
                    token="orchestrator",
                    path=["orchestrator"],
                    deadline=now() + timedelta(seconds=timeout),
                    idempotency_key=f"{rid}:{spec['skill']}:{step_id}",
                ),
                skill=spec["skill"],
                input=payload,
                untrusted={"request_text": row["text"]},  # 고객 글은 데이터 칸으로만
            )
            try:
                result = await asyncio.wait_for(engine.caller(dept, req), timeout=timeout + 5)
            except TimeoutError:
                return "부서 응답 시간 초과"
            if retry and result.status == TaskStatus.CANCELED and canceled < MAX_CANCELED_RETRIES:
                canceled += 1
                await asyncio.sleep(self._retry_delay)
                continue
            retryable = result.output.get("retryable") is not False
            if (
                retry
                and result.status == TaskStatus.FAILED
                and retryable
                and failed < MAX_FAILED_RETRIES
            ):
                failed += 1
                await asyncio.sleep(self._retry_delay)
                continue
            return result

    def _apply(
        self, rid: str, step_id: str, result: AgentResult | str, spec: dict[str, Any]
    ) -> None:
        """부서 결과를 단계 상태로 옮긴다. 부서가 돌려준 문장은 옮기지 않고 상태·코드만 둔다."""
        done = now().isoformat()
        if isinstance(result, str):
            self._update(rid, step_id, status="failed", note=result, finished_at=done)
            return
        status, outcome = result.status, result.outcome or ""
        out = result.output or {}
        if status == TaskStatus.COMPLETED and outcome not in TRANSFER_CODES:
            self._complete(rid, step_id, spec, out, outcome)
        elif status == TaskStatus.INPUT_REQUIRED:
            self._wait(rid, step_id, result, out)
        elif status == TaskStatus.WORKING:
            task_id = out.get("task_id")
            if isinstance(task_id, str) and task_id:
                self._update(
                    rid,
                    step_id,
                    status="waiting_async",
                    async_task_id=task_id,
                    last_signal_at=done,
                    note="부서가 긴 작업으로 받아 진행 중",
                )
                self._drain_early(rid, step_id, spec, task_id)
            else:
                self._update(
                    rid,
                    step_id,
                    status="failed",
                    note="긴 작업을 받았다는데 작업 번호가 없다",
                    finished_at=done,
                )
        elif status == TaskStatus.REJECTED and outcome == ErrorCode.SERVICE_DISABLED.value:
            self._update(
                rid,
                step_id,
                status="skipped",
                reason_code=outcome,
                note="확인할 수 없다 (받는 서비스가 꺼져 있다)",
                finished_at=done,
            )
        else:
            self._update(
                rid,
                step_id,
                status="failed",
                reason_code=outcome or None,
                note=f"{status.value}: {outcome or '사유 없음'}",
                finished_at=done,
            )

    @staticmethod
    def _ref_of(out: dict[str, Any], spec: dict[str, Any]) -> str | None:
        """부서가 정한 결과 번호만 읽는다. 번호 모양이 아니면(문장 등) 버린다."""
        key = spec.get("ref_key")
        ref = _dig(out, key) if key else None
        return ref if isinstance(ref, str) and REF_PATTERN.fullmatch(ref) else None

    def _wait(self, rid: str, step_id: str, result: AgentResult, out: dict[str, Any]) -> None:
        """부서가 사람·다른 부서의 판단을 기다린다 (input-required). 번호만 적고 멈춘다."""
        reason = result.reason_code
        code = reason.value if reason else None
        if result.outcome == HANDED_TO_HUMAN:
            tid = out.get("task_id")
            self._update(
                rid,
                step_id,
                status="waiting_human",
                reason_code=code,
                handoff_task_id=tid if isinstance(tid, str) else None,
                note="부서 서비스가 꺼져 있어 사람 할 일함으로 넘어갔다",
            )
        elif reason == InputReason.HUMAN_INPUT:
            review = out.get("review_id")
            self._update(
                rid,
                step_id,
                status="held",
                reason_code=code,
                review_id=review if isinstance(review, str) else None,
                note="사람 승인을 기다린다",
            )
        else:
            self._update(
                rid,
                step_id,
                status="waiting_human",
                reason_code=code,
                note="부서가 더 필요한 입력을 기다린다",
            )

    # ---- 긴 작업 알림 ---------------------------------------------------------------
    def _carried(self, row: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
        """앞 단계가 다음 단계로 넘기려고 둔 값. 없으면 None."""
        source = [spec["carry_from"]] if spec.get("carry_from") else spec.get("after") or []
        for before in source:
            value = self._requests.get_carry(row["request_id"], before)
            if value:
                return value
        return None

    def _complete(
        self, rid: str, step_id: str, spec: dict[str, Any], out: dict[str, Any], outcome: str
    ) -> None:
        """단계를 완료로 닫는다. 결과 번호를 읽고, 다음 단계가 통째로 받을 값(`carries`)을 둔다."""
        key = spec.get("carries")
        keys = spec.get("carry_keys") or []
        if keys:  # 결과의 여러 칸을 한 묶음으로 (하나라도 비면 넘기지 않는다)
            bundle = {k: out.get(k) for k in keys}
            self._requests.set_carry(rid, step_id, bundle if all(bundle.values()) else None)
        elif key:
            value = out.get(key)
            ok = isinstance(value, dict) and bool(value)
            if key == "insight":  # 기획 입력은 근거(facts)가 있어야 쓸 수 있다
                ok = ok and bool(value.get("facts"))
            self._requests.set_carry(rid, step_id, value if ok else None)
        if spec.get("takes_carry"):  # 넘겨받은 값은 쓰고 나면 지운다
            source = [spec["carry_from"]] if spec.get("carry_from") else spec.get("after") or []
            for before in source:
                self._requests.set_carry(rid, before, None)
        self._update(
            rid,
            step_id,
            status="completed",
            note=outcome,
            result_ref=self._ref_of(out, spec),
            finished_at=now().isoformat(),
        )

    def _context_of(self, row: dict[str, Any]) -> str:
        return row.get("context_id") or f"ctx_{row['request_id']}"

    def receive_async(
        self, kind: str, source: str, data: dict[str, Any], context_id: str | None
    ) -> str:
        """부서의 `task.progress`·`task.completed`를 기다리는 단계에 반영한다.

        돌려주는 값: progressed · resumed(완료를 반영하고 다음 단계를 이어 시킴) ·
        ignored(보낸 곳이나 skill이 맡긴 부서와 다름: 위조 방지) · kept(단계가 아직 대기에 안 올라
        보관) · unmatched(이미 끝났거나 맞는 의뢰·단계가 없음).
        """
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not context_id:
            return "unmatched"
        row = next(
            (r for r in self._requests.open_requests() if self._context_of(r) == context_id), None
        )
        if row is None:
            return "unmatched"
        steps = row["steps"]
        for sid, step in steps.items():
            if step["status"] == "waiting_async" and step.get("async_task_id") == task_id:
                spec = next(s for s in row["plan"] if s["step_id"] == sid)
                return self._apply_async(row["request_id"], sid, spec, kind, source, data)
        if any(s.get("async_task_id") == task_id for s in steps.values()):
            return "unmatched"  # 이미 끝난 작업의 되풀이
        if not any(s["status"] in ("pending", "running") for s in steps.values()):
            return "unmatched"
        key = (context_id, task_id)
        if key not in self._early and len(self._early) >= MAX_EARLY_KEYS:
            return "unmatched"
        kept = self._early.setdefault(key, [])
        if len(kept) >= MAX_EARLY_EACH:
            return "unmatched"
        kept.append((kind, source, data))
        return "kept"

    def _drain_early(self, rid: str, step_id: str, spec: dict[str, Any], task_id: str) -> None:
        """접수 응답보다 먼저 온 알림이 있으면 대기에 오른 단계에 이어서 반영한다."""
        row = self._requests.get(rid)
        if row is None:
            return
        for kind, source, data in self._early.pop((self._context_of(row), task_id), []):
            step = (self._requests.get(rid) or row)["steps"][step_id]
            if step["status"] != "waiting_async":
                break
            self._apply_async(rid, step_id, spec, kind, source, data)

    def _apply_async(
        self,
        rid: str,
        step_id: str,
        spec: dict[str, Any],
        kind: str,
        source: str,
        data: dict[str, Any],
    ) -> str:
        if source != spec.get("dept") or data.get("skill") != spec.get("skill"):
            log.warning("긴 작업 알림의 보낸 곳·skill이 맡긴 부서와 달라 무시: %s/%s", rid, step_id)
            return "ignored"
        if (self._requests.get(rid) or {}).get("stopped_at"):  # 사람이 멈춘 뒤 온 결과
            return "unmatched"
        stamp = now().isoformat()
        if kind == "progress":  # 부서가 쓴 문장은 옮기지 않고 진행률만 둔다
            percent = data.get("percent")
            note = "부서가 긴 작업으로 받아 진행 중"
            if isinstance(percent, int):
                note += f" ({percent}%)"
            self._update(rid, step_id, last_signal_at=stamp, note=note)
            return "progressed"
        if data.get("status") == "done":
            out = data.get("output")
            self._complete(rid, step_id, spec, out if isinstance(out, dict) else {}, "긴 작업 완료")
        else:
            self._update(
                rid,
                step_id,
                status="failed",
                note="긴 작업이 끝나지 못했다",
                finished_at=stamp,
            )
        self.start(rid)
        return "resumed"

    def expire_silent(self, max_silence: float = ASYNC_SILENCE) -> list[str]:
        """알림이 `max_silence`초 넘게 없는 긴 작업 단계를 응답 끊김으로 실패로 닫고 마무리한다."""
        expired: list[str] = []
        for row in self._requests.open_requests():
            rid = row["request_id"]
            for sid, step in row["steps"].items():
                last = step.get("last_signal_at") or step.get("started_at")
                if step["status"] != "waiting_async" or not last:
                    continue
                if (now() - datetime.fromisoformat(last)).total_seconds() < max_silence:
                    continue
                self._update(
                    rid,
                    sid,
                    status="failed",
                    reason_code=ASYNC_SILENT,
                    note=f"긴 작업 알림이 {max_silence:.0f}초 넘게 없어 끝내지 못했다",
                    finished_at=now().isoformat(),
                )
                if rid not in expired:
                    expired.append(rid)
        for rid in expired:
            self.start(rid)
        return expired

    async def watch_async(
        self, interval: float = WATCH_INTERVAL, max_silence: float = ASYNC_SILENCE
    ) -> None:
        """긴 작업 알림이 끊긴 단계를 주기적으로 닫는다 (서버가 도는 동안 계속)."""
        while True:
            await asyncio.sleep(interval)
            try:
                self.expire_silent(max_silence)
            except Exception:  # noqa: BLE001 - 점검 실패가 서버를 멈추지 않게 한다
                log.exception("긴 작업 응답 끊김 점검 실패")

    # ---- 마무리 ------------------------------------------------------------------
    def _finish(self, rid: str) -> None:
        """남은 단계를 정리하고 의뢰 상태를 정한다 (끝남·실패·기다림)."""
        row = self._requests.get(rid)
        if row is None or row.get("stopped_at"):  # 사람이 삭제한 의뢰: 상태(rejected)를 그대로 둔다
            return
        self._skip_blocked(row)
        row = self._requests.get(rid)
        if row is None:
            return
        states = [s["status"] for s in row["steps"].values()]
        if any(s in ("held", "waiting_human") for s in states):
            self._requests.set_status(rid, "waiting", "승인이나 사람 확인을 기다린다")
            self._tell(row, "확인이 필요한 단계가 있어 담당자의 확인을 기다리고 있어요.")
        elif "waiting_async" in states:  # 부서가 긴 작업을 하는 중: 알림이 오면 이어 간다
            self._requests.set_status(rid, "running", "부서의 긴 작업이 끝나기를 기다린다")
            self._tell(row, "부서가 분석을 진행 중이에요. 끝나면 이어서 진행할게요.")
        elif "failed" in states:  # 재시도 뒤에도 끝내지 못했다: 멈추지 않고 사람에게 넘긴다
            self._requests.set_status(rid, "needs_human", "단계가 끝나지 못해 사람에게 넘겼다")
            self._tell(row, "일부 단계를 끝내지 못했어요. 담당자가 확인할 거예요.")
            failed = next(k for k, v in row["steps"].items() if v["status"] == "failed")
            if self.escalate is not None:
                self.escalate(rid, "step_failed", failed)
        elif any(v["reason_code"] == NO_INSIGHT for v in row["steps"].values()):
            self._requests.set_status(
                rid, "needs_human", "기획에 넘길 근거가 없어 영상 제작안을 만들지 못했다"
            )
            self._tell(row, "영상 제작안을 만들 근거가 부족해 담당자가 확인할 거예요.")
            skipped = next(k for k, v in row["steps"].items() if v["reason_code"] == NO_INSIGHT)
            if self.escalate is not None:
                self.escalate(rid, "step_failed", skipped)
        elif any(s in NOT_OK for s in states):  # 사람이 거절했거나 앞 단계 때문에 건너뜀
            self._requests.set_status(rid, "failed", "사람이 거절했거나 앞 단계가 끝나지 못했다")
            self._tell(row, "의뢰를 진행하지 않기로 했어요.")
        elif all(s == "completed" for s in states):
            self._requests.set_status(rid, "done", "모든 단계를 마쳤다")
            self._tell(row, "모든 단계를 마쳤어요.")
        else:
            self._requests.set_status(
                rid, "needs_human", "진행 상태를 알 수 없어 사람 확인이 필요하다"
            )

    def _tell(self, row: dict[str, Any], text: str) -> None:
        """의뢰를 접수한 대화로 진행을 알린다. 알리지 못해도 의뢰 진행은 멈추지 않는다."""
        thread = row.get("thread_id")
        if self.notify is None or not thread:
            return
        try:
            self.notify(thread, f"[의뢰 {row['request_id']}] {text}")
        except Exception:  # noqa: BLE001
            log.warning("의뢰 진행 알림 실패: %s", row["request_id"])

    # ---- 사람 확인·승인 -------------------------------------------------------------
    def decide(self, request_id: str, step_id: str, approved: bool, by: str | None = None) -> str:
        """사람이 확인·승인을 정한다. 돌려주는 값: ok · missing · not_waiting.

        - 사람 확인 단계: 승인하면 완료로 닫고 뒤 단계를 이어 시킨다. 거절하면 `rejected_by_human`.
        - 승인 대기(상태를 바꾸는 일): 승인하면 시킬 수 있게 되돌린다(부서의 안전 관문은
          따로 거친다). 거절하면 `rejected_by_human`.
        어느 쪽이든 뒤 단계는 앞 단계가 성공해야만 시킨다.
        """
        row = self._requests.get(request_id)
        step = (row or {}).get("steps", {}).get(step_id)
        if row is None or step is None:
            return "missing"
        if step["status"] != "waiting_human" or step["reason_code"] not in (
            HUMAN_CHECK,
            NEEDS_APPROVAL,
        ):
            return "not_waiting"
        who = f" ({by})" if by else ""
        done = now().isoformat()
        if not approved:
            self._update(
                request_id,
                step_id,
                status="rejected_by_human",
                note=f"사람이 거절했다{who}",
                finished_at=done,
            )
        elif step["reason_code"] == HUMAN_CHECK:
            self._update(
                request_id,
                step_id,
                status="completed",
                note=f"사람이 확인했다{who}",
                finished_at=done,
            )
        else:
            self._update(
                request_id,
                step_id,
                status="pending",
                reason_code=APPROVED,
                note=f"사람이 승인했다{who}",
            )
        self.start(request_id)
        return "ok"

    def rerun(self, request_id: str, task_name: str, by: str | None = None) -> str:
        """사람이 일을 골라 의뢰를 다시 돌린다.

        돌려주는 값: ok · missing · unknown_task · busy · stopped.
        계획과 단계 진행은 새로 시작한다(이전 선택은 기록에 남는다). 상태를 바꾸는 단계는 이번에도
        승인을 거친다. 부서에 보내는 중이거나 기다리는 단계가 있으면(`busy`) 바꾸지 않는다.
        사람이 삭제해 멈춘 의뢰(`stopped_at`)는 되돌려 숨김을 풀었어도 다시 돌리지 않는다
        (`stopped`). 고친 의뢰문으로 새로 접수하는 길(`edit`)만 있다.
        """
        row = self._requests.get(request_id)
        if row is None:
            return "missing"
        if row.get("stopped_at"):
            return "stopped"
        task = request_tasks.get(task_name)
        if task is None:
            return "unknown_task"
        busy = request_id in self._running or any(
            s["status"] in ("running", "held", "waiting_async") for s in row["steps"].values()
        )
        if busy:
            return "busy"
        self._requests.replan(request_id, task=task.name, plan=request_tasks.plan_of(task), by=by)
        self.start(request_id)
        return "ok"

    # ---- 의뢰 삭제(멈추고 숨김)·의뢰문 수정 (11/23) -------------------------------------
    def hide(self, request_id: str, by: str | None = None) -> str:
        """사람이 의뢰를 삭제한다 = 멈추고 숨김. 돌려주는 값: ok · missing.

        끝나지 않은 의뢰(받음·일 고름·진행·기다림·사람 확인 필요)면 남은 단계
        (시작 전·승인·사람 확인·긴 작업 대기)를 `skipped`("사람이 의뢰를 삭제함")로 닫고
        의뢰는 `rejected`. 사람 확인 거절과 같은 길이고 이유만 다르다.
        이미 부서에 맡긴 긴 작업은 취소하지 않는다(취소 계약 없음).
        결과가 와도 다음 단계로 넘기지 않는다(`stopped_at`). 끝난 의뢰는 숨기기만 한다.
        """
        row = self._requests.get(request_id)
        if row is None:
            return "missing"
        if row["status"] in (*OPEN_STATES, "needs_human") and not row.get("stopped_at"):
            self._requests.mark_stopped(request_id)
            who = f" ({by})" if by else ""
            done = now().isoformat()
            for sid, step in row["steps"].items():
                if step["status"] in ("pending", *STEP_WAITING):
                    self._update(request_id, sid, status="skipped",
                                 note=f"{HIDE_NOTE}{who}", finished_at=done)  # fmt: skip
            self._requests.set_status(request_id, "rejected", f"{HIDE_NOTE}{who}")
        self._requests.hide(request_id, by)
        return "ok"

    def unhide(self, request_id: str, by: str | None = None) -> str:
        """숨김만 되돌린다 (멈춘 단계·상태는 그대로). 돌려주는 값: ok · missing."""
        if self._requests.get(request_id) is None:
            return "missing"
        self._requests.unhide(request_id, by)
        return "ok"

    async def edit(
        self, request_id: str, text: str, intake: Any, llm: Any, by: str | None = None
    ) -> tuple[str, str | None]:
        """의뢰문 수정 = 고쳐서 다시 접수. (결과, 새 의뢰 번호).

        결과: ok · missing · same(바뀐 내용 없음) · not_request(새 글이 의뢰로 접수되지 않음).
        순서: 대화 접수와 같은 함수(`RequestIntake.receive`, 분류·되묻기 그대로)로 새 의뢰를 먼저
        만들고, 성공하면 옛 의뢰를 삭제(멈추고 숨김)하고 둘을 잇는다. 새 접수에 실패하면(예외 포함)
        옛 의뢰는 건드리지 않는다.
        """
        row = self._requests.get(request_id)
        if row is None:
            return "missing", None
        if " ".join(text.split()) == " ".join((row.get("text") or "").split()):
            return "same", None
        result = await intake.receive(row.get("account"), text, llm,
                                      thread_id=row.get("thread_id"),
                                      buyer_id=row.get("buyer_id"))  # fmt: skip
        new_id = result.request_id
        if not result.handled or not new_id or new_id == request_id:
            return "not_request", None
        self.hide(request_id, by)
        self._requests.link_replacement(request_id, new_id)
        return "ok", new_id

    def _skip_blocked(self, row: dict[str, Any]) -> None:
        """앞 단계가 성공하지 못해 시킬 수 없게 된 뒤 단계를 건너뜀으로 닫는다 (연달아 반복)."""
        rid = row["request_id"]
        changed = True
        while changed:
            changed = False
            row = self._requests.get(rid) or row
            for spec in row["plan"]:
                step = row["steps"][spec["step_id"]]
                if step["status"] != "pending":
                    continue
                bad = [a for a in spec.get("after") or [] if row["steps"][a]["status"] in NOT_OK]
                if bad:
                    self._update(
                        rid,
                        spec["step_id"],
                        status="skipped",
                        note=f"앞 단계가 끝나지 못해 건너뜀 ({', '.join(bad)})",
                        finished_at=now().isoformat(),
                    )
                    changed = True

    def _update(self, rid: str, step_id: str, **fields: Any) -> None:
        self._requests.update_step(rid, step_id, **fields)
