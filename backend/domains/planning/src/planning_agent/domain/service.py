"""기획개발 상태 변화 (프로토타입 `data.ts` 리듀서의 모든 액션을 메서드로 옮긴 것).

규칙:

- 상태를 바꾸는 단계(요청 등록·기획서 저장·작업 생성·PR 생성·머지·이벤트 전송·자동 범위 변경)는
  템플릿 실행 관문 `ax_agent_template.execute.execute()`를 거친다. 관문이 `executed`가 아닌 결과
  (거절·보류·무응답)를 주면 `GateRefused`를 던지고 그 액션 전체를 버린다(fail-closed).
  승급 제안(`auto_policy_propose`, C)만은 보류(`held`)가 정상 결과다 (팀장 승인 대기).
- 액션은 상태 복사본 위에서 계산하고, 끝까지 성공했을 때만 바꿔 끼운다
  (중간 실패 시 원래 상태 유지).
- 기획개발이 다른 부서로 보낸 활동은 모두 그 부서 창구(outbox)에도 쌓는다.
- 숫자·중복·판정은 `rules.py`의 코드로만 계산한다. ID·시각은 주입한 시계와 상태 길이로 만든다.
- 시뮬레이터 액션(`inbound`, `dept_chat`)은 목 모드에서만 받는다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ax_agent_template import config as template_config
from ax_agent_template import execute as gate_module
from ax_contracts.schemas import TENANT_ID_DEFAULT, Evidence, ExecuteDecision, RequestMeta
from ax_contracts.schemas.planning import ChangeResultInput, DevHandoffInput, DevResultInput
from ax_contracts.schemas.record import RecordCase, RecordKind
from planning_agent import coder, repo_routing, vcs, vcs_bridge
from planning_agent.dev_handoff import (
    RECEIVE_DEV_RESULT,
    ROLES,
    START_DEV,
    DevSender,
    dev_result_key,
    send_to_service,
    start_dev_key,
)
from planning_agent.domain.autocheck import (
    WAIT_CROSS_CHECK,
    Check,
    planned_area,
    planned_paths,
    pr_check,
    pr_wait_state,
    spec_check,
    spec_wait_state,
)
from planning_agent.domain.clock import Clock, default_clock_factory
from planning_agent.domain.docs import gather_blocks, next_block_id
from planning_agent.domain.handover import HandoverCheck, Item, check_handover, in_progress
from planning_agent.domain.models import (
    DEPT_NAME,
    PR,
    Activity,
    AutoLog,
    AutoPolicy,
    AutoProposal,
    AutoReview,
    AutoRun,
    ChatMessage,
    ChatPlan,
    ChatThread,
    CodingFile,
    CodingReview,
    CodingRun,
    Decision,
    Doc,
    DocBlock,
    Incident,
    IncidentCandidate,
    Message,
    OutMsg,
    Para,
    PrGate,
    RepoTarget,
    ReqState,
    Request,
    Spec,
    State,
    Task,
    dump,
)
from planning_agent.domain.rules import (
    AUTO_LEVELS,
    PROMOTE_AFTER,
    auto_limit,
    check_simple,
    deploy_of,
    estimate,
    find_duplicate,
    incident_of,
    merged_in_window,
    para_of,
    verify_rollback,
)
from planning_agent.domain.seed import initial_state
from planning_agent.domain.trace import changed_paths_for
from planning_agent.result_notify import ResultSender, send_change_result

AGENT_NAME = "planning"
TEAM_LEAD = "노예진"  # 확정·머지·승급 승인 버튼을 누르는 팀장 (목)

Gate = Callable[[str, dict, RequestMeta, Evidence], Awaitable[ExecuteDecision]]

INBOUND_KINDS = (
    "support_ticket",
    "marketing_ticket",
    "hr_draft",
    "data_draft",
    "data_query",
    "infra_query",
    "orch_incident",
    "safety_verify",
    "support_simple",
    "marketing_simple",
)
ADVANCE_LIMIT = 40  # 다음 단계 반복 상한 (무한 반복 방지)
_TICKET_KINDS = ("support_ticket", "marketing_ticket", "support_simple", "marketing_simple")

SAMPLE_TICKETS = {
    "support": [
        "배송 조회 화면에서 송장 번호가 안 보인다",
        "쿠폰 적용 후 결제 금액이 두 번 계산된다",
        "결제 실패 뒤 장바구니가 비워진다",
    ],
    "marketing": [
        "기획전 배너가 모바일에서 잘린다",
        "신간 알림 신청 버튼 추가",
        "장바구니 보존 문구를 공지에도 반영",
    ],
}
SIMPLE_TICKETS = {
    "support": [
        "상품 상세의 배송 안내 문구 오타 수정",
        "고객센터 운영 시간 안내 문구 변경",
        "마이페이지 메뉴 라벨 오타 수정",
    ],
    "marketing": [
        '신간 목록 정렬 라벨을 "최신순"으로 변경',
        "이벤트 페이지 링크가 지난 기획전으로 연결된다",
        "기획전 페이지 안내 문구 전체 교체",
    ],
}
DEPT_CHAT = {
    "support": [
        "배송 조회 송장 번호 문의가 하루 10건 넘게 들어옵니다. 우선순위 올려 주실 수 있나요?",
        "REQ-12 처리 상태 고객에게 안내해도 될까요?",
    ],
    "marketing": [
        "다음 주 기획전 배너 문구 확정본 공유드려요. 장바구니 보존 공지는 REQ-12 완료 뒤에 낼게요.",
        "신간 알림 버튼 기획 일정이 궁금합니다.",
    ],
    "hr": [
        '이번 주 인수인계 질문 중 "결제 실패 안내"가 3번 반복됐습니다. '
        "운영 매뉴얼 반영 초안 보낼게요.",
        "결정 카드 DC-44에 SPEC-7 결정 이유를 붙였습니다.",
    ],
    "data": [
        "결제 실패율이 14:00~14:10에 또 올랐습니다. 직전 배포가 있었는지 확인 부탁드려요.",
        "장바구니 이탈률 지표 정의 공유드립니다 (코드로 계산).",
    ],
    "infra": [
        "shop-api v2.4.1 롤백 후 p95가 정상 범위로 돌아왔습니다.",
        "카나리 배포 설정 초안 준비됐습니다. REQ-101에 붙여 주세요.",
    ],
}
# answerQuestion의 목 해석 (프로토타입과 같은 고정 문장. 실제로는 extract_decision이 만든다)
ANSWER_R1_TEXT = "결제 실패 시 장바구니 상태 보존 (품절 상품은 남기고 품절 표시)"
ANSWER_DECISION = {
    "text": "품절 상품은 장바구니에 남기고 품절 표시",
    "reason": "고객지원 의견: 다시 담는 불편이 더 큼",
    "alternatives": "품절 상품 자동 삭제",
}
ANSWER_WHO = "박지아 (고객지원)"


# ---------------------------------------------------------------- 관문·결과 타입


async def template_gate(
    action: str, params: dict, context: RequestMeta, evidence: Evidence
) -> ExecuteDecision:
    """템플릿 실행 관문. 모듈 속성으로 찾아 부르므로 테스트에서 `_send_to_safety`를 바꿀 수 있다."""
    return await gate_module.execute(action, params, context, evidence)


def _pr_size_params(lines: int, files: int, paths: list[str], task: str) -> dict:
    """`generate_pr` execute params. 안전 레이어 코드 규칙(#83)이 읽는 키 이름을 쓴다."""
    return {
        "changed_lines": lines,
        "changed_files": files,
        "changed_paths": sorted(set(paths)),
        "task": task,
    }


class GateRefused(Exception):
    """관문이 실행을 허락하지 않았다. 액션 전체를 버린다."""

    def __init__(self, action: str, idempotency_key: str, decision: ExecuteDecision):
        super().__init__(f"{action} 실행 거부 ({decision.status})")
        self.action = action
        self.idempotency_key = idempotency_key
        self.decision = decision


class ActionError(Exception):
    """잘못된 액션 (모르는 type, 빠진 필드, 목 모드 밖 시뮬레이터)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class GateCall:
    action: str
    idempotency_key: str
    status: str
    grade: str
    execution_id: str


@dataclass
class ActionResult:
    changed: bool = False
    outcome: str | None = None
    output: dict[str, Any] = field(default_factory=dict)


# ---- 액션 입력 (프로토타입 Action과 같은 모양)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class InboundParams(_Params):
    kind: Literal[INBOUND_KINDS]  # type: ignore[valid-type]


class SendParams(_Params):
    to: str
    point: str
    text: str
    ctx: str = "ctx_req_0012"


class ToParams(_Params):
    to: str


class SpecParams(_Params):
    spec: str


class AnswerParams(_Params):
    spec: str
    text: str


class PrParams(_Params):
    pr: str | int


class RepoParams(_Params):
    """올릴 레포 승인. connection을 안 주면 제안된 연결로."""

    pr: str | int
    connection: str | None = None


class RequestParams(_Params):
    request: str


class RejectDraftParams(_Params):
    """개선 초안 기각. 이유·검토한 대안이 비면 hr에 보내지 않고 `MISSING_DECISION_REASON`."""

    request: str
    decision_reason: str = ""
    alternatives_considered: list[str] = Field(default_factory=list)


class RejectDecisionParams(_Params):
    """기각한 안을 결정으로 남긴다 (예: 기획서 검토 중 버린 안)."""

    spec: str
    text: str
    decision_reason: str = ""
    alternatives_considered: list[str] = Field(default_factory=list)


class EmptyParams(_Params):
    pass


class PostMessageParams(_Params):
    thread: str
    text: str


class DeptChatParams(_Params):
    from_: str = Field(alias="from")


class ExtractDecisionParams(_Params):
    thread: str
    index: int


class NewDocParams(_Params):
    title: str = ""


class GatherDocParams(_Params):
    about: str
    by: Literal["ai", "human"] | None = None


class RenameDocParams(_Params):
    doc: str
    title: str


class AddBlockParams(_Params):
    doc: str
    kind: Literal["h2", "p", "todo", "ref"]
    text: str | None = None


class EditBlockParams(_Params):
    doc: str
    block: str
    text: str


class BlockParams(_Params):
    doc: str
    block: str


# snake_case 액션 이름 → (메서드, 입력 모델). 화면이 보내는 camelCase(`confirmSpec`)도 받는다.
ACTIONS: dict[str, tuple[str, type[_Params]]] = {
    "inbound": ("_inbound", InboundParams),
    "send": ("_send", SendParams),
    "mark_read": ("_mark_read", ToParams),
    "confirm_spec": ("_confirm_spec", SpecParams),
    "check_spec": ("_check_spec", SpecParams),
    "answer_question": ("_answer_question", AnswerParams),
    "generate_prs": ("_generate_prs", SpecParams),
    "review_pr": ("_review_pr", PrParams),
    "merge_pr": ("_merge_pr", PrParams),
    "approve_repo": ("_approve_repo", RepoParams),
    "reject_repo": ("_reject_repo", PrParams),
    "accept_draft": ("_accept_draft", RequestParams),
    "reject_draft": ("_reject_draft", RejectDraftParams),
    "reject_decision": ("_reject_decision", RejectDecisionParams),
    "approve_promotion": ("_approve_promotion", EmptyParams),
    "reject_promotion": ("_reject_promotion", EmptyParams),
    "post_message": ("_post_message", PostMessageParams),
    "dept_chat": ("_dept_chat", DeptChatParams),
    "extract_decision": ("_extract_decision", ExtractDecisionParams),
    "new_doc": ("_new_doc", NewDocParams),
    "gather_doc": ("_gather_doc", GatherDocParams),
    "rename_doc": ("_rename_doc", RenameDocParams),
    "add_block": ("_add_block", AddBlockParams),
    "edit_block": ("_edit_block", EditBlockParams),
    "toggle_todo": ("_toggle_todo", BlockParams),
    "remove_block": ("_remove_block", BlockParams),
}
SIMULATOR_ACTIONS = {"inbound", "dept_chat"}


def action_name(raw: str) -> str:
    """`confirmSpec` → `confirm_spec`. 이미 snake_case면 그대로."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", raw).lower()


