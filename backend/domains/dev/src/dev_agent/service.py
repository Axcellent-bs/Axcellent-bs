"""개발 서비스 (dev, 8111): PR·검수·머지·저장소·배포, 코딩 에이전트·개발 대화·구독형 작업,
장애 연결·관련 PR·롤백 확인 (분리 5단계: `planning_agent.domain.service`에서 옮김).

공용 뼈대는 `ax_workflow.base.PlanningCore`. 기획 서비스 코드는 import하지 않는다
(다음 단계에서 `domains/dev`로 옮긴다).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from ax_agent_template import config as template_config
from ax_contracts.schemas import TENANT_ID_DEFAULT, ExecuteDecision
from ax_contracts.schemas.planning import (
    DevResultInput,
    ReceiveReleaseOrderInput,
    ReleasePr,
    RequestPauseInput,
    RequestReleaseCheckInput,
)
from ax_contracts.schemas.record import RecordCase, RecordKind
from ax_workflow import cause_inbox, hr_link, related
from ax_workflow.autocheck import (
    WAIT_CROSS_CHECK,
    WAIT_HUMAN,
    Check,
    planned_area,
    planned_paths,
    pr_check,
    pr_wait_state,
)
from ax_workflow.base import _TICKET_KINDS, AGENT_NAME, DEV_ACTIONS, TEAM_LEAD, PlanningCore
from ax_workflow.clock import KST, Clock
from ax_workflow.core import (
    ActionError,
    ActionResult,
    Gate,
    GateCall,
    GateRefused,
)
from ax_workflow.dev_handoff import (
    RECEIVE_DEV_RESULT,
    START_DEV,
    DevSender,
    dev_result_key,
)
from ax_workflow.handover import (
    HandoverCheck,
    Item,
    area_of_paths,
    area_of_text,
    check_handover,
    in_progress,
)
from ax_workflow.hr_link import HrClient, HrRecordResult
from ax_workflow.models import (
    DEPT_NAME,
    PR,
    Activity,
    AutoLog,
    AutoPolicy,
    AutoProposal,
    AutoRun,
    ChatMessage,
    ChatPlan,
    ChatThread,
    CodingFile,
    CodingReview,
    CodingRun,
    Incident,
    IncidentCandidate,
    Para,
    PrGate,
    RelatedItem,
    RepoTarget,
    Request,
    Spec,
    State,
    Task,
    dump,
)
from ax_workflow.result_notify import ResultSender
from ax_workflow.rules import (
    AUTO_LEVELS,
    PROMOTE_AFTER,
    ROLLBACK_LOOKBACK,
    auto_limit,
    deploy_of,
    estimate,
    find_duplicate,
    incident_of,
    merged_in_window,
    para_of,
    verify_rollback,
)
from ax_workflow.trace import changed_paths_for
from dev_agent import coder, cross_check, llm_key, repo_routing, vcs, vcs_bridge
from dev_agent.cross_check import CrossChecker, CrossEvidence
from dev_agent.deploy_store import get_deploy_store
from dev_agent.external_actions import MOCK_CONNECTION, VCS_WRITE_ACTIONS
from dev_agent.vcs import inventory
from dev_agent.vcs.base import VcsError

MAX_INSTRUCTION_CHARS = 1000  # 사람 수정 지시 길이


POLICY_SENT = "auto_policy"  # dev: 마지막으로 보낸 자동 처리 범위 지문 (`_dev_sent` 키)


def _fmt(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%m-%d %H:%M UTC")


def _text_warnings(s: State, x: Task) -> list[str]:
    """작업 글 경고 (work_queue). 순환 import를 피해 안에서 부른다."""
    from dev_agent import work_queue

    return work_queue.task_text_warnings(s, x)


class DevService(PlanningCore):
    """개발 서비스 (dev, 8111): 넘겨받은 기획서로 PR·검수·머지·배포, 코딩 에이전트·개발 대화,
    장애 연결·관련 PR·롤백 확인. 자동 처리 범위(auto_policy)의 주인이다."""

    ROLE = "dev"
    ACTIONS = DEV_ACTIONS

    def __init__(
        self,
        clock: Clock | None = None,
        gate: Gate | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
        result_sender: ResultSender | None = None,
        dev_sender: DevSender | None = None,
        real_example: bool = False,
        relater: related.Relater | None | Literal["default"] = "default",
        cross_checker: CrossChecker | None | Literal["default"] = "default",
        hr_client: HrClient | None | Literal["default"] = "default",
    ):
        super().__init__(clock, gate, tenant_id, result_sender, dev_sender, real_example, relater)
        # 인사 record_case (인사 연동 2단계: PR 머지·요청 완료). 목 모드면 None (부르지 않는다)
        self._hr = hr_link.default_client() if hr_client == "default" else hr_client
        self._resuming = False  # 대기 대화를 깨우는 중 (다시 깨우지 않는다)
        self._pr_sync: asyncio.Task | None = None  # 바깥에서 머지·닫은 PR 맞추기 루프
        self._polling_wait = False  # 다른 서비스 요청을 기다리는 대화를 주기적으로 보는 중
        # 코딩 에이전트 실행 (작업 id → 실행 중인 태스크·실행 번호). 번호가 바뀐 실행은 버린다
        self._coding: dict[str, asyncio.Task] = {}
        self._coding_live: dict[str, int] = {}
        self._coding_seq = 0
        # 교차검증 근거를 모으는 곳 (None이면 모으지 않고 safety 판정만)
        self._cross_checker: CrossChecker | None = (
            cross_check.default_checker() if cross_checker == "default" else cross_checker
        )
        # 넘겨받은 요청 작업 중 서버 AI로 띄울 것 (바꿔 끼운 뒤 잠금 밖에서 띄운다)
        self._pending_spawn: list[str] = []
        self._init_state()
        self._release_base = self._done_specs(self.state)

    def _spawn_hold(self, task_id: str, reason: str | None = None, key: str | None = None) -> None:
        try:
            task = asyncio.get_running_loop().create_task(self._hold_to_inbox(task_id, reason, key))
        except RuntimeError:
            return
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    # ---- 외부 연결 쓰기 액션: 관문에 연결 id를 싣는다 (external_actions.py)

    async def _execute(
        self,
        action: str,
        ctx: str,
        target: str,
        summary: str,
        params: dict | None = None,
        refs: list[str] | None = None,
        allow: tuple[str, ...] = ("executed",),
    ) -> ExecuteDecision:
        """외부 연결에 쓰는 액션이면 params에 `connection_id`를 채워 관문에 보낸다."""
        if action in VCS_WRITE_ACTIONS:
            params = dict(params or {})
            if not params.get("connection_id"):
                params["connection_id"] = self._connection_for(target, params)
        return await super()._execute(action, ctx, target, summary, params, refs, allow)

    def _connection_for(self, target: str, params: dict) -> str:
        """이 쓰기가 닿는 연결 id. 못 정하면 첫 연결, 연결이 없으면 `mock`."""
        s = self.state
        known = {c.id for c in vcs.routing_connections()}

        def of_task(task_id: str | None) -> str | None:
            t = next((x for x in s.tasks if x.id == task_id), None) if task_id else None
            return t.coding.connection if t is not None and t.coding is not None else None

        def of_pr(ref: str) -> str | None:
            p = self._find_pr(s, ref)
            if p is None:
                return None
            return (p.target.connection_id if p.target else None) or of_task(p.task)

        head = target.split(":", 1)[0]
        found = (
            params.get("connection")
            or of_task(params.get("task"))
            or (of_pr(head) if head.startswith("PR") else None)
            or next((c for c in known if target.startswith((f"{c}#", f"{c}:"))), None)
            or of_task(head)
        )
        if found:
            return str(found)
        rows = vcs.routing_connections()
        return rows[0].id if rows else MOCK_CONNECTION

    def start_pr_sync(self) -> None:
        """PR_SYNC_SECONDS마다 sync_prs·오래 기다리는 대화 알림을 돈다 (한 번만 켠다)."""
        if self._pr_sync is not None:
            return

        from dev_agent import dev_chat, work_queue

        async def loop() -> None:
            try:
                await dev_chat.recover_orphans(self)  # 꺼질 때 답하던 대화를 되돌린다
            except Exception:  # noqa: BLE001 - 되돌리기 실패로 루프가 안 켜지면 안 된다
                pass
            while True:
                await asyncio.sleep(PR_SYNC_SECONDS)
                try:
                    await self.sync_prs()
                    await self.recheck_ci()  # CI 실패로 멈춘 PR: 나중에 통과하면 다시 판단
                    await work_queue.expire_leases(self)  # 진행 보고가 끊긴 구독형 작업 되돌리기
                    # 앞 요청을 오래 기다리는 대화에 알린다 (상태가 안 바뀌어도 시간이 지나면)
                    await dev_chat.notice_long_waits(self)
                    await dev_chat.close_idle(self)  # 30분 말이 없는 대화를 끝낸다
                except Exception:  # noqa: BLE001 - 맞추기 실패로 루프가 멈추지 않게 한다
                    continue  # 다음 차례에 다시

        self._pr_sync = asyncio.get_running_loop().create_task(loop())

    # ---- 기획 → 개발 넘기기 (서비스 역할이 있을 때만)

    def _dev_outgoing(self, s: State, full: bool = False) -> list[tuple[str, str, BaseModel, str]]:
        """이번 액션으로 보낼 넘기기 목록 (skill, context_id, 입력, idempotency_key).

        작업·PR·요청 상태가 지난번과 달라진 기획서 (receive_dev_result).
        full이면(다시 켰을 때 맞추기) 넘긴 기획서를 모두 다시 보낸다. 같은 멱등 키라 받는 쪽이
        이미 가진 건 그대로 두고, 잃은 쪽만 다시 반영한다."""
        out: list[tuple[str, str, BaseModel, str]] = []
        for sp in s.specs:
            req = self._request_of_spec(s, sp)
            if not sp.handed_to_dev or req is None:
                continue
            tasks = [t for t in s.tasks if t.spec == sp.id]
            ids = {t.id for t in tasks}
            own = sp.origin == "dev_team"  # 개발팀 기획은 기획 서비스에 요청·기획서가 없다
            res = DevResultInput(
                spec_id=sp.id,
                request_id=req.id,
                request_state=req.state,
                tasks=[dump(t) for t in tasks],
                prs=[dump(p) for p in s.prs if p.task in ids],
                request=dump(req) if own else None,
                spec=dump(sp) if own else None,
            )
            raw = json.dumps(res.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            fp = hashlib.sha1(raw.encode()).hexdigest()[:12]
            if self._dev_sent.get(sp.id) == fp and not full:
                continue
            self._dev_sent[sp.id] = fp
            out.append(
                (RECEIVE_DEV_RESULT, req.context_id, res, dev_result_key(req.context_id, sp.id, fp))
            )
        out += self._follow_up_outgoing(s, full)
        return self._with_policy(s, out, full) + self._release_outgoing(s, full)

    # ---- 배포 전 기획서 대조 (dev → planning `request_release_check`, planning → dev 배포 지시)

    @staticmethod
    def _release_prs(s: State, sp: Spec) -> list[ReleasePr]:
        """기획서 하나에 묶인 PR과 개발 검수(review_pr) 결과. 닫힌 PR은 뺀다."""
        tasks = {t.id: t for t in s.tasks if t.spec == sp.id}
        merged = [p.id for p in s.prs if p.task in tasks and p.merged and not p.closed]
        # 앞 작업 PR이 이미 반영해 PR 없이 끝난 작업(covered): 그 문단은 반영한 PR이 맡는다
        # (반영한 PR을 모르면 이 기획서의 머지된 PR 전부)
        extra: dict[str, list[str]] = {}
        for t in tasks.values():
            if t.coding is not None and t.coding.status == "covered":
                for pid in [x for x in t.coding.covered_by if x in merged] or merged:
                    extra.setdefault(pid, []).append(t.para)
        out: list[ReleasePr] = []
        for p in s.prs:
            t = tasks.get(p.task)
            if t is None or p.closed:
                continue
            last = p.reviews[-1] if p.reviews else None
            review = "match" if last == "통과" else "mismatch" if last == "불일치" else "none"
            notes = t.coding.review.notes if t.coding and t.coding.review else []
            out.append(
                ReleasePr(
                    number=p.number,
                    task_id=t.id,
                    merged=p.merged,
                    changed_paths=list(p.changed_paths),
                    review=review,  # type: ignore[arg-type]
                    review_note=(" / ".join(notes)[:300] or None) if notes else None,
                    reviewed_paras=[t.para, *(x for x in extra.get(p.id, []) if x != t.para)],
                )
            )
        return out

    @staticmethod
    def _done_specs(s: State) -> set[str]:
        """작업이 모두 끝난 기획서. 켤 때 이미 끝나 있던 것은 배포 전 대조를 묻지 않는다."""
        return {
            sp.id
            for sp in s.specs
            if (ts := [t for t in s.tasks if t.spec == sp.id])
            and all(t.state == "완료" for t in ts)
        }

    def _release_outgoing(self, s: State, full: bool) -> list[tuple[str, str, BaseModel, str]]:
        """작업이 모두 끝난(머지된) 기획서마다 배포 전 대조를 기획에 한 번 묻는다.

        같은 PR 묶음(지문)이면 다시 묻지 않는다. 켤 때 이미 끝나 있던 기획서와 다시 켠 뒤 맞추기
        (`full`)에서는 묻지 않는다 (상태 맞추기가 아니라 일이라서). 판정·배포 지시는 기획이 하고,
        지시가 오면 `receive_release_order`로 지금의 배포 요청 흐름(prod·보호 경로는 사람 승인)을
        탄다."""
        out: list[tuple[str, str, BaseModel, str]] = []
        if full:
            return out
        base = getattr(self, "_release_base", set())
        for sp in s.specs:
            tasks = [t for t in s.tasks if t.spec == sp.id]
            req = self._request_of_spec(s, sp)
            if sp.id in base or not tasks or req is None or any(t.state != "완료" for t in tasks):
                continue
            prs = self._release_prs(s, sp)
            if not any(p.merged for p in prs):
                continue
            inp = RequestReleaseCheckInput(
                spec_id=sp.id,
                request_id=req.id,
                prs=prs,
                summary=f"{sp.id} 작업 {len(tasks)}개 머지 → 배포 전 대조",
            )
            raw = json.dumps(
                [p.model_dump(mode="json") for p in prs], sort_keys=True, ensure_ascii=False
            )
            fp = hashlib.sha1(raw.encode()).hexdigest()[:12]
            mark = f"release:{sp.id}"
            if self._dev_sent.get(mark) == fp:
                continue
            self._dev_sent[mark] = fp
            key = f"{req.context_id}:release_check:{sp.id}:{fp}"
            out.append((RELEASE_CHECK, req.context_id, inp, key))
        return out

    def _spawn_send(self, skill: str, ctx: str, payload: BaseModel, key: str) -> None:
        """배포 전 대조는 같이 나간 작업·PR 결과(receive_dev_result)가 기획에 닿은 뒤 보낸다
        (기획이 작업·PR을 모르는 채로 대조하면 task_not_merged로 잘못 보류된다)."""
        if skill != RELEASE_CHECK:
            super()._spawn_send(skill, ctx, payload, key)
            return
        before = list(self._sending)

        async def after() -> None:
            if before:
                await asyncio.gather(*before, return_exceptions=True)
            await self._send_one(skill, ctx, payload, key)

        task = asyncio.create_task(after())
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    # ---- 요청 단계별 사람 중단 (planning → dev `receive_request_pause`)

    def task_paused(self, s: State, task_id: str) -> bool:
        """이 작업의 요청이 사람 중단 중인가."""
        t = next((x for x in s.tasks if x.id == task_id), None)
        sp = next((x for x in s.specs if t and x.id == t.spec), None)
        req = self._request_of_spec(s, sp) if sp else None
        return bool(req and req.paused)

    async def receive_request_pause(
        self,
        *,
        context_id: str,
        requester: str,
        inp: RequestPauseInput,
        idempotency_key: str,
    ) -> ActionResult:
        """기획이 요청을 중단·재개했다 (A).

        중단: 돌고 있는 서버 AI 코딩은 멈추고 대기로 둔다(초안은 남김). 안 가져간 내 Claude 작업은
        대기열에서 빠지고(work_queue), 열린 PR은 자동 머지하지 않는다. 배포 지시는 거절한다.
        재개: 멈춘 작업을 다시 띄우고(초안이 있으면 이어서), 열린 PR은 자동 머지를 다시 판단한다.
        같은 멱등성 키의 두 번째 알림은 `duplicate`."""
        cached = self._idempotent.get(idempotency_key)
        if cached is not None:
            return ActionResult(
                changed=False, outcome="duplicate", output={**cached.output, "outcome": "duplicate"}
            )
        req0 = next((r for r in self.state.requests if r.id == inp.request_id), None)
        if req0 is None:
            out = {"outcome": "unknown_request", "tasks_stopped": [], "prs_held": []}
            return ActionResult(changed=False, outcome="unknown_request", output=out)
        specs = {sp.id for sp in self.state.specs if self._request_of_spec(self.state, sp) is req0}
        task_ids = [t.id for t in self.state.tasks if t.spec in specs]
        stopping = [i for i in task_ids if i in self._coding] if inp.paused else []
        for i in stopping:  # 실행부터 끊는다 (다음 단계에서 CoderGone, LLM 호출도 취소)
            self._coding_live.pop(i, None)
            running = self._coding.pop(i, None)
            if running is not None:
                running.cancel()
        restart: list[str] = []

        async def run(s: State) -> ActionResult:
            req = next(r for r in s.requests if r.id == inp.request_id)
            ctx = req.context_id or context_id
            how = "중단" if inp.paused else "재개"
            who = "사람" if inp.decided_by == "human" else inp.decided_by
            text = f"{req.id} {who}이 요청 {how}" + (f" ({inp.reason})" if inp.reason else "")
            await self._execute(
                "receive_request_pause", ctx, f"{req.id}:{how}:{len(s.activity)}", text,
                {"request": req.id, "paused": inp.paused},
            )  # fmt: skip
            req.paused = inp.paused
            tasks = [t for t in s.tasks if t.id in task_ids]
            held: list[int] = []
            if inp.paused:
                for t in tasks:
                    if t.id in stopping and t.coding is not None:
                        t.coding.status = "queued"
                        t.coding.error = f"{PAUSE} → 재개하면 이어서"
                        t.coding.updated_at = self.clock()
                for p in s.prs:
                    if p.task in task_ids and not p.merged and not p.closed:
                        held.append(p.number)
            else:
                for t in tasks:
                    cr = t.coding
                    if cr is None:
                        continue
                    if (cr.error or "").startswith(PAUSE):
                        cr.error = None
                        cr.updated_at = self.clock()
                        if cr.mode == "agent" and cr.status == "queued":
                            restart.append(t.id)
                for p in s.prs:
                    rules = p.auto_review.rules if p.auto_review else []
                    if (
                        p.task in task_ids
                        and not p.merged
                        and not p.closed
                        and ("request_paused" in rules)
                    ):
                        p.auto_review = None
                        await self._try_auto_merge(s, p)
                        t = next((x for x in tasks if x.id == p.task), None)
                        if t is not None and t.coding is not None and p.merged:
                            t.coding.status, t.coding.error = "merged", None
                        held.append(p.number)
            s.activity.append(
                self._act(self.clock(), ctx, requester, "dev", "receive_request_pause", "A2A",
                          f"{text} · 멈춘 작업 {len(stopping)} · PR {len(held)}", "A")
            )  # fmt: skip
            out = {"outcome": "applied", "tasks_stopped": stopping, "prs_held": held}
            return ActionResult(outcome="applied", output=out)

        result = await self._apply(run)
        self._idempotent[idempotency_key] = result
        for i in restart:
            self._spawn_coder(i)
        self._save()
        return result

    async def receive_release_order(
        self,
        *,
        context_id: str,
        requester: str,
        inp: ReceiveReleaseOrderInput,
        idempotency_key: str,
    ) -> ActionResult:
        """기획의 배포 지시를 받아 지금의 배포 요청 흐름을 탄다 (A).

        prod·보호 경로면 배포는 사람 승인 대기로 남는다(지시가 승인을 건너뛰지 않는다).
        같은 멱등성 키의 두 번째 지시는 `duplicate`로 처음 결과를 돌려준다."""
        cached = self._idempotent.get(idempotency_key)
        if cached is not None:
            return ActionResult(
                changed=False, outcome="duplicate", output={**cached.output, "outcome": "duplicate"}
            )

        async def run(s: State) -> ActionResult:
            req = next((r for r in s.requests if r.id == inp.request_id), None)
            ctx = req.context_id if req else context_id
            prs = {p.number: p for p in s.prs}
            unknown = [n for n in inp.pr_numbers if n not in prs]
            open_ = [n for n in inp.pr_numbers if n in prs and not prs[n].merged]
            if req is not None and req.paused:
                s.activity.append(
                    self._act(self.clock(), ctx, requester, "dev", "receive_release_order", "A2A",
                              f"{inp.check_id} 배포 지시 받지 않음: request_paused", "A")
                )  # fmt: skip
                return ActionResult(
                    outcome="rejected",
                    output={"outcome": "rejected", "reason_code": "request_paused"},
                )
            who = "사람" if inp.decided_by == "human" else "기획 AI"
            nums = ", ".join(f"#{n}" for n in inp.pr_numbers)
            if unknown or open_:
                code = "unknown_pr" if unknown else "pr_not_merged"
                text = f"{inp.check_id} 배포 지시 ({who}, {inp.env}, PR {nums}) 받지 않음: {code}"
                s.activity.append(
                    self._act(self.clock(), ctx, requester, "dev", "receive_release_order",
                              "A2A", text, "A")
                )  # fmt: skip
                return ActionResult(
                    outcome="rejected", output={"outcome": "rejected", "reason_code": code}
                )
            text = f"{inp.check_id} {inp.spec_id} 배포 지시 받음 ({who}, {inp.env}, PR {nums})"
            await self._execute(
                "receive_release_order", ctx, inp.check_id, text,
                {"spec": inp.spec_id, "env": inp.env, "prs": inp.pr_numbers,
                 "decided_by": inp.decided_by},
            )  # fmt: skip
            dep = await get_deploy_store().request(self, inp.pr_numbers, inp.env)
            wait = dep.status == "requested"
            how = f"{dep.id} 사람 승인 대기 ({', '.join(dep.approval_reasons)})" if wait else dep.id
            s.activity.append(
                self._act(self.clock(), ctx, requester, "dev", "receive_release_order", "A2A",
                          f"{text} → 배포 요청 {how}", "A")
            )  # fmt: skip
            return ActionResult(
                outcome="accepted",
                output={"outcome": "accepted", "deployment_id": dep.id, "needs_approval": wait},
            )

        result = await self._apply(run)
        self._idempotent[idempotency_key] = result
        self._save()
        return result

    def _follow_up_outgoing(self, s: State, full: bool) -> list[tuple[str, str, BaseModel, str]]:
        """장애 연결(link_incident)로 개발이 만든 재발 방지 요청을 기획 서비스에 넘긴다.

        요청 목록·기획서 작성은 기획 서비스가 맡으니, 아직 기획서가 없는 후속 요청을 요청 자체로
        보낸다 (`spec_id` 빈 값 = 기획서 없이 요청만). 기획서가 생기면 그 뒤로는 기획서 단위."""
        out: list[tuple[str, str, BaseModel, str]] = []
        for r in s.requests:
            if not r.via.startswith("link_incident") or r.spec:
                continue
            res = DevResultInput(
                spec_id="", request_id=r.id, request_state=r.state, request=dump(r)
            )
            raw = json.dumps(res.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            fp = hashlib.sha1(raw.encode()).hexdigest()[:12]
            mark = f"follow:{r.id}"
            if self._dev_sent.get(mark) == fp and not full:
                continue
            self._dev_sent[mark] = fp
            out.append(
                (RECEIVE_DEV_RESULT, r.context_id, res, dev_result_key(r.context_id, mark, fp))
            )
        return out

    async def _inbound(self, s: State, kind: str) -> ActionResult | None:
        """시뮬레이터: 다른 부서가 관련 PR을 묻거나 사건 연결·롤백 확인을 요청한 것처럼 (목)."""
        if kind in _TICKET_KINDS or kind in ("hr_draft", "data_draft"):
            raise self._wrong_service(kind, "기획")
        seq, t, _ctx_req, last_merged = self._inbound_start(s)

        def act(ctx, frm, to, point, kind_, text, grade=None):
            return self._act(t, ctx, frm, to, point, kind_, text, grade, seq)

        if kind in ("data_query", "infra_query"):
            frm = "data" if kind == "data_query" else "infra"
            ctx = s.incidents[0].context_id if s.incidents else "ctx_inc_0001"
            found = f"{last_merged.id} ({last_merged.title})" if last_merged else "후보 없음"
            ask = (
                "이상 구간 직전 배포가 있었나 질의"
                if frm == "data"
                else "장애 시각 직전 머지 PR 질의"
            )
            s.activity.append(act(ctx, frm, "planning", "find_related_prs", "A2A", ask, "read"))
            s.activity.append(
                act(
                    ctx,
                    "planning",
                    frm,
                    "find_related_prs",
                    "A2A",
                    f"관련 PR 후보 회신: {found} · 원인 판정은 하지 않음",
                    "read",
                )
            )
            return None

        if kind == "orch_incident":
            n = len(s.incidents) + 1
            while any(i.context_id == f"ctx_inc_{n:04d}" for i in s.incidents):
                n += 1
            ctx = f"ctx_inc_{n:04d}"
            pr = last_merged or (s.prs[0] if s.prs else None)
            if pr is None:
                return None
            s.activity.append(
                act(
                    ctx,
                    "orchestrator",
                    "planning",
                    "incident.opened",
                    "이벤트",
                    f"사건 {ctx} 개시 (infra.anomaly_detected 감지)",
                )
            )
            s.activity.append(
                act(
                    ctx,
                    "orchestrator",
                    "planning",
                    "link_incident",
                    "A2A",
                    f"사건을 {pr.id}에 연결 요청",
                    "A",
                )
            )
            linked = await self._link(
                s,
                ctx,
                [pr],
                title=f"결제 API 응답 지연 (시뮬레이터 {n}회차)",
                follow_up_title=f"{pr.id} 관련 재발 방지 검토",
                follow_from="infra",
                at=t,
                seq=seq,
            )
            s.activity.append(
                act(ctx, "planning", "orchestrator", "link_incident", "A2A", linked["reply"], "A")
            )
            s.activity.extend(linked["demote_acts"])
            return ActionResult(
                outcome="linked_duplicate" if linked["duplicate"] else None,
                output={
                    "incident": ctx,
                    "linked_paras": linked["linked_paras"],
                    "follow_up_request_id": linked["follow_id"],
                },
            )

        # safety_verify: infra rollback_deployment(B)의 교차검증 (시뮬레이터는 마지막 머지 PR 기준)
        ok = last_merged is not None
        ctx = s.incidents[0].context_id if s.incidents else "ctx_inc_0001"
        s.activity.append(
            act(
                ctx,
                "safety",
                "planning",
                "verify_rollback_target",
                "A2A",
                "infra rollback_deployment(B) 교차검증 요청",
                "read",
            )
        )
        reply = (
            f"승인 · 장애 직전 머지 {last_merged.id} 확인 (deploy_before_incident)"
            if last_merged
            else "거절 · 기간 안 배포 없음 (no_deploy_in_window) → completed + rejected_by_review"
        )
        s.activity.append(
            act(ctx, "planning", "safety", "verify_rollback_target", "A2A", reply, "read")
        )
        return ActionResult(outcome=None if ok else "rejected_by_review", output={"approved": ok})

    async def _advance_step(self, s: State) -> bool:
        """넘겨받은 flow 기획서는 사람 클릭 없이 PR 만들기·검수·재검수·자동 머지 검사까지 간다."""
        flow = {r.id for r in s.requests if r.flow}
        for sp in s.specs:
            if sp.request not in flow or sp.status != "확정":
                continue
            has_pr = {p.task for p in s.prs}
            todo = [
                t
                for t in s.tasks
                if t.spec == sp.id
                and t.state == "진행 중"
                and t.id not in has_pr
                and t.coding is None
            ]
            if not todo:
                continue
            req = self._request_of_spec(s, sp)
            if self._real_coding():
                # 실제 레포에 올릴 때: 작업마다 코딩 작업을 만든다 (회사 설정대로 서버 AI·내 Claude)
                await self._queue_coding(s, sp, req, todo)
            elif sp.auto and req and req.auto and req.auto.ok:
                # planning이 넘긴 간단 요청: 원래 간단 요청 경로대로 PR·머지까지 한 번에
                await self._auto_build(s, req, sp, todo[0])
            else:
                await self._generate_prs(s, sp.id)
            return True
        for p in s.prs:
            task = next((t for t in s.tasks if t.id == p.task), None)
            sp = next((x for x in s.specs if task and x.id == task.spec), None)
            if sp is None or sp.request not in flow or p.merged or p.auto_review is not None:
                continue
            if p.reviews and p.reviews[-1] == "불일치" and p.reviews.count("불일치") < 2:
                await self._review_pr(s, p.id)
                return True
        return False

    async def create_dev_task(
        self,
        *,
        title: str,
        plan: str,
        done: str | None = None,
        paths: list[str] | None = None,
        connection: str | None = None,
        skip_queue: bool = False,
        mode: str = "agent",
        assignee: str | None = None,
    ) -> ActionResult:
        """개발팀원이 쓴 기획으로 요청·확정 기획서·작업 하나를 만들고 코딩 에이전트를 띄운다 (A).

        `assignee`: 내 Claude 작업을 맡긴 사람 (그 사람의 Claude만 가져간다, 구독형만).

        에이전트는 바꿔 끼운 뒤 잠금 밖에서 돈다. 진행은 작업 `coding`에 적힌다.
        `mode="subscription"`이면 서버 에이전트를 띄우지 않고 `queued`로 두어 개발자 본인 Claude가
        MCP로 가져가기를 기다린다 (work_queue.py). LLM 설정도 필요 없다."""
        if mode not in ("agent", "subscription"):
            raise ActionError("INVALID_INPUT", "mode는 agent·subscription 중 하나다")
        sub = mode == "subscription"
        over = None if sub else self.budget_over(self.state)
        if over:
            code = "BUDGET_EXHAUSTED" if "예산" in over else "TRIAL_EXHAUSTED"
            raise ActionError(code, f"{over}. 내 Claude로")
        title = " ".join((title or "").split())
        plan = (plan or "").strip()
        if not title or not plan:
            raise ActionError("INVALID_INPUT", "title과 plan은 비울 수 없다")
        if len(plan) > coder.MAX_PLAN_CHARS:
            raise ActionError("INVALID_INPUT", f"plan은 {coder.MAX_PLAN_CHARS}자까지다")
        hints = vcs.clean_paths(paths)
        cid = self._coding_connection(connection)
        llm = None if sub else self._coding_llm()
        done_lines = [x.strip() for x in (done or "").splitlines() if x.strip()]
        done_lines = done_lines[: coder.MAX_DONE_LINES] or [coder.DEFAULT_DONE]

        async def run(s: State) -> ActionResult:
            req_id, spec_id, task_id, ctx = self._dev_ids(s)
            t = self.clock()
            who = "개발자 Claude" if sub else "코딩 에이전트"
            text = (
                f'{task_id} 개발팀 기획 "{title}" → {who} 대기 '
                f"(완료 기준 {len(done_lines)}, 연결 {cid})"
            )
            params = {"request": req_id, "spec": spec_id, "connection": cid, "paths": list(hints)}
            if sub:
                params["mode"] = mode
            await self._execute(
                "dev_task_create", ctx, task_id, text, params, [req_id, spec_id, task_id]
            )
            s.requests.append(
                Request(
                    id=req_id,
                    context_id=ctx,
                    tenant_id=self.tenant_id,
                    from_="dev",
                    via=coder.DEV_TEAM,
                    title=title,
                    evidence="개발팀 기획 (개발 화면)",
                    state="작업 중",
                    spec=spec_id,
                )
            )
            s.specs.append(
                Spec(
                    id=spec_id,
                    title=title,
                    request=req_id,
                    status="확정",
                    problem=plan,
                    goal=title,
                    paras=[
                        Para(id="R1", kind="req", text=title, paths=list(hints)),
                        *[
                            Para(id=f"C{i}", kind="done", text=line)
                            for i, line in enumerate(done_lines, 1)
                        ],
                    ],
                    basis=["개발팀 기획 (개발 화면)"],
                    human_sentences=1,
                    handed_to_dev=True,
                    origin=coder.DEV_TEAM,
                )
            )
            s.tasks.append(
                Task(
                    id=task_id,
                    spec=spec_id,
                    para="R1",
                    title=title,
                    owner="개발자 Claude" if sub else "에이전트",
                    state="진행 중",
                    coding=CodingRun(
                        status="queued",
                        model=None if sub else coder.model_name(llm),
                        updated_at=t,
                        connection=cid,
                        skip_queue=skip_queue,
                        mode="subscription" if sub else "agent",
                        assignee=assignee if sub else None,
                    ),
                )
            )
            made = s.tasks[-1]
            assert made.coding is not None
            made.coding.text_warnings = _text_warnings(s, made)
            s.activity.append(self._act(t, ctx, "dev", "dev", "dev_task_create", "A2A", text, "A"))
            return ActionResult(
                outcome="queued",
                output={
                    "request_id": req_id,
                    "spec_id": spec_id,
                    "task_id": task_id,
                    "status": "queued",
                },
            )

        result = await self._apply(run)
        if not sub:  # 구독형은 개발자 Claude가 가져갈 때까지 대기열에 둔다
            self._spawn_coder(result.output["task_id"])
        return result

    def reset(self) -> None:
        """초기 데이터로 되돌린다. 돌던 코딩 실행은 다음 단계에서 멈추고 작성해 둔 코드도 지운다."""
        self._coding_live.clear()
        coder.clear_drafts(self)
        super().reset()
        self._release_base = self._done_specs(self.state)
        get_deploy_store().reset()  # 배포 기록도 시드로

    async def _send_hr(
        self,
        ctx: str,
        kind: RecordKind,
        key: str,
        case: str,
        summary: str,
        reason: str,
        refs: list[str],
        area: str | None = None,
    ) -> HrRecordResult | None:
        """인사 `record_case`로 PR 머지·요청 완료를 보낸다 (인사 연동 2단계).

        목 모드(`_hr` None)면 예전처럼 관문 기록만 남긴다. 실패해도 머지·완료는 되돌리지 않고
        "인사 기록 실패"만 `soft_holds`·활동에 남긴다."""
        record = RecordCase(
            context_id=ctx,
            tenant_id=self.tenant_id,
            kind=kind,
            summary=summary,
            decision_reason=reason,
            source_refs=refs,
            author=AGENT_NAME,
            area=area,
        )
        gate = await self._execute(
            "record_case", ctx, key, case, {"record_case": record.model_dump(mode="json")}, refs
        )
        if self._hr is None or gate.status != "executed":
            return None
        res = await self._hr.record(record, key)
        if not res.ok:
            self.soft_holds.append(GateCall("record_case", key, "hr_failed", "-", res.reason or ""))
        return res

    @staticmethod
    def _hr_area(s: State, prs: list[PR], *texts: str) -> str | None:
        """인사 기록 영역: PR이 바꾼 경로 → 없으면 기획서·요청 제목 (못 정하면 None)."""
        return area_of_paths([x for p in prs for x in p.changed_paths]) or area_of_text(*texts)

    @staticmethod
    def _hr_titles(s: State, spec_id: str | None) -> list[str]:
        """영역을 글로 정할 때 볼 제목: 기획서 제목, 그 기획서의 요청 제목."""
        sp = next((x for x in s.specs if x.id == spec_id), None)
        return [
            x
            for x in [
                sp.title if sp else "",
                *(r.title for r in s.requests if spec_id and r.spec == spec_id),
            ]
            if x
        ]

    def _hr_act(self, ctx: str, case: str, hr: HrRecordResult | None) -> Activity:
        """인사 기록 활동 한 줄. 실제로 불렀으면 결과(카드·실패)를 붙인다."""
        text = case if hr is None else f"{case} → {hr.text}"
        grade = "A" if hr is None or hr.ok else None
        return self._act(self.clock(), ctx, "planning", "hr", "record_case", "A2A", text, grade)

    async def tick(self) -> None:
        """화면이 열려 있는 동안 오래된 교차검증 대기를 사람 확인으로 넘긴다."""
        await self.escalate_waits()

    def _mark(self, s: State) -> dict[str, Any]:
        mark = super()._mark(s)
        mark["failed"] = {t.id for t in s.tasks if t.coding and t.coding.status == "failed"}
        mark["pr_human"] = {
            p.id for p in s.prs if p.auto_review is not None and p.auto_review.state == WAIT_HUMAN
        }
        return mark

    def _after(self, s: State, mark: dict[str, Any], changed: bool) -> None:
        super()._after(s, mark, changed)
        if changed:
            self._spawn_more_holds(s, mark)
        spawn, self._pending_spawn = self._pending_spawn, []
        for task_id in spawn if changed else []:  # 넘겨받은 요청 작업 → 서버 AI 코딩
            self._spawn_coder(task_id)

    def _spawn_more_holds(self, s: State, mark: dict[str, Any]) -> None:
        """사람이 봐야 하는데 받은 요청에 안 가던 두 가지도 올린다 (사람 확인 멈춤은 base가 올린다).

        - 코딩 작업이 새로 실패함 → 다시 시도·폐기·기획에 돌려보내기 판단
        - PR 머지가 사람 확인으로 새로 넘어감 (교차검증 응답 없음·근거에 걸림 등) → 머지·보류 판단
          그 작업이 이미 사람 확인 멈춤으로 올라갔으면 다시 올리지 않는다"""
        for t in s.tasks:
            cr = t.coding
            if cr and cr.status == "failed" and t.id not in mark.get("failed", set()):
                self._spawn_hold(
                    t.id,
                    f"개발 AI 작업 실패: {cr.error or '이유 없음'} → 다시 시도·폐기·돌려보내기",
                    f"dev_fail:{t.id}:{cr.updated_at.isoformat()}",
                )
        for p in s.prs:
            hold = p.auto_review
            if hold is None or hold.state != WAIT_HUMAN or p.id in mark.get("pr_human", set()):
                continue
            task = next((t for t in s.tasks if t.id == p.task), None)
            if task is None or task.coding is None or task.coding.status == "waiting_human":
                continue
            why = ", ".join(hold.rules) or "자동 머지 기준 밖"
            self._spawn_hold(
                task.id,
                f"{p.id} 머지 사람 확인: {why}",
                f"dev_pr_hold:{p.id}:{hold.at.isoformat()}",
            )

    # ---- 요청 작업 코딩 방식 (회사 설정) · 서버 AI 체험 한도

    @staticmethod
    def _real_coding() -> bool:
        """넘겨받은 요청 작업을 실제 코딩 작업으로 돌리나 (아니면 시연용 PR 기록).

        목 모드가 아니고 형상관리 연결이 있으면 돌린다. `PLANNING_REQUEST_CODING=1`이면 늘."""
        if os.getenv("PLANNING_REQUEST_CODING", "").strip().lower() in ("1", "true", "on"):
            return True
        return not template_config.is_mock(AGENT_NAME) and bool(vcs_bridge.targets())

    def month_key(self) -> str:
        """월 예산을 세는 달 (KST 기준 YYYY-MM)."""
        return self.clock().astimezone(KST).strftime("%Y-%m")

    def month_used(self, s: State) -> int:
        return s.usage_by_month.get(self.month_key(), 0)

    def budget_over(self, s: State) -> str | None:
        """서버 AI를 더 못 쓰는 이유 (없으면 None).

        고객사 키(BYOK)면 월 예산만, 기본 키면 체험 한도와 월 예산 둘 다 본다."""
        budget = s.monthly_budget_tokens
        if budget is not None and self.month_used(s) >= budget:
            return f"이번 달 서버 AI 예산을 다 씀 ({self.month_used(s):,}/{budget:,} 토큰)"
        used, limit = self.trial_used(s), self.trial_limit()
        if not llm_key.active() and used >= limit:
            return f"서버 AI 체험 한도를 다 씀 ({used:,}/{limit:,} 토큰)"
        return None

    async def set_budget(self, tokens: int | None) -> ActionResult:
        """회사 설정: 서버 AI 코딩 월 예산 (토큰, None이면 없음. 관리자)."""
        if tokens is not None and (
            isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0 or tokens > 10**12
        ):
            raise ActionError("INVALID_INPUT", "월 예산은 0 이상 정수(토큰)거나 비워야 한다")

        async def run(s: State) -> ActionResult:
            if s.monthly_budget_tokens != tokens:
                shown = f"{tokens:,} 토큰" if tokens is not None else "없음"
                s.monthly_budget_tokens = tokens
                s.activity.append(
                    self._act(self.clock(), "dev_settings", "human", "dev", "dev.settings", "사람",
                              f"서버 AI 월 예산 → {shown}")
                )  # fmt: skip
            return ActionResult(output=self.coding_settings(s))

        return await self._apply(run)

    @staticmethod
    def trial_limit() -> int:
        """서버 AI 체험 한도 (회사 전체, 입력+출력 토큰). `PLANNING_TRIAL_TOKENS`."""
        return coder.limit("PLANNING_TRIAL_TOKENS", TRIAL_TOKENS)

    @staticmethod
    def trial_used(s: State) -> int:
        """서버 AI로 돈 코딩 작업이 지금까지 쓴 토큰."""
        return sum(t.coding.llm_tokens for t in s.tasks if t.coding and t.coding.mode == "agent")

    @staticmethod
    def coding_estimate(s: State) -> dict[str, Any]:
        """서버 AI 코딩 한 건의 예상 규모: 지난 작업(파일을 다 쓴 것)의 중앙값.

        화면이 대기 중인 작업에 "예상 토큰·호출·파일"을 보여 준다. 기록이 없으면 samples 0."""
        done = ("opened", "merged", "waiting_human", "covered")
        runs = [
            t.coding for t in s.tasks
            if t.coding is not None and t.coding.mode == "agent"
            and t.coding.status in done and t.coding.llm_tokens > 0
        ]  # fmt: skip

        def mid(xs: list[int]) -> int:
            xs = sorted(xs)
            return xs[len(xs) // 2] if xs else 0

        return {
            "samples": len(runs),
            "tokens": mid([r.llm_tokens for r in runs]),
            "tokens_max": max((r.llm_tokens for r in runs), default=0),
            "calls": mid([r.llm_calls for r in runs]),
            "files": mid([len(r.files) for r in runs]),
        }

    def agents_connected(self, s: State) -> list[str]:
        """최근 본인 Claude(MCP)로 작업을 본 개발자."""
        since = self.clock() - timedelta(days=AGENT_SEEN_DAYS)
        return sorted(who for who, at in s.agents_seen.items() if at >= since)

    def coding_choice(self, s: State) -> tuple[str, str]:
        """넘겨받은 요청 작업을 누가 코딩하나 (agent·subscription)와 그 이유 한 줄."""
        if s.coding_mode == "subscription":
            return "subscription", "회사 설정: 내 Claude"
        seen = self.agents_connected(s)
        if s.coding_mode == "auto" and seen:
            return "subscription", f"본인 Claude 연결 {len(seen)}명 → 내 Claude"
        over = self.budget_over(s)
        if over:
            return "subscription", f"{over} → 내 Claude"
        if s.coding_mode == "agent":
            return "agent", "회사 설정: 서버 AI" + (" (고객사 키)" if llm_key.active() else "")
        if llm_key.active():
            return "agent", "본인 Claude 연결 없음 → 서버 AI (고객사 키)"
        return "agent", "본인 Claude 연결 없음 → 서버 AI (체험)"

    def _task_connection(self, sp: Spec, req: Request | None, t: Task) -> str | None:
        """작업을 올릴 연결: 하나면 그것, 여럿이면 규칙 점수로 하나 (없으면 None)."""
        conns = vcs_bridge.targets()
        if len(conns) <= 1:
            return conns[0].id if conns else None
        para = next((p for p in sp.paras if p.id == t.para), None)
        inp = repo_routing.RouteInput(
            title=t.title,
            text=para.text if para else "",
            changed_paths=tuple(para.paths) if para else (),
            request_title=req.title if req else "",
        )
        cands = [repo_routing.Candidate(c.id, c.repo, c.description, tuple(c.paths)) for c in conns]
        return repo_routing.score_rules(inp, cands).connection_id

    async def _queue_coding(
        self, s: State, sp: Spec, req: Request | None, todo: list[Task]
    ) -> None:
        """넘겨받은 기획서의 작업마다 코딩 작업을 만든다. 서버 AI면 바꿔 끼운 뒤 띄우고,
        내 Claude면 대기열에 둔다 (개발자 본인 Claude가 MCP로 가져감)."""
        mode, why = self.coding_choice(s)
        llm = coder.get_llm() if mode == "agent" else None
        if mode == "agent" and llm is None:
            mode, why = "subscription", "서버 AI 설정(LLM) 없음 → 내 Claude"
        ctx = req.context_id if req else f"spec:{sp.id}"
        for t in todo:
            cid = self._task_connection(sp, req, t)
            who = "코딩 에이전트" if mode == "agent" else "개발자 Claude"
            text = f"{t.id} 요청 작업 → {who} 대기 ({why}, 연결 {cid or '-'})"
            await self._execute(
                "dev_task_create", ctx, t.id, text,
                {"spec": sp.id, "connection": cid, "mode": mode, "from_request": True},
                [sp.id, t.id],
            )  # fmt: skip
            t.coding = CodingRun(
                status="queued",
                model=coder.model_name(llm) if llm else None,
                updated_at=self.clock(),
                connection=cid,
                mode=mode,  # type: ignore[arg-type]
            )
            t.coding.text_warnings = _text_warnings(s, t)
            if mode == "agent":
                self._pending_spawn.append(t.id)
            s.activity.append(
                self._act(self.clock(), ctx, "dev", "dev", "dev_task_create", "A2A", text, "A")
            )

    async def set_coding_models(self, models: dict[str, Any]) -> ActionResult:
        """회사 설정: 서버 AI 코딩 단계별 모델 (관리자). 빈 값이면 그 단계는 기본 모델."""
        bad = [k for k in models if k not in coder.STEPS]
        if bad:
            raise ActionError("INVALID_INPUT", f"단계는 {', '.join(coder.STEPS)} 중 하나다: {bad}")
        clean: dict[str, str] = {}
        for k, v in models.items():
            name = str(v or "").strip()
            if len(name) > 100 or any(ch.isspace() for ch in name):
                raise ActionError("INVALID_INPUT", f"{k} 모델 이름이 올바르지 않다")
            if name:
                clean[k] = name

        async def run(s: State) -> ActionResult:
            if s.coding_models != clean:
                shown = ", ".join(f"{k}={v}" for k, v in clean.items()) or "모두 기본 모델"
                s.coding_models = clean
                s.activity.append(
                    self._act(self.clock(), "dev_settings", "human", "dev", "dev.settings", "사람",
                              f"코딩 단계별 모델 → {shown}")
                )  # fmt: skip
            return ActionResult(output=self.coding_settings(s))

        return await self._apply(run)

    async def set_coding_mode(self, mode: str) -> ActionResult:
        """회사 설정: 넘겨받은 요청 작업을 누가 코딩하나 (auto·agent·subscription, 관리자)."""
        if mode not in ("auto", "agent", "subscription"):
            raise ActionError("INVALID_INPUT", "mode는 auto·agent·subscription 중 하나다")

        moved: list[str] = []

        async def run(s: State) -> ActionResult:
            if s.coding_mode != mode:
                text = f"요청 작업 코딩 방식 {s.coding_mode} → {mode}"
                s.coding_mode = mode  # type: ignore[assignment]
                s.activity.append(
                    self._act(
                        self.clock(), "dev_settings", "human", "dev", "dev.settings", "사람", text
                    )
                )
                # 이제 서버 AI가 코딩하면: 아직 아무도 안 가져간 개발자 Claude 대기 작업도 넘긴다
                if self.coding_choice(s)[0] == "agent" and coder.get_llm() is not None:
                    for t in self._unclaimed(s):
                        await self._to_agent(s, t, f"코딩 방식 {mode} → 서버 AI로 넘김")
                        moved.append(t.id)
            return ActionResult(output={**self.coding_settings(s), "moved_to_agent": moved})

        result = await self._apply(run)
        for task_id in moved:
            self._spawn_coder(task_id)
        return result

    @staticmethod
    def _unclaimed(s: State) -> list[Task]:
        """개발자 Claude 대기열에 있고 아직 아무도 안 가져간 작업."""
        return [
            t for t in s.tasks
            if t.coding is not None and t.coding.mode == "subscription"
            and t.coding.status == "queued" and not t.coding.claimed_by
        ]  # fmt: skip

    async def _to_agent(self, s: State, x: Task, why: str) -> None:
        """구독형 대기 작업을 서버 코딩 에이전트로 넘긴다 (관문 dev_task_to_agent 뒤)."""
        assert x.coding is not None
        sp = next((p for p in s.specs if p.id == x.spec), None)
        req = self._request_of_spec(s, sp) if sp else None
        ctx = req.context_id if req else f"task:{x.id}"
        llm = coder.get_llm()
        text = f"{x.id} {why} (연결 {x.coding.connection})"
        n = sum(1 for g in self.gate_calls if g.action == "dev_task_to_agent") + 1
        await self._execute(
            "dev_task_to_agent", ctx, f"{x.id}:{n}", text, {"connection": x.coding.connection}
        )
        x.coding.mode = "agent"
        x.coding.model = coder.model_name(llm) if llm else None
        x.coding.progress = None
        x.coding.lease_until = None
        x.coding.updated_at = self.clock()
        s.activity.append(self._act(self.clock(), ctx, "dev", "dev", "dev_task_to_agent", "A2A",
                                    text, "A"))  # fmt: skip

    async def hand_to_agent(self, task_id: str) -> ActionResult:
        """사람이 '서버 AI로 넘기기': 개발자 Claude 대기열에서 아무도 안 가져간 작업을 서버 코딩
        에이전트가 맡는다. 이미 가져간 작업은 그 개발자가 놓거나 기한이 지나야 넘길 수 있다."""
        task = next((t for t in self.state.tasks if t.id == task_id), None)
        if task is None or task.coding is None:
            raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
        if task.coding.mode != "subscription" or task.coding.status != "queued":
            raise ActionError("NOT_MOVABLE", f"{task_id}는 개발자 Claude 대기 중이 아니다")
        if task.coding.claimed_by:
            raise ActionError(
                "NOT_MOVABLE", f"{task_id}는 {task.coding.claimed_by}가 가져갔다 → 놓은 뒤 넘기기"
            )
        if coder.get_llm() is None:
            raise ActionError("NOT_MOVABLE", "서버 AI 설정(LLM)이 없어 넘길 수 없다")
        over = self.budget_over(self.state)
        if over:
            raise ActionError("NOT_MOVABLE", f"{over} → 서버 AI로 넘길 수 없다")

        async def run(s: State) -> ActionResult:
            x = next((t for t in s.tasks if t.id == task_id), None)
            if x is None or x.coding is None:
                raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
            await self._to_agent(s, x, "사람이 서버 AI로 넘김 (사람 클릭)")
            return ActionResult(
                outcome="queued", output={"task_id": task_id, "status": "queued", "mode": "agent"}
            )

        result = await self._apply(run)
        self._spawn_coder(task_id)
        return result

    def coding_settings(self, s: State | None = None) -> dict[str, Any]:
        s = s or self.state
        mode, why = self.coding_choice(s)
        return {
            "mode": s.coding_mode,
            "resolved": mode,
            "why": why,
            "agents_connected": self.agents_connected(s),
            "trial_used": self.trial_used(s),
            "trial_limit": self.trial_limit(),
            # 코딩 실행 한 번의 한도 (화면이 작업마다 쓴 양을 비율로 보여 준다)
            "run_max_calls": coder.limit("PLANNING_CODER_MAX_CALLS", coder.RUN_MAX_CALLS),
            "run_max_tokens": coder.limit("PLANNING_CODER_MAX_TOKENS", coder.RUN_MAX_TOKENS),
            # 서버 AI 단계별 모델 (plan·write·review). 비운 단계는 기본 모델 `model_default`
            "models": coder.step_models(s.coding_models),
            "model_default": coder.model_name(coder.get_llm()),
            # 고객사 API 키(BYOK): 넣었는지·끝 4자리만. 있으면 체험 한도 대신 월 예산만 본다
            "company_key": llm_key.public(),
            "budget": {
                "month": self.month_key(),
                "limit": s.monthly_budget_tokens,
                "used": self.month_used(s),
            },
            # 대기 중인 작업에 보여 줄 예상 규모 (지난 서버 AI 코딩의 중앙값)
            "estimate": self.coding_estimate(s),
        }

    def me(self, who: str | None) -> dict[str, Any]:
        """지금 화면을 쓰는 사람과 그 사람의 본인 Claude 연결 (사람 토큰 이름 기준)."""
        seen = self.state.agents_seen.get(who) if who else None
        return {
            "name": who,
            "claude_connected": bool(who) and who in self.agents_connected(self.state),
            "claude_seen_at": seen.isoformat() if seen else None,
            "team": [
                {"name": n, "seen_at": at.isoformat()}
                for n, at in sorted(
                    self.state.agents_seen.items(), key=lambda x: x[1], reverse=True
                )
            ],
        }

    async def mark_agent_seen(self, who: str) -> None:
        """본인 Claude(MCP 작업 대기열 서버)로 작업을 본 개발자를 적는다 (10분에 한 번만)."""
        now = self.clock()
        last = self.state.agents_seen.get(who)
        if last is not None and now - last < timedelta(minutes=10):
            return

        async def run(s: State) -> None:
            s.agents_seen[who] = now

        await self._apply(run)

    def _kick_waiting(self) -> None:
        """충돌하는 요청이 끝나기를 기다리는 대화가 있으면 깨운다 (dev_chat.resume_waiting)."""
        if self._resuming or not any(c.status == "waiting" for c in self.state.chats):
            return
        from dev_agent import dev_chat

        async def go() -> None:
            self._resuming = True
            try:
                await dev_chat.resume_waiting(self)
            finally:
                self._resuming = False
            # 다른 서비스 요청을 기다리면 이 서버 상태가 안 바뀌어도 풀려야 하니
            # 주기적으로 다시 본다
            if self._polling_wait or not self._remote_wait():
                return
            self._polling_wait = True
            try:
                await asyncio.sleep(WAIT_POLL_SECONDS)
            finally:
                self._polling_wait = False
            self._kick_waiting()

        try:
            task = asyncio.get_running_loop().create_task(go())
        except RuntimeError:  # 돌고 있는 루프가 없으면(동기 시험) 깨우지 않는다
            return
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    def _remote_wait(self) -> bool:
        """기다리는 대화 중 이 서버에 없는(다른 서비스) 요청을 기다리는 것이 있나."""
        mine = {r.id for r in self.state.requests}
        return any(
            c.status == "waiting" and any(rid not in mine for rid in c.waiting_for)
            for c in self.state.chats
        )

    async def preview_conflicts(self, title: str, text: str) -> list[RelatedItem]:
        """아직 만들지 않은 요청이 진행 중인 요청과 충돌하는지 미리 본다 (개발 AI 대화, 읽기만)."""
        if self._relater is None:
            return []
        draft = Request(
            id="REQ-NEW",
            context_id="chat:preview",
            from_="dev",
            via=coder.DEV_TEAM,
            title=f"{title}\n{text}"[:600],
            evidence="개발 AI 대화",
            state="접수",
        )
        try:
            draft.related = await self._relater.relate(self.state, draft)
        except Exception:  # noqa: BLE001 - 미리 보기가 안 되면 묻지 않고 진행한다
            return []
        return related.open_conflicts(self.state, draft)

    def _relate_paths(self, s: State, known_prs: set[str]) -> None:
        """새로 열린 PR이 다른 열린 PR과 같은 파일을 고치면 두 요청에 서로 충돌 가능으로 표시."""
        for pr in [p for p in s.prs if p.id not in known_prs and not p.merged]:
            mine = related.request_of_pr(s, pr)
            hits = related.path_overlaps(s, pr)
            if mine is None or not hits:
                continue
            for hit in hits:
                if not any(x.id == hit.id and x.by == "path" for x in mine.related):
                    mine.related.append(hit)
                other = next((r for r in s.requests if r.id == hit.id), None)
                if other is not None and not any(
                    x.id == mine.id and x.by == "path" for x in other.related
                ):
                    other.related.append(
                        hit.model_copy(
                            update={
                                "id": mine.id,
                                "title": mine.title,
                                "reason": f"{pr.id}가 " + hit.reason.split("와 ", 1)[-1],
                            }
                        )
                    )
            s.activity.append(
                self._act(
                    self.clock(),
                    mine.context_id,
                    "planning",
                    "planning",
                    "request.related",
                    "API",
                    f"{pr.id} 같은 파일을 고치는 열린 PR: "
                    + ", ".join(f"{h.id} ({h.reason})" for h in hits),
                    "read",
                )
            )

    def _with_policy(
        self, s: State, out: list[tuple[str, str, BaseModel, str]], full: bool
    ) -> list[tuple[str, str, BaseModel, str]]:
        """dev: 자동 처리 범위가 지난번 보낸 것과 다르면 이번 결과 하나에 실어 보낸다.

        보낼 결과가 없으면 마지막으로 넘겨받은 기획서의 결과를 다시 보낸다 (planning이 간단 요청
        판정에 같은 범위를 쓰도록). 범위 지문은 `_dev_sent["auto_policy"]`에 남긴다."""
        pol = dump(s.auto_policy)
        fp = hashlib.sha1(json.dumps(pol, sort_keys=True).encode()).hexdigest()[:12]
        if self._dev_sent.get(POLICY_SENT) == fp and not full:
            return out
        if not out:
            last = next(
                (sp for sp in reversed(s.specs)
                 if sp.handed_to_dev and self._request_of_spec(s, sp) is not None),
                None,
            )  # fmt: skip
            if last is None:
                return out
            req = self._request_of_spec(s, last)
            assert req is not None
            ids = {t.id for t in s.tasks if t.spec == last.id}
            res = DevResultInput(
                spec_id=last.id,
                request_id=req.id,
                request_state=req.state,
                tasks=[dump(t) for t in s.tasks if t.spec == last.id],
                prs=[dump(p) for p in s.prs if p.task in ids],
            )
            out.append((RECEIVE_DEV_RESULT, req.context_id, res, ""))
        skill, ctx, res, key = out[-1]
        assert isinstance(res, DevResultInput)
        res.auto_policy = pol
        out[-1] = (skill, ctx, res, dev_result_key(ctx, res.spec_id, f"policy-{fp}"))
        self._dev_sent[POLICY_SENT] = fp
        return out

    async def _hold_to_inbox(
        self, task_id: str, reason: str | None = None, key: str | None = None
    ) -> None:
        """사람 확인으로 멈춘 코딩 작업을 받은 요청에 올리고 활동에 남긴다 (잠금 밖에서 보낸다).

        `reason`·`key`는 작업 실패·PR 머지 사람 확인처럼 다른 이유로 올릴 때 쓴다."""
        task = next((t for t in self.state.tasks if t.id == task_id), None)
        if task is None or task.coding is None:
            return
        pr = next((p for p in self.state.prs if p.id == task.coding.pr), None)
        sp = next((x for x in self.state.specs if x.id == task.spec), None)
        req = self._request_of_spec(self.state, sp) if sp else None
        ctx = req.context_id if req else f"task:{task_id}"
        body = cause_inbox.hold_handoff(task, pr, ctx, self.tenant_id, reason=reason, key=key)
        text = await cause_inbox.send_hold(body, self._cause_poster)
        if text is None:
            return

        async def log(s: State) -> None:
            s.activity.append(
                self._act(self.clock(), ctx, "dev", "human", "handoff", "A2A", text, "A")
            )

        await self._apply(log)

    async def sync_prs(self) -> int:
        """사람이 GitHub 등에서 직접 머지하거나 닫은 PR을 기록에 맞춘다. 맞춘 개수.

        머지됐으면 사람 머지와 같은 길(기록·요청 닫기·작업 완료)로, 닫혔으면 작업을 중단으로
        둔다 (재개하면 최신 코드로 다시 쓴다). 돌고 있는 코딩 작업의 PR은 건드리지 않는다."""
        live = [
            p for p in self.state.prs
            if p.vcs_number is not None and not p.merged and not p.closed
            and p.task not in self._coding
        ]  # fmt: skip
        seen = {p.id: await vcs_bridge.change_state(p) for p in live}
        done = {k: v for k, v in seen.items() if v in ("merged", "closed")}
        if not done:
            return 0

        async def run(s: State) -> None:
            for p in s.prs:
                how = done.get(p.id)
                if how is None or p.merged or p.closed:
                    continue
                ctx = self._ctx_of_pr(s, p)
                where = "형상관리에서 직접 " + ("머지됨" if how == "merged" else "닫힘")
                s.activity.append(
                    self._act(self.clock(), ctx, "human", "dev", "vcs.synced", "API",
                              f"{p.id} {where} → 기록 맞춤")
                )  # fmt: skip
                task = next((t for t in s.tasks if t.id == p.task), None)
                if how == "merged":
                    try:
                        await self._do_merge(s, p, pr_check(s, p), auto=False)
                    except GateRefused:
                        pass
                    if not p.merged:  # 이미 머지된 사실은 관문·병합 호출 결과와 상관없이 맞춘다
                        p.merged = True
                        p.merged_at = self.clock()
                        if task is not None:
                            task.state = "완료"
                    p.auto_review = None
                    continue
                p.closed = True
                p.auto_review = None
                if task is not None:
                    task.state = "중단"
                    cr = task.coding
                    if cr is not None and cr.status not in ("merged", "canceled"):
                        cr.status = "canceled"
                        cr.error = f"{p.id} {where} → 재개하면 최신 코드로 다시 씀"
                        cr.updated_at = self.clock()

        await self._apply(run)
        return len(done)

    async def recheck_ci(self) -> int:
        """CI 실패·시간 초과로 사람 확인에 둔 PR의 CI를 다시 본다. 다시 판단한 PR 수.

        나중에 CI가 통과하면(사람이 테스트 레포를 고친 경우 등) 사람 확인을 풀고 자동 머지 규칙을
        다시 돌린다. 규칙에 걸리면 그 이유로 다시 기다린다. 아직 실패·진행 중이면 그대로 둔다."""
        held = [
            p for p in self.state.prs
            if p.vcs_number is not None and not p.merged and not p.closed
            and p.task not in self._coding
            and any(t.id == p.task and t.coding is not None and t.coding.ci_hold
                    and t.coding.status == "waiting_human" for t in self.state.tasks)
        ]  # fmt: skip
        passed = {p.id for p in held if await vcs_bridge.checks_state(p) == "passed"}
        if not passed:
            return 0

        async def run(s: State) -> None:
            for p in s.prs:
                task = next((t for t in s.tasks if t.id == p.task), None)
                cr = task.coding if task is not None else None
                if p.id not in passed or cr is None or not cr.ci_hold or p.merged or p.closed:
                    continue
                ctx = self._ctx_of_pr(s, p)
                cr.ci_hold = False
                p.auto_review = None
                s.activity.append(
                    self._act(self.clock(), ctx, "dev", "dev", "code.ci", "API",
                              f"{p.id} CI 나중에 통과 → 자동 머지 다시 판단")
                )  # fmt: skip
                await self._try_auto_merge(s, p)
                cr.updated_at = self.clock()
                if p.merged:
                    cr.status, cr.error = "merged", None
                elif p.auto_review is not None:
                    why = ", ".join(p.auto_review.rules) or f"safety {p.auto_review.gate_status}"
                    cr.error = f"CI 통과 · 자동 머지 안 함 → {p.auto_review.state} ({why})"
                else:
                    cr.error = f"CI 통과 → {p.id} 사람 머지 버튼으로 머지"

        await self._apply(run)
        return len(passed)

    @staticmethod
    def _find_pr(s: State, ref: str | int) -> PR | None:
        if isinstance(ref, int):
            return next((p for p in s.prs if p.number == ref), None)
        found = next((p for p in s.prs if p.id == ref), None)
        if found:
            return found
        digits = re.sub(r"\D", "", ref)
        return next((p for p in s.prs if digits and p.number == int(digits)), None)

    async def _cross_check(
        self, s: State, p: PR, check: Check, ctx: str
    ) -> tuple[Check, CrossEvidence | None]:
        """장애 났던 경로(`pr_linked_incident`)에 걸리면 다른 부서 근거로 그 규칙을 대신한다.

        총괄·데이터분석에 묻고 `cross_check=true`와 근거를 params에 더한다. 근거에 걸리면
        `merge_cross_*` 규칙이 남아 사람 확인으로 간다. 최종 판정은 safety가 다시 한다."""
        if "pr_linked_incident" not in check.rules or self._cross_checker is None:
            return check, None
        ev = await self._cross_checker.gather(s, p, ctx, self.clock())
        rules = [r for r in check.rules if r != "pr_linked_incident"] + ev.rules
        # 통과해 머지되거나 사람이 결정한 뒤에도 PR 화면에 남긴다 (auto_review는 그때 비워진다)
        p.cross_evidence = [line.dump() for line in ev.lines]
        p.cross_checked_at = self.clock()
        s.activity.append(
            self._act(
                self.clock(),
                ctx,
                "planning",
                "orchestrator",
                "cross_check",
                "A2A",
                f"{p.id} 교차검증 근거: "
                + " · ".join(f"{x.label} {x.value}" for x in ev.lines)
                + (f" → 걸림 {', '.join(ev.rules)}" if ev.rules else " → 근거 통과"),
                "read",
            )
        )
        return Check(params={**check.params, **ev.params}, rules=rules), ev

    async def generate_prs(self, spec: str) -> ActionResult:
        return await self.dispatch({"type": "generate_prs", "spec": spec})

    async def review_pr(self, pr: str) -> ActionResult:
        return await self.dispatch({"type": "review_pr", "pr": pr})

    async def merge_pr(self, pr: str) -> ActionResult:
        return await self.dispatch({"type": "merge_pr", "pr": pr})

    async def approve_repo(self, pr: str, connection: str | None = None) -> ActionResult:
        return await self.dispatch({"type": "approve_repo", "pr": pr, "connection": connection})

    async def reject_repo(self, pr: str) -> ActionResult:
        return await self.dispatch({"type": "reject_repo", "pr": pr})

    async def approve_promotion(self) -> ActionResult:
        return await self.dispatch({"type": "approve_promotion"})

    async def reject_promotion(self) -> ActionResult:
        return await self.dispatch({"type": "reject_promotion"})

    def _auto_prelude(self, s: State) -> tuple[datetime, datetime | None]:
        """간단 요청 처리 시각과 승급 제안 시각 (시계를 부르는 순서를 원래대로 둔다)."""
        pol = s.auto_policy
        t = self.clock()
        propose = (
            pol.proposal is None
            and pol.streak + 1 >= PROMOTE_AFTER
            and pol.level < len(AUTO_LEVELS) - 1
        )
        return t, (self.clock() if propose else None)

    async def _auto_build(
        self,
        s: State,
        req: Request,
        sp: Spec,
        task: Task,
        prelude: tuple[datetime, datetime | None] | None = None,
    ) -> None:
        """간단 요청 뒤쪽: generate_pr(B) → review_pr → merge_pr(B) → record_case →
        회신·승급 제안."""
        t, propose_at = prelude or self._auto_prelude(s)
        spec_id = sp.id
        task_id = task.id
        number = len(s.prs) + 30
        pr_id = f"PR #{number}"
        lines, files = estimate(req.title)
        limit = auto_limit(s.auto_policy)
        ctx = req.context_id
        pol = s.auto_policy
        streak = pol.streak + 1
        propose = propose_at is not None
        next_lv = AUTO_LEVELS[min(pol.level + 1, len(AUTO_LEVELS) - 1)]
        why = f"L{pol.level}에서 연속 {streak}건 정상 머지, 자동 머지 PR의 장애 연결 0건"

        def step(frm, to, point, kind, text, grade=None) -> Activity:
            act = self._act(self.clock(), ctx, frm, to, point, kind, text, grade)
            s.activity.append(act)
            return act

        gen_text = f"{pr_id} 변경 {lines}줄·{files}파일·{limit['paths']} → 코드 규칙 통과"
        auto_paths = changed_paths_for(spec_id, "R1", limit["paths"])
        await self._execute(
            "generate_pr",
            ctx,
            pr_id,
            gen_text,
            vcs_bridge.pr_size_params(lines, files, auto_paths, task_id),
        )
        pr = PR(
            id=pr_id,
            number=number,
            task=task_id,
            title=f"R1: {req.title}",
            branch=f"gen/{task_id.lower()}",
            reviews=["통과"],
            gate=PrGate(result="통과", lines=lines, files=files, paths=limit["paths"]),
            merged=False,
            changed_paths=auto_paths,
            auto=True,
        )
        # 관문을 지난 뒤에만 형상관리에 실제로 올린다 (목 설정이면 아무것도 안 함).
        # 연결이 여럿이면 올릴 레포를 제안만 하고 사람 승인(approveRepo)을 기다린다
        routed = await self._route_and_open(pr, req, spec_id, "R1", ctx, req.title)
        s.prs.append(pr)
        step("planning", "safety", "execute(generate_pr)", "execute", gen_text, "B")
        s.activity.extend(routed)
        step(
            "planning",
            "shop",
            "github_create_pull",
            "API",
            f"{pr_id} 생성 ({pr.branch}, 라벨 generated-pr·auto-simple)",
        )

        review_text = f"{pr_id} 검수 통과 (완료 기준 C1), CI 통과"
        await self._execute("review_pr", ctx, f"{pr_id}:r1", review_text)
        step("planning", "planning", "review_pr", "A2A", review_text, "A")

        if self._repo_pending(pr):
            # 올릴 레포 승인 전: PR은 열어 두고 머지 대기 (승인하면 자동 머지 규칙대로 이어 간다)
            task.state = "머지 대기"
            for r in s.requests:
                if r.id == req.id:
                    r.spec = spec_id
                    r.state = "작업 중"
            s.activity.append(self._repo_wait_act(ctx, pr))
            return

        merge_text = f"{pr_id} 간단 요청 규칙·검수·CI 통과 → 자동 머지 허용"
        merge_rules, ev = await self._cross_check(s, pr, pr_check(s, pr), ctx)
        try:
            await self._execute(
                "merge_pr", ctx, pr_id, merge_text, {**merge_rules.params, "auto": True}
            )
        except GateRefused as exc:
            # safety가 보류·거절하면 PR은 열어 두고 교차검증 대기로 둔다 (판단은 사람 머지 버튼).
            # 근거를 이미 모았으면 사람 확인
            state = WAIT_HUMAN if ev else WAIT_CROSS_CHECK
            self._hold(pr, state, merge_rules, exc.decision, ev)
            for x in s.tasks:
                if x.id == task_id:
                    x.state = "머지 대기"
            for r in s.requests:
                if r.id == req.id:
                    r.spec = spec_id
                    r.state = "작업 중"
            step(
                "planning",
                req.from_,
                "get_request_status",
                "A2A",
                f"{req.id} {pr_id} 자동 머지 보류 ({exc.decision.status}) → 교차검증 대기",
                "read",
            )
            return
        step("planning", "safety", "execute(merge_pr)", "execute", merge_text, "B")
        # merge_pr 관문을 지난 뒤 형상관리에서 실제로 병합한다. 실패하면 머지 대기로 둔다
        merged_note = await vcs_bridge.merge_pr(pr)
        if merged_note:
            step("planning", "planning", merged_note.point, "API", merged_note.text)
            if not merged_note.ok:
                task.state = "머지 대기"
                for r in s.requests:
                    if r.id == req.id:
                        r.spec = spec_id
                        r.state = "작업 중"
                return
        merged_act = step(
            "planning", "shop", "github_merge_pull", "API", f"{pr_id} 자동 머지 → dev 배포"
        )
        pr.merged = True
        pr.merged_at = merged_act.at
        task.state = "완료"

        merged_case = f"① kind pr_merged · {pr_id} (자동 머지)"
        merged_hr = await self._send_hr(
            ctx, RecordKind.PR_MERGED, f"{pr_id}:pr_merged", merged_case,
            f"{pr_id} {pr.title} 자동 머지 (간단 요청, 사후 감사 대상)",
            merge_text, [f"pr:{pr.number}", f"request:{req.id}"],
            self._hr_area(s, [pr], req.title),
        )  # fmt: skip
        s.activity.append(self._hr_act(ctx, merged_case, merged_hr))
        closed_case = f"① kind request_closed · {req.id}"
        closed_hr = await self._send_hr(
            ctx, RecordKind.REQUEST_CLOSED, f"{spec_id}:request_closed", closed_case,
            f'{req.id} "{req.title}" 완료 ({pr_id} 머지, 사람 승인 없이 자동 처리)',
            "간단 요청 규칙 통과 → 기획서 확정·PR 머지까지 자동",
            [f"request:{req.id}", f"spec:{spec_id}"],
            self._hr_area(s, [pr], req.title),
        )  # fmt: skip
        s.activity.append(self._hr_act(ctx, closed_case, closed_hr))
        step(
            "planning",
            req.from_,
            "get_request_status",
            "A2A",
            f'{req.id} "{req.title}" 완료 · {pr_id} 머지 (사람 승인 없이 자동 처리)',
            "read",
        )

        for r in s.requests:
            if r.id == req.id:
                r.spec = spec_id
                r.state = "완료"
        await self._notify_result(s, req, "completed", prs=[pr], spec_id=spec_id)
        s.first_pr_minutes.append(3)

        # 자동 처리 이력을 쌓고, 연속 정상이 기준에 닿으면 승급을 제안한다 (실행은 사람 승인 뒤)
        pol.streak = streak
        pol.history.append(
            AutoRun(pr=pr_id, req=req.id, at=t, lines=lines, files=files, result="정상")
        )
        if propose and propose_at is not None:
            text = (
                f"자동 처리 범위 L{pol.level} → L{pol.level + 1}"
                f"({next_lv.lines}줄·{next_lv.files}파일)"
                f" 승급 제안 · {why} → 팀장 승인 대기"
            )
            # 승급 제안은 C: 보류(held)가 정상 결과다. 실제 승급은 사람 승인(approve_promotion) 뒤
            await self._execute(
                "auto_policy_propose",
                "auto_policy",
                f"L{pol.level}->L{pol.level + 1}:{pr_id}",
                text,
                {"to": pol.level + 1},
                allow=("executed", "held"),
            )
            pol.proposal = AutoProposal(to=pol.level + 1, at=t, why=why)
            pol.log.append(
                AutoLog(at=t, text=f"L{pol.level} → L{pol.level + 1} 승급 제안 ({why})", by="자동")
            )
            s.activity.append(
                self._act(
                    propose_at,
                    ctx,
                    "planning",
                    "safety",
                    "auto_policy.propose",
                    "execute",
                    text,
                    "C",
                )
            )

    # ---------------------------------------------------------------- 장애 연결 (link_incident)

    @staticmethod
    def _next_follow_id(s: State, n: int) -> str:
        ids = {r.id for r in s.requests}
        while f"REQ-{100 + n}" in ids:
            n += 1
        return f"REQ-{100 + n}"

    async def _link(
        self,
        s: State,
        ctx: str,
        prs: list[PR],
        *,
        title: str | None,
        follow_up_title: str | None,
        follow_from: str,
        at: datetime,
        seq: int | None,
    ) -> dict:
        """사건을 PR·문단에 연결하고, 재발 방지 요청을 등록(같은 제목이면 연결만)한다.
        자동 머지 PR이 연결되면 자동 처리 범위를 한 단계 줄인다 (줄이는 쪽은 승인 없이)."""
        pr_ids = [p.id for p in prs]
        await self._execute(
            "link_incident",
            ctx,
            ",".join(pr_ids) or "none",
            f"사건 {ctx}를 {', '.join(pr_ids) or 'PR 없음'}에 연결",
            {"pr_numbers": [p.number for p in prs]},
        )
        incident = next((i for i in s.incidents if i.context_id == ctx), None)
        if incident is None:
            incident = Incident(
                context_id=ctx,
                tenant_id=self.tenant_id,
                title=title or f"사건 {ctx}",
                opened_at=at,
            )
            s.incidents.insert(0, incident)
        labels: list[str] = []
        linked_paras: list[str] = []
        for pr in prs:
            spec_id, para_id = para_of(s, pr)
            if not any(c.pr == pr.id for c in incident.candidates):
                incident.candidates.append(
                    IncidentCandidate(
                        pr=pr.id,
                        spec=spec_id or "-",
                        para=para_id or "-",
                        merged_at=pr.merged_at or at,
                    )
                )
            labels.append(f"{spec_id or '-'} {para_id or '-'}")
            spec = next((x for x in s.specs if x.id == spec_id), None)
            para = next((p for p in spec.paras if p.id == para_id), None) if spec else None
            if para:
                para.incident = ctx
                linked_paras.append(f"{spec_id}:{para_id}")
        incident.linked = True

        follow_id: str | None = None
        duplicate = False
        if follow_up_title:
            dup = find_duplicate(s.requests, follow_up_title)
            if dup:
                follow_id, duplicate = dup, True
            else:
                follow_id = self._next_follow_id(s, len(s.incidents))
                await self._execute(
                    "create_ticket",
                    ctx,
                    follow_id,
                    f"재발 방지 요청 {follow_id} 등록: {follow_up_title}",
                    {"title": follow_up_title, "via": "link_incident"},
                )
                s.requests.append(
                    Request(
                        id=follow_id,
                        context_id=ctx,
                        tenant_id=self.tenant_id,
                        from_=follow_from,
                        via="link_incident (재발 방지)",
                        title=follow_up_title,
                        evidence=f"사건 {ctx}",
                        state="접수",
                    )
                )
            incident.follow_up = follow_id

        demote_acts: list[Activity] = []
        auto_prs = [p for p in prs if p.auto]
        if auto_prs:
            pol: AutoPolicy = s.auto_policy
            label = ", ".join(p.id for p in auto_prs)
            ids = {p.id for p in auto_prs}
            lv = pol.level
            text = (
                f"자동 머지 {label}가 장애에 연결 → 자동 처리 범위 L{lv} → L{lv - 1} 축소"
                " (줄이는 쪽은 승인 없이)"
            )
            try:
                if lv > 0:
                    await self._execute(
                        "auto_policy_update", ctx, f"L{lv}->L{lv - 1}", text, {"to": lv - 1}
                    )
            except GateRefused as exc:
                # 범위 축소가 관문에서 막혀도 사건 연결(장애 표시)은 한다.
                # 축소만 사람에게 남긴다
                lv = 0
                demote_acts.append(
                    self._act(
                        at,
                        ctx,
                        "planning",
                        "safety",
                        "auto_policy.update_held",
                        "execute",
                        f"자동 처리 범위 축소 보류 ({exc.decision.status}) → 사람이 범위를 확인",
                        "B",
                        seq,
                    )
                )
            if lv > 0:
                pol.level = lv - 1
                pol.log.append(
                    AutoLog(
                        at=at,
                        text=(
                            f"{label}(자동 머지)가 사건 {ctx}에 연결 → L{lv} → L{lv - 1} 자동 축소"
                        ),
                        by="자동",
                    )
                )
                demote_acts.append(
                    self._act(
                        at,
                        ctx,
                        "planning",
                        "safety",
                        "auto_policy.updated",
                        "이벤트",
                        text,
                        None,
                        seq,
                    )
                )
            pol.streak = 0
            pol.proposal = None
            for h in pol.history:
                if h.pr in ids:
                    h.result = "장애 연결"

        where = ", ".join(labels)
        if not where:
            # 연결할 PR이 없으면 문단에 장애를 표시하지 않는다 (재발 방지 요청만 알린다)
            reply = "연결할 PR 없음 · 장애 표시 안 함" + (
                f", 재발 방지 요청 {follow_id}" if follow_id else ""
            )
        elif follow_id and duplicate:
            reply = f"{where}에 장애 표시, 같은 요청 {follow_id}에 연결 (linked_duplicate)"
        elif follow_id:
            reply = f"{where}에 장애 표시, 재발 방지 요청 {follow_id} 등록"
        else:
            reply = f"{where}에 장애 표시"
        return {
            "linked_paras": linked_paras,
            "follow_id": follow_id,
            "duplicate": duplicate,
            "demote_acts": demote_acts,
            "reply": reply,
        }

    # ------------------------------------------------------------ A2A skill (handlers.py가 부른다)

    async def find_related_prs(
        self,
        *,
        context_id: str,
        requester: str,
        window_from: datetime,
        window_to: datetime,
        component: str | None,
    ) -> list[tuple[PR, str | None, str | None]]:
        """구간 안 머지 PR 후보 (read). 원인은 판정하지 않는다. 활동 로그만 남긴다.

        배포는 증상보다 먼저 나가므로 구간 시작보다 `ROLLBACK_LOOKBACK`(30분) 앞부터 본다.
        총괄은 영향 분석과 같은 구간(장애 시작 시각부터)을 보내서, 장애 직전에 머지된 PR이
        빠졌다."""
        window_from = window_from - ROLLBACK_LOOKBACK
        found = [
            (p, *para_of(self.state, p))
            for p in merged_in_window(self.state, window_from, window_to, component)
        ]

        async def log(s: State) -> None:
            t = self.clock()
            comp = f", 경로 {component}" if component else ""
            ask = f"구간 {_fmt(window_from)}~{_fmt(window_to)} 머지 PR 질의{comp}"
            ids = ", ".join(p.id for p, _, _ in found) or "후보 없음"
            s.activity.append(
                self._act(
                    t, context_id, requester, "planning", "find_related_prs", "A2A", ask, "read"
                )
            )
            s.activity.append(
                self._act(
                    t,
                    context_id,
                    "planning",
                    requester,
                    "find_related_prs",
                    "A2A",
                    f"관련 PR 후보 회신: {ids} · 원인 판정은 하지 않음",
                    "read",
                )
            )

        await self._apply(log)
        return found

    def rollback_record(
        self, context_id: str, service: str, version: str
    ) -> tuple[PR | None, Incident | None]:
        """롤백 검증에 쓸 planning 자기 기록: (그 버전으로 나간 머지 PR, 요청 사건)."""
        return deploy_of(self.state, service, version), incident_of(self.state, context_id)

    async def verify_rollback_target(
        self,
        *,
        context_id: str,
        requester: str,
        service: str,
        target_version: str,
        deployed_at: datetime,
        incident_opened_at: datetime,
    ) -> tuple[bool, str, list[tuple[PR, str | None, str | None]]]:
        """infra rollback_deployment(B)의 교차검증 (read)."""
        approved, reason, prs = verify_rollback(self.state, deployed_at, incident_opened_at)
        related = [(p, *para_of(self.state, p)) for p in prs]

        async def log(s: State) -> None:
            t = self.clock()
            s.activity.append(
                self._act(
                    t,
                    context_id,
                    requester,
                    "planning",
                    "verify_rollback_target",
                    "A2A",
                    f"{service} {target_version} rollback_deployment(B) 교차검증 요청",
                    "read",
                )
            )
            if approved:
                reply = f"승인 · 배포 직전 머지 {', '.join(p.id for p in prs)} 확인 ({reason})"
            else:
                reply = f"거절 · {reason} → completed + rejected_by_review"
            s.activity.append(
                self._act(
                    t,
                    context_id,
                    "planning",
                    requester,
                    "verify_rollback_target",
                    "A2A",
                    reply,
                    "read",
                )
            )

        await self._apply(log)
        return approved, reason, related

    async def list_in_progress(
        self, *, context_id: str, requester: str, owner_id: str, area: str | None
    ) -> tuple[list[Item], datetime]:
        """담당자·영역의 진행 중 항목과 조회 시각 (read). 활동 로그만 남긴다."""
        items = in_progress(self.state, owner_id, area)
        t = self.clock()

        async def log(s: State) -> None:
            where = owner_id + (f" · {area}" if area else "")
            ids = ", ".join(i.item_id for i in items) or "없음"
            s.activity.append(
                self._act(
                    t,
                    context_id,
                    requester,
                    "planning",
                    "list_in_progress",
                    "A2A",
                    f"진행 중 항목 조회 ({where})",
                    "read",
                )
            )
            s.activity.append(
                self._act(
                    t,
                    context_id,
                    "planning",
                    requester,
                    "list_in_progress",
                    "A2A",
                    f"진행 중 항목 {len(items)}건 회신: {ids}",
                    "read",
                )
            )

        await self._apply(log)
        return items, t

    async def list_dev_outcomes(
        self,
        *,
        context_id: str,
        requester: str,
        since: datetime | None = None,
        until: datetime | None = None,
        coder: str | None = None,
    ) -> tuple[list[Any], Any, datetime]:
        """개발 작업 결과와 합계 (read, 평가 근거). 활동 로그만 남긴다 (outcomes.py)."""
        from dev_agent import outcomes

        rows, summary = outcomes.list_outcomes(self.state, since=since, until=until, coder=coder)
        t = self.clock()
        who = coder or "전체"

        async def log(s: State) -> None:
            s.activity.append(
                self._act(t, context_id, requester, "planning", "list_dev_outcomes", "A2A",
                          f"개발 작업 결과 조회 ({who})", "read")
            )  # fmt: skip
            s.activity.append(
                self._act(t, context_id, "planning", requester, "list_dev_outcomes", "A2A",
                          f"작업 {summary.total}건 · 머지 {summary.merged}건 회신", "read")
            )  # fmt: skip

        await self._apply(log)
        return rows, summary, t

    async def verify_handover(
        self,
        *,
        context_id: str,
        requester: str,
        handover_id: str,
        owner_id: str,
        area: str | None,
        handover_item_ids: list[str],
    ) -> tuple[HandoverCheck, datetime]:
        """hr confirm_handover(B)의 교차검증과 확인 시각 (read). 빠진 진행 중 항목만 본다."""
        check = check_handover(self.state, owner_id, area, handover_item_ids)
        t = self.clock()

        async def log(s: State) -> None:
            s.activity.append(
                self._act(
                    t,
                    context_id,
                    requester,
                    "planning",
                    "verify_handover",
                    "A2A",
                    f"인수인계 {handover_id} confirm_handover(B) 교차검증 요청",
                    "read",
                )
            )
            if check.missing:
                ids = ", ".join(i.item_id for i in check.missing)
                reply = f"거절 · 인계 목록에 빠진 진행 중 항목 {ids} (missing_items)"
            else:
                n = len(check.current)
                reply = f"승인 · 진행 중 항목 {n}건 모두 인계 목록에 있음 (complete)"
            if check.stale_item_ids:
                reply += f" · 이미 끝난 항목 {', '.join(check.stale_item_ids)}"
            s.activity.append(
                self._act(
                    t, context_id, "planning", requester, "verify_handover", "A2A", reply, "read"
                )
            )

        await self._apply(log)
        return check, t

    async def link_incident(
        self,
        *,
        context_id: str,
        requester: str,
        incident_context_id: str,
        pr_numbers: list[int],
        follow_up_title: str | None,
        idempotency_key: str,
    ) -> ActionResult:
        """사건을 PR·문단에 연결 (A). 같은 idempotency_key의 두 번째 요청은 이전 결과를 돌려준다."""
        cached = self._idempotent.get(idempotency_key)
        if cached is not None:
            return ActionResult(changed=False, outcome=cached.outcome, output=dict(cached.output))

        async def run(s: State) -> ActionResult:
            t = self.clock()
            prs: list[PR] = []
            for n in dict.fromkeys(pr_numbers):
                pr = next((p for p in s.prs if p.number == n), None)
                if pr:
                    prs.append(pr)
            ctx = incident_context_id
            ids = ", ".join(p.id for p in prs) or "PR 없음"
            s.activity.append(
                self._act(
                    t,
                    ctx,
                    requester,
                    "planning",
                    "link_incident",
                    "A2A",
                    f"사건을 {ids}에 연결 요청",
                    "A",
                )
            )
            linked = await self._link(
                s,
                ctx,
                prs,
                title=None,
                follow_up_title=follow_up_title,
                follow_from=requester if requester in DEPT_NAME else "infra",
                at=t,
                seq=None,
            )
            s.activity.append(
                self._act(
                    t, ctx, "planning", requester, "link_incident", "A2A", linked["reply"], "A"
                )
            )
            s.activity.extend(linked["demote_acts"])
            missing = [n for n in pr_numbers if not any(p.number == n for p in prs)]
            return ActionResult(
                outcome="linked_duplicate" if linked["duplicate"] else None,
                output={
                    "linked_paras": linked["linked_paras"],
                    "follow_up_request_id": linked["follow_id"],
                    "linked_prs": [p.id for p in prs],
                    "missing_pr_numbers": missing,
                },
            )

        result = await self._apply(run)
        self._idempotent[idempotency_key] = result
        self._save()  # 중복 거르기 기록도 바로 남긴다 (재시작 뒤 같은 요청을 다시 처리하지 않게)
        return result

    async def start_dev(
        self,
        *,
        context_id: str,
        requester: str,
        request: dict,
        spec: dict,
        tasks: list[dict],
        idempotency_key: str,
    ) -> ActionResult:
        """dev 서비스: planning이 넘긴 확정 기획서·작업을 받는다 (A).

        요청·기획서·작업은 id로 덮어쓴다.

        받은 뒤 다음 단계(PR 만들기·검수·자동 머지)는 기존 규칙대로 이어 간다.
        같은 idempotency_key의 두 번째 요청은 이전 결과를 돌려준다."""
        cached = self._idempotent.get(idempotency_key)
        if cached is not None:
            return ActionResult(changed=False, outcome=cached.outcome, output=dict(cached.output))
        req = Request.model_validate(request)
        sp = Spec.model_validate(spec)
        got = [Task.model_validate(t) for t in tasks]
        req.flow = True  # 받은 뒤 사람 클릭 없이 PR까지 잇는다
        sp.handed_to_dev = True
        again = {t.id for t in got}  # 사람이 "개발에 다시 맡기기"로 다시 넘긴 작업 (keep_dev)

        async def run(s: State) -> ActionResult:
            t = self.clock()
            ctx = req.context_id
            ids = [x.id for x in got]
            text = f"{sp.id} 작업 {', '.join(ids) or '없음'} 받음 → PR 만들기"
            await self._execute(
                START_DEV, ctx, sp.id, text, {"request": req.id, "tasks": ids}, [sp.id, *ids]
            )
            self._upsert(s.requests, [req])
            self._upsert(s.specs, [sp])
            self._upsert(s.tasks, got)
            if sp.keep_dev:  # 돌려보냈던 작업을 처음부터 다시 코딩한다 (다시 돌려보내지 않음)
                for x in s.tasks:
                    if x.id in again and x.coding is not None and x.coding.status == "returned":
                        x.coding, x.state = None, "진행 중"
            frm = requester if requester in DEPT_NAME else "planning"
            s.activity.append(self._act(t, ctx, frm, "dev", START_DEV, "A2A", text, "A"))
            return ActionResult(
                outcome="accepted",
                output={"accepted": True, "spec_id": sp.id, "task_ids": ids},
            )

        result = await self._apply(run)
        self._idempotent[idempotency_key] = result
        self._save()  # 중복 거르기 기록도 바로 남긴다 (재시작 뒤 같은 요청을 다시 처리하지 않게)
        return result

    # ---------------------------------------------------------------- 코딩 에이전트 (개발팀 기획)

    def _coding_connection(self, connection: str | None, keep: str | None = None) -> str:
        """올릴 연결을 정한다: 준 연결 → (재시도면) 전에 쓴 연결 → 연결이 하나면 그것.

        연결이 여럿인데 고르지 않았으면 `INVALID_INPUT` (v1은 코딩 작업에 레포 제안·승인을 쓰지
        않는다. 개발팀원이 화면에서 연결을 고른다)."""
        conns = vcs_bridge.targets()
        if not conns:
            raise ActionError("NO_VCS", "형상관리 연결이 없다")
        ids = [c.id for c in conns]
        if connection:
            if connection not in ids:
                raise ActionError("INVALID_INPUT", f"모르는 연결: {connection}")
            return connection
        if keep and keep in ids:
            return keep
        if len(ids) == 1:
            return ids[0]
        raise ActionError("INVALID_INPUT", "연결이 여럿이라 connection을 골라야 한다")

    def _coding_llm(self) -> Any:
        llm = coder.get_llm()
        if llm is None:
            raise ActionError(
                "LLM_NOT_CONFIGURED", "LLM 설정이 없다 (PLANNING_LLM_MODEL·BASE_URL 또는 LLM_*)"
            )
        return llm

    @staticmethod
    def _dev_ids(s: State) -> tuple[str, str, str, str]:
        """개발팀 기획의 요청·기획서·작업·사건 id. planning 번호와 겹치지 않게 D를 붙인다."""
        n = sum(1 for r in s.requests if r.via == coder.DEV_TEAM) + 1
        while any(r.id == f"REQ-D{n}" for r in s.requests) or any(
            t.id == f"T-D{n}" for t in s.tasks
        ):
            n += 1
        return f"REQ-D{n}", f"SPEC-D{n}", f"T-D{n}", f"ctx_dev_{n:04d}"

    # ---- 개발 AI 대화 (dev_chat.py). 메시지 하나 = 작은 액션 하나 (관문 dev_chat A)

    def chat_of(self, thread_id: str) -> ChatThread | None:
        return next((c for c in self.state.chats if c.id == thread_id), None)

    @staticmethod
    def chat_ctx(thread_id: str) -> str:
        return "ctx_" + thread_id.replace("-", "_")

    async def chat_add(
        self,
        thread_id: str | None,
        role: str,
        text: str,
        *,
        kind: str = "text",
        plan: ChatPlan | None = None,
        task_id: str | None = None,
        status: str | None = None,
        connection: str | None = None,
        title: str | None = None,
        waiting_for: list[str] | None = None,
        mode: str | None = None,
        who: str | None = None,
    ) -> ChatThread:
        """대화에 메시지 하나를 더한다. thread_id가 없으면 새 대화(`chat-n`)를 연다."""

        async def run(s: State) -> ActionResult:
            t = self.clock()
            if thread_id is None:
                nums = [int(c.id.split("-")[1]) for c in s.chats if c.id[5:].isdigit()]
                th = ChatThread(
                    id=f"chat-{max(nums, default=0) + 1}",
                    title=title or "새 대화",
                    created_at=t,
                    updated_at=t,
                )
                s.chats.append(th)
            else:
                found = next((c for c in s.chats if c.id == thread_id), None)
                if found is None:
                    raise ActionError("NOT_FOUND", f"대화 {thread_id}가 없다")
                th = found
            mid = f"m{len(th.messages) + 1}"
            await self._execute(
                "dev_chat",
                self.chat_ctx(th.id),
                f"{th.id}:{mid}",
                f"{th.id} {role} 메시지 {mid} ({kind})",
                {"thread": th.id, "role": role, "kind": kind, "task": task_id},
                [th.id],
            )
            th.messages.append(
                ChatMessage(
                    id=mid, role=role, text=text, at=t, kind=kind, plan=plan, task_id=task_id
                )
            )
            if status:
                th.status = status  # type: ignore[assignment]
            if task_id:
                th.task_id = task_id
            if connection:
                th.connection = connection
            if who and th.who is None:
                th.who = who
            if title and thread_id is not None:
                th.title = title
            if waiting_for is not None:
                th.waiting_for = list(waiting_for)
            if mode:
                th.mode = mode  # type: ignore[assignment]
            th.updated_at = t
            return ActionResult(outcome="chat", output={"thread_id": th.id})

        result = await self._apply(run)
        found = self.chat_of(result.output["thread_id"])
        assert found is not None
        return found

    async def chat_set(
        self, thread_id: str, *, status: str | None = None, waiting_for: list[str] | None = None
    ) -> ChatThread:
        """메시지 없이 대화 상태만 바꾼다 (답 뒤 되돌리기·끝내기).

        대화 기록일 뿐이라 관문을 다시 타지 않는다 (메시지는 chat_add가 dev_chat 관문을 탄다)."""

        async def run(s: State) -> ActionResult:
            th = next((c for c in s.chats if c.id == thread_id), None)
            if th is None:
                raise ActionError("NOT_FOUND", f"대화 {thread_id}가 없다")
            if status:
                th.status = status  # type: ignore[assignment]
            if waiting_for is not None:
                th.waiting_for = list(waiting_for)
            th.updated_at = self.clock()
            return ActionResult(outcome="chat", output={"thread_id": th.id})

        await self._apply(run)
        found = self.chat_of(thread_id)
        assert found is not None
        return found

    async def retry_dev_task(
        self, task_id: str, connection: str | None = None, approved: bool = False
    ) -> ActionResult:
        """실패·사람 확인으로 멈췄거나 사람이 중단한 코딩 작업을 처음부터 다시 돌린다 (재개).

        열린 PR이 있으면 안 된다 (중단하면 PR은 닫히므로 중단한 작업은 재개할 수 있다).
        머지가 충돌로 실패한 작업만 예외: 그 PR을 닫고 최신 코드로 다시 쓴다.
        다시 돌 때 레포의 최신 코드를 다시 읽는다.
        `approved`: 사람이 generate_pr 보류 건을 승인함 → 보류 때 키로 보낸다 (같은 경로일 때)."""
        task = next((t for t in self.state.tasks if t.id == task_id), None)
        if task is None or task.coding is None:
            raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
        if task.discarded:
            raise ActionError("NOT_RETRYABLE", f"{task_id}는 폐기한 작업이다")
        if self.task_paused(self.state, task_id):
            raise ActionError("REQUEST_PAUSED", f"{task_id}의 요청이 사람 중단 중이다 → 재개 뒤")
        resumable = ("failed", "waiting_human", "canceled", "returned")
        if task_id in self._coding or task.coding.status not in resumable:
            raise ActionError("NOT_RETRYABLE", f"{task_id}는 진행 중이거나 끝났다")
        conflict = task.coding.merge_conflict
        if any(p.task == task_id and not p.closed and (p.merged or not conflict)
               for p in self.state.prs):  # fmt: skip
            raise ActionError(
                "NOT_RETRYABLE", f"{task_id}는 이미 PR이 있다 → PR 화면에서 머지·판단"
            )
        cid = self._coding_connection(connection, task.coding.connection)
        sub = task.coding.mode == "subscription"  # 구독형은 다시 대기열에만 올린다
        over = None if sub else self.budget_over(self.state)
        if over:
            raise ActionError("BUDGET_EXHAUSTED", f"{over} → 내 Claude로 넘기거나 예산을 늘린다")
        llm = None if sub else self._coding_llm()

        async def run(s: State) -> ActionResult:
            x = next((t for t in s.tasks if t.id == task_id), None)
            if x is None or x.coding is None:
                raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
            sp = next((p for p in s.specs if p.id == x.spec), None)
            req = self._request_of_spec(s, sp) if sp else None
            ctx = req.context_id if req else f"task:{task_id}"
            t = self.clock()
            how = "재개" if x.coding.status == "canceled" else "다시 시도"
            if x.coding.status == "returned" or x.coding.returned_to:
                # 사람이 "코드 변경이 아니다" 판단을 뒤집음: 이 기획서는 다시 돌려보내지 않는다
                how = "개발에 다시 맡기기 (코드 변경으로 확정)"
                if sp is not None:
                    sp.keep_dev = True
            if approved and x.coding.hold_key:
                how = "보류 승인 → PR 열기"
            if x.coding.merge_conflict:
                how = "충돌 → 최신 코드로 다시 쓰기"
                for pr in [p for p in s.prs if p.task == task_id and not p.closed]:
                    note = await vcs_bridge.close_pr(pr)
                    if note is not None:
                        s.activity.append(
                            self._act(self.clock(), ctx, "dev", "dev", note.point, "API", note.text)
                        )
                    if note is not None and not note.ok:
                        raise ActionError("NOT_RETRYABLE", f"{pr.id}를 닫지 못해 다시 쓰지 않음")
                    pr.closed = True
                    pr.auto_review = None
            who = "개발자 Claude 대기열" if sub else "코딩 에이전트"
            text = f"{task_id} {who} {how} (연결 {cid})"
            n = sum(1 for g in self.gate_calls if g.action == "dev_task_retry") + 1
            await self._execute("dev_task_retry", ctx, f"{task_id}:{n}", text, {"connection": cid})
            x.coding = CodingRun(
                status="queued", model=coder.model_name(llm) if llm else None, updated_at=t,
                connection=cid, llm_calls=x.coding.llm_calls, llm_tokens=x.coding.llm_tokens,
                mode=x.coding.mode, hold_key=x.coding.hold_key, hold_review=x.coding.hold_review,
                hold_paths=x.coding.hold_paths, hold_ok=approved and x.coding.hold_key is not None,
                text_warnings=x.coding.text_warnings, text_ok_by=x.coding.text_ok_by,
            )  # fmt: skip
            x.state = "진행 중"
            s.activity.append(self._act(t, ctx, "dev", "dev", "dev_task_retry", "A2A", text, "A"))
            return ActionResult(
                outcome="queued",
                output={
                    "request_id": req.id if req else None,
                    "spec_id": x.spec,
                    "task_id": task_id,
                    "status": "queued",
                },
            )

        result = await self._apply(run)
        if not sub:
            self._spawn_coder(task_id)
        return result

    async def renew_hold(self, task_id: str) -> ActionResult:
        """generate_pr 보류 건을 새로 받는다 (보류가 만료됐거나 키를 모를 때).

        코딩 결과(파일 목록)로 관문에 새 키로 다시 물어 새 보류 건(review_id)을 돌려준다. 사람이
        그 건을 승인한 뒤 `retry_dev_task(approved=True)`로 같은 키를 보내 PR을 연다."""
        task = next((t for t in self.state.tasks if t.id == task_id), None)
        if task is None or task.coding is None:
            raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
        busy = task_id in self._coding or task.coding.pr is not None
        if busy or task.coding.status != "waiting_human":
            raise ActionError("NOT_RETRYABLE", f"{task_id}는 PR 열기 보류 상태가 아니다")
        if not task.coding.files:
            raise ActionError("NOT_RETRYABLE", f"{task_id}는 코딩 결과가 없다 → 다시 시도")

        async def run(s: State) -> ActionResult:
            x = next((t for t in s.tasks if t.id == task_id), None)
            if x is None or x.coding is None:
                raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
            cr = x.coding
            sp = next((p for p in s.specs if p.id == x.spec), None)
            req = self._request_of_spec(s, sp) if sp else None
            ctx = req.context_id if req else f"task:{task_id}"
            paths = sorted(vcs.logical_paths(cr.connection, [f.path for f in cr.files]))
            lines, files = coder.total_lines(cr.files), len(cr.files)
            n = sum(1 for g in self.gate_calls if g.action == "generate_pr") + 1
            key = f"{task_id}:hold{n}:{self.clock().strftime('%H%M%S')}"
            text = f"{task_id} PR 열기 보류 다시 요청 ({lines}줄·{files}파일)"
            cr.hold_key, cr.hold_paths, cr.hold_ok = key, paths, False
            cr.updated_at = self.clock()
            try:
                await self._execute(
                    "generate_pr", ctx, key, text,
                    vcs_bridge.pr_size_params(lines, files, paths, x.id),
                )  # fmt: skip
            except GateRefused as exc:
                if exc.decision.status != "held":
                    raise ActionError(
                        "NOT_RETRYABLE", f"관문이 generate_pr를 거절함 ({exc.decision.status})"
                    ) from exc
                cr.hold_review = exc.decision.review_id
                s.activity.append(
                    self._act(
                        self.clock(), ctx, "dev", "safety", "execute(generate_pr)", "execute",
                        f"{text} → 보류 {cr.hold_review}", "C",
                    )
                )  # fmt: skip
                return ActionResult(
                    outcome="held", output={"task_id": task_id, "review_id": cr.hold_review}
                )
            cr.hold_review = None  # 이번엔 관문이 바로 허락함 → 같은 키로 다시 돌리면 연다
            return ActionResult(outcome="executed", output={"task_id": task_id, "review_id": None})

        return await self._apply(run)

    async def cancel_dev_task(self, task_id: str) -> ActionResult:
        """코딩 작업을 사람이 중단한다. 돌던 실행은 멈추고, 열린(병합 안 된) PR은 닫고
        브랜치를 지운다. 이미 병합된 작업은 되돌리지 않는다 (되돌리기 PR은 사람이 판단)."""
        task = next((t for t in self.state.tasks if t.id == task_id), None)
        if task is None or task.coding is None:
            raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
        if task.coding.status in ("merged", "canceled"):
            raise ActionError(
                "NOT_CANCELABLE",
                f"{task_id}는 이미 {'머지됨' if task.coding.status == 'merged' else '중단됨'}"
                " → 되돌리려면 되돌리기 PR이 필요하다",
            )
        # 먼저 실행을 끊는다: 다음 단계에서 CoderGone으로 멈추고, 기다리던 LLM 호출도 취소한다
        self._coding_live.pop(task_id, None)
        running = self._coding.pop(task_id, None)
        if running is not None:
            running.cancel()
        coder.drop_draft(self, task_id)

        async def run(s: State) -> ActionResult:
            x = next((t for t in s.tasks if t.id == task_id), None)
            if x is None or x.coding is None:
                raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
            sp = next((p for p in s.specs if p.id == x.spec), None)
            req = self._request_of_spec(s, sp) if sp else None
            ctx = req.context_id if req else f"task:{task_id}"
            t = self.clock()
            text = f"{task_id} 코딩 작업 중단 (사람 클릭)"
            n = sum(1 for g in self.gate_calls if g.action == "dev_task_cancel") + 1
            await self._execute("dev_task_cancel", ctx, f"{task_id}:{n}", text)
            s.activity.append(self._act(t, ctx, "human", "dev", "dev_task_cancel", "사람", text))
            pr = next((p for p in s.prs if p.task == task_id and not p.merged), None)
            if pr is not None and not pr.closed:
                note = await vcs_bridge.close_pr(pr)
                if note is not None:
                    s.activity.append(
                        self._act(self.clock(), ctx, "dev", "dev", note.point, "API", note.text)
                    )
                if note is None or note.ok:
                    pr.closed = True
                    pr.auto_review = None  # 닫혔으니 사람 확인 대기에서 뺀다
            x.coding.status = "canceled"
            closed = pr is not None and pr.closed
            x.coding.error = "사람이 중단" + (f" · {pr.id} 닫음" if closed else "")
            x.coding.updated_at = self.clock()
            x.state = "중단"
            return ActionResult(
                outcome="canceled", output={"task_id": task_id, "status": "canceled"}
            )

        return await self._apply(run)

    async def return_dev_task(
        self, task_id: str, to: str, reason: str, who: str | None = None
    ) -> ActionResult:
        """사람이 개발 작업을 보고 기획에 돌려보낸다 (코드 변경이 아닌 일, 다른 부서 일).

        코딩 AI의 `not_code` 판단과 같은 결과(`coding.status = returned`, `returned_to`·
        `returned_reason`, 작업 `중단`)로, 바뀐 작업은 `receive_dev_result`로 기획에 간다.
        돌고 있으면 멈추고 열린 PR은 닫는다(중단과 같게). 내 Claude 작업에도 된다.
        머지된 작업·폐기한 작업·이미 돌려보낸 작업은 안 된다."""
        if to not in coder.DEPARTMENTS:
            raise ActionError("INVALID_INPUT", f"to는 {', '.join(coder.DEPARTMENTS)} 중 하나다")
        why = " ".join(str(reason or "").split())[:200]
        if not why:
            raise ActionError("INVALID_INPUT", "돌려보내는 이유 한 줄이 필요하다")
        task = next((t for t in self.state.tasks if t.id == task_id), None)
        if task is None:
            raise ActionError("NOT_FOUND", f"작업 {task_id}가 없다")
        status = task.coding.status if task.coding else None
        if task.discarded or status in ("merged", "covered", "returned") or task.state == "완료":
            raise ActionError(
                "NOT_RETURNABLE", f"{task_id}는 머지·완료·폐기했거나 이미 돌려보낸 작업이다"
            )
        if any(p.task == task_id and p.merged for p in self.state.prs):
            raise ActionError("NOT_RETURNABLE", f"{task_id}는 이미 머지된 PR이 있다")
        # 먼저 실행을 끊는다 (중단과 같게): 다음 단계에서 CoderGone으로 멈춘다
        self._coding_live.pop(task_id, None)
        running = self._coding.pop(task_id, None)
        if running is not None:
            running.cancel()
        coder.drop_draft(self, task_id)
        label = coder.DEPT_LABEL[to]

        async def run(s: State) -> ActionResult:
            x = next((t for t in s.tasks if t.id == task_id), None)
            if x is None:
                raise ActionError("NOT_FOUND", f"작업 {task_id}가 없다")
            sp = next((p for p in s.specs if p.id == x.spec), None)
            req = self._request_of_spec(s, sp) if sp else None
            ctx = req.context_id if req else f"task:{task_id}"
            t = self.clock()
            text = f"{task_id} 기획에 돌려보냄 ({label}, 사람{f' · {who}' if who else ''}): {why}"
            n = sum(1 for g in self.gate_calls if g.action == "dev_task_return") + 1
            await self._execute("dev_task_return", ctx, f"{task_id}:{n}", text, {"to": to})
            s.activity.append(self._act(t, ctx, "human", "dev", "dev_task_return", "사람", text))
            pr = next((p for p in s.prs if p.task == task_id and not p.merged), None)
            if pr is not None and not pr.closed:
                note = await vcs_bridge.close_pr(pr)
                if note is not None:
                    s.activity.append(
                        self._act(self.clock(), ctx, "dev", "dev", note.point, "API", note.text)
                    )
                if note is None or note.ok:
                    pr.closed = True
                    pr.auto_review = None
            if x.coding is None:
                x.coding = CodingRun(updated_at=t)
            x.coding.status = "returned"
            x.coding.returned_to, x.coding.returned_reason = to, why
            closed = pr is not None and pr.closed
            x.coding.error = f"사람이 기획에 돌려보냄 ({label}): {why}" + (
                f" · {pr.id} 닫음" if closed else ""
            )
            x.coding.updated_at = t
            x.state = "중단"
            return ActionResult(
                outcome="returned",
                output={"task_id": task_id, "status": "returned", "returned_to": to},
            )

        return await self._apply(run)

    DISCARDABLE = ("canceled", "failed", "returned")

    async def discard_dev_task(self, task_id: str, who: str | None = None) -> ActionResult:
        """멈춘 코딩 작업을 사람이 폐기한다. 상태는 그대로 두고 `discarded`만 표시한다.

        중단·실패·기획에 돌려보낸 작업만 된다. 돌고 있거나 열린 PR이 있으면 먼저 중단한다.
        폐기한 작업은 다시 돌리지 않는다 (재개·다시 시도 막힘). 이미 폐기했으면 그대로 돌려준다."""
        task = next((t for t in self.state.tasks if t.id == task_id), None)
        if task is None:
            raise ActionError("NOT_FOUND", f"작업 {task_id}가 없다")
        if task.discarded:
            return ActionResult(
                changed=False, outcome="discarded", output={"task_id": task_id, "discarded": True}
            )
        status = task.coding.status if task.coding else None
        stopped = status in self.DISCARDABLE or (status is None and task.state == "중단")
        if task_id in self._coding or not stopped:
            raise ActionError(
                "NOT_DISCARDABLE", f"{task_id}는 멈춘 작업이 아니다 → 먼저 작업을 중단한다"
            )
        if any(p.task == task_id and not p.merged and not p.closed for p in self.state.prs):
            raise ActionError("NOT_DISCARDABLE", f"{task_id}에 열린 PR이 있다 → 먼저 중단한다")

        async def run(s: State) -> ActionResult:
            x = next((t for t in s.tasks if t.id == task_id), None)
            if x is None:
                raise ActionError("NOT_FOUND", f"작업 {task_id}가 없다")
            sp = next((p for p in s.specs if p.id == x.spec), None)
            req = self._request_of_spec(s, sp) if sp else None
            ctx = req.context_id if req else f"task:{task_id}"
            t = self.clock()
            text = f"{task_id} 작업 폐기 (사람 클릭{f' · {who}' if who else ''})"
            await self._execute("dev_task_discard", ctx, task_id, text)
            s.activity.append(self._act(t, ctx, "human", "dev", "dev_task_discard", "사람", text))
            x.discarded, x.discarded_by, x.discarded_at = True, who, t
            x.state = "중단"
            return ActionResult(outcome="discarded", output={"task_id": task_id, "discarded": True})

        return await self._apply(run)

    async def repo_write(
        self,
        action: str,
        target: str,
        text: str,
        params: dict | None,
        op: Callable[[], Awaitable[Any]],
    ) -> Any:
        """레포 작업 공간 사람 버튼 (사람이 직접 올린 PR·브랜치). 관문을 지난 뒤에만 형상관리를
        부르고 활동을 남긴다. 형상관리 오류는 실패 활동을 남긴 뒤 `VcsError`로 다시 던진다."""
        box: dict[str, Any] = {}
        ctx = f"repo:{target.split('#', 1)[0].split(':', 1)[0]}"

        async def run(s: State) -> ActionResult:
            n = sum(1 for g in self.gate_calls if g.action == action) + 1
            await self._execute(action, ctx, f"{target}:{n}", text, params)
            try:
                box["result"] = await op()
            except VcsError as exc:
                box["error"] = exc
                note = f"{text} 실패 ({exc.code})"
                s.activity.append(
                    self._act(self.clock(), ctx, "dev", "dev", f"vcs.{action}_failed", "API", note)
                )
                return ActionResult(outcome="failed")
            s.activity.append(self._act(self.clock(), ctx, "human", "dev", action, "사람", text))
            return ActionResult(outcome=action)

        await self._apply(run)
        if "error" in box:
            raise box["error"]
        return box.get("result")

    async def revise_dev_task(self, task_id: str, note: str) -> ActionResult:
        """대기 중인 코딩 PR에 사람이 수정 지시를 준다. 개발 AI가 지시대로 고치고 리뷰한 뒤
        같은 PR에 커밋을 더한다 (PR 게시판에 새 PR을 늘리지 않는다)."""
        text = " ".join(str(note or "").split())
        if not text or len(text) > MAX_INSTRUCTION_CHARS:
            raise ActionError("INVALID_INPUT", f"지시는 1~{MAX_INSTRUCTION_CHARS}자여야 한다")
        task = next((t for t in self.state.tasks if t.id == task_id), None)
        if task is None or task.coding is None:
            raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
        if task.coding.mode == "subscription":
            raise ActionError(
                "NOT_REVISABLE", f"{task_id}는 개발자 Claude 작업이다 → PR에 직접 코멘트로 지시"
            )
        pr = next((p for p in self.state.prs if p.task == task_id), None)
        if (
            task_id in self._coding
            or task.coding.status != "waiting_human"
            or pr is None
            or pr.merged
            or pr.closed
        ):
            raise ActionError(
                "NOT_REVISABLE", f"{task_id}는 열린 PR로 사람 확인을 기다리는 작업이 아니다"
            )
        llm = self._coding_llm()

        async def run(s: State) -> ActionResult:
            x = next((t for t in s.tasks if t.id == task_id), None)
            if x is None or x.coding is None:
                raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
            sp = next((p for p in s.specs if p.id == x.spec), None)
            req = self._request_of_spec(s, sp) if sp else None
            ctx = req.context_id if req else f"task:{task_id}"
            t = self.clock()
            act = f"{task_id} 수정 지시: {text[:80]}"
            n = sum(1 for g in self.gate_calls if g.action == "dev_task_revise") + 1
            await self._execute("dev_task_revise", ctx, f"{task_id}:{n}", act, {"chars": len(text)})
            x.coding.status = "queued"
            x.coding.error = None
            x.coding.instruction = text
            x.coding.model = coder.model_name(llm)
            x.coding.attempts = 0
            x.coding.updated_at = t
            x.state = "진행 중"
            s.activity.append(self._act(t, ctx, "human", "dev", "dev_task_revise", "사람", act))
            return ActionResult(outcome="queued", output={"task_id": task_id, "status": "queued"})

        result = await self._apply(run)
        self._spawn_coder(task_id)
        return result

    async def escalate_waits(self) -> None:
        """교차검증 대기가 오래되면 사람 확인으로 넘긴다.

        `PLANNING_CROSS_CHECK_TIMEOUT_MIN`(기본 10분). 교차검증자가 답하지 않아 PR이 멈춰 있지
        않게 한다. 화면 SSE가 열려 있는 동안 주기적으로 부른다."""
        mins = _cross_check_timeout_min()
        now = self.clock()
        stale = {
            p.id
            for p in self.state.prs
            if not p.merged
            and not p.closed
            and p.auto_review is not None
            and p.auto_review.state == WAIT_CROSS_CHECK
            and now - p.auto_review.at >= timedelta(minutes=mins)
        }
        if not stale:
            return

        async def run(s: State) -> None:
            for p in s.prs:
                if p.id not in stale or p.auto_review is None:
                    continue
                p.auto_review.state = WAIT_HUMAN
                why = f"교차검증 {mins}분 응답 없음 → 사람 확인"
                s.activity.append(
                    self._act(
                        self.clock(),
                        self._ctx_of_pr(s, p),
                        "planning",
                        "planning",
                        "auto_review",
                        "execute",
                        f"{p.id} {why}",
                        "B",
                    )
                )
                task = next((t for t in s.tasks if t.id == p.task), None)
                if task and task.coding is not None and task.coding.status == "waiting_human":
                    task.coding.error = why

        await self._apply(run)

    def coding_pr(self, job: coder.Job) -> PR | None:
        """이 작업이 연 PR (병합·닫힘 아닌 것). 수정 지시 실행이 고칠 대상."""
        return next(
            (p for p in self.state.prs if p.task == job.task_id and not p.merged and not p.closed),
            None,
        )

    def _spawn_coder(self, task_id: str) -> None:
        if self.task_paused(self.state, task_id):
            return  # 사람이 요청을 중단함: 재개할 때 띄운다 (receive_request_pause)
        self._coding_seq += 1
        run_id = self._coding_live[task_id] = self._coding_seq
        task = asyncio.create_task(self._run_coder(task_id, run_id))
        self._coding[task_id] = task

        def done(t: asyncio.Task) -> None:
            if self._coding.get(task_id) is t:
                self._coding.pop(task_id, None)

        task.add_done_callback(done)

    async def _run_coder(self, task_id: str, run_id: int) -> None:
        job = self.coding_job(task_id, run_id)
        if job is not None:
            await coder.run(self, job)

    async def wait_coding(self) -> None:
        """돌고 있는 코딩 작업이 끝날 때까지 기다린다 (테스트용)."""
        while self._coding:
            await asyncio.gather(*list(self._coding.values()), return_exceptions=True)

    def coding_job(self, task_id: str, run_id: int) -> coder.Job | None:
        """상태에서 코딩 작업 입력을 꺼낸다. 없으면 None."""
        task = next((t for t in self.state.tasks if t.id == task_id), None)
        sp = next((x for x in self.state.specs if task and x.id == task.spec), None)
        if task is None or task.coding is None or sp is None:
            return None
        req = self._request_of_spec(self.state, sp)
        para = next((p for p in sp.paras if p.id == task.para), None)
        plan = (
            sp.problem if para is None or para.text in sp.problem else f"{sp.problem}\n{para.text}"
        )
        return coder.Job(
            run_id=run_id,
            task_id=task.id,
            spec_id=sp.id,
            request_id=req.id if req else sp.request,
            ctx=req.context_id if req else f"task:{task.id}",
            title=task.title,
            plan=plan,
            done=tuple(p.text for p in sp.paras if p.kind == "done"),
            hints=tuple(para.paths) if para else (),
            connection=task.coding.connection,
            instruction=task.coding.instruction,
            skip_queue=task.coding.skip_queue,
            # 기획 서비스가 넘긴 기획서 (개발팀이 직접 쓴 기획도 handed_to_dev라 origin을 본다)
            from_planning=sp.handed_to_dev and sp.origin != coder.DEV_TEAM,
            keep_dev=sp.keep_dev,
        )

    def coding_alive(self, job: coder.Job) -> bool:
        """이 실행이 아직 살아 있나 (중단·초기화·새 실행이면 False)."""
        return self._coding_live.get(job.task_id) == job.run_id

    def queue_blockers(self, job: coder.Job, paths: list[str]) -> tuple[list[str], list[str]]:
        """이 작업보다 먼저 만든, 같은 연결의 진행 중 코딩 작업 중
        (같은 파일을 고치는 작업, 아직 고칠 파일을 안 고른 작업)."""
        active = ("queued", "reading", "writing", "reviewing", "opened", "waiting_human")
        mine = set(paths)
        same: list[str] = []
        undecided: list[str] = []
        for t in self.state.tasks:
            if t.id == job.task_id:
                break  # 뒤에 만든 작업은 보지 않는다 (먼저 만든 쪽이 먼저 간다)
            cr = t.coding
            if cr is None or cr.status not in active or cr.connection != job.connection:
                continue
            if cr.mode == "subscription":  # 개발자 Claude 작업은 언제 끝날지 몰라 기다리지 않는다
                continue
            files = {f.path for f in cr.files}
            if not files:
                undecided.append(t.id)
            elif files & mine:
                same.append(t.id)
        return same, undecided

    def _coding_of(self, s: State, job: coder.Job) -> tuple[Task, CodingRun]:
        """이 실행의 작업. 초기화됐거나 새 실행으로 바뀌었으면 `CoderGone`."""
        if self._coding_live.get(job.task_id) != job.run_id:
            raise coder.CoderGone(job.task_id)
        task = next((t for t in s.tasks if t.id == job.task_id), None)
        if task is None or task.coding is None:
            raise coder.CoderGone(job.task_id)
        return task, task.coding

    def _code_act(self, s: State, job: coder.Job, point: str, text: str) -> None:
        grade = "read" if point == "code.read" else "A"
        s.activity.append(
            self._act(
                self.clock(), job.ctx, "dev", "dev", point, "A2A", f"{job.task_id} {text}", grade
            )
        )

    async def coding_step(
        self,
        job: coder.Job,
        status: str,
        *,
        point: str | None = None,
        text: str | None = None,
        **fields: Any,
    ) -> None:
        """진행 상태 하나를 적는다 (작은 액션). 활동 point가 있으면 한 줄 남긴다."""

        async def run(s: State) -> None:
            task, cr = self._coding_of(s, job)
            cr.status = status  # type: ignore[assignment]
            for key, value in fields.items():
                setattr(cr, key, value)
            m = coder.meter_now()
            if m is not None:  # 단계마다 쓴 양을 적는다 (화면에 보인다)
                before = cr.llm_tokens
                cr.llm_calls, cr.llm_tokens = m.base_calls + m.calls, m.base_tokens + m.tokens
                if cr.mode == "agent" and cr.llm_tokens > before:  # 월 예산용
                    month = self.month_key()
                    s.usage_by_month[month] = (
                        s.usage_by_month.get(month, 0) + cr.llm_tokens - before
                    )
            cr.updated_at = self.clock()
            if status == "waiting_human" and cr.pr is None:
                task.state = "C 보류"
            if status == "returned":  # 기획에 돌려보냄: receive_dev_result로 기획이 받는다
                task.state = "중단"
            if point:
                self._code_act(s, job, point, text or "")
            if status == "covered":
                cr.error = None
                await self._finish_covered(s, task, job.ctx)

        await self._apply(run)

    # ---- 앞 작업이 이미 반영한 작업 (바뀐 내용 없음 → PR 없이 완료)

    def covering_prs(self, task_id: str, paths: list[str]) -> list[str]:
        """같은 기획서의 다른 작업이 머지한 PR 중 이 파일들을 바꾼 것 (표시 id)."""
        s = self.state
        task = next((t for t in s.tasks if t.id == task_id), None)
        if task is None or not task.spec:
            return []
        others = {t.id for t in s.tasks if t.spec == task.spec and t.id != task_id}
        mine = {p.strip("/") for p in paths if p}

        def touches(changed: list[str]) -> bool:
            # PR 경로는 회사 코드 위치(앞머리)가 붙었을 수 있어 끝이 같으면 같은 파일로 본다
            return any(c == m or c.endswith("/" + m) for c in changed for m in mine)

        return [
            p.id
            for p in s.prs
            if p.task in others and p.merged and not p.closed and touches(p.changed_paths)
        ]

    async def _finish_covered(self, s: State, task: Task, ctx: str) -> None:
        """covered 작업을 완료로 두고, 기획서 작업이 모두 끝났으면 요청을 닫는다."""
        task.state = "완료"
        spec_id = task.spec
        tasks = [t for t in s.tasks if t.spec == spec_id]
        if not spec_id or any(t.state != "완료" for t in tasks):
            return
        closed = [r for r in s.requests if r.spec == spec_id and r.state != "완료"]
        if not closed:
            return
        spec_prs = [p for p in s.prs if p.task in {t.id for t in tasks}]
        ids = ", ".join(r.id for r in closed)
        case = f"① kind request_closed · {ids}"
        hr = await self._send_hr(
            ctx, RecordKind.REQUEST_CLOSED, f"{spec_id}:request_closed", case,
            f"{ids} 완료 (기획서 {spec_id} 작업 전부 끝남, {task.id}는 앞 작업이 반영)",
            f"{spec_id} 작업 {len(tasks)}개 완료",
            [f"request:{r.id}" for r in closed] + [f"spec:{spec_id}"],
            self._hr_area(s, spec_prs, *self._hr_titles(s, spec_id)),
        )  # fmt: skip
        s.activity.append(self._hr_act(ctx, case, hr))
        for r in closed:
            r.state = "완료"
        for r in closed:
            await self._notify_result(s, r, "completed", prs=spec_prs, spec_id=spec_id)
        s.activity.append(
            self._act(
                self.clock(), ctx, "planning", closed[0].from_, "get_request_status", "A2A",
                f'{ids} "완료"를 요청 부서에 회신', "read",
            )
        )  # fmt: skip

    async def ok_task_text(self, task_id: str, who: str) -> ActionResult:
        """사람이 작업 글의 경고를 보고 "글 확인함" (내 Claude가 가져갈 수 있게 된다).

        에이전트·작업 토큰으로는 부를 수 없다 (api.py HUMAN). 경고가 없으면 할 일이 없다."""

        async def run(s: State) -> ActionResult:
            x = next((t for t in s.tasks if t.id == task_id), None)
            if x is None or x.coding is None:
                raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
            warns = _text_warnings(s, x)
            if not warns:
                raise ActionError("NOTHING_TO_REVIEW", f"{task_id} 작업 글에 경고가 없다")
            x.coding.text_warnings = warns
            x.coding.text_ok_by = who
            x.coding.updated_at = self.clock()
            sp = next((p for p in s.specs if p.id == x.spec), None)
            req = self._request_of_spec(s, sp) if sp else None
            ctx = req.context_id if req else f"task:{task_id}"
            s.activity.append(
                self._act(self.clock(), ctx, "human", "dev", "dev_task_text_ok", "사람",
                          f"{task_id} 작업 글 확인함 ({who}, 경고: {', '.join(warns)})")
            )  # fmt: skip
            return ActionResult(output={"task_id": task_id, "text_ok_by": who, "warnings": warns})

        return await self._apply(run)

    async def close_dev_task_covered(self, task_id: str) -> ActionResult:
        """사람이 '반영됨으로 닫기': 바뀐 내용이 없어 멈춘 작업을 앞 작업이 반영한 것으로 완료한다.

        열린 PR이 있거나 돌고 있는 작업은 안 된다. 반영한 PR은 같은 기획서의 머지된 PR로 적는다."""
        task = next((t for t in self.state.tasks if t.id == task_id), None)
        if task is None or task.coding is None:
            raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
        if task.coding.status not in ("failed", "waiting_human", "canceled"):
            raise ActionError(
                "NOT_CLOSABLE", f"{task_id}는 {task.coding.status} 상태라 반영됨으로 닫을 수 없다"
            )
        if any(p.task == task_id and not p.merged and not p.closed for p in self.state.prs):
            raise ActionError("NOT_CLOSABLE", f"{task_id}에 열린 PR이 있다 → 머지하거나 중단")
        coder.drop_draft(self, task_id)

        async def run(s: State) -> ActionResult:
            x = next((t for t in s.tasks if t.id == task_id), None)
            if x is None or x.coding is None:
                raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
            sp = next((p for p in s.specs if p.id == x.spec), None)
            req = self._request_of_spec(s, sp) if sp else None
            ctx = req.context_id if req else f"task:{task_id}"
            others = {t.id for t in s.tasks if t.spec == x.spec and t.id != task_id}
            by = [p.id for p in s.prs if p.task in others and p.merged and not p.closed]
            text = f"{task_id} 반영됨으로 닫기" + (f" ({', '.join(by)}가 반영)" if by else "")
            await self._execute("dev_task_covered", ctx, f"{task_id}:covered", text)
            s.activity.append(
                self._act(
                    self.clock(), ctx, "human", "dev", "dev_task_covered", "사람",
                    f"{text} (사람 클릭)",
                )
            )  # fmt: skip
            x.coding.status = "covered"
            x.coding.covered_by = by
            x.coding.error = None
            x.coding.updated_at = self.clock()
            await self._finish_covered(s, x, ctx)
            return ActionResult(
                outcome="covered",
                output={"task_id": task_id, "status": "covered", "covered_by": by},
            )

        return await self._apply(run)

    def _coding_target(self, cid: str | None) -> RepoTarget:
        conn = vcs.find_connection(cid) if cid else None
        return RepoTarget(
            connection_id=cid or "default",
            repo=(conn.repo if conn else "") or (cid or "default"),
            status="approved",
            confidence="high",
            reasons=["개발팀이 고른 연결"],
            decided_by="개발팀",
            decided_at=self.clock(),
        )

    async def coding_open(
        self, job: coder.Job, contents: dict[str, str], rows: list[CodingFile], body: str
    ) -> bool:
        """관문 generate_pr(B) 뒤 진짜 파일로 브랜치·커밋·PR을 올린다. 실패하면 남기고 False."""
        opened = False

        async def run(s: State) -> None:
            nonlocal opened
            task, cr = self._coding_of(s, job)
            number = len(s.prs) + 30
            pr_id = f"PR #{number}"
            # 관문·역추적은 회사 코드 기준 경로(연결 앞머리를 붙인 것). 커밋은 레포 안 경로
            paths = sorted(vcs.logical_paths(cr.connection, [r.path for r in rows]))
            lines, files = coder.total_lines(rows), len(rows)
            area = coder.area_of(paths)
            gen_text = f"{pr_id} 변경 {lines}줄·{files}파일·{area} → 코드 규칙 확인"
            t = self.clock()
            # 다시 시도마다 새 키로 보낸다. 같은 키면 관문이 처음 결정(보류)을 그대로 돌려준다.
            # 사람이 보류 건을 승인하고 "승인하고 PR 열기"로 돌렸고 경로가 같으면 그 키로 보낸다
            reuse = cr.hold_ok and cr.hold_key is not None and cr.hold_paths == paths
            key = cr.hold_key if reuse and cr.hold_key else f"{pr_id}:run{job.run_id}"
            if reuse:
                gen_text += " (사람이 보류 건 승인)"
            try:
                await self._execute(
                    "generate_pr",
                    job.ctx,
                    key,
                    gen_text,
                    vcs_bridge.pr_size_params(lines, files, paths, task.id),
                )
            except GateRefused as exc:
                held = exc.decision.status == "held"
                cr.status = "waiting_human" if held else "failed"
                cr.error = f"실행 관문이 generate_pr를 허락하지 않음 ({exc.decision.status})"
                cr.error += inventory.hold_note(cr.connection) if held else ""
                if held:
                    cr.hold_key, cr.hold_review = key, exc.decision.review_id
                    cr.hold_paths, cr.hold_ok = paths, False
                conn = vcs.find_connection(cr.connection) if cr.connection else None
                if held and conn is not None and not conn.mount:
                    # 떼어 온 레포는 경로가 src/…라서 회사 코드 기준 허용 경로 밖으로 보인다
                    cr.error += (
                        " · 연결의 '회사 코드 위치'가 비어 있어"
                        " 경로가 허용 경로 밖으로 보였을 수 있음"
                        " → 연결 수정에서 넣고 다시 시도"
                    )
                cr.updated_at = t
                if held:
                    task.state = "C 보류"
                self._code_act(s, job, "code.failed" if not held else "code.write", cr.error)
                return
            pr = PR(
                id=pr_id,
                number=number,
                task=task.id,
                title=f"R1: {job.title}",
                branch=f"gen/{task.id.lower()}-{job.run_id}",
                reviews=[],
                gate=PrGate(result="통과", lines=lines, files=files, paths=area),
                merged=False,
                changed_paths=paths,
                target=self._coding_target(cr.connection),
            )
            note = await vcs_bridge.open_pr(
                pr, job.request_id, job.spec_id, "R1", files=contents, body=body
            )
            s.activity.append(
                self._act(
                    t, job.ctx, "dev", "safety", "execute(generate_pr)", "execute", gen_text, "B"
                )
            )
            if note is not None:
                s.activity.append(
                    self._act(self.clock(), job.ctx, "dev", "dev", note.point, "API", note.text)
                )
            if note is None or not note.ok:
                cr.status = "failed"
                cr.error = note.text if note else "형상관리 연결이 없다"
                cr.updated_at = self.clock()
                self._code_act(s, job, "code.failed", cr.error)
                return
            s.prs.append(pr)
            task.state = "PR 검수"
            cr.status = "opened"
            cr.pr = pr.id
            cr.hold_key, cr.hold_review, cr.hold_paths, cr.hold_ok = None, None, [], False
            cr.updated_at = self.clock()
            s.activity.append(
                self._act(
                    self.clock(),
                    job.ctx,
                    "dev",
                    "shop",
                    "github_create_pull",
                    "API",
                    f"{pr.id} 생성 ({pr.branch}, 라벨 generated-pr·coder)",
                )
            )
            opened = True

        await self._apply(run)
        return opened

    async def coding_review(
        self,
        job: coder.Job,
        verdict: str,
        notes: list[str],
        earlier: list[str] | None = None,
        ci_hold: str | None = None,
    ) -> bool:
        """리뷰 결과를 관문 review_pr 뒤 PR에 남긴다. 통과면 기존 자동 머지 규칙대로 잇는다.
        earlier는 PR 전에 고치느라 지나간 리뷰 결과다 (PR 리뷰 기록 앞에 붙인다).
        ci_hold는 CI가 실패·시간 초과해 자동 머지하지 않고 사람 확인으로 둘 이유다.

        돌려주는 값: 불일치라 한 번 더 고쳐야 하면 True."""
        fix = False

        async def run(s: State) -> None:
            nonlocal fix
            task, cr = self._coding_of(s, job)
            p = next((x for x in s.prs if x.id == cr.pr), None)
            if p is None:
                raise coder.CoderStop("failed", "PR이 없다")
            cr.ci_hold = False  # 이번 리뷰 결과로 다시 정한다
            p.reviews.extend(earlier or [])  # type: ignore[arg-type]
            text = f"{p.id} 리뷰 {verdict} · 노트 {len(notes)}개"
            await self._execute(
                "review_pr",
                job.ctx,
                f"{p.id}:r{len(p.reviews) + 1}",
                text,
                {"verdict": verdict, "notes": len(notes), "auto": True},
            )
            p.reviews.append(verdict)  # type: ignore[arg-type]
            cr.review = CodingReview(verdict=verdict, notes=notes)  # type: ignore[arg-type]
            cr.updated_at = self.clock()
            self._code_act(s, job, "code.review", f"리뷰 {verdict} · 노트 {len(notes)}개")
            if verdict == "통과" and ci_hold:
                task.state = "머지 대기"
                self._hold(p, WAIT_HUMAN, pr_check(s, p))
                cr.status = "waiting_human"
                cr.error = ci_hold
                cr.ci_hold = True
                self._code_act(s, job, "code.ci", ci_hold)
                return
            if verdict == "통과":
                task.state = "머지 대기"
                await self._try_auto_merge(s, p)
                if p.merged:
                    cr.status = "merged"
                elif p.auto_review is not None:
                    cr.status = "waiting_human"
                    why = ", ".join(p.auto_review.rules) or f"safety {p.auto_review.gate_status}"
                    cr.error = f"자동 머지 안 함 → {p.auto_review.state} ({why})"
                else:
                    cr.status = "failed"
                    fail = next(
                        (a for a in reversed(s.activity)
                         if a.point == "vcs.merge_failed" and a.text.startswith(f"{p.id} ")),
                        None,
                    )  # fmt: skip
                    cr.merge_conflict = fail is not None and vcs_bridge.CONFLICT_MARK in fail.text
                    if cr.merge_conflict:
                        cr.error = f"{p.id} 머지 충돌 → 다시 시도하면 PR을 닫고 최신 코드로 다시 씀"
                    elif fail is not None:
                        cr.error = f"{fail.text} → 사람 머지 버튼으로 다시 시도"
                    else:
                        cr.error = "형상관리 병합 실패 → 사람 머지 버튼으로 다시 시도"
                    self._code_act(s, job, "code.failed", cr.error)
                return
            task.state = "PR 검수"
            if cr.attempts < coder.MAX_ATTEMPTS:
                cr.status = "writing"
                fix = True
                return
            check = pr_check(s, p)
            hold = self._hold(p, pr_wait_state(check), check)
            cr.status = "waiting_human"
            cr.error = f"리뷰 불일치 {p.reviews.count('불일치')}회 → {hold.state}"
            s.activity.append(
                self._act(
                    hold.at,
                    job.ctx,
                    "planning",
                    "planning",
                    "auto_review",
                    "execute",
                    f"{p.id} 자동 머지 안 함 → {hold.state} ({', '.join(hold.rules)})",
                    "B",
                )
            )

        await self._apply(run)
        return fix

    async def coding_commit(
        self, job: coder.Job, changed: dict[str, str], rows: list[CodingFile], message: str
    ) -> bool:
        """관문 code_write(A) 뒤 고친 파일을 같은 PR 브랜치에 커밋한다 (사람 수정 지시)."""
        ok = False

        async def run(s: State) -> None:
            nonlocal ok
            task, cr = self._coding_of(s, job)
            p = next((x for x in s.prs if x.id == cr.pr), None)
            if p is None:
                raise coder.CoderStop("failed", "PR이 없다")
            paths = sorted(r.path for r in rows)
            lines, files = coder.total_lines(rows), len(rows)
            n = sum(1 for g in self.gate_calls if g.action == "code_write") + 1
            await self._execute(
                "code_write",
                job.ctx,
                f"{p.id}:commit{n}",
                f"{p.id} {message} ({len(changed)}개 파일)",
                vcs_bridge.pr_size_params(lines, files, paths, task.id),
            )
            note = await vcs_bridge.update_pr(p, changed, message)
            if note is not None:
                s.activity.append(
                    self._act(self.clock(), job.ctx, "dev", "dev", note.point, "API", note.text)
                )
            if note is not None and not note.ok:
                cr.status = "failed"
                cr.error = note.text
                cr.updated_at = self.clock()
                self._code_act(s, job, "code.failed", cr.error)
                return
            p.gate = PrGate(result="통과", lines=lines, files=files, paths=coder.area_of(paths))
            p.changed_paths = paths
            p.auto_review = None
            cr.files = rows
            cr.updated_at = self.clock()
            ok = True

        await self._apply(run)
        return ok

    async def _generate_prs(self, s: State, spec: str) -> None:
        todo = [t for t in s.tasks if t.spec == spec and t.state == "진행 중"]
        if not todo:
            return
        sp = next((x for x in s.specs if x.id == spec), None)
        req = self._request_of_spec(s, sp) if sp else None
        ctx = req.context_id if req else f"spec:{spec}"
        n = len(s.prs) + 30
        reqs = [p for p in sp.paras if p.kind == "req"] if sp else []
        created: list[PR] = []
        for i, t in enumerate(todo):
            number = n + i
            review = "불일치" if i == 1 else "통과"
            paths = planned_area(i)
            lines, files = (86, 3) if i == 0 else (41, 2)
            para = next((p for p in reqs if p.id == t.para), None)
            changed = (
                planned_paths(sp, para, reqs.index(para))
                if sp and para
                else changed_paths_for(t.spec, t.para, paths)
            )
            pr = PR(
                id=f"PR #{number}",
                number=number,
                task=t.id,
                title=f"{t.para}: {t.title}",
                branch=f"gen/{t.id.lower()}",
                reviews=[review],
                gate=PrGate(result="통과", lines=lines, files=files, paths=paths),
                merged=False,
                changed_paths=changed,
            )
            gen_text = f"{pr.id} 변경 {lines}줄·{files}파일·{paths} → 코드 규칙 통과"
            await self._execute(
                "generate_pr",
                ctx,
                pr.id,
                gen_text,
                vcs_bridge.pr_size_params(lines, files, pr.changed_paths, t.id),
            )
            review_text = f"{pr.id} 검수 {review}{' (완료 기준 C2)' if review == '불일치' else ''}"
            await self._execute("review_pr", ctx, f"{pr.id}:r1", review_text)
            # 관문을 지난 뒤에만 형상관리에 실제로 올린다 (목 설정이면 아무것도 안 함).
            # 연결이 여럿이면 올릴 레포를 제안만 하고 사람 승인(approveRepo)을 기다린다
            s.activity.extend(
                await self._route_and_open(
                    pr, req, spec, t.para, ctx, para.text if para else t.title
                )
            )
            s.prs.append(pr)
            created.append(pr)
            t.state = "머지 대기" if review == "통과" else "PR 검수"
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "planning",
                    "safety",
                    "execute(generate_pr)",
                    "execute",
                    gen_text,
                    "B",
                )
            )
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "planning",
                    "shop",
                    "github_create_pull",
                    "API",
                    f"{pr.id} 생성 ({pr.branch}, 라벨 generated-pr)",
                )
            )
            s.activity.append(
                self._act(
                    self.clock(), ctx, "planning", "planning", "review_pr", "A2A", review_text, "A"
                )
            )
        s.first_pr_minutes.append(52)
        # 검수 통과 PR은 merge_pr 규칙(#171)에 안 걸리면 자동 머지, 걸리면 대기열
        for pr in created:
            await self._try_auto_merge(s, pr)

    async def _review_pr(self, s: State, pr: str | int) -> None:
        p = self._find_pr(s, pr)
        if p is None:
            return
        task = next((t for t in s.tasks if t.id == p.task), None)
        ctx = self._ctx_of_pr(s, p)
        text = f"{p.id} 수정 커밋 후 재검수 통과"
        await self._execute("review_pr", ctx, f"{p.id}:r{len(p.reviews) + 1}", text)
        p.reviews.append("통과")
        if task:
            task.state = "머지 대기"
        s.activity.append(
            self._act(self.clock(), ctx, "planning", "planning", "review_pr", "A2A", text, "A")
        )
        await self._try_auto_merge(s, p)

    async def _merge_pr(self, s: State, pr: str | int) -> None:
        """사람 머지 버튼 (교차검증 대기·사람 확인 건의 판단도 여기서 한다)."""
        p = self._find_pr(s, pr)
        if p is None or p.merged:
            return
        if self._repo_pending(p):
            raise ActionError("REPO_NOT_APPROVED", f"{p.id} 올릴 레포를 먼저 승인해야 한다")
        await self._do_merge(s, p, pr_check(s, p), auto=False)

    async def _try_auto_merge(self, s: State, p: PR) -> bool:
        """마지막 검수 통과 + merge_pr 규칙에 안 걸리면 자동 머지.

        걸리면 "교차검증 대기", 되돌릴 수 없는 영역이면 "사람 확인"으로 두고 머지하지 않는다.
        safety가 merge_pr을 보류·거절해도 "교차검증 대기" (PR 생성·검수는 그대로 남는다)."""
        if p.merged or not p.reviews or p.reviews[-1] != "통과":
            return False
        if self.task_paused(s, p.task):
            hold = self._hold(p, WAIT_HUMAN, pr_check(s, p))
            hold.rules = [*hold.rules, "request_paused"]
            s.activity.append(
                self._act(self.clock(), self._ctx_of_pr(s, p), "dev", "dev", "auto_review",
                          "execute", f"{p.id} 요청이 사람 중단 중 → 자동 머지 안 함", "B")
            )  # fmt: skip
            return False
        if self._repo_pending(p):
            s.activity.append(self._repo_wait_act(self._ctx_of_pr(s, p), p))
            return False
        check, ev = await self._cross_check(s, p, pr_check(s, p), self._ctx_of_pr(s, p))
        if check.ok:
            try:
                await self._do_merge(s, p, check, auto=True)
                return True
            except GateRefused as exc:
                if exc.action != "merge_pr":
                    raise
                # 근거를 모은 뒤에도 safety가 막으면 더 기다릴 곳이 없어 사람 확인
                state = WAIT_HUMAN if ev else WAIT_CROSS_CHECK
                hold = self._hold(p, state, check, exc.decision, ev)
        else:
            hold = self._hold(p, pr_wait_state(check), check, evidence=ev)
        why = ", ".join(hold.rules) or f"safety {hold.gate_status}"
        s.activity.append(
            self._act(
                hold.at,
                self._ctx_of_pr(s, p),
                "planning",
                "planning",
                "auto_review",
                "execute",
                f"{p.id} 자동 머지 안 함 → {hold.state} ({why})",
                "B",
            )
        )
        return False

    # ---- 올릴 레포 (연결이 여럿일 때 제안 → 사람 승인)

    @staticmethod
    def _repo_pending(p: PR) -> bool:
        """올릴 레포가 아직 승인되지 않았나 (제안·기각). 목 설정이면 늘 False."""
        return p.target is not None and p.target.status != "approved"

    def _repo_wait_act(self, ctx: str, p: PR) -> Activity:
        state = "제안" if p.target and p.target.status == "proposed" else "기각"
        return self._act(
            self.clock(),
            ctx,
            "planning",
            "planning",
            "vcs.repo_pending",
            "API",
            f"{p.id} 올릴 레포 {state} 상태 → 승인 전까지 머지 대기 (approveRepo)",
        )

    async def _route_and_open(
        self,
        pr: PR,
        req: Request | None,
        spec_id: str | None,
        para_id: str | None,
        ctx: str,
        para_text: str,
    ) -> list[Activity]:
        """올릴 레포를 정하고, 승인된 연결이면 바로 올린다. 남길 활동 줄을 돌려준다."""
        inp = repo_routing.RouteInput(
            title=pr.title,
            text=para_text,
            changed_paths=tuple(pr.changed_paths),
            request_title=req.title if req else "",
            area=pr.gate.paths,
        )
        pr.target = await vcs_bridge.propose(pr, inp, self.clock())
        acts: list[Activity] = []
        if self._repo_pending(pr):
            text = vcs_bridge.proposed_text(pr) + " → 사람 승인 대기"
            acts.append(
                self._act(
                    self.clock(), ctx, "planning", "planning", "vcs.repo_proposed", "API", text
                )
            )
            return acts
        note = await vcs_bridge.open_pr(pr, req.id if req else None, spec_id, para_id)
        if note:
            acts.append(
                self._act(self.clock(), ctx, "planning", "planning", note.point, "API", note.text)
            )
        return acts

    def _repo_pr(self, s: State, pr: str | int) -> PR:
        p = self._find_pr(s, pr)
        if p is None:
            raise ActionError("NOT_FOUND", f"PR {pr}가 없다")
        if not vcs_bridge.enabled():
            raise ActionError("NO_VCS", "형상관리 연결이 없다 (목 설정)")
        if p.merged or p.vcs_number is not None:
            raise ActionError("INVALID_ACTION", f"{p.id}는 이미 형상관리에 올렸다")
        return p

    async def _approve_repo(self, s: State, pr: str | int, connection: str | None) -> None:
        """사람이 올릴 레포를 승인한다 (다른 연결을 고를 수도 있다). 승인 뒤 바로 올리고,
        이 승인만 기다리던 PR이면 자동 머지 규칙대로 이어 간다 (걸리면 대기열·사람 머지)."""
        p = self._repo_pr(s, pr)
        cid = connection or (p.target.connection_id if p.target else None)
        conn = next((c for c in vcs_bridge.targets() if c.id == cid), None)
        if conn is None:
            raise ActionError("UNKNOWN_CONNECTION", f"모르는 연결: {cid}")
        repo = conn.repo or conn.id
        ctx = self._ctx_of_pr(s, p)
        text = f"팀장 {TEAM_LEAD}이 {p.id} → {repo} 레포 승인"
        await self._execute("approve_repo", ctx, f"{p.id}:{conn.id}", text, {"connection": conn.id})
        old = p.target
        same = old is not None and old.connection_id == conn.id
        if old is not None and same:
            reasons = list(old.reasons)
        elif old is not None:
            reasons = [f"사람이 고름 (제안 {old.repo})"]
        else:
            reasons = ["사람이 고름"]
        p.target = RepoTarget(
            connection_id=conn.id,
            repo=repo,
            status="approved",
            confidence=old.confidence if old is not None and same else "low",
            reasons=reasons,
            candidates=list(old.candidates) if old is not None else [],
            decided_by=TEAM_LEAD,
            decided_at=self.clock(),
        )
        s.activity.append(
            self._act(
                self.clock(), ctx, "human", "planning", "레포 승인", "사람", f"{text} (사람 클릭)"
            )
        )
        task = next((t for t in s.tasks if t.id == p.task), None)
        sp = next((x for x in s.specs if task and x.id == task.spec), None)
        req = self._request_of_spec(s, sp) if sp else None
        note = await vcs_bridge.open_pr(
            p, req.id if req else None, task.spec if task else None, task.para if task else None
        )
        if note:
            s.activity.append(
                self._act(self.clock(), ctx, "planning", "planning", note.point, "API", note.text)
            )
        if note is not None and note.ok and p.auto_review is None:
            await self._try_auto_merge(s, p)

    async def _reject_repo(self, s: State, pr: str | int) -> None:
        """사람이 제안된 레포를 기각한다. 아무것도 올리지 않는다 (나중에 연결을 골라 승인)."""
        p = self._repo_pr(s, pr)
        if p.target is None:
            raise ActionError("INVALID_ACTION", f"{p.id}에 제안된 레포가 없다")
        ctx = self._ctx_of_pr(s, p)
        text = f"팀장 {TEAM_LEAD}이 {p.id} → {p.target.repo} 레포 제안 기각"
        await self._execute("reject_repo", ctx, f"{p.id}:reject", text)
        p.target.status = "rejected"
        p.target.decided_by = TEAM_LEAD
        p.target.decided_at = self.clock()
        s.activity.append(
            self._act(
                self.clock(), ctx, "human", "planning", "레포 기각", "사람", f"{text} (사람 클릭)"
            )
        )

    def _ctx_of_pr(self, s: State, p: PR) -> str:
        task = next((t for t in s.tasks if t.id == p.task), None)
        sp = next((x for x in s.specs if task and x.id == task.spec), None)
        req = self._request_of_spec(s, sp) if sp else None
        return req.context_id if req else f"pr:{p.number}"

    async def _do_merge(self, s: State, p: PR, check: Check, *, auto: bool) -> None:
        task = next((t for t in s.tasks if t.id == p.task), None)
        spec_id = task.spec if task else None
        sp = next((x for x in s.specs if x.id == spec_id), None)
        req = self._request_of_spec(s, sp) if sp else None
        ctx = req.context_id if req else f"pr:{p.number}"
        c = check.params
        rule_text = (
            f"{c['changed_lines']}줄·{c['changed_files']}파일·검수 불일치 "
            f"{c['review_mismatch_count']}·장애 연결 {c['linked_incident_count']}"
            f"·되돌릴 수 없음 {c['irreversible_count']}"
        )
        if auto:
            merge_text = f"{p.id} 자동 머지 ({rule_text} → merge_pr 규칙 통과, 사후 감사)"
        else:
            merge_text = f"사람이 {p.id} 머지"
        await self._execute("merge_pr", ctx, p.id, merge_text, {**check.params, "auto": auto})
        # merge_pr 관문을 지난 뒤 형상관리에서 실제로 병합한다.
        # 실패하면 한 줄 남기고 병합하지 않는다
        note = await vcs_bridge.merge_pr(p)
        if note:
            s.activity.append(
                self._act(self.clock(), ctx, "planning", "planning", note.point, "API", note.text)
            )
            if not note.ok:
                return
        case = f"① kind pr_merged · {p.id}" + (" (자동 머지)" if auto else "")
        merged_hr = await self._send_hr(
            ctx, RecordKind.PR_MERGED, f"{p.id}:pr_merged", case,
            f"{p.id} {p.title} " + ("자동 머지 (사후 감사 대상)" if auto else "사람 머지"),
            f"merge_pr 규칙 통과: {rule_text}" if auto else "사람이 머지 버튼으로 승인",
            [f"pr:{p.number}"],
            self._hr_area(s, [p], *self._hr_titles(s, spec_id)),
        )  # fmt: skip
        if task:
            task.state = "완료"
        all_done = bool(spec_id) and all(t.state == "완료" for t in s.tasks if t.spec == spec_id)
        closed = [r for r in s.requests if r.spec == spec_id] if all_done else []
        closed_ids = ", ".join(r.id for r in closed)
        closed_case = f"① kind request_closed · {closed_ids}"
        closed_hr = None
        if closed:
            closed_hr = await self._send_hr(
                ctx, RecordKind.REQUEST_CLOSED, f"{spec_id}:request_closed", closed_case,
                f"{closed_ids} 완료 (기획서 {spec_id} 작업 전부 머지)",
                f"{spec_id} 작업 {sum(1 for x in s.tasks if x.spec == spec_id)}개 머지",
                [f"request:{r.id}" for r in closed] + [f"spec:{spec_id}"],
                self._hr_area(
                    s,
                    [x for x in s.prs if x.task in {t.id for t in s.tasks if t.spec == spec_id}],
                    *self._hr_titles(s, spec_id),
                ),
            )  # fmt: skip
        t = self.clock()
        p.merged = True
        p.merged_at = t
        p.auto_review = None
        if task and task.coding is not None:
            # 코딩 에이전트 PR은 사람 머지로 끝나도 진행 상태를 맞춘다
            task.coding.status = "merged"
            task.coding.error = None
            task.coding.updated_at = t
        if auto:
            p.auto = True
            s.activity.append(
                self._act(
                    t, ctx, "planning", "safety", "execute(merge_pr)", "execute", merge_text, "B"
                )
            )
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "planning",
                    "shop",
                    "github_merge_pull",
                    "API",
                    f"{p.id} 자동 머지 → dev 배포",
                )
            )
        else:
            s.activity.append(
                self._act(
                    t,
                    ctx,
                    "human",
                    "planning",
                    "PR 머지",
                    "사람",
                    f"사람이 {p.id} 머지 (사람 클릭)",
                )
            )
        s.activity.append(self._hr_act(ctx, case, merged_hr))
        if closed:
            s.activity.append(self._hr_act(ctx, closed_case, closed_hr))
            for r in closed:
                r.state = "완료"
            spec_tasks = {x.id for x in s.tasks if x.spec == spec_id}
            spec_prs = [x for x in s.prs if x.task in spec_tasks]
            for r in closed:
                await self._notify_result(s, r, "completed", prs=spec_prs, spec_id=spec_id)
            to = req.from_ if req else closed[0].from_
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "planning",
                    to,
                    "get_request_status",
                    "A2A",
                    f'{closed_ids} "완료"를 요청 부서에 회신',
                    "read",
                )
            )

    async def _approve_promotion(self, s: State) -> None:
        pol = s.auto_policy
        if pol.proposal is None:
            return
        to = pol.proposal.to
        lv = AUTO_LEVELS[to]
        t = self.clock()
        text = (
            f"간단 요청 기준 {lv.lines}줄·{lv.files}파일·{pol.paths}로 갱신 요청"
            " (기준표 pr_changed_lines·files)"
        )
        await self._execute(
            "auto_policy_update",
            "auto_policy",
            f"L{pol.level}->L{to}",
            text,
            {"to": to, "lines": lv.lines, "files": lv.files, "paths": pol.paths},
        )
        s.activity.append(
            self._act(
                t,
                "auto_policy",
                "human",
                "planning",
                "자동 범위 승급 승인",
                "사람",
                f"팀장 {TEAM_LEAD}이 L{pol.level} → L{to} 승인 (사람 클릭)",
            )
        )
        s.activity.append(
            self._act(t, "auto_policy", "planning", "safety", "auto_policy.updated", "이벤트", text)
        )
        pol.log.append(
            AutoLog(
                at=t,
                text=f"L{pol.level} → L{to} 승급 승인 ({lv.lines}줄·{lv.files}파일)",
                by="사람",
            )
        )
        pol.level = to
        pol.streak = 0
        pol.proposal = None

    async def _reject_promotion(self, s: State) -> None:
        pol = s.auto_policy
        if pol.proposal is None:
            return
        t = self.clock()
        pol.log.append(
            AutoLog(at=t, text=f"L{pol.proposal.to} 승급 보류 (연속 건수 다시 셈)", by="사람")
        )
        pol.streak = 0
        pol.proposal = None


PAUSE = "사람이 요청 중단"  # 요청 중단으로 멈춘 코딩 작업의 오류 줄 앞머리
RELEASE_CHECK = "request_release_check"  # dev → planning 배포 전 대조
TRIAL_TOKENS = 1_000_000  # 서버 AI 체험 한도 기본값 (PLANNING_TRIAL_TOKENS)
AGENT_SEEN_DAYS = 7  # 이 기간 안에 본인 Claude로 작업을 본 개발자가 있으면 '연결됨'
WAIT_POLL_SECONDS = 30  # 다른 서비스 요청을 기다리는 대화를 다시 보는 간격


PR_SYNC_SECONDS = 60  # 형상관리에서 직접 머지·닫은 PR을 맞추는 간격


def _cross_check_timeout_min() -> int:
    """교차검증 대기를 사람 확인으로 넘기기까지 분 (`PLANNING_CROSS_CHECK_TIMEOUT_MIN`, 기본 10)."""
    try:
        return max(1, int(os.getenv("PLANNING_CROSS_CHECK_TIMEOUT_MIN", "10")))
    except ValueError:
        return 10
