"""기획 서비스 상태 변화 (프로토타입 `data.ts` 리듀서의 모든 액션을 메서드로 옮긴 것).

규칙:

- 상태를 바꾸는 단계(요청 등록·기획서 저장·작업 생성·PR 생성·머지·이벤트 전송·자동 범위 변경)는
  템플릿 실행 관문 `ax_agent_template.execute.execute()`를 거친다. 관문이 `executed`가 아닌 결과
  (거절·보류·무응답)를 주면 `GateRefused`를 던지고 그 액션 전체를 버린다(fail-closed).
  승급 제안(`auto_policy_propose`, C)만은 보류(`held`)가 정상 결과다 (팀장 승인 대기).
- 액션은 상태 복사본 위에서 계산하고, 끝까지 성공했을 때만 바꿔 끼운다
  (중간 실패 시 원래 상태 유지).
- 기획이 다른 부서로 보낸 활동은 모두 그 부서 창구(outbox)에도 쌓는다.
- 숫자·중복·판정은 `rules.py`의 코드로만 계산한다. ID·시각은 주입한 시계와 상태 길이로 만든다.
- 시뮬레이터 액션(`inbound`, `dept_chat`)은 목 모드에서만 받는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from ax_agent_template.services import BY_ID as SERVICE_BY_ID
from ax_agent_template.services import department_of
from ax_contracts.schemas import TENANT_ID_DEFAULT, ExecuteDecision
from ax_contracts.schemas.hr import PlanDraft
from ax_contracts.schemas.planning import (
    AnswerPlanningQuestionInput,
    DevHandoffInput,
    ReceiveReleaseOrderInput,
    RequestPauseInput,
    RequestReleaseCheckInput,
)
from ax_contracts.schemas.record import RecordCase, RecordKind
from ax_workflow import hr_link
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
from ax_workflow.hr_link import HrClient, HrRecordResult, PlanCheck
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
    CLOSED_REQUEST_STATES,
    check_simple,
    find_duplicate,
)
from planning_agent import (
    ask_requester,
    cause_inbox,
    notion_import,
    proposals,
    related,
    release_check,
    spec_fill,
    spec_template,
)
from planning_agent.ask_requester import Asker, missing_info_question
from planning_agent.domain.docs import gather_blocks, next_block_id
from planning_agent.domain.proposal_actions import PROPOSAL_ORIGIN, ProposalMixin
from planning_agent.spec_writer import SpecWriter, default_writer

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


def _hr_state(hr: HrRecordResult | None) -> str | None:
    """결정 목록에 보일 인사 기록 결과. 인사가 보류로 답하면 held, 그 밖의 실패는 failed."""
    if hr is None:
        return None
    if hr.ok:
        return "ok"
    reason = (hr.reason or "").lower()
    return "held" if reason.startswith(("input-required", "held")) else "failed"


class ReleaseOrderParams(BaseModel):
    """대조가 사람 확인(needs_human)으로 끝난 건을 사람이 보고 배포 지시를 보낸다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    check: str  # RC-n
    note: str | None = None


class RejectSpecParams(SpecParams):
    """확인 대기 기획서 거절. 이유가 있어야 인사에 기각 기록으로 남긴다."""

    reason: str = Field(min_length=1)


class DocParams(BaseModel):
    """문서 하나를 가리킨다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    doc: str


class NotionImportParams(BaseModel):
    """노션 페이지 주소(또는 ID)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    url: str = Field(min_length=1, max_length=500)


class PauseParams(RequestParams):
    """사람이 요청을 중단한다. 이유는 활동에만 남긴다."""

    reason: str = ""


class EditSpecParams(SpecParams):
    """확정 전 기획서를 사람이 고친다. 빈 값은 그대로 둔다."""

    title: str = ""
    problem: str = ""
    goal: str = ""
    paras: dict[str, str] = Field(default_factory=dict)
    body_md: str | None = Field(default=None, max_length=20000)  # 자유 작성 칸 (None = 그대로)
    # 요구사항·완료 기준 목록을 통째로 바꾼다 (자동 채우기 뒤 저장, 확정 전만). None = 그대로
    reqs: list[str] | None = Field(default=None, max_length=10)
    dones: list[str] | None = Field(default=None, max_length=10)
    # 양식 추가 칸 (spec_template.EXTRA_KEYS). 빈 글이면 그 칸을 비운다. None = 그대로
    extra: dict[str, str] | None = None