def _token() -> str:
    try:
        return template_config.agent_token(AGENT_NAME)
    except RuntimeError:
        return "unconfigured"  # 관문 쪽에서 거부된다 (fail-closed)


def _fmt(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%m-%d %H:%M UTC")


# ---------------------------------------------------------------- 서비스


class PlanningService:
    """프로세스 메모리에 상태 하나를 두고 액션을 적용한다 (목 모드 저장소)."""

    def __init__(
        self,
        clock: Clock | None = None,
        gate: Gate | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
        result_sender: ResultSender | None = None,
        role: str | None = None,
        dev_sender: DevSender | None = None,
    ):
        # 서비스 역할: "" = 합친 모드(원래 동작), "planning" = 기획서까지, "dev" = PR·머지만
        self.role = (os.getenv("PLANNING_ROLE", "") if role is None else role).strip()
        if self.role and self.role not in ROLES:
            raise ValueError(f"모르는 PLANNING_ROLE: {self.role}")
        self._send_dev: DevSender = dev_sender or send_to_service
        self._sending: set[asyncio.Task] = set()
        # 코딩 에이전트 실행 (작업 id → 실행 중인 태스크·실행 번호). 번호가 바뀐 실행은 버린다
        self._coding: dict[str, asyncio.Task] = {}
        self._coding_live: dict[str, int] = {}
        self._coding_seq = 0
        self._clock_factory: Callable[[], Clock] = (
            (lambda: clock) if clock else default_clock_factory()
        )
        self._gate = gate or template_gate
        self._send_result: ResultSender = result_sender or send_change_result
        self.tenant_id = tenant_id
        self.gate_calls: list[GateCall] = []
        self._subscribers: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
        self._locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self.version = 0
        self._init_state()

    # ---- 상태·구독

    def _init_state(self) -> None:
        self.clock: Clock = self._clock_factory()
        self.state: State = initial_state()
        self.state.tenant_id = self.tenant_id
        self._idempotent: dict[str, ActionResult] = {}
        self._dev_sent: dict[str, str] = {}  # dev: 기획서 → 마지막으로 돌려준 결과 지문

    def reset(self) -> None:
        """초기 데이터로 되돌린다 (구독자는 유지하고 새 상태를 알린다)."""
        self._init_state()
        self._coding_live.clear()  # 돌던 코딩 실행은 다음 단계에서 멈춘다
        self.__dict__.pop("_coding_drafts", None)  # 작성해 둔 코드도 지운다
        self.gate_calls.clear()
        self.version += 1
        self._notify()

    def snapshot(self) -> dict:
        return dump(self.state)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[id(queue)] = (asyncio.get_running_loop(), queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.pop(id(queue), None)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def _notify(self) -> None:
        for key, (loop, queue) in list(self._subscribers.items()):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, self.version)
            except RuntimeError:  # 닫힌 이벤트 루프
                self._subscribers.pop(key, None)

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = self._locks[loop] = asyncio.Lock()
        return lock

    # ---- 적용 틀

    async def _apply(self, fn: Callable[..., Awaitable[ActionResult | None]], **params: Any):
        """복사본에 액션을 적용하고, 끝까지 성공하면 바꿔 끼운다. 관문 거부면 원래 상태 유지."""
        async with self._lock():
            s = self.state.model_copy(deep=True)
            before = len(s.activity)
            result = await fn(s, **params) or ActionResult()
            await self._advance(s)
            outgoing = self._dev_outgoing(s)
            self._to_outbox(s, before)
            result.changed = s != self.state
            if result.changed:
                self.state = s
                self.version += 1
                self._notify()
        # 넘기기는 상태를 바꿔 끼운 뒤 잠금 밖에서 보낸다 (잠금을 쥔 채 네트워크를 부르지 않는다)
        for item in outgoing:
            self._spawn_send(*item)
        return result

    # ---- 기획 → 개발 넘기기 (서비스 역할이 있을 때만)

    def _dev_outgoing(self, s: State) -> list[tuple[str, str, BaseModel, str]]:
        """이번 액션으로 보낼 넘기기 목록 (skill, context_id, 입력, idempotency_key).

        planning: 이번에 넘김 표시가 붙은 기획서.
        dev: 작업·PR·요청 상태가 지난번과 달라진 기획서."""
        if not self.role:
            return []
        out: list[tuple[str, str, BaseModel, str]] = []
        if self.role == "planning":
            was = {sp.id for sp in self.state.specs if sp.handed_to_dev}
            for sp in s.specs:
                req = self._request_of_spec(s, sp)
                if not sp.handed_to_dev or sp.id in was or req is None:
                    continue
                inp = DevHandoffInput(
                    request=dump(req),
                    spec=dump(sp),
                    tasks=[dump(t) for t in s.tasks if t.spec == sp.id],
                )
                out.append((START_DEV, req.context_id, inp, start_dev_key(req.context_id, sp.id)))
            return out
        for sp in s.specs:
            req = self._request_of_spec(s, sp)
            if not sp.handed_to_dev or req is None:
                continue
            tasks = [t for t in s.tasks if t.spec == sp.id]
            ids = {t.id for t in tasks}
            res = DevResultInput(
                spec_id=sp.id,
                request_id=req.id,
                request_state=req.state,
                tasks=[dump(t) for t in tasks],
                prs=[dump(p) for p in s.prs if p.task in ids],
            )
            raw = json.dumps(res.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            fp = hashlib.sha1(raw.encode()).hexdigest()[:12]
            if self._dev_sent.get(sp.id) == fp:
                continue
            self._dev_sent[sp.id] = fp
            out.append(
                (RECEIVE_DEV_RESULT, req.context_id, res, dev_result_key(req.context_id, sp.id, fp))
            )
        return out

    def _spawn_send(self, skill: str, ctx: str, payload: BaseModel, key: str) -> None:
        task = asyncio.create_task(self._send_one(skill, ctx, payload, key))
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    async def _send_one(self, skill: str, ctx: str, payload: BaseModel, key: str) -> None:
        """한 건 보낸다. 실패해도 상태는 그대로 두고 활동에 한 줄 남긴다."""
        try:
            how = await self._send_dev(skill, ctx, self.tenant_id, payload, key)
        except Exception:  # 넘기기 실패가 이미 바꾼 상태를 되돌리지 않게 한다
            how = "보내지 못함 (오류)"
        if how.startswith("보냄"):
            return
        frm, to = ("planning", "dev") if self.role == "planning" else ("dev", "planning")

        async def log(s: State) -> None:
            s.activity.append(
                self._act(self.clock(), ctx, frm, to, skill, "A2A", f"{skill} {how}", "A")
            )

        await self._apply(log)

    async def wait_sent(self) -> None:
        """보내는 중인 넘기기가 끝날 때까지 기다린다 (테스트용)."""
        while self._sending:
            await asyncio.gather(*list(self._sending), return_exceptions=True)

    def _hand_to_dev(self, s: State, sp: Spec, req: Request | None, ctx: str) -> None:
        """planning 역할: 확정된 기획서를 dev 서비스로 넘긴다고 표시한다 (보내기는 바꿔 끼운 뒤)."""
        if self.role != "planning" or req is None or sp.handed_to_dev:
            return
        ids = [t.id for t in s.tasks if t.spec == sp.id and t.state == "진행 중"]
        if not ids:
            return
        sp.handed_to_dev = True
        s.activity.append(
            self._act(
                self.clock(),
                ctx,
                "planning",
                "dev",
                START_DEV,
                "A2A",
                f"{sp.id} 작업 {', '.join(ids)} → 개발·배포 서비스로 넘김",
                "A",
            )
        )

    def _to_outbox(self, s: State, before: int) -> None:
        """기획개발이 다른 부서로 보낸 활동은 그 부서 창구(outbox)에도 쌓는다."""
        next_id = len(s.outbox)
        for a in s.activity[before:]:
            if a.from_ == "planning" and a.to not in ("planning", "dev"):
                next_id += 1
                s.outbox.append(
                    OutMsg(
                        id=next_id,
                        at=a.at,
                        context_id=a.context_id,
                        tenant_id=a.tenant_id,
                        to=a.to,
                        point=a.point,
                        text=a.text,
                    )
                )

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
        """실행 관문을 거친다. 허용 상태가 아니면 `GateRefused` (fail-closed)."""
        key = f"{ctx}:{action}:{target}"
        meta = RequestMeta(
            context_id=ctx,
            tenant_id=self.tenant_id,
            token=_token(),
            deadline=datetime.now(UTC) + timedelta(seconds=30),
            idempotency_key=key,
        )
        evidence = Evidence(data_summary=summary, ref_ids=refs or [target])
        try:
            decision = await self._gate(
                action, {"target": target, **(params or {})}, meta, evidence
            )
        except Exception:
            decision = ExecuteDecision(execution_id="exe_unavailable", grade="C", status="rejected")
        self.gate_calls.append(
            GateCall(action, key, decision.status, decision.grade, decision.execution_id)
        )
        if decision.status not in allow:
            raise GateRefused(action, key, decision)
        return decision

    def _act(
        self,
        at: datetime,
        ctx: str,
        frm: str,
        to: str,
        point: str,
        kind: str,
        text: str,
        grade: str | None = None,
        seq: int | None = None,
    ) -> Activity:
        return Activity(
            seq=seq,
            at=at,
            context_id=ctx,
            tenant_id=self.tenant_id,
            from_=frm,
            to=to,
            point=point,
            kind=kind,
            grade=grade,
            text=text,
        )

    @staticmethod
    def _request_of_spec(s: State, spec: Spec) -> Request | None:
        return next((r for r in s.requests if r.spec == spec.id), None) or next(
            (r for r in s.requests if r.id == spec.request), None
        )

    @staticmethod
    def _find_pr(s: State, ref: str | int) -> PR | None:
        if isinstance(ref, int):
            return next((p for p in s.prs if p.number == ref), None)
        found = next((p for p in s.prs if p.id == ref), None)
        if found:
            return found
        digits = re.sub(r"\D", "", ref)
        return next((p for p in s.prs if digits and p.number == int(digits)), None)

    def _hold(
        self,
        target: Spec | PR,
        state: str,
        check: Check,
        decision: ExecuteDecision | None = None,
    ) -> AutoReview:
        """자동 확정·자동 머지를 멈추고 대기열에 올린다 (판단은 기존 사람 버튼)."""
        target.auto_review = AutoReview(
            state=state,
            rules=list(check.rules),
            params=dict(check.params),
            at=self.clock(),
            gate_status=decision.status if decision else None,
            review_id=decision.review_id if decision else None,
        )
        return target.auto_review

    # ---- 화면 액션 진입점

    async def dispatch(self, action: dict) -> ActionResult:
        """`{"type": "confirmSpec", "spec": "SPEC-7"}` 같은 프로토타입 Action을 적용한다."""
        if not isinstance(action, dict) or not isinstance(action.get("type"), str):
            raise ActionError("INVALID_ACTION", "type이 없다")
        name = action_name(action["type"])
        if name not in ACTIONS:
            raise ActionError("UNKNOWN_ACTION", f"모르는 액션: {action['type']}")
        if name in SIMULATOR_ACTIONS and not template_config.is_mock(AGENT_NAME):
            raise ActionError("SIMULATOR_DISABLED", "시뮬레이터 액션은 목 모드에서만 받는다")
        method, model = ACTIONS[name]
        try:
            params = model.model_validate({k: v for k, v in action.items() if k != "type"})
        except ValidationError as exc:
            raise ActionError("INVALID_ACTION", str(exc)) from exc
        kwargs = {k: v for k, v in params.model_dump().items()}
        return await self._apply(getattr(self, method), **kwargs)

    # 이름이 있는 메서드 (테스트·내부 호출용). 모두 dispatch와 같은 길을 탄다.
    async def inbound(self, kind: str) -> ActionResult:
        return await self.dispatch({"type": "inbound", "kind": kind})

    async def confirm_spec(self, spec: str) -> ActionResult:
        return await self.dispatch({"type": "confirm_spec", "spec": spec})

    async def check_spec(self, spec: str) -> ActionResult:
        return await self.dispatch({"type": "check_spec", "spec": spec})

    async def answer_question(self, spec: str, text: str) -> ActionResult:
        return await self.dispatch({"type": "answer_question", "spec": spec, "text": text})

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

    async def accept_draft(self, request: str) -> ActionResult:
        return await self.dispatch({"type": "accept_draft", "request": request})

    async def reject_draft(
        self,
        request: str,
        decision_reason: str = "",
        alternatives_considered: list[str] | None = None,
    ) -> ActionResult:
        return await self.dispatch(
            {
                "type": "reject_draft",
                "request": request,
                "decision_reason": decision_reason,
                "alternatives_considered": alternatives_considered or [],
            }
        )

    async def reject_decision(
        self,
        spec: str,
        text: str,
        decision_reason: str = "",
        alternatives_considered: list[str] | None = None,
    ) -> ActionResult:
        return await self.dispatch(
            {
                "type": "reject_decision",
                "spec": spec,
                "text": text,
                "decision_reason": decision_reason,
                "alternatives_considered": alternatives_considered or [],
            }
        )

    async def approve_promotion(self) -> ActionResult:
        return await self.dispatch({"type": "approve_promotion"})

    async def reject_promotion(self) -> ActionResult:
        return await self.dispatch({"type": "reject_promotion"})

    async def post_message(self, thread: str, text: str) -> ActionResult:
        return await self.dispatch({"type": "post_message", "thread": thread, "text": text})

    async def dept_chat(self, from_: str) -> ActionResult:
        return await self.dispatch({"type": "dept_chat", "from": from_})

    async def extract_decision(self, thread: str, index: int) -> ActionResult:
        return await self.dispatch({"type": "extract_decision", "thread": thread, "index": index})

    async def new_doc(self, title: str = "") -> ActionResult:
        return await self.dispatch({"type": "new_doc", "title": title})

    async def gather_doc(self, about: str, by: str | None = None) -> ActionResult:
        return await self.dispatch({"type": "gather_doc", "about": about, "by": by})

    async def rename_doc(self, doc: str, title: str) -> ActionResult:
        return await self.dispatch({"type": "rename_doc", "doc": doc, "title": title})

    async def add_block(self, doc: str, kind: str, text: str | None = None) -> ActionResult:
        return await self.dispatch({"type": "add_block", "doc": doc, "kind": kind, "text": text})

    async def edit_block(self, doc: str, block: str, text: str) -> ActionResult:
        return await self.dispatch({"type": "edit_block", "doc": doc, "block": block, "text": text})

    async def toggle_todo(self, doc: str, block: str) -> ActionResult:
        return await self.dispatch({"type": "toggle_todo", "doc": doc, "block": block})

    async def remove_block(self, doc: str, block: str) -> ActionResult:
        return await self.dispatch({"type": "remove_block", "doc": doc, "block": block})

    async def send(self, to: str, point: str, text: str) -> ActionResult:
        return await self.dispatch({"type": "send", "to": to, "point": point, "text": text})

    async def mark_read(self, to: str) -> ActionResult:
        return await self.dispatch({"type": "mark_read", "to": to})

    # ---------------------------------------------------------------- 들어오는 연동 (시뮬레이터)

    async def _inbound(self, s: State, kind: str) -> ActionResult | None:
        seq = s.seq + 1
        t = self.clock()
        ctx_req = f"ctx_req_{20 + seq:04d}"
        merged = [p for p in s.prs if p.merged]
        last_merged = merged[-1] if merged else None
        s.seq = seq

        def act(ctx, frm, to, point, kind_, text, grade=None):
            return self._act(t, ctx, frm, to, point, kind_, text, grade, seq)

        if kind in _TICKET_KINDS:
            frm = "support" if kind.startswith("support") else "marketing"
            simple = kind.endswith("simple")
            pool = SIMPLE_TICKETS[frm] if simple else SAMPLE_TICKETS[frm]
            # 간단 요청은 아직 안 보낸 것부터, 일반 요청은 돌아가며 (중복 연결 시연용)
            used = {r.title for r in s.requests}
            if simple:
                title = next(
                    (x for x in pool if x not in used), f"{pool[seq % len(pool)]} ({seq}차)"
                )
            else:
                title = pool[seq % len(pool)]
            return await self._register_ticket(s, frm, title, ctx_req, t, seq)

        if kind in ("hr_draft", "data_draft"):
            frm = "hr" if kind == "hr_draft" else "data"
            req_id = f"REQ-{20 + seq}"
            title = (
                "반복되는 인수인계 질문을 운영 매뉴얼에 반영"
                if frm == "hr"
                else "같은 시간대 결제 실패율 반복 상승 원인 개선"
            )
            via = "ticket_draft_propose (③)" if frm == "hr" else "개선 초안 (원인 추적 루프 ③)"
            text = f"개선 초안 수신 → 요청함 {req_id}"
            await self._execute(
                "create_ticket", ctx_req, req_id, text, {"title": title, "via": via}
            )
            s.drafts_in += 1
            s.requests.append(
                Request(
                    id=req_id,
                    context_id=ctx_req,
                    tenant_id=self.tenant_id,
                    from_=frm,
                    via=via,
                    title=title,
                    evidence=f"{DEPT_NAME[frm]} 시뮬레이터에서 보냄",
                    state="접수",
                    flow=True,
                )
            )
            point = "ticket_draft_propose" if frm == "hr" else "개선 초안 (루프 ③)"
            s.activity.append(act(ctx_req, frm, "planning", point, "A2A", text, "A"))
            return ActionResult(output={"request_id": req_id})

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

    async def _register_ticket(
        self,
        s: State,
        frm: str,
        title: str,
        ctx_req: str,
        t: datetime,
        seq: int,
        *,
        evidence: str | None = None,
        source_ref: str | None = None,
        evidence_ids: list[str] | None = None,
        req_id: str | None = None,
    ) -> ActionResult:
        """create_ticket: 같은 제목이면 중복 연결, 아니면 등록 후 간단 요청 판정.

        `source_ref`·`evidence_ids`는 저장만 한다 (중복·간단 요청 판정에 쓰지 않는다).
        """
        dup_id = find_duplicate(s.requests, title)
        req_id = req_id or f"REQ-{20 + seq}"
        auto = None if dup_id else check_simple(title, s.auto_policy)
        text = (
            f'"{title}" → 같은 요청 {dup_id}에 연결 (linked_duplicate)'
            if dup_id
            else f"변경 요청 {req_id} 등록: {title}"
        )
        await self._execute(
            "create_ticket", ctx_req, req_id, text, {"title": title, "duplicate_of": dup_id}
        )
        req = Request(
            id=req_id,
            context_id=ctx_req,
            tenant_id=self.tenant_id,
            from_=frm,
            via="create_ticket",
            title=title,
            evidence=evidence or f"{DEPT_NAME[frm]} 시뮬레이터에서 보냄",
            state="중복 연결" if dup_id else "접수",
            duplicate_of=dup_id,
            auto=auto,
            source_ref=source_ref,
            evidence_ids=list(evidence_ids or []),
        )
        s.requests.append(req)
        s.activity.append(
            self._act(t, ctx_req, frm, "planning", "create_ticket", "A2A", text, "A", seq)
        )
        if auto is None:
            s.activity.append(
                self._act(
                    t,
                    ctx_req,
                    "planning",
                    frm,
                    "get_request_status",
                    "A2A",
                    f"{req_id}는 {dup_id}에 연결됨",
                    "read",
                    seq,
                )
            )
            return ActionResult(
                outcome="linked_duplicate", output={"request_id": req_id, "duplicate_of": dup_id}
            )
        if auto.ok:
            judged = "간단 요청 판정 → 사람 승인 없이 끝까지 처리"
        else:
            need = [r for r in auto.reasons if "필요" in r or "초과" in r]
            judged = f"일반 요청 · {', '.join(need)} → 기획서 초안부터 사람 클릭 없이 진행"
        s.activity.append(
            self._act(t, ctx_req, "planning", "planning", "classify_request", "A2A", judged, "read")
        )
        if auto.ok:
            await self._auto_complete(s, req)
            return ActionResult(output={"request_id": req_id, "auto": True})
        req.flow = True
        s.activity.append(
            self._act(
                t,
                ctx_req,
                "planning",
                frm,
                "get_request_status",
                "A2A",
                f"{req_id} 접수됨 → 기획서 작성",
                "read",
            )
        )
        return ActionResult(output={"request_id": req_id, "auto": False})

    # ---- 사람 클릭 없이 다음 단계로 (flow 요청만, 시드 건은 그대로)

    async def _advance(self, s: State) -> None:
        """액션마다 더 진행할 게 없을 때까지 다음 단계를 돈다: 기획서 초안(개선 초안은 채택 먼저) →
        자동 확정 검사 → PR 만들기 → 검수 불일치면 수정 후 재검수 → 자동 머지 검사.

        멈추는 곳: 자동 확정·머지 규칙에 걸린 건(`auto_review` 대기열), 검수 불일치 2회,
        safety 보류·거절. 관문이 거부하면 그 단계에서 멈추고 앞선 단계는 그대로 둔다."""
        if s.auto_policy.level == 0:  # L0 = 자동 처리 중지: 접수에서 멈춘다
            return
        for _ in range(ADVANCE_LIMIT):
            try:
                if not await self._advance_step(s):
                    return
            except GateRefused:
                return

    async def _advance_step(self, s: State) -> bool:
        """역할 planning은 기획서 초안·자동 확정까지, 역할 dev는 PR 만들기부터 한다.

        합친 모드(역할 없음)는 전부 한다."""
        flow = {r.id for r in s.requests if r.flow}
        for r in s.requests:
            if self.role == "dev":
                break  # dev는 요청으로 기획서를 쓰지 않는다 (start_dev로 확정본을 받는다)
            if r.flow and r.state == "접수" and r.spec is None and r.duplicate_of is None:
                if r.via != "create_ticket":
                    await self._accept_draft(s, r.id)
                await self._draft_spec(s, r)
                return True
        if self.role == "planning":
            return False  # PR 만들기·재검수·머지는 dev 서비스 몫
        for sp in s.specs:
            if sp.request not in flow or sp.status != "확정":
                continue
            has_pr = {p.task for p in s.prs}
            todo = [
                t
                for t in s.tasks
                if t.spec == sp.id and t.state == "진행 중" and t.id not in has_pr
            ]
            if not todo:
                continue
            req = self._request_of_spec(s, sp)
            if self.role == "dev" and sp.auto and req and req.auto and req.auto.ok:
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

    async def _draft_spec(self, s: State, req: Request) -> None:
        """요청으로 기획서 초안을 쓰고(write_spec) 자동 확정 규칙으로 넘긴다."""
        spec_id = f"SPEC-{len(s.specs) + 6}"
        ctx = req.context_id
        t = self.clock()
        s.activity.append(
            self._act(
                t, ctx, "planning", "hr", "check_plan", "A2A", "② 기각 이력 질의 → 없음", "read"
            )
        )
        text = f"{spec_id} 초안 작성 (요구사항 1, 완료 기준 1, 질문 0) → 확정 조건 확인"
        await self._execute("write_spec", ctx, spec_id, text, {"request": req.id})
        sp = Spec(
            id=spec_id,
            title=req.title,
            request=req.id,
            status="초안",
            problem=f"{DEPT_NAME[req.from_]}에서 받은 요청: {req.evidence}",
            goal=req.title,
            paras=[
                Para(id="R1", kind="req", text=req.title),
                Para(id="C1", kind="done", text="바뀐 뒤 같은 문의·이상 신호가 다시 생기지 않는다"),
            ],
            basis=[f"{req.id} ({DEPT_NAME[req.from_]})", req.evidence],
        )
        s.specs.append(sp)
        req.spec = spec_id
        req.state = "기획 중"
        s.activity.append(self._act(t, ctx, "planning", "planning", "write_spec", "A2A", text, "A"))
        await self._try_auto_confirm(s, sp)

    async def _auto_complete(self, s: State, req: Request) -> None:
        """간단 요청 한 번에 처리: write_spec → 자동 확정 → split_tasks → generate_pr(B) →
        review_pr → merge_pr(B) → record_case → 요청 부서 회신. 사람 클릭 없음.

        역할 planning은 작업 분할까지 하고 dev 서비스로 넘긴다.
        나머지는 dev가 `_auto_build`로 한다."""
        split = self.role == "planning"
        prelude = None if split else self._auto_prelude(s)
        sp, task = await self._auto_plan(s, req, done=not split)
        if split:
            for r in s.requests:
                if r.id == req.id:
                    r.spec = sp.id
                    r.state = "작업 중"
            self._hand_to_dev(s, sp, req, req.context_id)
            return
        await self._auto_build(s, req, sp, task, prelude)

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

    async def _auto_plan(self, s: State, req: Request, *, done: bool) -> tuple[Spec, Task]:
        """간단 요청 앞쪽: write_spec → 자동 확정 → split_tasks.

        `done`이면 작업을 바로 완료로 둔다 (합친 모드)."""
        spec_id = f"SPEC-{len(s.specs) + 6}"
        task_id = f"T-{len(s.tasks) + 10}"
        ctx = req.context_id

        def step(frm, to, point, kind, text, grade=None) -> Activity:
            act = self._act(self.clock(), ctx, frm, to, point, kind, text, grade)
            s.activity.append(act)
            return act

        step("planning", "hr", "check_plan", "A2A", "② 기각 이력 질의 → 없음", "read")

        spec_text = (
            f"{spec_id} 작성 (요구사항 1, 완료 기준 1, 질문 0) → 간단 요청 규칙으로 자동 확정"
        )
        await self._execute("write_spec", ctx, spec_id, spec_text, {"request": req.id})
        sp = Spec(
            id=spec_id,
            title=req.title,
            request=req.id,
            status="확정",
            auto=True,
            problem=f"{DEPT_NAME[req.from_]}에서 받은 요청: {req.title}",
            goal="화면 표시를 요청대로 고친다 (동작 변경 없음)",
            paras=[
                Para(id="R1", kind="req", text=req.title),
                Para(id="C1", kind="done", text="바뀐 화면에서 요청한 표시가 그대로 보인다"),
            ],
            basis=[f"{req.id} ({DEPT_NAME[req.from_]})", "간단 요청 규칙 통과"],
            human_sentences=0,
        )
        s.specs.append(sp)
        step("planning", "planning", "write_spec", "A2A", spec_text, "A")
        # 자동 확정도 confirm_spec 관문을 거친다 (#171 규칙 키, 값은 기획서에서 코드로 계산)
        spec_rules = spec_check(sp)
        await self._execute(
            "confirm_spec",
            ctx,
            spec_id,
            f"{spec_id} 간단 요청 규칙으로 자동 확정",
            {**spec_rules.params, "auto": True},
        )

        await self._execute("split_tasks", ctx, task_id, f"R1 → 작업 {task_id}", {"spec": spec_id})
        task = Task(
            id=task_id,
            spec=spec_id,
            para="R1",
            title=req.title,
            owner="에이전트",
            state="완료" if done else "진행 중",
        )
        s.tasks.append(task)
        step("planning", "planning", "split_tasks", "A2A", f"R1 → 작업 {task_id}", "A")
        return sp, task

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
            _pr_size_params(lines, files, auto_paths, task_id),
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
        merge_rules = pr_check(s, pr)
        try:
            await self._execute(
                "merge_pr", ctx, pr_id, merge_text, {**merge_rules.params, "auto": True}
            )
        except GateRefused as exc:
            # safety가 보류·거절하면 PR은 열어 두고 교차검증 대기로 둔다 (판단은 사람 머지 버튼)
            self._hold(pr, WAIT_CROSS_CHECK, merge_rules, exc.decision)
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

        case_text = f"① spec_confirmed·pr_merged·request_closed · {req.id} (자동 처리)"
        await self._execute("record_case", ctx, f"{req.id}:auto_closed", case_text)
        step("planning", "hr", "record_case", "A2A", case_text, "A")
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
            if pol.level > 0:
                lv = pol.level
                text = (
                    f"자동 머지 {label}가 장애에 연결 → 자동 처리 범위 L{lv} → L{lv - 1} 축소"
                    " (줄이는 쪽은 승인 없이)"
                )
                await self._execute(
                    "auto_policy_update", ctx, f"L{lv}->L{lv - 1}", text, {"to": lv - 1}
                )
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
        """구간 안 머지 PR 후보 (read). 원인은 판정하지 않는다. 활동 로그만 남긴다."""
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

    async def create_ticket(
        self,
        *,
        context_id: str,
        requester: str,
        title: str,
        evidence: str,
        source_ref: str | None,
        evidence_ids: list[str],
        idempotency_key: str,
    ) -> ActionResult:
        """변경 요청 접수 (A, #174). 같은 요청이 있으면 묶고(linked_duplicate), 없으면 새 REQ와
        `ctx_req_`를 발급한다(created). 같은 idempotency_key의 두 번째 요청은 이전 결과를 돌려준다.

        - 중복 판정은 기존 도메인 규칙(`find_duplicate`, 같은 제목)만 쓴다. `source_ref`·
          `evidence_ids`는 저장만 한다.
        - 묶이면 원래 요청의 `context_id`를 돌려준다 (요청 부서는 그 사건에 문의를 잇는다).
        - 결과 통지는 같은 `context_id`에 `path`를 비운 새 요청으로 보낸다(합의). 요청 부서의 수신
          skill이 아직 없어 지금은 활동 기록(get_request_status)까지만 남긴다.
        - 요청을 보낸 A2A `context_id`는 활동 근거로만 남기고 사건 ID로 쓰지 않는다.
        """
        cached = self._idempotent.get(idempotency_key)
        if cached is not None:
            return ActionResult(changed=False, outcome=cached.outcome, output=dict(cached.output))

        async def run(s: State) -> ActionResult:
            seq = s.seq + 1
            s.seq = seq
            t = self.clock()
            dup_id = find_duplicate(s.requests, title)
            original = next((r for r in s.requests if r.id == dup_id), None) if dup_id else None
            used_ctx = {r.context_id for r in s.requests}
            n = 20 + seq
            while f"ctx_req_{n:04d}" in used_ctx:
                n += 1
            ctx_req = original.context_id if original else f"ctx_req_{n:04d}"
            used_ids = {r.id for r in s.requests}
            m = 20 + seq
            while f"REQ-{m}" in used_ids:
                m += 1
            frm = requester if requester in DEPT_NAME else "orchestrator"
            result = await self._register_ticket(
                s,
                frm,
                title,
                ctx_req,
                t,
                seq,
                evidence=evidence,
                source_ref=source_ref,
                evidence_ids=evidence_ids,
                req_id=f"REQ-{m}",
            )
            req_id = result.output["request_id"]
            return ActionResult(
                outcome="linked_duplicate" if dup_id else "created",
                output={
                    "request_id": req_id,
                    "context_id": ctx_req,
                    "duplicate_of": dup_id,
                    "auto": result.output.get("auto"),
                    "via_context_id": context_id,
                },
            )

        result = await self._apply(run)
        self._idempotent[idempotency_key] = result
        return result

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
            frm = requester if requester in DEPT_NAME else "planning"
            s.activity.append(self._act(t, ctx, frm, "dev", START_DEV, "A2A", text, "A"))
            return ActionResult(
                outcome="accepted",
                output={"accepted": True, "spec_id": sp.id, "task_ids": ids},
            )

        result = await self._apply(run)
        self._idempotent[idempotency_key] = result
        return result

    async def receive_dev_result(
        self,
        *,
        context_id: str,
        requester: str,
        spec_id: str,
        request_id: str,
        request_state: str,
        tasks: list[dict],
        prs: list[dict],
        idempotency_key: str,
    ) -> ActionResult:
        """planning 서비스: dev가 돌려준 작업·PR·요청 상태를 반영한다 (A).

        작업·PR은 id로 덮어쓴다."""
        cached = self._idempotent.get(idempotency_key)
        if cached is not None:
            return ActionResult(changed=False, outcome=cached.outcome, output=dict(cached.output))
        if request_state not in get_args(ReqState):
            raise ValueError(f"모르는 요청 상태: {request_state}")
        got_tasks = [Task.model_validate(t) for t in tasks]
        got_prs = [PR.model_validate(p) for p in prs]

        async def run(s: State) -> ActionResult:
            t = self.clock()
            req = next((r for r in s.requests if r.id == request_id), None)
            ctx = req.context_id if req else context_id
            merged = sum(1 for p in got_prs if p.merged)
            text = (
                f"{spec_id} 작업 {len(got_tasks)}·PR {len(got_prs)}(머지 {merged}) 반영"
                f" · {request_id} {request_state}"
            )
            await self._execute(
                RECEIVE_DEV_RESULT,
                ctx,
                f"{spec_id}:{len(s.activity)}",
                text,
                {"request": request_id, "request_state": request_state},
                [spec_id, request_id],
            )
            self._upsert(s.tasks, got_tasks)
            self._upsert(s.prs, got_prs)
            if req:
                req.state = request_state  # type: ignore[assignment]
            frm = requester if requester in DEPT_NAME else "dev"
            s.activity.append(
                self._act(t, ctx, frm, "planning", RECEIVE_DEV_RESULT, "A2A", text, "A")
            )
            return ActionResult(output={"applied": len(got_tasks) + len(got_prs)})

        result = await self._apply(run)
        self._idempotent[idempotency_key] = result
        return result

    @staticmethod
    def _upsert(rows: list, items: list) -> None:
        """id가 같으면 그 자리에서 바꾸고, 없으면 뒤에 붙인다."""
        index = {r.id: i for i, r in enumerate(rows)}
        for item in items:
            if item.id in index:
                rows[index[item.id]] = item
            else:
                index[item.id] = len(rows)
                rows.append(item)

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

    async def create_dev_task(
        self,
        *,
        title: str,
        plan: str,
        done: str | None = None,
        paths: list[str] | None = None,
        connection: str | None = None,
    ) -> ActionResult:
        """개발팀원이 쓴 기획으로 요청·확정 기획서·작업 하나를 만들고 코딩 에이전트를 띄운다 (A).

        에이전트는 바꿔 끼운 뒤 잠금 밖에서 돈다. 진행은 작업 `coding`에 적힌다."""
        title = " ".join((title or "").split())
        plan = (plan or "").strip()
        if not title or not plan:
            raise ActionError("INVALID_INPUT", "title과 plan은 비울 수 없다")
        if len(plan) > coder.MAX_PLAN_CHARS:
            raise ActionError("INVALID_INPUT", f"plan은 {coder.MAX_PLAN_CHARS}자까지다")
        if self.role == "planning":
            raise ActionError("WRONG_ROLE", "코딩 에이전트는 개발 서비스(dev)에서 돈다")
        hints = vcs.clean_paths(paths)
        cid = self._coding_connection(connection)
        llm = self._coding_llm()
        done_lines = [x.strip() for x in (done or "").splitlines() if x.strip()]
        done_lines = done_lines[: coder.MAX_DONE_LINES] or [coder.DEFAULT_DONE]

        async def run(s: State) -> ActionResult:
            req_id, spec_id, task_id, ctx = self._dev_ids(s)
            t = self.clock()
            text = (
                f'{task_id} 개발팀 기획 "{title}" → 코딩 에이전트 대기 '
                f"(완료 기준 {len(done_lines)}, 연결 {cid})"
            )
            await self._execute(
                "dev_task_create",
                ctx,
                task_id,
                text,
                {"request": req_id, "spec": spec_id, "connection": cid, "paths": list(hints)},
                [req_id, spec_id, task_id],
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
                    owner="에이전트",
                    state="진행 중",
                    coding=CodingRun(
                        status="queued", model=coder.model_name(llm), updated_at=t, connection=cid
                    ),
                )
            )
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
        self._spawn_coder(result.output["task_id"])
        return result

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
            if title and thread_id is not None:
                th.title = title
            th.updated_at = t
            return ActionResult(outcome="chat", output={"thread_id": th.id})

        result = await self._apply(run)
        found = self.chat_of(result.output["thread_id"])
        assert found is not None
        return found

    async def retry_dev_task(self, task_id: str, connection: str | None = None) -> ActionResult:
        """실패·사람 확인으로 멈춘 코딩 작업을 처음부터 다시 돌린다 (PR을 아직 안 만든 것만)."""
        task = next((t for t in self.state.tasks if t.id == task_id), None)
        if task is None or task.coding is None:
            raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
        if task_id in self._coding or task.coding.status not in ("failed", "waiting_human"):
            raise ActionError("NOT_RETRYABLE", f"{task_id}는 진행 중이거나 끝났다")
        if any(p.task == task_id for p in self.state.prs):
            raise ActionError(
                "NOT_RETRYABLE", f"{task_id}는 이미 PR이 있다 → PR 화면에서 머지·판단"
            )
        cid = self._coding_connection(connection, task.coding.connection)
        llm = self._coding_llm()

        async def run(s: State) -> ActionResult:
            x = next((t for t in s.tasks if t.id == task_id), None)
            if x is None or x.coding is None:
                raise ActionError("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
            sp = next((p for p in s.specs if p.id == x.spec), None)
            req = self._request_of_spec(s, sp) if sp else None
            ctx = req.context_id if req else f"task:{task_id}"
            t = self.clock()
            text = f"{task_id} 코딩 에이전트 다시 시도 (연결 {cid})"
            n = sum(1 for g in self.gate_calls if g.action == "dev_task_retry") + 1
            await self._execute("dev_task_retry", ctx, f"{task_id}:{n}", text, {"connection": cid})
            x.coding = CodingRun(
                status="queued", model=coder.model_name(llm), updated_at=t, connection=cid
            )
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
        self._spawn_coder(task_id)
        return result

    def _spawn_coder(self, task_id: str) -> None:
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
        r1 = next((p for p in sp.paras if p.id == "R1"), None)
        return coder.Job(
            run_id=run_id,
            task_id=task.id,
            spec_id=sp.id,
            request_id=req.id if req else sp.request,
            ctx=req.context_id if req else f"task:{task.id}",
            title=task.title,
            plan=sp.problem,
            done=tuple(p.text for p in sp.paras if p.kind == "done"),
            hints=tuple(r1.paths) if r1 else (),
            connection=task.coding.connection,
        )

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
            cr.updated_at = self.clock()
            if status == "waiting_human" and cr.pr is None:
                task.state = "C 보류"
            if point:
                self._code_act(s, job, point, text or "")

        await self._apply(run)

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
            # 안전 관문·역추적은 회사 코드 기준 경로(연결의 앞머리를 붙인 것)로 본다. 커밋은 레포 안 경로
            paths = sorted(vcs.logical_paths(cr.connection, [r.path for r in rows]))
            lines, files = coder.total_lines(rows), len(rows)
            area = coder.area_of(paths)
            gen_text = f"{pr_id} 변경 {lines}줄·{files}파일·{area} → 코드 규칙 확인"
            t = self.clock()
            try:
                # 다시 시도마다 새 키로 보낸다. 같은 키면 관문이 처음 결정(보류)을 그대로 돌려준다
                await self._execute(
                    "generate_pr",
                    job.ctx,
                    f"{pr_id}:run{job.run_id}",
                    gen_text,
                    _pr_size_params(lines, files, paths, task.id),
                )
            except GateRefused as exc:
                held = exc.decision.status == "held"
                cr.status = "waiting_human" if held else "failed"
                cr.error = f"실행 관문이 generate_pr를 허락하지 않음 ({exc.decision.status})"
                conn = vcs.find_connection(cr.connection) if cr.connection else None
                if held and conn is not None and not conn.mount:
                    # 떼어 온 레포는 경로가 src/…라서 회사 코드 기준 허용 경로 밖으로 보인다
                    cr.error += (
                        " · 연결의 '회사 코드 위치'가 비어 있어 경로가 허용 경로 밖으로 보였을 수 있음"
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

    async def coding_review(self, job: coder.Job, verdict: str, notes: list[str]) -> bool:
        """리뷰 결과를 관문 review_pr 뒤 PR에 남긴다. 통과면 기존 자동 머지 규칙대로 잇는다.

        돌려주는 값: 불일치라 한 번 더 고쳐야 하면 True."""
        fix = False

        async def run(s: State) -> None:
            nonlocal fix
            task, cr = self._coding_of(s, job)
            p = next((x for x in s.prs if x.id == cr.pr), None)
            if p is None:
                raise coder.CoderStop("failed", "PR이 없다")
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

    async def coding_fix(
        self, job: coder.Job, changed: dict[str, str], rows: list[CodingFile]
    ) -> bool:
        """관문 code_write(A) 뒤 리뷰 지적을 고친 파일을 같은 PR 브랜치에 커밋한다."""
        ok = False

        async def run(s: State) -> None:
            nonlocal ok
            task, cr = self._coding_of(s, job)
            p = next((x for x in s.prs if x.id == cr.pr), None)
            if p is None:
                raise coder.CoderStop("failed", "PR이 없다")
            paths = sorted(r.path for r in rows)
            lines, files = coder.total_lines(rows), len(rows)
            text = f"{p.id} 리뷰 지적 반영 수정 커밋 ({len(changed)}개 파일)"
            await self._execute(
                "code_write",
                job.ctx,
                f"{p.id}:fix{cr.attempts}",
                text,
                _pr_size_params(lines, files, paths, task.id),
            )
            note = await vcs_bridge.update_pr(p, changed, f"리뷰 지적 반영: {job.title}")
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
            p.gate = PrGate(result="통과", lines=lines, files=files, paths=coder.area_of(paths))
            p.changed_paths = paths
            cr.files = rows
            cr.updated_at = self.clock()
            ok = True

        await self._apply(run)
        return ok

    # ---------------------------------------------------------------- 사람·에이전트 액션

    async def _send(self, s: State, to: str, point: str, text: str, ctx: str) -> None:
        t = self.clock()
        await self._execute("send_message", ctx, f"{to}:{point}:{len(s.activity)}", text)
        s.seq += 1
        s.activity.append(self._act(t, ctx, "planning", to, point, "A2A", text))

    async def _mark_read(self, s: State, to: str) -> None:
        for m in s.outbox:
            if m.to == to:
                m.read = True

    async def _answer_question(self, s: State, spec: str, text: str) -> None:
        sp = next((x for x in s.specs if x.id == spec), None)
        if sp is None or not sp.question:
            return
        dec_id = f"D-{len(s.decisions)}"
        req = self._request_of_spec(s, sp)
        ctx = req.context_id if req else f"spec:{sp.id}"
        th = next((x for x in s.threads if x.on == spec), None)
        summary = f"스레드에서 결정 {dec_id} 추출, R1 반영, 질문 닫음"
        await self._execute("extract_decision", ctx, dec_id, summary, {"spec": spec})
        case = f"① kind decision_extracted · {dec_id} (결정·이유·대안)"
        await self._execute("record_case", ctx, f"{dec_id}:decision_extracted", case)
        for x in s.threads:
            if x.on == spec:
                x.messages.append(
                    Message(who=ANSWER_WHO, dept="support", text=text, decision=dec_id)
                )
        sp.question = None
        for p in sp.paras:
            if p.id == "R1":
                p.text = ANSWER_R1_TEXT
        s.decisions.append(
            Decision(
                id=dec_id,
                spec=spec,
                from_=f"스레드 {th.id}" if th else "스레드",
                sent_to_hr=True,
                **ANSWER_DECISION,
            )
        )
        s.activity.append(
            self._act(
                self.clock(), ctx, "planning", "planning", "extract_decision", "A2A", summary, "A"
            )
        )
        s.activity.append(
            self._act(self.clock(), ctx, "planning", "hr", "record_case", "A2A", case, "A")
        )
        # 질문이 닫혔으면 자동 확정 규칙을 다시 본다 (통과하면 확정·작업 분할, 아니면 대기열)
        await self._try_auto_confirm(s, sp)

    async def _confirm_spec(self, s: State, spec: str) -> None:
        """사람 확정 버튼 (교차검증 대기·답 대기 건의 판단도 여기서 한다)."""
        sp = next((x for x in s.specs if x.id == spec), None)
        if sp is None or sp.status == "확정":
            return
        await self._do_confirm(s, sp, spec_check(sp), auto=False)

    async def _check_spec(self, s: State, spec: str) -> ActionResult | None:
        """자동 확정 규칙을 다시 본다 (예: 인사 경고가 풀린 뒤). 통과하면 확정, 아니면 대기열."""
        sp = next((x for x in s.specs if x.id == spec), None)
        if sp is None or sp.status == "확정":
            return None
        confirmed = await self._try_auto_confirm(s, sp)
        review = sp.auto_review
        return ActionResult(
            outcome="auto_confirmed" if confirmed else "waiting",
            output={"spec": sp.id, "state": None if confirmed else review and review.state},
        )

    async def _try_auto_confirm(self, s: State, sp: Spec) -> bool:
        """질문 0·경고 0·근거 1 이상·되돌릴 수 없음 0이면 자동 확정하고 작업 분할로 간다.

        아니면 확정하지 않고 "답 대기"(질문 남음) 또는 "교차검증 대기"로 둔다.
        safety가 confirm_spec을 보류·거절해도 "교차검증 대기" (앞선 단계는 그대로 남는다)."""
        if sp.status == "확정":
            return False
        req = self._request_of_spec(s, sp)
        ctx = req.context_id if req else f"spec:{sp.id}"
        check = spec_check(sp)
        if check.ok:
            try:
                await self._do_confirm(s, sp, check, auto=True)
                return True
            except GateRefused as exc:
                if exc.action != "confirm_spec":
                    raise
                hold = self._hold(sp, WAIT_CROSS_CHECK, check, exc.decision)
        else:
            hold = self._hold(sp, spec_wait_state(check), check)
        why = ", ".join(hold.rules) or f"safety {hold.gate_status}"
        s.activity.append(
            self._act(
                hold.at,
                ctx,
                "planning",
                "planning",
                "auto_review",
                "execute",
                f"{sp.id} 자동 확정 안 함 → {hold.state} ({why})",
                "A",
            )
        )
        return False

    async def _do_confirm(self, s: State, sp: Spec, check: Check, *, auto: bool) -> None:
        req = self._request_of_spec(s, sp)
        ctx = req.context_id if req else f"spec:{sp.id}"
        reqs = [p for p in sp.paras if p.kind == "req"]
        base = len(s.tasks) + 10
        tasks = [
            Task(
                id=f"T-{base + i}",
                spec=sp.id,
                para=p.id,
                title=p.text.split(" (")[0],
                owner="백엔드" if i == 0 else "프론트",
                state="진행 중",
            )
            for i, p in enumerate(reqs)
        ]
        # FR-SPEC-006: 인사 경고(warn)가 남은 문단을 둔 채 확정하면 "warn 무시 진행"을 감사 기록으로
        # 남긴다 (execute params·record_case·활동 세 곳). 경고 수는 #171 `warnings`로도 보낸다.
        warn_ignored = [
            {"para": p.id, "card": p.warn.card, "text": p.warn.text} for p in sp.paras if p.warn
        ]
        audit: str | None = None
        if warn_ignored:
            where = ", ".join(f"{w['para']}({w['card']})" for w in warn_ignored)
            audit = f"warn 무시 진행 · {sp.id} {where} 경고를 둔 채 확정 (팀장 {TEAM_LEAD})"
        c = check.params
        rule_text = (
            f"질문 {c['open_questions']}·경고 {c['warnings']}·근거 {c['basis_count']}"
            f"·되돌릴 수 없음 {c['irreversible_count']}"
        )
        if auto:
            confirm_text = f"{sp.id} 자동 확정 ({rule_text} → confirm_spec 규칙 통과, 사후 감사)"
        else:
            confirm_text = f"{sp.id} 확정 (팀장 {TEAM_LEAD})" + (f" · {audit}" if audit else "")
        params: dict[str, Any] = {**check.params, "auto": auto}
        if warn_ignored:
            params["warn_ignored"] = warn_ignored
        await self._execute("confirm_spec", ctx, sp.id, confirm_text, params)
        split = f"요구사항 {len(reqs)}개 → 작업 {', '.join(t.id for t in tasks)}"
        await self._execute("split_tasks", ctx, sp.id, split, {"tasks": [t.id for t in tasks]})
        case = f"① kind spec_confirmed · {sp.id}" + (" (자동 확정)" if auto else "")
        if auto:
            summary = f"{sp.id} {sp.title} 자동 확정 (사후 감사 대상)"
        else:
            summary = f"{sp.id} {sp.title} 확정" + (f" ({audit})" if audit else "")
        record = RecordCase(
            context_id=ctx,
            tenant_id=self.tenant_id,
            kind=RecordKind.SPEC_CONFIRMED,
            summary=summary,
            decision_reason=f"confirm_spec 규칙 통과: {rule_text}" if auto else None,
            source_refs=[f"spec:{sp.id}"]
            + [f"para:{sp.id}:{w['para']}" for w in warn_ignored]
            + [f"card:{w['card']}" for w in warn_ignored],
            author=AGENT_NAME,
        )
        case_params: dict[str, Any] = {"record_case": record.model_dump(mode="json")}
        if warn_ignored:
            case_params["warn_ignored"] = warn_ignored
        await self._execute(
            "record_case", ctx, f"{sp.id}:spec_confirmed", case, case_params, record.source_refs
        )
        sp.status = "확정"
        sp.auto_review = None
        if auto:
            sp.auto = True
        s.tasks.extend(tasks)
        for r in s.requests:
            if r.spec == sp.id:
                r.state = "작업 중"
        if auto:
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "planning",
                    "planning",
                    "audit.auto_confirmed",
                    "execute",
                    confirm_text,
                    "A",
                )
            )
        else:
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "human",
                    "planning",
                    "기획서 확정",
                    "사람",
                    f"팀장 {TEAM_LEAD}이 {sp.id} 확정 (사람 클릭 1)",
                )
            )
        if audit:
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "planning",
                    "planning",
                    "audit.warn_ignored",
                    "execute",
                    audit,
                    "A",
                )
            )
        s.activity.append(
            self._act(self.clock(), ctx, "planning", "planning", "split_tasks", "A2A", split, "A")
        )
        s.activity.append(
            self._act(self.clock(), ctx, "planning", "hr", "record_case", "A2A", case, "A")
        )
        if audit:
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "planning",
                    "hr",
                    "record_case",
                    "A2A",
                    f"{case} · {audit}",
                    "A",
                )
            )
        if req:
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "planning",
                    req.from_,
                    "get_request_status",
                    "A2A",
                    f'{req.id} 상태 "작업 중"을 요청 부서가 조회',
                    "read",
                )
            )
        self._hand_to_dev(s, sp, req, ctx)

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
                _pr_size_params(lines, files, pr.changed_paths, t.id),
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
        if self._repo_pending(p):
            s.activity.append(self._repo_wait_act(self._ctx_of_pr(s, p), p))
            return False
        check = pr_check(s, p)
        if check.ok:
            try:
                await self._do_merge(s, p, check, auto=True)
                return True
            except GateRefused as exc:
                if exc.action != "merge_pr":
                    raise
                hold = self._hold(p, WAIT_CROSS_CHECK, check, exc.decision)
        else:
            hold = self._hold(p, pr_wait_state(check), check)
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
            merge_text = f"팀장 {TEAM_LEAD}이 {p.id} 머지"
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
        if auto:
            record = RecordCase(
                context_id=ctx,
                tenant_id=self.tenant_id,
                kind=RecordKind.PR_MERGED,
                summary=f"{p.id} {p.title} 자동 머지 (사후 감사 대상)",
                decision_reason=f"merge_pr 규칙 통과: {rule_text}",
                source_refs=[f"pr:{p.number}"],
                author=AGENT_NAME,
            )
            await self._execute(
                "record_case",
                ctx,
                f"{p.id}:pr_merged",
                case,
                {"record_case": record.model_dump(mode="json")},
                record.source_refs,
            )
        else:
            await self._execute("record_case", ctx, f"{p.id}:pr_merged", case)
        if task:
            task.state = "완료"
        all_done = bool(spec_id) and all(t.state == "완료" for t in s.tasks if t.spec == spec_id)
        closed = [r for r in s.requests if r.spec == spec_id] if all_done else []
        closed_ids = ", ".join(r.id for r in closed)
        if closed:
            await self._execute(
                "record_case",
                ctx,
                f"{spec_id}:request_closed",
                f"① kind request_closed · {closed_ids}",
            )
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
                    f"팀장 {TEAM_LEAD}이 {p.id} 머지 (사람 클릭)",
                )
            )
        s.activity.append(
            self._act(self.clock(), ctx, "planning", "hr", "record_case", "A2A", case, "A")
        )
        if closed:
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "planning",
                    "hr",
                    "record_case",
                    "A2A",
                    f"① kind request_closed · {closed_ids}",
                    "A",
                )
            )
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

    async def _accept_draft(self, s: State, request: str) -> None:
        r = next((x for x in s.requests if x.id == request), None)
        if r is None or r.state != "접수":
            return
        text = f"{r.id} 채택 → 기획서 작성 대기열"
        await self._execute("review_ticket_draft", r.context_id, r.id, text)
        s.drafts_accepted += 1
        r.state = "기획 중"
        s.activity.append(
            self._act(
                self.clock(),
                r.context_id,
                "planning",
                "planning",
                "review_ticket_draft",
                "A2A",
                text,
                "A",
            )
        )

    # ---- 기각 (hr record_case kind decision_rejected)

    def _rejection_record(
        self,
        ctx: str,
        summary: str,
        decision_reason: str,
        alternatives_considered: list[str],
        source_refs: list[str],
    ) -> RecordCase:
        """`decision_rejected` 기록을 만든다. 이유·검토한 대안이 없으면 보내지 않고 오류.

        hr `check_plan`이 "과거에 기각된 안"을 판정하는 재료라서 둘 다 필수로 둔다.
        """
        reason = decision_reason.strip()
        alternatives = [a.strip() for a in alternatives_considered if a.strip()]
        if not reason or not alternatives:
            raise ActionError(
                "MISSING_DECISION_REASON",
                "decision_rejected에는 decision_reason과 alternatives_considered가 필요하다"
                " (hr에 보내지 않았다)",
            )
        return RecordCase(
            context_id=ctx,
            tenant_id=self.tenant_id,
            kind=RecordKind.DECISION_REJECTED,
            summary=summary,
            decision_reason=reason,
            alternatives_considered=alternatives,
            source_refs=source_refs,
            author=AGENT_NAME,
        )

    async def _notify_result(
        self,
        s: State,
        req: Request,
        status: Literal["completed", "declined"],
        *,
        prs: list[PR] | None = None,
        spec_id: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        """요청을 닫을 때 요청 부서에 `receive_change_result`를 보낸다(계약 #293).

        같은 요청에 묶인 중복 요청(`duplicate_of`)의 부서에도 한 번씩 보낸다. summary는 코드가 만든
        한 줄이고 고객 응대 문구로 쓰지 않는다(#293 리뷰). 보내기 실패는 요청 닫기를 막지 않는다.
        """
        merged = [p for p in (prs or []) if p.merged] if status == "completed" else []
        times = [p.merged_at for p in merged if p.merged_at]
        label = "완료" if status == "completed" else "기각"
        summary = f"{req.id} {label}" + (
            f" · {', '.join(p.id for p in merged)} 머지" if merged else ""
        )
        result = ChangeResultInput(
            request_id=req.id,
            status=status,
            pr_numbers=[p.number for p in merged],
            merged_at=max(times) if times else None,
            spec_id=spec_id,
            reason_code=reason_code if status == "declined" else None,
            summary=summary,
        )
        seen: set[str] = set()
        for r in [req, *(x for x in s.requests if x.duplicate_of == req.id)]:
            if r.from_ in seen:
                continue
            seen.add(r.from_)
            try:
                how = await self._send_result(r.from_, req.context_id, self.tenant_id, result)
            except Exception:  # 통지 실패가 요청 닫기를 되돌리지 않게 한다
                how = "보내지 못함 (오류)"
            s.activity.append(
                self._act(
                    self.clock(),
                    req.context_id,
                    "planning",
                    r.from_,
                    "receive_change_result",
                    "A2A",
                    f"{summary} 결과 통지 · {how}",
                    "A",
                )
            )

    async def _send_rejection(self, s: State, record: RecordCase, target: str) -> None:
        """hr `record_case`(kind decision_rejected)를 execute로 보내고 활동에 남긴다."""
        case = f"① kind decision_rejected · {target}"
        await self._execute(
            "record_case",
            record.context_id,
            f"{target}:decision_rejected",
            case,
            {"record_case": record.model_dump(mode="json")},
            record.source_refs,
        )
        s.activity.append(
            self._act(
                self.clock(),
                record.context_id,
                "planning",
                "hr",
                "record_case",
                "A2A",
                f"{case} (이유: {record.decision_reason})",
                "A",
            )
        )

    async def _reject_draft(
        self,
        s: State,
        request: str,
        decision_reason: str,
        alternatives_considered: list[str],
    ) -> ActionResult | None:
        """개선 초안 기각 → hr record_case(decision_rejected). 이유·대안이 없으면 하지 않는다."""
        r = next((x for x in s.requests if x.id == request), None)
        record = self._rejection_record(
            r.context_id if r else f"req:{request}",
            f"[기각한 안] {r.title if r else request}",
            decision_reason,
            alternatives_considered,
            [f"req:{request}"],
        )
        if r is None or r.state != "접수":
            return None
        reason = record.decision_reason or ""
        text = f"{r.id} 기각 (이유: {reason})"
        await self._execute(
            "review_ticket_draft", r.context_id, r.id, text, {"decision": "rejected"}
        )
        await self._send_rejection(s, record, r.id)
        dec_id = f"D-{len(s.decisions)}"
        s.decisions.append(
            Decision(
                id=dec_id,
                spec=r.spec or "-",
                text=f"[기각한 안] {r.title}",
                reason=reason,
                alternatives=", ".join(record.alternatives_considered),
                from_=f"개선 초안 {r.id}",
                sent_to_hr=True,
            )
        )
        r.state = "기각"
        await self._notify_result(s, r, "declined", reason_code="draft_rejected")
        s.activity.append(
            self._act(
                self.clock(),
                r.context_id,
                "planning",
                "planning",
                "review_ticket_draft",
                "A2A",
                text,
                "A",
            )
        )
        s.activity.append(
            self._act(
                self.clock(),
                r.context_id,
                "planning",
                r.from_,
                "get_request_status",
                "A2A",
                f'{r.id} "{r.title}" 기각 (이유: {reason})',
                "read",
            )
        )
        return ActionResult(
            output={"request_id": r.id, "decision_id": dec_id, "record_kind": "decision_rejected"}
        )

    async def _reject_decision(
        self,
        s: State,
        spec: str,
        text: str,
        decision_reason: str,
        alternatives_considered: list[str],
    ) -> ActionResult | None:
        """기획서 검토 중 기각한 안을 결정으로 남기고 hr에 decision_rejected로 보낸다."""
        sp = next((x for x in s.specs if x.id == spec), None)
        req = self._request_of_spec(s, sp) if sp else None
        ctx = req.context_id if req else f"spec:{spec}"
        record = self._rejection_record(
            ctx,
            f"[기각한 안] {text.strip()}",
            decision_reason,
            alternatives_considered,
            [f"spec:{spec}"],
        )
        if sp is None or not text.strip():
            return None
        dec_id = f"D-{len(s.decisions)}"
        await self._send_rejection(s, record, dec_id)
        s.decisions.append(
            Decision(
                id=dec_id,
                spec=sp.id,
                text=f"[기각한 안] {text.strip()}",
                reason=record.decision_reason or "",
                alternatives=", ".join(record.alternatives_considered),
                from_=f"기획서 {sp.id}",
                sent_to_hr=True,
            )
        )
        return ActionResult(output={"decision_id": dec_id, "record_kind": "decision_rejected"})

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

    # ---- 소통

    async def _post_message(self, s: State, thread: str, text: str) -> None:
        th = next((x for x in s.threads if x.id == thread), None)
        if th is None or not text.strip():
            return
        body = text.strip()
        t = self.clock()
        req = next((r for r in s.requests if th.on and r.spec == th.on), None)
        ctx = req.context_id if req else f"thread:{th.id}"
        if th.peer:
            await self._execute("send_message", ctx, f"{th.id}:{len(th.messages)}", body)
            s.seq += 1
            s.activity.append(self._act(t, ctx, "planning", th.peer, "message", "A2A", body))
        th.messages.append(Message(who=TEAM_LEAD, dept="planning", text=body, at=t))

    async def _dept_chat(self, s: State, from_: str) -> None:
        pool = DEPT_CHAT.get(from_)
        th = next((x for x in s.threads if x.peer == from_), None)
        if not pool or th is None:
            return
        seq = s.seq + 1
        t = self.clock()
        text = pool[sum(1 for m in th.messages if m.dept == from_) % len(pool)]
        s.seq = seq
        th.messages.append(Message(who=f"{DEPT_NAME[from_]} 에이전트", dept=from_, text=text, at=t))
        s.activity.append(
            self._act(t, f"thread:{th.id}", from_, "planning", "message", "A2A", text, None, seq)
        )

    async def _extract_decision(self, s: State, thread: str, index: int) -> None:
        th = next((x for x in s.threads if x.id == thread), None)
        if th is None or not 0 <= index < len(th.messages) or th.messages[index].decision:
            return
        m = th.messages[index]
        dec_id = f"D-{len(s.decisions)}"
        ctx = f"thread:{th.id}"
        summary = f"{th.title} 메시지에서 결정 {dec_id} 추출"
        await self._execute("extract_decision", ctx, dec_id, summary)
        case = f"① kind decision_extracted · {dec_id}"
        await self._execute("record_case", ctx, f"{dec_id}:decision_extracted", case)
        m.decision = dec_id
        s.decisions.append(
            Decision(
                id=dec_id,
                spec=th.on or "-",
                text=m.text,
                reason=f"{m.who} 의견 ({th.title})",
                alternatives="-",
                from_=f"소통 {th.title}",
                sent_to_hr=True,
            )
        )
        s.activity.append(
            self._act(
                self.clock(), ctx, "planning", "planning", "extract_decision", "A2A", summary, "A"
            )
        )
        s.activity.append(
            self._act(self.clock(), ctx, "planning", "hr", "record_case", "A2A", case, "A")
        )

    # ---- 문서 (사람이 고치는 정리 문서. 상태 변경 관문 대상 아님)

    def _patch_doc(self, s: State, doc: str, fn: Callable[[Doc], None]) -> None:
        d = next((x for x in s.docs if x.id == doc), None)
        if d is None:
            return
        fn(d)
        d.updated_at = self.clock()

    async def _new_doc(self, s: State, title: str) -> ActionResult:
        doc_id = f"DOC-{len(s.docs) + 1}"
        s.docs.append(
            Doc(
                id=doc_id,
                title=title or "제목 없는 문서",
                updated_at=self.clock(),
                blocks=[DocBlock(id="b1", kind="p", text="")],
            )
        )
        return ActionResult(output={"doc_id": doc_id})

    async def _gather_doc(self, s: State, about: str, by: str | None) -> ActionResult | None:
        r = next((x for x in s.requests if x.id == about), None)
        if r is None:
            return None
        doc_id = f"DOC-{len(s.docs) + 1}"
        blocks = gather_blocks(s, about)
        s.docs.append(
            Doc(
                id=doc_id,
                title=f"{r.id} 관련 정리 · {r.title}",
                about=r.id,
                updated_at=self.clock(),
                blocks=blocks,
                by=by or "human",
            )
        )
        ai = by == "ai"
        prefix = "AI 어시스턴트 요청으로 " if ai else ""
        s.activity.append(
            self._act(
                self.clock(),
                r.context_id,
                "planning" if ai else "human",
                "planning",
                "gather_doc",
                "A2A" if ai else "사람",
                f"{prefix}{r.id}에 걸린 문서·작업·PR·대화·결정을 {doc_id}로 정리"
                f" (블록 {len(blocks)}개)",
                "read",
            )
        )
        return ActionResult(output={"doc_id": doc_id, "blocks": len(blocks)})

    async def _rename_doc(self, s: State, doc: str, title: str) -> None:
        self._patch_doc(s, doc, lambda d: setattr(d, "title", title))

    async def _add_block(self, s: State, doc: str, kind: str, text: str | None) -> None:
        def add(d: Doc) -> None:
            d.blocks.append(
                DocBlock(
                    id=next_block_id(d),
                    kind=kind,
                    text=text or "",
                    done=False if kind == "todo" else None,
                )
            )

        self._patch_doc(s, doc, add)

    async def _edit_block(self, s: State, doc: str, block: str, text: str) -> None:
        def edit(d: Doc) -> None:
            for b in d.blocks:
                if b.id == block:
                    b.text = text

        self._patch_doc(s, doc, edit)

    async def _toggle_todo(self, s: State, doc: str, block: str) -> None:
        def toggle(d: Doc) -> None:
            for b in d.blocks:
                if b.id == block:
                    b.done = not b.done

        self._patch_doc(s, doc, toggle)

    async def _remove_block(self, s: State, doc: str, block: str) -> None:
        def remove(d: Doc) -> None:
            d.blocks = [b for b in d.blocks if b.id != block]

        self._patch_doc(s, doc, remove)


__all__ = [
    "ACTIONS",
    "INBOUND_KINDS",
    "ActionError",
    "ActionResult",
    "GateCall",
    "GateRefused",
    "PlanningService",
    "action_name",
]
