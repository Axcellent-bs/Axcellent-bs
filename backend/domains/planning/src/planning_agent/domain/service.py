"""기획·개발 상태 변화 (프로토타입 `data.ts` 리듀서의 모든 액션을 메서드로 옮긴 것).

규칙:

- 상태를 바꾸는 단계(요청 등록·기획서 저장·작업 생성·PR 생성·머지·이벤트 전송·자동 범위 변경)는
  템플릿 실행 관문 `ax_agent_template.execute.execute()`를 거친다. 관문이 `executed`가 아닌 결과
  (거절·보류·무응답)를 주면 `GateRefused`를 던지고 그 액션 전체를 버린다(fail-closed).
  승급 제안(`auto_policy_propose`, C)만은 보류(`held`)가 정상 결과다 (팀장 승인 대기).
- 액션은 상태 복사본 위에서 계산하고, 끝까지 성공했을 때만 바꿔 끼운다
  (중간 실패 시 원래 상태 유지).
- 기획·개발이 다른 부서로 보낸 활동은 모두 그 부서 창구(outbox)에도 쌓는다.
- 숫자·중복·판정은 `rules.py`의 코드로만 계산한다. ID·시각은 주입한 시계와 상태 길이로 만든다.
- 시뮬레이터 액션(`inbound`, `dept_chat`)은 목 모드에서만 받는다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel

from ax_agent_template.services import BY_ID as SERVICE_BY_ID
from ax_agent_template.services import department_of
from ax_contracts.schemas import TENANT_ID_DEFAULT, ExecuteDecision
from ax_contracts.schemas.hr import PlanDraft
from ax_contracts.schemas.planning import (
    AnswerPlanningQuestionInput,
    DevHandoffInput,
)
from ax_contracts.schemas.record import RecordCase, RecordKind
from ax_workflow.autocheck import (
    WAIT_ANSWER,
    WAIT_CROSS_CHECK,
    WAIT_HUMAN,
    Check,
    spec_check,
    spec_wait_state,
)
from ax_workflow.base import (  # noqa: F401  옛 경로로 가져오는 코드를 위해 다시 내보낸다
    _TICKET_KINDS,
    ACTIONS,
    ADVANCE_LIMIT,
    AGENT_NAME,
    BOTH_ACTIONS,
    DEV_ACTIONS,
    DEV_ONLY_ACTIONS,
    HR_EXECUTED_ACTIONS,
    INBOUND_KINDS,
    PLANNING_ACTIONS,
    RETRY_SECONDS,
    SIMULATOR_ACTIONS,
    SOFT_ACTIONS,
    TEAM_LEAD,
    AddBlockParams,
    AnswerParams,
    BlockParams,
    DeptChatParams,
    EditBlockParams,
    EmptyParams,
    ExtractDecisionParams,
    GatherDocParams,
    InboundParams,
    NewDocParams,
    PlanningCore,
    PostMessageParams,
    PrParams,
    RejectDecisionParams,
    RejectDraftParams,
    RenameDocParams,
    RepoParams,
    RequestParams,
    SendParams,
    SpecParams,
    ToParams,
    _Params,
    action_name,
)
from ax_workflow.clock import Clock
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
    start_dev_key,
)
from ax_workflow.handover import (
    area_of_text,
)
from ax_workflow.models import (
    DEPT_NAME,
    PR,
    Activity,
    AutoPolicy,
    Decision,
    Doc,
    DocBlock,
    Message,
    Para,
    ParaWarn,
    ReqState,
    Request,
    Spec,
    State,
    Task,
    dump,
)
from ax_workflow.result_notify import ResultSender
from ax_workflow.rules import (
    check_simple,
    find_duplicate,
)
from planning_agent import (
    ask_requester,
    cause_inbox,
    hr_link,
    related,
)
from planning_agent.ask_requester import Asker, missing_info_question
from planning_agent.domain.docs import gather_blocks, next_block_id
from planning_agent.hr_link import HrClient, HrRecordResult, PlanCheck

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


class PlanningService(PlanningCore):
    """기획 서비스 (planning, 8101): 요청 접수·기획서 초안·확정·인사 연동·되묻기·소통·문서.

    확정한 기획서는 개발 서비스로 넘기고(start_dev) 결과를 돌려받는다(receive_dev_result)."""

    ROLE = "planning"
    ACTIONS = PLANNING_ACTIONS

    def __init__(
        self,
        clock: Clock | None = None,
        gate: Gate | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
        result_sender: ResultSender | None = None,
        dev_sender: DevSender | None = None,
        real_example: bool = False,
        relater: related.Relater | None | Literal["default"] = "default",
        hr_client: HrClient | None | Literal["default"] = "default",
        asker: Asker | None | Literal["default"] = "default",
    ):
        super().__init__(clock, gate, tenant_id, result_sender, dev_sender, real_example, relater)
        # 인사 record_case를 실제로 부르는 곳 (None이면 관문·활동 기록만, 목 모드 기본값)
        self._hr = hr_link.default_client() if hr_client == "default" else hr_client
        # 요청 서비스에 되묻는 곳 (None이면 묻지 않음, 목 모드 기본값)
        self._asker: Asker | None = ask_requester.default_asker() if asker == "default" else asker
        self._init_state()

    def _hand_to_dev(self, s: State, sp: Spec, req: Request | None, ctx: str) -> None:
        """확정된 기획서를 dev 서비스로 넘긴다고 표시한다 (보내기는 바꿔 끼운 뒤)."""
        if req is None or sp.handed_to_dev:
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

    # ---- 기획 → 개발 넘기기 (서비스 역할이 있을 때만)

    def _dev_outgoing(self, s: State, full: bool = False) -> list[tuple[str, str, BaseModel, str]]:
        """이번 액션으로 보낼 넘기기 목록 (skill, context_id, 입력, idempotency_key).

        이번에 넘김 표시가 붙은 기획서 (start_dev).
        full이면(다시 켰을 때 맞추기) 넘긴 기획서를 모두 다시 보낸다. 같은 멱등 키라 받는 쪽이
        이미 가진 건 그대로 두고, 잃은 쪽만 다시 반영한다."""
        out: list[tuple[str, str, BaseModel, str]] = []
        was = {sp.id for sp in self.state.specs if sp.handed_to_dev}
        for sp in s.specs:
            req = self._request_of_spec(s, sp)
            if not sp.handed_to_dev or (sp.id in was and not full) or req is None:
                continue
            inp = DevHandoffInput(
                request=dump(req),
                spec=dump(sp),
                tasks=[dump(t) for t in s.tasks if t.spec == sp.id],
            )
            out.append((START_DEV, req.context_id, inp, start_dev_key(req.context_id, sp.id)))
        return out

    async def _inbound(self, s: State, kind: str) -> ActionResult | None:
        """시뮬레이터: 다른 부서 요청·개선 초안이 들어온 것처럼 (목 모드)."""
        if kind not in _TICKET_KINDS and kind not in ("hr_draft", "data_draft"):
            raise self._wrong_service(kind, "개발")
        seq, t, ctx_req, _last = self._inbound_start(s)

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

    async def _advance_step(self, s: State) -> bool:
        """flow 요청은 사람 클릭 없이 기획서 초안·자동 확정까지 간다 (PR부터는 개발 서비스)."""
        for r in s.requests:
            if r.flow and r.state == "접수" and r.spec is None and r.duplicate_of is None:
                if r.via != "create_ticket":
                    await self._accept_draft(s, r.id)
                await self._draft_spec(s, r)
                return True
        return False

    async def _auto_complete(self, s: State, req: Request) -> None:
        """간단 요청: write_spec → 자동 확정 → split_tasks까지 하고 개발 서비스로 넘긴다.

        generate_pr(B) → review_pr → merge_pr(B) → record_case → 요청 부서 회신은 개발 서비스가
        `_auto_build`로 한다. 사람 클릭 없음."""
        sp, _task = await self._auto_plan(s, req, done=False)
        for r in s.requests:
            if r.id == req.id:
                r.spec = sp.id
                r.state = "작업 중"
        self._hand_to_dev(s, sp, req, req.context_id)

    async def _relate_new(self, s: State, known: set[str]) -> None:
        """이번 액션으로 새로 생긴 요청마다 비슷한·충돌하는 요청을 찾아 붙인다 (제안만)."""
        if self._relater is None:
            return
        for req in [r for r in s.requests if r.id not in known]:
            try:
                req.related = await self._relater.relate(s, req)
            except Exception:  # noqa: BLE001 - 찾기가 실패해도 요청은 받는다
                req.related = []
            if self._relater.index is not None:
                try:  # 다음 요청이 찾을 수 있게 memory 색인에 넣는다
                    await self._relater.index.write(req, related.text_of(s, req))
                except Exception:  # noqa: BLE001, S110 - 색인 실패가 요청 받기를 막지 않는다
                    pass
            if not req.related:
                continue
            label = {
                "same": "같은 일",
                "conflict": "충돌 가능",
                "update": "이어서 바꿈",
                "related": "비슷함",
            }
            text = ", ".join(f"{x.id}({label[x.relation]})" for x in req.related)
            s.activity.append(
                self._act(
                    self.clock(),
                    req.context_id,
                    "planning",
                    "planning",
                    "request.related",
                    "API",
                    f"{req.id} 비슷한 요청: {text}",
                    "read",
                )
            )

    async def _check_plan(self, ctx: str, spec_id: str, req: Request) -> PlanCheck | None:
        """기획서 초안을 인사 `check_plan`에 묻는다 (인사 연동 3단계). 목 모드면 None.

        영역을 정하지 못하면 묻지 않고 확인 못 함으로 둔다 (지어낸 영역을 보내지 않는다).
        요청 근거는 다른 부서가 쓴 외부 문구라 `untrusted`로 따로 보낸다.
        """
        if self._hr is None:
            return None
        area = area_of_text(req.title, req.evidence)
        if area is None:
            return PlanCheck("unknown", message="영역을 정하지 못함")
        draft = PlanDraft(title=req.title, summary=req.title, area=area, kind="spec", ref=spec_id)
        return await self._hr.check_plan(ctx, draft, {"요청 근거": req.evidence})

    async def _record_hr(
        self, decision: ExecuteDecision, record: RecordCase, key: str
    ) -> HrRecordResult | None:
        """`record_case`를 인사에 실제로 보낸다 (인사 연동 1단계). 인사가 자기 관문으로 실행한다.

        목 모드(`_hr` None)면 부르지 않는다. 실패해도 본 작업은 되돌리지
        않고 "인사 기록 실패"만 `soft_holds`에 남긴다.
        """
        if self._hr is None or decision.status != "executed":
            return None
        res = await self._hr.record(record, key)
        if not res.ok:
            self.soft_holds.append(GateCall("record_case", key, "hr_failed", "-", res.reason or ""))
        return res

    def _decision_record(
        self,
        ctx: str,
        dec_id: str,
        text: str,
        reason: str,
        alternatives: list[str],
        refs: list[str],
        *area_hints: str,
    ) -> RecordCase:
        """결정 추출 → 인사 `record_case` 입력. 결정 번호를 첫 근거로 둔다 (인사 중복 키)."""
        return RecordCase(
            context_id=ctx,
            tenant_id=self.tenant_id,
            kind=RecordKind.DECISION_EXTRACTED,
            summary=text,
            decision_reason=reason,
            alternatives_considered=[a for a in alternatives if a and a != "-"],
            source_refs=[f"decision:{dec_id}", *refs],
            author=AGENT_NAME,
            area=area_of_text(*area_hints, text),
        )

    def _hr_act(self, ctx: str, case: str, hr: HrRecordResult | None) -> Activity:
        """인사 기록 활동 한 줄. 실제로 불렀으면 결과(카드·실패)를 붙인다."""
        if hr is None:
            return self._act(self.clock(), ctx, "planning", "hr", "record_case", "A2A", case, "A")
        return self._act(
            self.clock(),
            ctx,
            "planning",
            "hr",
            "record_case",
            "A2A",
            f"{case} → {hr.text}",
            "A" if hr.ok else None,
        )

    async def confirm_spec(self, spec: str) -> ActionResult:
        return await self.dispatch({"type": "confirm_spec", "spec": spec})

    async def check_spec(self, spec: str) -> ActionResult:
        return await self.dispatch({"type": "check_spec", "spec": spec})

    async def answer_question(self, spec: str, text: str) -> ActionResult:
        return await self.dispatch({"type": "answer_question", "spec": spec, "text": text})

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
        from_service: str | None = None,
    ) -> ActionResult:
        """create_ticket: 같은 제목이면 중복 연결, 아니면 등록 후 간단 요청 판정.

        `source_ref`·`evidence_ids`는 저장만 한다 (중복·간단 요청 판정에 쓰지 않는다).
        """
        dup_id = find_duplicate(s.requests, title)
        req_id = req_id or f"REQ-{20 + seq}"
        # 사건(ctx_inc_)에서 온 원인 확인 요청은 사람이 원인을 볼 일이라
        # 자동 기획·PR로 넘기지 않는다.
        # 자동으로 돌리면 원인을 아무도 안 봤는데 문서 PR이 머지되고 '완료'가 됐다 (10/30)
        investigate = not dup_id and (source_ref or "").startswith("ctx_inc_")
        auto = None if dup_id or investigate else check_simple(title, s.auto_policy)
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
            from_service=from_service,
        )
        s.requests.append(req)
        s.activity.append(
            self._act(t, ctx_req, frm, "planning", "create_ticket", "A2A", text, "A", seq)
        )
        if investigate:
            s.activity.append(
                self._act(
                    t,
                    ctx_req,
                    "planning",
                    frm,
                    "get_request_status",
                    "A2A",
                    f"{req_id} 원인 확인 요청 접수 ({source_ref})"
                    " → 자동 기획·PR 없이 사람이 원인 확인",
                    "read",
                    seq,
                )
            )
            return ActionResult(output={"request_id": req_id, "auto": False, "investigate": True})
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

    async def _draft_spec(self, s: State, req: Request) -> None:
        """요청으로 기획서 초안을 쓰고(write_spec) 자동 확정 규칙으로 넘긴다."""
        spec_id = f"SPEC-{len(s.specs) + 6}"
        ctx = req.context_id
        t = self.clock()
        plan = await self._check_plan(ctx, spec_id, req)
        s.activity.append(
            self._act(
                t,
                ctx,
                "planning",
                "hr",
                "check_plan",
                "A2A",
                plan.text if plan else "② 기각 이력 질의 → 없음",
                "read",
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
                Para(
                    id="R1",
                    kind="req",
                    text=req.title,
                    warn=(
                        ParaWarn(card=plan.card or "-", text=plan.message or "기각 이력 경고")
                        if plan and plan.verdict == "warn"
                        else None
                    ),
                ),
                Para(id="C1", kind="done", text="바뀐 뒤 같은 문의·이상 신호가 다시 생기지 않는다"),
            ],
            basis=[f"{req.id} ({DEPT_NAME[req.from_]})", req.evidence],
            hr_check=plan.verdict if plan else None,
        )
        s.specs.append(sp)
        req.spec = spec_id
        req.state = "기획 중"
        s.activity.append(self._act(t, ctx, "planning", "planning", "write_spec", "A2A", text, "A"))
        await self._ask_requester(s, sp, req)
        await self._try_auto_confirm(s, sp)

    async def _ask_requester(self, s: State, sp: Spec, req: Request) -> None:
        """근거가 모자라면 요청을 보낸 서비스에 되묻는다 (answer_planning_question).

        - 답하면 바로 기획서 근거·결정으로 남긴다.
        - 사람에게 물어야 한다(`needs_human`)면 질문을 열어 둔다 → 자동 확정 규칙이 답 대기로 둔다.
        - 모르거나·창구가 없거나·응답이 없으면 활동에만 남기고 예전처럼 이어 간다 (지어내지 않음).
        목 모드(`_asker` None)나 서비스를 모르는 요청(시뮬레이터·부서 이름으로 온 것)은 묻지 않는다.
        """
        if self._asker is None or not req.from_service:
            return
        need = missing_info_question(req.evidence, req.evidence_ids)
        if need is None:
            return
        about, question = need
        qid = f"Q-{req.id}-1"
        q = AnswerPlanningQuestionInput(
            question_id=qid, request_id=req.id, spec_id=sp.id, question=question, about=about
        )
        ctx = req.context_id
        svc = req.from_service
        asked = await self._asker.ask(svc, ctx, q)
        if not asked.ok or asked.out is None:
            text = f"{qid} {svc}에 근거 질문 → 확인 못 함 ({asked.reason}), 질문 없이 진행"
            self._ask_act(s, ctx, req, text)
            return
        out = asked.out
        if out.status == "answered" and out.answer:
            self._ask_act(s, ctx, req, f"{qid} {svc}에 근거 질문 → 답 받음")
            await self._apply_answer(
                s, sp, req, qid, out.answer, out.answered_by or "agent", out.evidence_ids
            )
            return
        if out.status == "needs_human":
            sp.question = question
            sp.question_id = qid
            sp.question_to = svc
            self._ask_act(s, ctx, req, f"{qid} {svc}에 근거 질문 → 사람 답 대기")
            return
        self._ask_act(s, ctx, req, f"{qid} {svc}에 근거 질문 → 기록에 없음, 질문 없이 진행")

    def _ask_act(self, s: State, ctx: str, req: Request, text: str) -> None:
        s.activity.append(
            self._act(
                self.clock(),
                ctx,
                "planning",
                req.from_,
                "answer_planning_question",
                "A2A",
                text,
                "read",
            )
        )

    async def _apply_answer(
        self,
        s: State,
        sp: Spec,
        req: Request | None,
        qid: str,
        answer: str,
        answered_by: str,
        evidence_ids: list[str],
    ) -> None:
        """되물은 질문의 답을 기획서에 남긴다: 질문 닫기 → 근거 추가 → 결정 → 인사 기록.

        답은 외부 문구라 근거·결정 문장으로만 넣는다 (문단·완료 기준은 고치지 않는다).
        """
        ctx = req.context_id if req else f"spec:{sp.id}"
        svc = sp.question_to or (req.from_service if req else None) or "요청 서비스"
        who = "사람" if answered_by == "human" else "에이전트"
        dec_id = f"D-{len(s.decisions)}"
        short = " ".join(answer.split())[:120]
        summary = f"{qid} 답 반영 ({svc} {who}) → 결정 {dec_id}"
        await self._execute(
            "receive_planning_answer",
            ctx,
            qid,
            summary,
            {"spec": sp.id, "answered_by": answered_by},
            [qid, *evidence_ids],
        )
        sp.question = None
        sp.question_id = None
        sp.question_to = None
        sp.basis.append(f"{svc} 답변 ({qid}): {short}")
        reason = f"{svc} {who} 답변 ({qid})"
        refs = [f"spec:{sp.id}", f"question:{qid}", *evidence_ids]
        record = self._decision_record(ctx, dec_id, short, reason, [], refs, sp.title)
        hr_key = f"{dec_id}:decision_extracted"
        case = f"① kind decision_extracted · {dec_id} (되묻기 답)"
        gate = await self._execute(
            "record_case",
            ctx,
            hr_key,
            case,
            {"record_case": record.model_dump(mode="json")},
            record.source_refs,
        )
        hr = await self._record_hr(gate, record, hr_key)
        s.decisions.append(
            Decision(
                id=dec_id,
                spec=sp.id,
                text=short,
                reason=reason,
                alternatives="-",
                from_=f"되묻기 {qid}",
                sent_to_hr=hr is None or hr.ok,
            )
        )
        s.activity.append(
            self._act(
                self.clock(),
                ctx,
                "planning",
                "planning",
                "receive_planning_answer",
                "A2A",
                summary,
                "A",
            )
        )
        s.activity.append(self._hr_act(ctx, case, hr))

    async def receive_planning_answer(
        self,
        *,
        question_id: str,
        request_id: str,
        answer: str,
        answered_by: str,
        evidence_ids: list[str],
        idempotency_key: str,
    ) -> ActionResult:
        """요청 서비스가 나중에 보낸 답 (A). 질문을 닫고 자동 확정 규칙을 다시 본다."""
        cached = self._idempotent.get(idempotency_key)
        if cached is not None:
            return ActionResult(changed=False, outcome=cached.outcome, output=dict(cached.output))

        async def run(s: State) -> ActionResult:
            sp = next((x for x in s.specs if x.question_id == question_id), None)
            req = self._request_of_spec(s, sp) if sp else None
            if sp is None or (req is not None and req.id != request_id):
                done = next((d for d in s.decisions if d.from_ == f"되묻기 {question_id}"), None)
                outcome = "duplicate" if done else "unknown_question"
                return ActionResult(
                    changed=False,
                    outcome=outcome,
                    output={"outcome": outcome, "spec_id": done.spec if done else None},
                )
            await self._apply_answer(s, sp, req, question_id, answer, answered_by, evidence_ids)
            await self._try_auto_confirm(s, sp)
            return ActionResult(outcome="applied", output={"outcome": "applied", "spec_id": sp.id})

        result = await self._apply(run)
        self._idempotent[idempotency_key] = result
        self._save()
        return result

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
            # 서비스 이름(inquiry 등)으로 와도 부서는 원래 부서로 남기고, 서비스 id는 따로 둔다
            dept = department_of(requester)
            frm = dept if dept in DEPT_NAME else "orchestrator"
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
                from_service=requester if requester in SERVICE_BY_ID else None,
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
            ), result.output.get("investigate", False)

        async def apply(s: State) -> ActionResult:
            out, investigate = await run(s)
            nonlocal to_inbox
            to_inbox = investigate
            return out

        to_inbox = False
        result = await self._apply(apply)
        self._idempotent[idempotency_key] = result
        self._save()  # 중복 거르기 기록도 바로 남긴다 (재시작 뒤 같은 요청을 다시 처리하지 않게)
        if to_inbox:
            await self._cause_to_inbox(result.output["request_id"])
        return result

    async def _cause_to_inbox(self, req_id: str) -> None:
        """원인 확인 요청을 받은 요청(사람 할 일함)에 올리고 활동에 남긴다 (잠금 밖에서 보낸다)."""
        req = next((r for r in self.state.requests if r.id == req_id), None)
        if req is None:
            return
        text = await cause_inbox.send(req, self.tenant_id, self._cause_poster)

        async def log(s: State) -> None:
            s.activity.append(
                self._act(
                    self.clock(), req.context_id, "planning", "human", "handoff", "A2A", text, "A"
                )
            )

        await self._apply(log)

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
        request: dict | None = None,
        spec: dict | None = None,
        auto_policy: dict | None = None,
    ) -> ActionResult:
        """planning 서비스: dev가 돌려준 작업·PR·요청 상태를 반영한다 (A).

        자동 처리 범위(`auto_policy`)가 같이 오면 dev 것으로 맞춘다 (범위의 주인은 dev).

        작업·PR은 id로 덮어쓴다. 개발팀이 직접 낸 기획이면 요청·기획서도 같이 와서 없으면 넣고
        있으면 덮어쓴다 (기획 쪽이 바뀌는 일을 모두 알도록)."""
        cached = self._idempotent.get(idempotency_key)
        if cached is not None:
            return ActionResult(changed=False, outcome=cached.outcome, output=dict(cached.output))
        if request_state not in get_args(ReqState):
            raise ValueError(f"모르는 요청 상태: {request_state}")
        got_tasks = [Task.model_validate(t) for t in tasks]
        got_prs = [PR.model_validate(p) for p in prs]
        got_req = Request.model_validate(request) if request else None
        got_spec = Spec.model_validate(spec) if spec else None
        got_policy = AutoPolicy.model_validate(auto_policy) if auto_policy else None

        async def run(s: State) -> ActionResult:
            t = self.clock()
            new_own = got_req is not None and not any(r.id == got_req.id for r in s.requests)
            if got_req is not None:
                self._upsert(s.requests, [got_req])
            if got_spec is not None:
                self._upsert(s.specs, [got_spec])
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
            if got_policy is not None:
                s.auto_policy = got_policy
            frm = requester if requester in DEPT_NAME else "dev"
            if new_own and got_spec is not None:
                s.activity.append(
                    self._act(
                        t, ctx, frm, "planning", RECEIVE_DEV_RESULT, "A2A",
                        f"개발팀 기획 {got_spec.id} 받음: {got_spec.title}", "A",
                    )
                )  # fmt: skip
            s.activity.append(
                self._act(t, ctx, frm, "planning", RECEIVE_DEV_RESULT, "A2A", text, "A")
            )
            return ActionResult(output={"applied": len(got_tasks) + len(got_prs)})

        result = await self._apply(run)
        self._idempotent[idempotency_key] = result
        self._save()  # 중복 거르기 기록도 바로 남긴다 (재시작 뒤 같은 요청을 다시 처리하지 않게)
        return result

    async def _answer_question(self, s: State, spec: str, text: str) -> None:
        sp = next((x for x in s.specs if x.id == spec), None)
        if sp is None or not sp.question:
            return
        if sp.question_id:  # 요청 서비스에 되물은 질문을 기획 화면에서 사람이 대신 답함
            await self._apply_answer(
                s, sp, self._request_of_spec(s, sp), sp.question_id, text, "human", []
            )
            await self._try_auto_confirm(s, sp)
            return
        dec_id = f"D-{len(s.decisions)}"
        req = self._request_of_spec(s, sp)
        ctx = req.context_id if req else f"spec:{sp.id}"
        th = next((x for x in s.threads if x.on == spec), None)
        summary = f"스레드에서 결정 {dec_id} 추출, R1 반영, 질문 닫음"
        await self._execute("extract_decision", ctx, dec_id, summary, {"spec": spec})
        case = f"① kind decision_extracted · {dec_id} (결정·이유·대안)"
        record = self._decision_record(
            ctx,
            dec_id,
            ANSWER_DECISION["text"],
            ANSWER_DECISION["reason"],
            [ANSWER_DECISION["alternatives"]],
            [f"spec:{spec}"] + ([f"thread:{th.id}"] if th else []),
            sp.title,
        )
        hr_key = f"{dec_id}:decision_extracted"
        gate = await self._execute(
            "record_case",
            ctx,
            hr_key,
            case,
            {"record_case": record.model_dump(mode="json")},
            record.source_refs,
        )
        hr = await self._record_hr(gate, record, hr_key)
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
                sent_to_hr=hr is None or hr.ok,
                **ANSWER_DECISION,
            )
        )
        s.activity.append(
            self._act(
                self.clock(), ctx, "planning", "planning", "extract_decision", "A2A", summary, "A"
            )
        )
        s.activity.append(self._hr_act(ctx, case, hr))
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
        # 아직 안 끝난 다른 요청과 충돌할 수 있으면 어느 쪽을 따를지 사람이 정한다 (planning 규칙)
        clash = related.open_conflicts(s, req) if req else []
        if clash:
            check = Check(params=check.params, rules=[*check.rules, "spec_open_conflict"])
        # 인사 ② check_plan을 못 받았으면 경고 없음으로 보지 않는다 (사람 확정, 인사 연동 3단계)
        if sp.hr_check == "unknown":
            check = Check(params=check.params, rules=[*check.rules, "spec_hr_unchecked"])
        if check.ok:
            try:
                await self._do_confirm(s, sp, check, auto=True)
                return True
            except GateRefused as exc:
                if exc.action != "confirm_spec":
                    raise
                hold = self._hold(sp, WAIT_CROSS_CHECK, check, exc.decision)
        else:
            wait = spec_wait_state(check)
            if clash and wait != WAIT_ANSWER:
                wait = WAIT_HUMAN
            hold = self._hold(sp, wait, check)
        why = ", ".join(hold.rules) or f"safety {hold.gate_status}"
        if clash:
            why += " · " + ", ".join(x.id for x in clash)
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
            area=area_of_text(sp.title, *(p.text for p in reqs)),
        )
        case_params: dict[str, Any] = {"record_case": record.model_dump(mode="json")}
        if warn_ignored:
            case_params["warn_ignored"] = warn_ignored
        hr_key = f"{sp.id}:spec_confirmed"
        gate = await self._execute(
            "record_case", ctx, hr_key, case, case_params, record.source_refs
        )
        hr = await self._record_hr(gate, record, hr_key)
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
        s.activity.append(self._hr_act(ctx, case, hr))
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

    async def _write_spec(self, s: State, request: str) -> ActionResult | None:
        """사람 버튼 "기획서 초안": 아직 기획서가 없는 요청으로 초안을 쓴다 (있으면 그대로)."""
        req = next((r for r in s.requests if r.id == request), None)
        if req is None:
            raise ActionError("NOT_FOUND", f"요청 {request}가 없다")
        if req.spec is not None or req.duplicate_of is not None:
            return None
        await self._draft_spec(s, req)
        return ActionResult(output={"request_id": req.id, "spec_id": req.spec})

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
        reason = f"{m.who} 의견 ({th.title})"
        refs = [f"thread:{th.id}"] + ([f"spec:{th.on}"] if th.on else [])
        record = self._decision_record(ctx, dec_id, m.text, reason, [], refs, th.title)
        hr_key = f"{dec_id}:decision_extracted"
        gate = await self._execute(
            "record_case",
            ctx,
            hr_key,
            case,
            {"record_case": record.model_dump(mode="json")},
            record.source_refs,
        )
        hr = await self._record_hr(gate, record, hr_key)
        m.decision = dec_id
        s.decisions.append(
            Decision(
                id=dec_id,
                spec=th.on or "-",
                text=m.text,
                reason=reason,
                alternatives="-",
                from_=f"소통 {th.title}",
                sent_to_hr=hr is None or hr.ok,
            )
        )
        s.activity.append(
            self._act(
                self.clock(), ctx, "planning", "planning", "extract_decision", "A2A", summary, "A"
            )
        )
        s.activity.append(self._hr_act(ctx, case, hr))

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


from planning_agent.dev_service import DevService  # noqa: E402,F401  옛 경로 (시험·런타임)