class PlanningService(ProposalMixin, PlanningCore):
    """기획 서비스 (planning, 8101): 요청 접수·기획서 초안·확정·인사 연동·되묻기·소통·문서.

    확정한 기획서는 개발 서비스로 넘기고(start_dev) 결과를 돌려받는다(receive_dev_result)."""

    ROLE = "planning"
    # 기획 전용 사람 버튼: 같은 요청으로 묶인 것을 사람이 풀어 따로 기획한다
    # 확인 대기 기획서를 사람이 거절한다 (승인은 기존 confirm_spec)
    ACTIONS = {
        **PLANNING_ACTIONS,
        "unlink_duplicate": ("_unlink_duplicate", RequestParams),
        "reject_spec": ("_reject_spec", RejectSpecParams),
        "edit_spec": ("_edit_spec", EditSpecParams),
        "pause_request": ("_pause_request", PauseParams),
        "delete_doc": ("_delete_doc", DocParams),
        "delete_spec": ("_delete_spec", SpecParams),
        "import_notion": ("_import_notion", NotionImportParams),
        "resume_request": ("_resume_request", RequestParams),
        "send_release_order": ("_send_release_order_action", ReleaseOrderParams),
        **ProposalMixin.PROPOSAL_ACTIONS,
    }

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
        spec_writer: SpecWriter | None | Literal["default"] = "default",
        insights: proposals.DataInsights | None | Literal["default"] = "default",
        marketing: proposals.MarketingBrief | None | Literal["default"] = "default",
        proposal_writer: proposals.ProposalWriter | None | Literal["default"] = "default",
        spec_filler: spec_fill.SpecFiller | None | Literal["default"] = "default",
    ):
        super().__init__(clock, gate, tenant_id, result_sender, dev_sender, real_example, relater)
        # 기획 제안: 데이터분석 인사이트를 받아오는 곳·마케팅에 넘기는 곳
        # (None이면 부르지 않음, 목 모드 기본값)
        self._insights = proposals.default_insights() if insights == "default" else insights
        self._marketing = proposals.default_marketing() if marketing == "default" else marketing
        # 자유 작성 → 필수 칸·질문 답 (None이면 소제목 읽기만, 목 모드 기본값)
        self._spec_filler = spec_fill.default_filler() if spec_filler == "default" else spec_filler
        pw = proposal_writer  # 할 일·이유·목표 초안 (None이면 규칙 초안)
        self._proposal_writer = proposals.default_writer() if pw == "default" else pw
        # 인사 record_case를 실제로 부르는 곳 (None이면 관문·활동 기록만, 목 모드 기본값)
        self._hr = hr_link.default_client() if hr_client == "default" else hr_client
        # 요청 서비스에 되묻는 곳 (None이면 묻지 않음, 목 모드 기본값)
        self._asker: Asker | None = ask_requester.default_asker() if asker == "default" else asker
        # 기획서 초안을 LLM으로 쓰는 곳 (None이면 규칙 초안, 목 모드 기본값)
        # 배포 전 대조 기록 (RC-n → 판정·지시 여부). 서버 메모리에만 둔다
        # (다시 켜면 비어 있고, 개발이 다시 물으면 새로 대조한다)
        self.release_checks: dict[str, dict[str, Any]] = {}
        # 접수할 때 이미 비슷한 요청을 찾은 요청 id (_relate_new가 다시 찾지 않게)
        self._pre_related: set[str] = set()
        # 노션 읽기 (시험은 가짜로 바꿔 끼운다)
        self.notion_fetch = notion_import.fetch
        self._spec_writer: SpecWriter | None = (
            default_writer() if spec_writer == "default" else spec_writer
        )
        self._init_state()

    def _hand_to_dev(self, s: State, sp: Spec, req: Request | None, ctx: str) -> None:
        """확정된 기획서를 dev 서비스로 넘긴다고 표시한다 (보내기는 바꿔 끼운 뒤)."""
        if req is None or sp.handed_to_dev or req.paused:
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
            if (
                r.flow
                and not r.paused
                and r.state == "접수"
                and r.spec is None
                and r.duplicate_of is None
            ):
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
            if req.id in self._pre_related:  # 접수할 때 이미 찾았다 (내용으로 묶기)
                self._pre_related.discard(req.id)
                continue
            await self._relate_one(s, req)

    async def _relate_one(self, s: State, req: Request) -> None:
        """요청 하나의 비슷한·충돌하는 요청을 찾아 붙이고, 내용이 같은 열린 요청이면 묶는다."""
        if self._relater is None:
            return
        try:
            req.related = await self._relater.relate(s, req)
        except Exception:  # noqa: BLE001 - 찾기가 실패해도 요청은 받는다
            req.related = []
        self._bind_by_content(s, req)
        if self._relater.index is not None:
            try:  # 다음 요청이 찾을 수 있게 memory 색인에 넣는다
                await self._relater.index.write(req, related.text_of(s, req))
            except Exception:  # noqa: BLE001, S110 - 색인 실패가 요청 받기를 막지 않는다
                pass
        if not req.related:
            return
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

    @property
    def _content_match(self) -> bool:
        """요청 묶기를 내용(LLM 판단)으로 하나. 아니면 예전 같은 제목 규칙."""
        return self._relater is not None and getattr(self._relater, "_llm", None) is not None

    def _bind_by_content(self, s: State, req: Request) -> None:
        """새 요청을 내용이 같은 열린 요청에 묶는다 (사람이 "풀기"로 되돌릴 수 있다).

        - LLM이 "같은 일"(same)로 본 이 서버의 열린 요청만 묶는다. 다른 서비스에만 있는 요청은
          비슷한 요청 표시로만 둔다.
        - LLM 판단이 하나도 없으면(실패) 같은 제목 규칙으로 대신한다.
        - 이미 묶였거나 접수 뒤 다른 단계로 간 요청, 개발이 만든 재발 방지 요청은 그대로 둔다.
        """
        if not self._content_match or req.duplicate_of or req.state != "접수":
            return
        # 개발이 장애 연결로 만든 재발 방지 요청은 묶지 않는다. 사건 번호가 개발 사본과 같아야
        # 사후관리(aftercare) 연결이 맞는다 (via "link_incident (재발 방지)", #363)
        if req.via.startswith("link_incident"):
            return
        others = [r for r in s.requests if r.id != req.id]
        by_id = {r.id: r for r in others}
        origin: Request | None = None
        why = ""
        if any(x.by == "llm" for x in req.related):
            for x in req.related:
                r = by_id.get(x.id)
                if x.relation != "same" or r is None:
                    continue
                top = by_id.get(r.duplicate_of) if r.duplicate_of else r
                if top is not None and top.state not in CLOSED_REQUEST_STATES:
                    origin, why = top, x.reason
                    break
        else:
            dup = find_duplicate(others, req.title)
            origin, why = by_id.get(dup) if dup else None, "같은 제목 (내용 판단 실패)"
        if origin is None:
            return
        old_ctx = req.context_id
        req.duplicate_of = origin.id
        req.context_id = origin.context_id
        req.state = "중복 연결"
        req.auto = None
        text = f"{req.id} → 같은 요청 {origin.id}에 연결 (내용 판단: {why})"[:240]
        for ctx in dict.fromkeys([origin.context_id, old_ctx]):
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "planning",
                    req.from_,
                    "get_request_status",
                    "A2A",
                    text,
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
        untrusted = {"요청 근거": req.evidence}
        sp = next((x for x in self.state.specs if x.id == spec_id), None)
        if sp is not None and sp.body_md:  # 자유 작성 칸도 인사 확인 근거로
            untrusted["자유 작성"] = sp.body_md[:2000]
        return await self._hr.check_plan(ctx, draft, untrusted)

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
        """create_ticket: 묶을 요청이 있으면 중복 연결, 아니면 등록 후 간단 요청 판정.

        기획 LLM이 있으면 여기서는 묶지 않고 `_relate_new`가 내용으로 판단해 묶는다.

        `source_ref`·`evidence_ids`는 저장만 한다 (중복·간단 요청 판정에 쓰지 않는다).
        """
        dup_id = None if self._content_match else find_duplicate(s.requests, title)
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
        if self._content_match:
            # 간단 요청은 아래에서 바로 처리되므로 그 전에 내용으로 비교해 묶는다
            await self._relate_one(s, req)
            self._pre_related.add(req.id)
            if req.duplicate_of:
                dup_id, ctx_req, investigate, auto = req.duplicate_of, req.context_id, False, None
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
            need = [r for r in auto.reasons if "아님" in r or "필요" in r or "초과" in r]
            # 일반 요청은 기획서 확정 규칙(질문·인사 경고·충돌·되돌릴 수 없음)으로 확정하고,
            # 결제 같은 보호 경로는 PR을 열기 전에 사람 승인을 받는다
            judged = f"일반 요청 · {', '.join(need)} → 기획서 초안 → 확정 규칙으로 판단"
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
        draft = (
            await self._spec_writer.draft(
                title=req.title,
                evidence=req.evidence,
                requester=DEPT_NAME.get(req.from_, req.from_),
                context_id=ctx,
            )
            if self._spec_writer is not None
            else None
        )
        if draft is not None:
            problem, goal = draft.problem, draft.goal
            reqs, done = draft.requirements, draft.done_criteria
            how = f"AI 작성{f' {draft.model}' if draft.model else ''}"
        else:
            problem = f"{DEPT_NAME[req.from_]}에서 받은 요청: {req.evidence}"
            goal = req.title
            reqs, done = [req.title], ["바뀐 뒤 같은 문의·이상 신호가 다시 생기지 않는다"]
            how = "규칙 작성"
        text = (
            f"{spec_id} 초안 작성 (요구사항 {len(reqs)}, 완료 기준 {len(done)}, 질문 0, {how})"
            " → 확정 조건 확인"
        )
        await self._execute("write_spec", ctx, spec_id, text, {"request": req.id})
        warn = (
            ParaWarn(card=plan.card or "-", text=plan.message or "기각 이력 경고")
            if plan and plan.verdict == "warn"
            else None
        )
        paras = [
            Para(id=f"R{i}", kind="req", text=r, warn=warn if i == 1 else None)
            for i, r in enumerate(reqs, 1)
        ] + [Para(id=f"C{i}", kind="done", text=c) for i, c in enumerate(done, 1)]
        sp = Spec(
            id=spec_id,
            title=req.title,
            request=req.id,
            status="초안",
            problem=problem,
            goal=goal,
            paras=paras,
            # 근거는 요청 쪽 사실만 (LLM 문장은 근거로 쓰지 않는다)
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
                hr_record=_hr_state(hr),
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

    # ---- 배포 전 기획서 대조 → 배포 지시

    async def request_release_check(
        self,
        *,
        context_id: str,
        requester: str,
        inp: RequestReleaseCheckInput,
        idempotency_key: str,
    ) -> ActionResult:
        """개발이 보낸 작업·PR을 기획서와 대조한다 (A, 코드 규칙). `ready`면 배포 지시를 보낸다.

        `needs_human`이면 사람이 기획 화면에서 보고 `send_release_order`로 지시한다.
        `hold`면 지시하지 않고 이유 코드만 돌려준다. 같은 멱등성 키는 이전 결과를 돌려준다.
        """
        cached = self._idempotent.get(idempotency_key)
        if cached is not None:
            return ActionResult(changed=False, outcome=cached.outcome, output=dict(cached.output))

        async def run(s: State) -> ActionResult:
            j = release_check.judge(s, inp)
            check_id = f"RC-{len(self.release_checks) + 1}"
            req = next((r for r in s.requests if r.id == inp.request_id), None)
            ctx = req.context_id if req else context_id
            why = f" ({', '.join(j.hold_reasons)})" if j.hold_reasons else ""
            text = f"{check_id} {inp.spec_id} 배포 전 대조 → {j.verdict}{why}"
            await self._execute(
                "request_release_check",
                ctx,
                check_id,
                text,
                {
                    "spec": inp.spec_id,
                    "verdict": j.verdict,
                    "hold_reasons": j.hold_reasons,
                    "prs": [p.number for p in inp.prs],
                },
            )
            items = [i.model_dump(mode="json") for i in j.items]
            self.release_checks[check_id] = {
                "check_id": check_id,
                "context_id": ctx,
                "spec_id": inp.spec_id,
                "request_id": inp.request_id,
                "env": inp.env,
                "verdict": j.verdict,
                "hold_reasons": list(j.hold_reasons),
                "items": items,
                "pr_numbers": [p.number for p in inp.prs if p.merged],
                "requester": requester,
                "at": self.clock().isoformat(),
                "order_sent": False,
                "order_result": None,
            }
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    requester,
                    "planning",
                    "request_release_check",
                    "A2A",
                    text,
                    "A",
                )
            )
            return ActionResult(
                outcome=j.verdict,
                output={
                    "check_id": check_id,
                    "verdict": j.verdict,
                    "items": items,
                    "hold_reasons": list(j.hold_reasons),
                    "order_sent": False,
                },
            )

        result = await self._apply(run)
        if result.output["verdict"] == "ready":
            sent = await self._send_release_order(result.output["check_id"], "agent")
            result = ActionResult(
                changed=result.changed,
                outcome=result.outcome,
                output={**result.output, "order_sent": sent},
            )
        self._idempotent[idempotency_key] = result
        self._save()
        return result

    async def _send_release_order(
        self, check_id: str, decided_by: str, note: str | None = None
    ) -> bool:
        """개발에 배포 지시(`receive_release_order`)를 보낸다. 잠금 밖에서 부른다.

        실행·prod 승인·보호 경로 승인은 개발과 안전 레이어가 그대로 한다
        (지시가 사람 승인을 건너뛰지 않는다).
        """
        rc = self.release_checks[check_id]
        if not rc["pr_numbers"]:
            return False
        order = ReceiveReleaseOrderInput(
            check_id=check_id,
            spec_id=rc["spec_id"],
            request_id=rc["request_id"],
            env=rc["env"],
            pr_numbers=rc["pr_numbers"],
            decided_by=decided_by,  # type: ignore[arg-type]
            note=note,
        )
        key = f"{rc['context_id']}:release_order:{check_id}"
        try:
            how = await self._send_dev(
                "receive_release_order", rc["context_id"], self.tenant_id, order, key
            )
        except Exception:  # noqa: BLE001 - 지시 실패가 대조 결과를 되돌리지 않는다
            how = "보내지 못함 (오류)"
        sent = how.startswith("보냄")
        rc["order_sent"] = sent
        rc["order_result"] = how
        who = "사람" if decided_by == "human" else "기획 AI"
        prs = ", ".join(f"#{n}" for n in rc["pr_numbers"])
        text = f"{check_id} {rc['spec_id']} 배포 지시 ({who}, {rc['env']}, PR {prs}) → {how}"

        async def log(s: State) -> None:
            s.activity.append(
                self._act(
                    self.clock(),
                    rc["context_id"],
                    "planning",
                    "dev",
                    "receive_release_order",
                    "A2A",
                    text,
                    "A",
                )
            )

        await self._apply(log)
        return sent

    async def _send_release_order_action(
        self, s: State, check: str, note: str | None
    ) -> ActionResult | None:
        """사람 버튼: 사람 확인으로 끝난 대조 건에 배포 지시를 보낸다 (보내기는 잠금 밖에서)."""
        rc = self.release_checks.get(check)
        if rc is None:
            raise ActionError("NOT_FOUND", f"대조 {check}가 없다")
        req = next((r for r in s.requests if r.id == rc["request_id"]), None)
        if req is not None and req.paused:
            raise ActionError("REQUEST_PAUSED", f"{req.id}가 중단돼 있어 배포를 지시할 수 없습니다")
        if rc["verdict"] == "hold":
            raise ActionError("RELEASE_HELD", f"{check}는 연결 규칙에 걸려 배포를 지시할 수 없다")
        if rc["order_sent"]:
            return None
        await self._execute(
            "send_release_order",
            rc["context_id"],
            check,
            f"{check} {rc['spec_id']} 사람이 배포 지시",
            {"spec": rc["spec_id"], "verdict": rc["verdict"], "prs": rc["pr_numbers"]},
        )
        task = asyncio.get_running_loop().create_task(
            self._send_release_order(check, "human", note)
        )
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)
        return ActionResult(output={"check_id": check, "order": "sending"})

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

        - 중복 판정은 내용으로 한다: 기획 LLM이 있으면 제목·근거(기획서가 있으면 문제·목표·요구)를
          비교해 "같은 일"로 본 열린 요청에 묶는다(`_relate_new`). 제목이 같아도 내용이 다르면
          묶지 않는다. LLM이 없거나 판단이 실패하면 예전 규칙(`find_duplicate`, 같은 제목).
          `source_ref`·`evidence_ids`는 저장만 한다.
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
            dup_id = None if self._content_match else find_duplicate(s.requests, title)
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
        # 내용 판단으로 묶였으면(_relate_new) 원래 요청의 사건 번호를 돌려준다
        bound = next(
            (r for r in self.state.requests if r.id == result.output.get("request_id")), None
        )
        if bound is not None and bound.duplicate_of and result.outcome != "linked_duplicate":
            to_inbox = False
            result = ActionResult(
                changed=result.changed,
                outcome="linked_duplicate",
                output={
                    **result.output,
                    "context_id": bound.context_id,
                    "duplicate_of": bound.duplicate_of,
                    "auto": None,
                },
            )
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
            if not spec_id and not new_own:
                # 기획서 없는 요청(재발 방지 후속)은 처음 한 번만 받는다. 그 뒤로는 기획이 주인이라
                # 다시 와도(재시작 뒤 맞추기 등) 기획서·상태를 덮어쓰지 않는다
                return ActionResult(changed=False, output={"applied": 0})
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
            if not spec_id and req is not None:
                # 기획서 없이 요청만 (개발이 장애 연결로 만든 재발 방지 요청) → 요청 목록에 받는다
                text = f"{req.via} 요청 {request_id} 받음: {req.title}"
            await self._execute(
                RECEIVE_DEV_RESULT,
                ctx,
                f"{spec_id or request_id}:{len(s.activity)}",
                text,
                {"request": request_id, "request_state": request_state},
                [x for x in (spec_id, request_id) if x],
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
                hr_record=_hr_state(hr),
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
        req = self._request_of_spec(s, sp)
        if req is not None and req.paused:
            raise ActionError(
                "REQUEST_PAUSED", f"{req.id}가 중단돼 있어 확정할 수 없습니다 (재개 먼저)"
            )
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
        if sp.origin == PROPOSAL_ORIGIN:  # 기획 제안 기획안: 마케팅·개발 중 사람이 고른다
            return False
        req = self._request_of_spec(s, sp)
        if req is not None and req.paused:  # 사람이 중단함: 재개할 때 다시 본다
            return False
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
        # 결정 문장(summary)에는 결정 내용만 둔다. 기획서 번호·"자동 확정"·"warn 무시 진행" 같은
        # 틀 문구가 붙으면 인사 병합 판정이 같은 결정을 "바뀜"으로 본다 (11/2 병합 재평가, 준하 님).
        # 확정 방식과 경고 무시는 이유(decision_reason)로 옮기고, 기획서 번호는 근거에 있다.
        if auto:
            reason = f"자동 확정 (사후 감사 대상): confirm_spec 규칙 통과 · {rule_text}"
        else:
            reason = f"팀장 {TEAM_LEAD}이 기획서를 보고 확정"
            if audit:
                reason += f" · {audit}"
        record = RecordCase(
            context_id=ctx,
            tenant_id=self.tenant_id,
            kind=RecordKind.SPEC_CONFIRMED,
            summary=sp.title,
            decision_reason=reason,
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

    async def _unlink_duplicate(self, s: State, request: str) -> ActionResult | None:
        """사람 버튼 "풀기": 같은 요청으로 묶인 요청을 따로 떼어 새 요청으로 기획한다.

        제목이 같아 자동으로 묶였지만 실제로는 다른 일일 때 사람이 판단해 푼다. 새 사건 번호
        (`ctx_req_`)를 발급하고 접수 상태로 되돌려, 다음 단계(기획서 초안)로 이어 간다. 요청을
        보낸 부서에는 새 사건 번호를 메시지로 알린다 (결과 회신은 새 사건 번호로 간다).
        묶인 요청이 아니면 바꾸지 않는다.
        """
        r = next((x for x in s.requests if x.id == request), None)
        if r is None:
            raise ActionError("NOT_FOUND", f"요청 {request}가 없다")
        if r.duplicate_of is None:
            return None
        origin = r.duplicate_of
        used = {x.context_id for x in s.requests}
        n = 20 + s.seq + 1
        while f"ctx_req_{n:04d}" in used:
            n += 1
        ctx = f"ctx_req_{n:04d}"
        text = f"{r.id} 묶음 풀기: {origin}과 따로 기획 (사람 판단) → 새 사건 {ctx}"
        await self._execute(
            "unlink_duplicate", ctx, r.id, text, {"from": origin, "old_context_id": r.context_id}
        )
        old_ctx = r.context_id
        r.duplicate_of = None
        r.context_id = ctx
        r.state = "접수"
        r.flow = True  # 사람 클릭 없이 기획서 초안부터 이어 간다
        t = self.clock()
        s.activity.append(self._act(t, ctx, "human", "planning", "unlink_duplicate", "사람", text))
        s.activity.append(
            self._act(t, old_ctx, "human", "planning", "unlink_duplicate", "사람", text)
        )
        if r.from_ in DEPT_NAME and r.from_ not in ("planning", "human"):
            await self._send(
                s,
                r.from_,
                "request_unlinked",
                f"{r.id}는 {origin}과 따로 기획해요. 새 사건 번호 {ctx}로 결과를 보내요",
                ctx,
            )
        return ActionResult(output={"request_id": r.id, "context_id": ctx, "from": origin})

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
            # 인사 check_plan은 영역이 같은 카드끼리만 비교한다 (영역이 없으면 경고가 안 뜸)
            area=area_of_text(summary),
        )

    async def _send_rejection(
        self, s: State, record: RecordCase, target: str
    ) -> HrRecordResult | None:
        """hr `record_case`(kind decision_rejected)를 보내고 활동에 남긴다 (인사 연동 2단계).

        실제 모드(인사 연결)에서는 인사를 A2A로 실제로 부른다. 인사가 자기 관문으로 실행하고,
        실패해도 기각은 되돌리지 않고 "인사 기록 실패"만 남긴다. 목 모드는 예전처럼 관문 기록만.
        """
        case = f"① kind decision_rejected · {target} (이유: {record.decision_reason})"
        key = f"{target}:decision_rejected"
        gate = await self._execute(
            "record_case",
            record.context_id,
            key,
            case,
            {"record_case": record.model_dump(mode="json")},
            record.source_refs,
        )
        hr = await self._record_hr(gate, record, key)
        s.activity.append(self._hr_act(record.context_id, case, hr))
        return hr

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
            # 인사가 카드 제목에 "[기각한 안]"을 붙이므로 요약에는 붙이지 않는다 (11/2 준하 님)
            r.title if r else request,
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
        hr = await self._send_rejection(s, record, r.id)
        dec_id = f"D-{len(s.decisions)}"
        s.decisions.append(
            Decision(
                id=dec_id,
                spec=r.spec or "-",
                text=f"[기각한 안] {r.title}",
                reason=reason,
                alternatives=", ".join(record.alternatives_considered),
                from_=f"개선 초안 {r.id}",
                sent_to_hr=hr is None or hr.ok,
                hr_record=_hr_state(hr),
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
            text.strip(),
            decision_reason,
            alternatives_considered,
            [f"spec:{spec}"],
        )
        if sp is None or not text.strip():
            return None
        dec_id = f"D-{len(s.decisions)}"
        # 같은 기획서에서 여러 안을 기각해도 인사가 하나로 합치지 않게 결정 번호를 첫 근거로 둔다
        record = record.model_copy(update={"source_refs": [f"decision:{dec_id}", f"spec:{spec}"]})
        hr = await self._send_rejection(s, record, dec_id)
        s.decisions.append(
            Decision(
                id=dec_id,
                spec=sp.id,
                text=f"[기각한 안] {text.strip()}",
                reason=record.decision_reason or "",
                alternatives=", ".join(record.alternatives_considered),
                from_=f"기획서 {sp.id}",
                sent_to_hr=hr is None or hr.ok,
                hr_record=_hr_state(hr),
            )
        )
        return ActionResult(output={"decision_id": dec_id, "record_kind": "decision_rejected"})

    async def _pause_request(self, s: State, request: str, reason: str) -> ActionResult | None:
        """사람 버튼 "중단": 이 요청은 재개 전까지 다음 단계로 가지 않는다.

        기획서 초안·자동 확정·개발 전달·배포 지시를 멈춘다. 이미 개발로 넘어간 작업을 멈추는 건
        개발 서비스 몫이다. 끝난(완료·기각) 요청은 바꾸지 않는다.
        """
        req = next((r for r in s.requests if r.id == request), None)
        if req is None:
            raise ActionError("NOT_FOUND", f"요청 {request}가 없습니다")
        if req.paused or req.state in CLOSED_REQUEST_STATES:
            return None
        req.paused = True
        why = f" · {reason.strip()}" if reason.strip() else ""
        s.activity.append(
            self._act(
                self.clock(), req.context_id, "human", "planning", "pause_request", "사람",
                f"{req.id} 사람이 중단 ({req.state}){why}",
            )
        )  # fmt: skip
        self._tell_dev_pause(s, req, reason.strip() or None)
        return ActionResult(output={"request": req.id, "paused": True})

    async def _resume_request(self, s: State, request: str) -> ActionResult | None:
        """사람 버튼 "재개": 멈춘 단계부터 다시 간다 (초안·자동 확정·개발 전달)."""
        req = next((r for r in s.requests if r.id == request), None)
        if req is None:
            raise ActionError("NOT_FOUND", f"요청 {request}가 없습니다")
        if not req.paused:
            return None
        req.paused = False
        s.activity.append(
            self._act(
                self.clock(), req.context_id, "human", "planning", "resume_request", "사람",
                f"{req.id} 사람이 재개 ({req.state})",
            )
        )  # fmt: skip
        sp = next((x for x in s.specs if x.id == req.spec), None)
        if sp is not None and sp.status != "확정":
            await self._try_auto_confirm(s, sp)
        elif sp is not None:
            self._hand_to_dev(s, sp, req, req.context_id)
        self._tell_dev_pause(s, req, None)
        return ActionResult(output={"request": req.id, "paused": False})

    def _tell_dev_pause(self, s: State, req: Request, reason: str | None) -> None:
        """이미 개발로 넘어간 요청이면 개발에 중단·재개를 알린다 (보내기는 잠금 밖에서)."""
        sp = next((x for x in s.specs if x.id == req.spec), None)
        if sp is None or not sp.handed_to_dev:
            return
        n = sum(
            1
            for a in s.activity
            if a.context_id == req.context_id and a.point in ("pause_request", "resume_request")
        )
        inp = RequestPauseInput(request_id=req.id, paused=req.paused, reason=reason)
        key = f"{req.context_id}:request_pause:{req.id}:{n}"
        ctx = req.context_id

        async def send() -> None:
            try:
                how = await self._send_dev("receive_request_pause", ctx, self.tenant_id, inp, key)
            except Exception as exc:  # noqa: BLE001 - 알림 실패가 중단·재개를 되돌리지 않는다
                how = f"보내지 못함 ({exc.__class__.__name__})"
            word = "중단" if inp.paused else "재개"
            await self._apply(self._note_pause_sent, ctx=ctx, text=f"{req.id} {word} → 개발 {how}")

        task = asyncio.get_running_loop().create_task(send())
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    async def _note_pause_sent(self, s: State, ctx: str, text: str) -> None:
        s.activity.append(
            self._act(
                self.clock(), ctx, "planning", "dev", "receive_request_pause", "A2A", text, "A"
            )
        )

    async def _edit_spec(
        self,
        s: State,
        spec: str,
        problem: str,
        goal: str,
        paras: dict[str, str],
        title: str = "",
        body_md: str | None = None,
        reqs: list[str] | None = None,
        dones: list[str] | None = None,
        extra: dict[str, str] | None = None,
    ) -> ActionResult | None:
        """사람 수정 ("수정" → "저장"): 기획서의 제목·문제·목표·문단 글을 고친다.

        확정된 기획서는 제목·문제·목표만 고친다. 문단(요구사항·완료 기준)은 작업이 개발로 나가
        고치지 않는다(SPEC_CONFIRMED, 개정 흐름은 다음 단계). 바뀐 곳마다 사람이 쓴
        문장 수를 늘리고 활동에 남긴다. 확정 조건은 다음 진행에서 다시 본다.
        """
        sp = next((x for x in s.specs if x.id == spec), None)
        if sp is None:
            raise ActionError("NOT_FOUND", f"기획서 {spec}가 없습니다")
        # 확정 뒤에도 제목·문제·목표(설명)는 고친다. 요구사항·완료 기준은 작업·대조 기준이라 막는다
        lists = [x for x in (reqs, dones) if x is not None]
        if sp.status == "확정" and (any((v or "").strip() for v in paras.values()) or lists):
            raise ActionError(
                "SPEC_CONFIRMED", f"{spec}는 확정돼서 요구사항·완료 기준은 고칠 수 없습니다"
            )
        changed: list[str] = []
        if body_md is not None and body_md != (sp.body_md or ""):
            sp.body_md = (
                body_md  # 자유 작성 칸은 확정 뒤에도 고친다 (유사·충돌 판단·인사 확인에 쓴다)
            )
            changed.append("자유 작성")
        for key, val in (extra or {}).items():  # 양식 추가 칸은 확정 뒤에도 고친다 (설명 칸)
            if key not in spec_template.EXTRA_KEYS:
                continue
            v = (val or "").strip()[:2000]
            if v != (sp.extra.get(key) or ""):
                if v:
                    sp.extra[key] = v
                else:
                    sp.extra.pop(key, None)
                changed.append(key)
        if title.strip() and title.strip() != sp.title:
            sp.title = title.strip()
            changed.append("제목")
        if problem.strip() and problem.strip() != sp.problem:
            sp.problem = problem.strip()
            changed.append("문제")
        if goal.strip() and goal.strip() != sp.goal:
            sp.goal = goal.strip()
            changed.append("목표")
        for kind, items, prefix, name in (
            ("req", reqs, "R", "요구사항"),
            ("done", dones, "C", "완료 기준"),
        ):
            clean = [t.strip()[:300] for t in items or [] if t.strip()]
            old = [p.text for p in sp.paras if p.kind == kind]
            if items is not None and clean and clean != old:
                keep = [p for p in sp.paras if p.kind != kind]
                new = [Para(id=f"{prefix}{i}", kind=kind, text=t) for i, t in enumerate(clean, 1)]
                sp.paras = [*new, *keep] if kind == "req" else [*keep, *new]
                changed.append(f"{name} {len(clean)}")
        for p in sp.paras:
            text = (paras.get(p.id) or "").strip()
            if text and text != p.text:
                p.text = text
                changed.append(p.id)
        if not changed:
            return None
        sp.human_sentences += len(changed)
        req = self._request_of_spec(s, sp)
        ctx = req.context_id if req else f"spec:{sp.id}"
        s.activity.append(
            self._act(
                self.clock(), ctx, "human", "planning", "edit_spec", "사람",
                f"{sp.id} 사람이 고침 · {', '.join(changed)}",
            )
        )  # fmt: skip
        return ActionResult(output={"spec": sp.id, "changed": changed})

    async def _reject_spec(self, s: State, spec: str, reason: str) -> ActionResult | None:
        """사람 버튼 "거절": 확인 대기(교차검증·사람 확인) 기획서를 확정하지 않는다.

        기획서는 초안으로 남기고 대기열에서 내린다. 요청은 기각으로 닫고 요청 부서에 알린다.
        거절한 기획서는 결정 이력에 남기고 인사에 기각 기록(decision_rejected)으로 보낸다.
        확정된 기획서·대기열에 없는 기획서는 바꾸지 않는다.
        """
        sp = next((x for x in s.specs if x.id == spec), None)
        if sp is None:
            raise ActionError("NOT_FOUND", f"기획서 {spec}가 없습니다")
        if sp.status == "확정" or sp.auto_review is None:
            return None
        # 검토한 대안은 "이대로 확정"이다 (인사 기각 기록에 대안이 꼭 있어야 한다)
        res = await self._reject_decision(s, sp.id, sp.title, reason, ["이대로 확정"])
        sp.auto_review = None
        req = self._request_of_spec(s, sp)
        ctx = req.context_id if req else f"spec:{sp.id}"
        text = f"{sp.id} 기획서 거절 · {reason.strip()}"
        s.activity.append(
            self._act(self.clock(), ctx, "human", "planning", "reject_spec", "사람", text)
        )
        # 요청은 기각으로 닫고 요청 부서에 알린다 (개선 초안 기각과 같게, 11/2 시연 F)
        if req is not None and req.state not in CLOSED_REQUEST_STATES:
            req.state = "기각"
            await self._notify_result(
                s, req, "declined", spec_id=sp.id, reason_code="spec_rejected"
            )
            s.activity.append(
                self._act(
                    self.clock(),
                    ctx,
                    "planning",
                    req.from_,
                    "get_request_status",
                    "A2A",
                    f'{req.id} "{req.title}" 기각 (기획서 {sp.id} 거절 · 이유: {reason.strip()})',
                    "read",
                )
            )
        return ActionResult(output={"spec": sp.id, **(res.output if res else {})})

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
                hr_record=_hr_state(hr),
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

    async def dispatch(self, action: dict) -> ActionResult:
        """기획 제안의 잠금 밖 액션(받아오기·요청으로)은 따로, 나머지는 원래 길."""
        if isinstance(action, dict) and isinstance(action.get("type"), str):
            name = action_name(action["type"])
            if name in self.OUTSIDE_LOCK:
                out = await self.dispatch_proposal(name, action)
                if out is not None:
                    return out
            if name == "fill_spec":  # LLM을 부르니 잠금 밖에서
                spec = action.get("spec")
                if not isinstance(spec, str):
                    raise ActionError("INVALID_ACTION", "spec이 없다")
                return await self.fill_spec(
                    spec,
                    overwrite=bool(action.get("overwrite")),
                    preview=bool(action.get("preview")),
                    body_md=body if isinstance(body := action.get("body_md"), str) else None,
                )
        return await super().dispatch(action)

    # 규칙 초안이 넣는 기본 글: 자동 채우기에서 빈 칸처럼 다룬다
    FILL_DEFAULTS = (
        "바뀐 뒤 같은 문의·이상 신호가 다시 생기지 않는다",
        "바뀐 화면에서 요청한 표시가 그대로 보인다",
        "성과를 잴 기준을 정한다",
    )

    async def fill_spec(
        self,
        spec: str,
        overwrite: bool = False,
        preview: bool = False,
        body_md: str | None = None,
    ) -> ActionResult:
        """사람 버튼 "자동 채우기": 자유 작성(body_md)을 읽어 필수 칸을 채운다 (spec_fill.py).

        빈 칸과 규칙 초안 기본 글만 채운다. `overwrite`면 전부 다시 쓴다. 확정된 기획서는
        문제·목표만 (요구사항·완료 기준은 작업이 나가 잠김)."""
        sp = next((x for x in self.state.specs if x.id == spec), None)
        if sp is None:
            raise ActionError("NOT_FOUND", f"기획서 {spec}가 없습니다")
        # 미리보기는 화면 초안의 (아직 저장 안 된) 자유 작성을 읽을 수 있다
        md = (body_md if preview and body_md is not None else sp.body_md) or ""
        if not md.strip():
            raise ActionError("SPEC_NO_FREE_TEXT", "자유 작성이 비어 있어요")
        req = self._request_of_spec(self.state, sp)
        current = {
            "problem": sp.problem,
            "goal": sp.goal,
            "requirements": [p.text for p in sp.paras if p.kind == "req"],
            "done_criteria": [p.text for p in sp.paras if p.kind == "done"],
        }
        got = await spec_fill.fill(
            md[:20000],
            title=sp.title,
            current=current,
            question=sp.question or "",
            context_id=req.context_id if req else f"spec:{sp.id}",
            filler=self._spec_filler,
            template=spec_template.template_of(sp.origin),
        )
        if got.empty():
            raise ActionError(
                "SPEC_FILL_EMPTY",
                "채울 내용을 찾지 못했어요 (기획 LLM이 없으면 ## 목표 같은 소제목이 필요해요)",
            )
        if preview:  # 화면 "수정" 중: 값만 돌려주고 저장은 사람이 "저장"으로 한다
            out = {
                "spec": spec, "by": got.by, "problem": got.problem, "goal": got.goal,
                "reqs": got.reqs, "done": got.done, "answer": got.answer if sp.question else "",
                "extra": got.extra,
            }  # fmt: skip
            return ActionResult(changed=False, outcome="preview", output=out)
        defaults = {*self.FILL_DEFAULTS, sp.title, req.title if req else ""}

        def blank(v: str) -> bool:
            t = (v or "").strip()
            return overwrite or not t or t in defaults

        async def run(s: State) -> ActionResult:
            x = next((y for y in s.specs if y.id == spec), None)
            if x is None:
                return ActionResult(changed=False)
            changed: list[str] = []
            src = f"{DEPT_NAME.get(req.from_, '')}에서 받은 요청:" if req else ""
            if got.problem and (blank(x.problem) or (src and x.problem.startswith(src))):
                x.problem = got.problem
                changed.append("문제")
            if got.goal and blank(x.goal):
                x.goal = got.goal
                changed.append("목표")
            if x.status != "확정":
                for kind, items, prefix in (("req", got.reqs, "R"), ("done", got.done, "C")):
                    cur = [p for p in x.paras if p.kind == kind]
                    if items and (not cur or all(blank(p.text) for p in cur)):
                        keep = [p for p in x.paras if p.kind != kind]
                        new = [
                            Para(id=f"{prefix}{i}", kind=kind, text=t)
                            for i, t in enumerate(items, 1)
                        ]
                        x.paras = [*new, *keep] if kind == "req" else [*keep, *new]
                        changed.append(
                            f"{'요구사항' if kind == 'req' else '완료 기준'} {len(items)}"
                        )
            if got.answer and x.question:  # 자유 작성에 남은 질문의 답 → 답하기 흐름 그대로
                await self._answer_question(s, spec, got.answer)
                changed.append("질문 답")
            if not changed:
                return ActionResult(
                    changed=False, output={"spec": spec, "filled": [], "by": got.by}
                )
            how = {"rule": "소제목", "llm": "AI", "llm+rule": "AI+소제목"}[got.by]
            ctx = req.context_id if req else f"spec:{spec}"
            text = f"{spec} 자유 작성으로 칸 채움 ({how}) · {', '.join(changed)}"
            s.activity.append(
                self._act(self.clock(), ctx, "planning", "planning", "fill_spec", "사람", text)
            )
            return ActionResult(output={"spec": spec, "filled": changed, "by": got.by})

        return await self._apply(run)

    @staticmethod
    def _next_doc_id(s: State) -> str:
        """지운 문서가 있어도 번호가 겹치지 않게 가장 큰 번호 + 1."""
        nums = [int(x.id.split("-")[1]) for x in s.docs if x.id.split("-")[-1].isdigit()]
        return f"DOC-{max(nums, default=0) + 1}"

    async def _delete_spec(self, s: State, spec: str) -> ActionResult | None:
        """사람 버튼 "삭제": 확정 전 기획서 초안을 지운다. 작업·PR이 붙었거나 확정됐으면 막는다.

        요청은 남기고 기획서 연결만 끊어 "접수"로 돌린다 (자동으로 다시 쓰지 않게 흐름도 끈다).
        기획 제안으로 만든 기획안이면 그 요청도 지우고 제안을 "확인 대기"로 돌린다."""
        sp = next((x for x in s.specs if x.id == spec), None)
        if sp is None:
            return None
        if sp.status == "확정" or sp.handed_to_dev or any(t.spec == spec for t in s.tasks):
            raise ActionError("SPEC_IN_USE", f"{spec}는 확정됐거나 작업이 나가 지울 수 없습니다")
        s.specs = [x for x in s.specs if x.id != spec]
        req = next((r for r in s.requests if r.id == sp.request), None)
        prop = next((p for p in s.proposals if p.spec == spec), None)
        if prop is not None:
            prop.status, prop.spec, prop.request = "확인 대기", None, None
            if req is not None:
                s.requests = [r for r in s.requests if r.id != req.id]
        elif req is not None:
            req.spec, req.state, req.flow = None, "접수", False
        ctx = req.context_id if req else f"spec:{spec}"
        text = f"{spec} 기획서 삭제" + (f" · {prop.id} 확인 대기로" if prop else "")
        s.activity.append(
            self._act(self.clock(), ctx, "human", "planning", "delete_spec", "사람", text)
        )
        return ActionResult(output={"spec_id": spec, "deleted": True})

    async def _delete_doc(self, s: State, doc: str) -> ActionResult | None:
        """사람 버튼 "삭제": 정리 문서를 지운다 (기획서는 지우지 않는다)."""
        d = next((x for x in s.docs if x.id == doc), None)
        if d is None:
            return None
        s.docs = [x for x in s.docs if x.id != doc]
        return ActionResult(output={"doc_id": doc, "deleted": True})

    async def _import_notion(self, s: State, url: str) -> ActionResult:
        """사람 버튼 "노션에서 가져오기": 노션 페이지를 읽어 새 정리 문서로 저장한다 (읽기만)."""
        try:
            page = await self.notion_fetch(url)
        except notion_import.NotionError as exc:
            raise ActionError(exc.code, exc.message) from exc
        doc_id = self._next_doc_id(s)
        blocks = [DocBlock(id="b1", kind="ref", text=f"노션 원본: {page.url}")]
        for n, (kind, text, done) in enumerate(page.blocks, start=2):
            blocks.append(DocBlock(id=f"b{n}", kind=kind, text=text, done=done))
        s.docs.append(
            Doc(
                id=doc_id,
                title=page.title,
                updated_at=self.clock(),
                created_at=self.clock(),
                blocks=blocks,
                by="human",
            )
        )
        skipped = f", 못 옮긴 블록 {page.skipped}개" if page.skipped else ""
        s.activity.append(
            self._act(
                self.clock(), f"doc:{doc_id}", "human", "planning", "import_notion", "사람",
                f"노션 페이지 → {doc_id} \"{page.title}\" (블록 {len(page.blocks)}개{skipped})",
            )
        )  # fmt: skip
        return ActionResult(
            output={"doc_id": doc_id, "blocks": len(page.blocks), "skipped": page.skipped}
        )

    async def _new_doc(self, s: State, title: str) -> ActionResult:
        doc_id = self._next_doc_id(s)
        s.docs.append(
            Doc(
                id=doc_id,
                title=title or "제목 없는 문서",
                updated_at=self.clock(),
                created_at=self.clock(),
                blocks=[DocBlock(id="b1", kind="p", text="")],
            )
        )
        return ActionResult(output={"doc_id": doc_id})

    async def _gather_doc(self, s: State, about: str, by: str | None) -> ActionResult | None:
        r = next((x for x in s.requests if x.id == about), None)
        if r is None:
            return None
        doc_id = self._next_doc_id(s)
        blocks = gather_blocks(s, about)
        s.docs.append(
            Doc(
                id=doc_id,
                title=f"{r.id} 관련 정리 · {r.title}",
                about=r.id,
                updated_at=self.clock(),
                created_at=self.clock(),
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
