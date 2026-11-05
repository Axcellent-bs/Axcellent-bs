"""marketing skill 핸들러 (필수 4개: draft_notice, publish_notice, get_notice, issue_coupon,
권장 1개: list_active_notices).

- 상태를 바꾸는 일(공지 게시, 쿠폰 지급)은 `execute()`가 `executed`를 돌려준 뒤에만 한다.
- 외부 입력(`request.untrusted`)은 읽지 않는다. 문안·금액은 사실 필드와 코드 계산으로만 만든다.
- 저장소는 메모리다(목 모드). 영속 저장은 shop-api 연동 때 붙인다(TODO).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from ax_agent_template.events import publish
from ax_agent_template.execute import execute
from ax_agent_template.logging import log_event
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    CouponIssuedData,
    EventEnvelope,
    EventType,
    Evidence,
    ExecuteDecision,
    HoldReason,
    InputReason,
    NoticePublishedData,
    TaskStatus,
)
from ax_contracts.schemas.planning import CampaignBriefInput, CampaignBriefOutput
from marketing_agent import shop_client
from marketing_agent.briefs import (
    BriefStorageError,
    BriefStore,
    CampaignBriefList,
    CampaignBriefView,
    GetCampaignBriefInput,
    ListCampaignBriefsInput,
    campaign_id_for,
    rejection_reason,
)
from marketing_agent.campaign import record_campaign_result, start_campaign
from marketing_agent.campaign_cancel import cancel_campaign
from marketing_agent.chat import (
    ChatRefused,
    ChatService,
    ChatThreadRef,
    ListChatThreadsInput,
    SendChatMessageInput,
    short,
)
from marketing_agent.chat_store import ChatStorageError, ChatStore
from marketing_agent.coupons import CouponLedger, SequenceGap, total_value
from marketing_agent.links import BuildCampaignLinkInput, CampaignLink, build_campaign_link
from marketing_agent.notice import NoticeStore, make_draft
from marketing_agent.schemas import (
    ActiveNotices,
    DraftNoticeInput,
    GetNoticeInput,
    IssueCouponInput,
    IssuedCoupon,
    ListActiveNoticesInput,
    PublishedNotice,
    PublishNoticeInput,
)
from marketing_agent.sns import SnsStore, check_sns_post, draft_sns_post

SOURCE = "marketing"
BANNER_TEXT_MAX = 200  # shop-api 배너 문구 상한


class State:
    def __init__(self) -> None:
        self.notices = NoticeStore()
        self.coupons = CouponLedger()
        self.briefs = BriefStore()
        self.sns = SnsStore()
        # 열 수 없으면 시작 때 ChatStorageError로 실패한다(briefs와 같다)
        self.chat = ChatService(ChatStore(), self.chat_facts)

    def chat_facts(self) -> dict[str, Any]:
        """대화에 넣는 사실. 마케팅 저장소를 읽기만 한다(구매자·고객 정보는 넣지 않는다)."""
        try:
            briefs, brief_total = self.briefs.list(10)
            received: Any = [
                {
                    "proposal_id": b.proposal_id,
                    "campaign_id": b.campaign_id,
                    "status": b.status,
                    "received_at": b.received_at.isoformat(),
                    "title": short(b.brief.title),
                    "idea": short(b.brief.idea),
                    "why": short(b.brief.why),
                    "facts": [f.model_dump(mode="json") for f in b.brief.facts][:12],
                    "target": b.brief.target.model_dump(mode="json") if b.brief.target else None,
                    "products": b.brief.products[:10],
                    "approved_by": b.brief.approved_by,
                    "note": short(b.brief.note),
                    "body_md": short(b.brief.body_md),
                }
                for b in briefs
            ]
        except BriefStorageError:
            brief_total, received = None, "받은 기획안을 읽지 못했다"
        notices, notice_total = self.notices.active(limit=10)
        return {
            "today": _now().strftime("%Y-%m-%d"),
            "received_briefs_total": brief_total,
            "received_briefs": received,
            "active_notices_total": notice_total,
            "active_notices": [
                {
                    "notice_id": n.notice_id,
                    "channel_code": n.channel_code,
                    "public_text": n.public_text,
                    "valid_from": n.valid_from.isoformat() if n.valid_from else None,
                    "valid_to": n.valid_to.isoformat() if n.valid_to else None,
                }
                for n in notices
            ],
            "coupons_issued_total": self.coupons.issued_count(),
            "campaign_status": "마케팅 서비스에는 캠페인 상태 저장소가 없다(예산·성과는 재무 소관)",
        }


STATE = State()


def reset_state() -> None:
    """테스트 사이에 저장소를 비운다."""
    global STATE
    STATE = State()


def _now() -> datetime:
    return datetime.now(UTC)


def _evidence(queries: list[str], summary: str, ref_ids: list[str] | None = None) -> Evidence:
    return Evidence(
        queries=queries, data_summary=summary, collected_at=_now(), ref_ids=ref_ids or []
    )


def _result(request: AgentRequest, status: TaskStatus, **kwargs: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=status, **kwargs)


def _invalid(request: AgentRequest, error: ValidationError) -> AgentResult:
    return _result(
        request,
        TaskStatus.FAILED,
        outcome="invalid_input",
        output={"errors": [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]},
    )


def _parse[T: BaseModel](request: AgentRequest, model: type[T]) -> T | AgentResult:
    try:
        return model.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)


def _from_decision(
    request: AgentRequest, decision: ExecuteDecision, evidence: Evidence
) -> AgentResult | None:
    """`executed`가 아니면 결과 Task를 만들어 돌려준다. `executed`면 None이다."""
    if decision.status == "executed":
        return None
    detail = {"execution_id": decision.execution_id, "grade": decision.grade}
    if decision.status == "verifying":
        return _result(request, TaskStatus.WORKING, outcome="verifying", output=detail)
    if decision.status == "held":
        detail |= {"review_id": decision.review_id, "hold_reason": decision.hold_reason}
        return _result(
            request,
            TaskStatus.INPUT_REQUIRED,
            reason_code=InputReason.HUMAN_INPUT,
            outcome="held",
            output=detail,
            evidence=evidence,
        )
    if decision.hold_reason == HoldReason.VERIFY_REJECTED:
        return _result(
            request,
            TaskStatus.COMPLETED,
            outcome="rejected_by_review",
            output=detail,
            evidence=evidence,
        )
    if decision.hold_reason == HoldReason.VERIFY_TIMEOUT:
        return _result(
            request, TaskStatus.CANCELED, outcome="TIMEOUT", output=detail, evidence=evidence
        )
    outcome = "execute_failed" if decision.status == "failed" else "execute_rejected"
    return _result(request, TaskStatus.FAILED, outcome=outcome, output=detail, evidence=evidence)


def _event_id(key: str) -> str:
    return f"evt_{hashlib.sha256(key.encode()).hexdigest()[:12]}"


async def _emit(request: AgentRequest, event_type: EventType, data: BaseModel, key: str) -> None:
    """같은 키는 같은 `event_id`라서 재전송해도 한 번만 전달된다."""
    envelope = EventEnvelope(
        event_id=_event_id(key),
        type=event_type,
        occurred_at=_now(),
        tenant_id=request.meta.tenant_id,
        context_id=request.meta.context_id,
        source=SOURCE,
        idempotency_key=key,
        data=data.model_dump(mode="json"),
    )
    try:
        await publish(envelope)
    except NotImplementedError:  # Redis 연동 전(TODO)
        log_event(
            request.meta.context_id,
            request.meta.tenant_id,
            "event_publish_skipped",
            {"type": event_type.value},
        )


async def draft_notice(request: AgentRequest) -> AgentResult:
    data = _parse(request, DraftNoticeInput)
    if isinstance(data, AgentResult):
        return data
    draft = make_draft(request.meta.context_id, data)
    STATE.notices.save_draft(draft)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=draft.model_dump(mode="json"),
        evidence=_evidence(["draft_notice"], f"{data.kind} 사실 필드로 고정 템플릿 문안 생성"),
        fallback=True,  # LLM 문안은 아직 연결하지 않았다(TODO). 고정 템플릿으로 대체
    )


async def publish_notice(request: AgentRequest) -> AgentResult:
    data = _parse(request, PublishNoticeInput)
    if isinstance(data, AgentResult):
        return data
    draft = STATE.notices.get_draft(data.draft_id)
    if draft is None:
        return _result(request, TaskStatus.FAILED, outcome="draft_not_found")
    if draft.channel_code == "sns":
        # SNS 게시는 별도 관문 행동 publish_sns(C등급, 항상 사람 승인)로만 한다.
        # publish_notice(A등급)로는 즉시 나가므로 안전 장치를 부르기 전에 막는다
        return _result(request, TaskStatus.FAILED, outcome="sns_use_publish_sns")

    to_shop = draft.channel_code == "banner" and shop_client.notice_source() == "shop-api"
    if to_shop and len(draft.public_text) > BANNER_TEXT_MAX:
        # 쇼핑몰 배너 문구는 200자까지다. 안전 장치를 부르기 전에 막아 실행 기록을 남기지 않는다
        return _result(request, TaskStatus.FAILED, outcome="notice_text_too_long")

    key = f"{request.meta.context_id}:publish_notice:{draft.draft_id}"
    evidence = _evidence(["publish_notice"], f"초안 {draft.draft_id} 게시 요청", [draft.draft_id])
    decision = await execute(
        "publish_notice",
        {"draft_id": draft.draft_id, "channel_code": draft.channel_code},
        request.meta.model_copy(update={"idempotency_key": key}),
        evidence,
    )
    blocked = _from_decision(request, decision, evidence)
    if blocked:
        return blocked

    notice_id = None
    if to_shop:
        # 쇼핑몰 배너 번호는 초안에서 만든다(같은 초안은 같은 번호라
        # 재요청·재시작에도 한 번만 올라간다).
        # 쇼핑몰에서 실패하면 게시한 것으로 기록하지 않고, 같은 요청을 다시 보내면 이어서 올린다
        notice_id = f"ntc_{draft.draft_id.removeprefix('drf_')}"
        try:
            await shop_client.post_banner(
                notice_id=notice_id,
                text=draft.public_text,
                valid_from=draft.valid_from,
                valid_to=draft.valid_to,
                context_id=request.meta.context_id,
            )
            if (
                draft.kind == "incident_resolved"
            ):  # 복구 공지가 나가면 같은 사건의 장애 배너를 내린다
                for earlier in STATE.notices.open_incident_notice_ids(request.meta.context_id):
                    await shop_client.end_banner(earlier)
        except shop_client.ShopError as exc:
            return _result(request, TaskStatus.FAILED, outcome=f"shop_banner_failed:{exc.code}")
    notice_id = STATE.notices.publish(request.meta.context_id, draft, notice_id)
    await _emit(
        request,
        EventType.NOTICE_PUBLISHED,
        NoticePublishedData(
            notice_id=notice_id, channel_code=draft.channel_code, context_id=request.meta.context_id
        ),
        f"notice.published:{notice_id}",
    )
    published = PublishedNotice(notice_id=notice_id, channel_code=draft.channel_code)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=published.model_dump(mode="json"),
        evidence=_evidence(["publish_notice"], "게시 완료", [notice_id, decision.execution_id]),
    )


async def get_notice(request: AgentRequest) -> AgentResult:
    data = _parse(request, GetNoticeInput)
    if isinstance(data, AgentResult):
        return data
    view = STATE.notices.view(data.notice_id)
    summary = "게시된 공지 조회" if view.found else "게시된 공지를 찾을 수 없음(확인할 수 없음)"
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=view.model_dump(mode="json"),
        evidence=_evidence(["get_notice"], summary, [data.notice_id]),
    )


async def list_active_notices(request: AgentRequest) -> AgentResult:
    data = _parse(request, ListActiveNoticesInput)
    if isinstance(data, AgentResult):
        return data
    items, total = STATE.notices.active(
        component=data.component,
        channel_code=data.channel_code,
        at=data.at,
        limit=data.limit,
    )
    listing = ActiveNotices(
        items=items,
        total=total,
        unknown_reason=None if items else "조건에 맞는 게시 중인 공지가 없음",
    )
    summary = f"게시 중인 공지 {total}건 조회" if items else "조건에 맞는 게시 중인 공지가 없음"
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=listing.model_dump(mode="json"),
        evidence=_evidence(
            ["list_active_notices"], summary, [item.notice_id for item in items if item.notice_id]
        ),
    )


async def receive_campaign_brief(request: AgentRequest) -> AgentResult:
    """기획안을 받아 보관한다. 캠페인은 시작하지 않는다.

    순서: 검증(`invalid_input`·`rejected`) → 중복(`duplicate`, 관문 안 부름) → 안전 관문
    `execute()` → `executed`일 때만 저장. 저장소는 메모리라 재시작하면 사라진다.
    """
    data = _parse(request, CampaignBriefInput)
    if isinstance(data, AgentResult):
        return data
    reason = rejection_reason(data)
    if reason is not None:
        rejected = CampaignBriefOutput(outcome="rejected", reason_code=reason)
        return _result(
            request,
            TaskStatus.COMPLETED,
            output=rejected.model_dump(mode="json"),
            outcome=rejected.outcome,
            evidence=_evidence(["receive_campaign_brief"], f"기획안 거절 {reason}", []),
        )
    try:
        existing = STATE.briefs.existing(data.proposal_id)
    except BriefStorageError:
        return _brief_storage_failed(request)
    if existing is not None:  # 관문 앞에서 막는다. 보관본은 그대로
        dup = CampaignBriefOutput(outcome="duplicate", campaign_id=existing.campaign_id)
        return _result(
            request,
            TaskStatus.COMPLETED,
            output=dup.model_dump(mode="json"),
            outcome=dup.outcome,
            evidence=_evidence(
                ["receive_campaign_brief"],
                f"기획안 {data.proposal_id} 이미 받음",
                [data.proposal_id, existing.campaign_id],
            ),
        )

    campaign_id = campaign_id_for(data.proposal_id)
    key = f"{request.meta.context_id}:receive_campaign_brief:{data.proposal_id}"
    evidence = _evidence(
        ["receive_campaign_brief"], f"기획안 {data.proposal_id} 받기 요청", [data.proposal_id]
    )
    decision = await execute(
        "receive_campaign_brief",
        {
            "proposal_id": data.proposal_id,
            "campaign_id": campaign_id,
            "insight_id": data.insight_id,
            "title": data.title,
            "approved_by": data.approved_by,
        },
        request.meta.model_copy(update={"idempotency_key": key}),
        evidence,
    )
    blocked = _from_decision(request, decision, evidence)
    if blocked:  # 보류·거절·관문 무응답: 저장하지 않는다(같은 요청을 다시 보내면 이어서 처리)
        return blocked
    try:
        out = STATE.briefs.add(data, _now())
    except BriefStorageError:  # 같은 요청을 다시 보내면 관문은 이어서 처리하고 저장을 다시 시도한다
        return _brief_storage_failed(request)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        outcome=out.outcome,
        evidence=evidence.model_copy(
            update={
                "data_summary": f"기획안 {data.proposal_id} {out.outcome}",
                "ref_ids": [data.proposal_id, campaign_id, decision.execution_id],
            }
        ),
    )


def _brief_storage_failed(request: AgentRequest) -> AgentResult:
    return _result(request, TaskStatus.FAILED, outcome="storage_error")


async def list_campaign_briefs(request: AgentRequest) -> AgentResult:
    data = _parse(request, ListCampaignBriefsInput)
    if isinstance(data, AgentResult):
        return data
    try:
        items, total = STATE.briefs.list(data.limit)
    except BriefStorageError:
        return _brief_storage_failed(request)
    listing = CampaignBriefList(
        items=items, total=total, unknown_reason=None if items else "받은 기획안이 없음"
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=listing.model_dump(mode="json"),
        evidence=_evidence(
            ["list_campaign_briefs"], f"받은 기획안 {total}건 조회", [i.proposal_id for i in items]
        ),
    )


async def get_campaign_brief(request: AgentRequest) -> AgentResult:
    data = _parse(request, GetCampaignBriefInput)
    if isinstance(data, AgentResult):
        return data
    if data.proposal_id is None and data.campaign_id is None:
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="invalid_input",
            output={"errors": ["proposal_id|campaign_id: missing"]},
        )
    try:
        item = STATE.briefs.get(data.proposal_id, data.campaign_id)
    except BriefStorageError:
        return _brief_storage_failed(request)
    view = CampaignBriefView(
        found=item is not None,
        item=item,
        unknown_reason=None if item else "받은 기획안을 찾을 수 없음(확인할 수 없음)",
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=view.model_dump(mode="json"),
        evidence=_evidence(
            ["get_campaign_brief"],
            "받은 기획안 조회" if item else "받은 기획안을 찾을 수 없음(확인할 수 없음)",
            [item.proposal_id] if item else [],
        ),
    )


async def build_campaign_link_skill(request: AgentRequest) -> AgentResult:
    """UTM 꼬리표를 붙인 링크를 만든다. 순수 계산이라 상태를 바꾸지 않는다(`execute()` 없음)."""
    data = _parse(request, BuildCampaignLinkInput)
    if isinstance(data, AgentResult):
        return data
    try:
        url = build_campaign_link(data.base_url, data.campaign_id, data.source, data.medium)
    except ValueError as error:
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="invalid_input",
            output={"errors": [str(error)]},
        )
    link = CampaignLink(
        url=url,
        utm_campaign=data.campaign_id,
        utm_source=data.source,
        utm_medium=data.medium,
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=link.model_dump(mode="json"),
        evidence=_evidence(["build_campaign_link"], "캠페인 링크 꼬리표 생성", [data.campaign_id]),
    )


async def issue_coupon(request: AgentRequest) -> AgentResult:
    data = _parse(request, IssueCouponInput)
    if isinstance(data, AgentResult):
        return data
    if data.purpose_code == "campaign" and not data.campaign_id:
        return _result(request, TaskStatus.FAILED, outcome="invalid_input")
    if data.purpose_code == "compensation" and request.sequence is None:
        return _result(request, TaskStatus.FAILED, outcome="invalid_input")

    context_id = request.meta.context_id
    key = f"{context_id}:issue_coupon:{data.buyer_id}" + (
        f":{data.campaign_id}" if data.campaign_id else ""
    )
    async with STATE.coupons.lock(context_id):
        if request.sequence is not None:
            replay = STATE.coupons.previous_by_sequence(context_id, request.sequence)
            if replay is not None:  # 같은 번호의 재요청은 이전 결과 그대로
                return replay.model_copy(update={"meta": request.meta})
            try:
                STATE.coupons.check_sequence(context_id, request.sequence)
            except SequenceGap:
                return _result(request, TaskStatus.REJECTED, outcome="SEQUENCE_GAP")
        previous = STATE.coupons.previous_by_key(key)
        if previous is not None:
            if request.sequence is None:  # 같은 키의 재요청은 이전 결과 그대로
                return previous.model_copy(update={"meta": request.meta})
            # 같은 구매자에게 번호만 달리 온 요청: 새로 지급하지 않고 이전 결과에 연결한다
            linked = previous.model_copy(
                update={"meta": request.meta, "outcome": "linked_duplicate"}
            )
            STATE.coupons.record(context_id, key, request.sequence, linked, cache=True)
            return linked

        total = total_value(data.count, data.unit_value)  # 계산은 코드로
        evidence = _evidence(
            ["issue_coupon"], f"{data.count}건 × {data.unit_value}원 = {total}원", [data.buyer_id]
        )
        params = data.model_dump(mode="json", exclude={"claimed_total"}) | {"total_value": total}
        if data.claimed_total is not None:
            params["claimed_total"] = data.claimed_total
        decision = await execute(
            "issue_coupon",
            params,
            request.meta.model_copy(update={"idempotency_key": key}),
            evidence,
        )
        blocked = _from_decision(request, decision, evidence)
        if blocked:
            if blocked.status != TaskStatus.WORKING:  # 회신한 건은 다음 번호로 넘어간다
                # 보류·취소·실패는 다시 시도할 수 있게 결과를 저장하지 않는다(번호만 소비)
                cache = blocked.status == TaskStatus.COMPLETED
                STATE.coupons.record(context_id, key, request.sequence, blocked, cache=cache)
            return blocked

        if shop_client.coupon_source() == "shop-api":
            # 안전 장치를 통과했으면 쇼핑몰에 쿠폰을 만든다. 같은 키의 재요청은 shop-api가
            # 이전 쿠폰을 돌려주므로 실패한 건을 다시 불러도 두 번 만들어지지 않는다.
            # 번호도 shop-api가 정한다
            try:
                coupon = await shop_client.issue_coupon(
                    idempotency_key=key,
                    purpose_code=data.purpose_code,
                    buyer_id=data.buyer_id,
                    count=data.count,
                    unit_value=data.unit_value,
                    campaign_id=data.campaign_id,
                    context_id=context_id,
                )
            except shop_client.ShopError as exc:
                failed = _result(
                    request, TaskStatus.FAILED, outcome=f"shop_coupon_failed:{exc.code}"
                )
                STATE.coupons.record(context_id, key, request.sequence, failed, cache=False)
                return failed
            coupon_id = str(coupon["coupon_id"])
        else:
            coupon_id = STATE.coupons.new_coupon_id()
        issued = IssuedCoupon(
            coupon_id=coupon_id,
            purpose_code=data.purpose_code,
            count=data.count,
            total_value=total,
        )
        result = _result(
            request,
            TaskStatus.COMPLETED,
            output=issued.model_dump(mode="json"),
            evidence=evidence.model_copy(update={"ref_ids": [coupon_id, decision.execution_id]}),
        )
        STATE.coupons.record(context_id, key, request.sequence, result, cache=True)
    await _emit(
        request,
        EventType.COUPON_ISSUED,
        CouponIssuedData(
            coupon_id=coupon_id,
            campaign_id=data.campaign_id if data.purpose_code == "campaign" else None,
            purpose_code=data.purpose_code,
            count=data.count,
            total_value=total,
            buyer_id=data.buyer_id,
            execution_id=decision.execution_id,
        ),
        # 쿠폰 번호는 재시작하면 다시 1부터라 이벤트 키로 쓰면 새 쿠폰이 이미 보낸 것으로 버려진다.
        # 사건·고객으로 만든 멱등 키를 쓴다
        f"coupon.issued:{key}",
    )
    return result


async def draft_sns_post_skill(request: AgentRequest) -> AgentResult:
    return await draft_sns_post(request, STATE.notices, STATE.sns)


async def check_sns_post_skill(request: AgentRequest) -> AgentResult:
    return await check_sns_post(request, STATE.sns)


def _chat_failed(request: AgentRequest, refused: ChatRefused) -> AgentResult:
    return _result(
        request, TaskStatus.FAILED, outcome=refused.outcome, output={"message": refused.message}
    )


def _chat_storage_failed(request: AgentRequest) -> AgentResult:
    return _result(request, TaskStatus.FAILED, outcome="storage_error")


def _chat_done(request: AgentRequest, skill: str, output: dict[str, Any]) -> AgentResult:
    # 대화 기록만 읽고 쓴다. 업무 상태(캠페인·공지·쿠폰)는 바꾸지 않아 execute()를 부르지 않는다
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=output,
        evidence=_evidence([skill], "마케팅 AI 대화"),
    )


async def send_chat_message(request: AgentRequest) -> AgentResult:
    data = _parse(request, SendChatMessageInput)
    if isinstance(data, AgentResult):
        return data
    try:
        thread = STATE.chat.send(data.who, data.thread_id, data.message)
    except ChatRefused as refused:
        return _chat_failed(request, refused)
    except ChatStorageError:
        return _chat_storage_failed(request)
    return _chat_done(request, "send_chat_message", {"thread": thread})


async def list_chat_threads(request: AgentRequest) -> AgentResult:
    data = _parse(request, ListChatThreadsInput)
    if isinstance(data, AgentResult):
        return data
    try:
        threads = STATE.chat.list(data.who, data.limit)
    except ChatStorageError:
        return _chat_storage_failed(request)
    return _chat_done(request, "list_chat_threads", {"threads": threads})


async def get_chat_thread(request: AgentRequest) -> AgentResult:
    data = _parse(request, ChatThreadRef)
    if isinstance(data, AgentResult):
        return data
    try:
        thread = STATE.chat.get(data.who, data.thread_id)
    except ChatRefused as refused:
        return _chat_failed(request, refused)
    except ChatStorageError:
        return _chat_storage_failed(request)
    return _chat_done(request, "get_chat_thread", {"thread": thread})


async def close_chat_thread(request: AgentRequest) -> AgentResult:
    data = _parse(request, ChatThreadRef)
    if isinstance(data, AgentResult):
        return data
    try:
        thread = STATE.chat.close(data.who, data.thread_id)
    except ChatRefused as refused:
        return _chat_failed(request, refused)
    except ChatStorageError:
        return _chat_storage_failed(request)
    return _chat_done(request, "close_chat_thread", {"thread": thread})


HANDLERS = {
    "draft_notice": draft_notice,
    "publish_notice": publish_notice,
    "get_notice": get_notice,
    "list_active_notices": list_active_notices,
    "receive_campaign_brief": receive_campaign_brief,
    "list_campaign_briefs": list_campaign_briefs,
    "get_campaign_brief": get_campaign_brief,
    "build_campaign_link": build_campaign_link_skill,
    "issue_coupon": issue_coupon,
    "start_campaign": start_campaign,
    "record_campaign_result": record_campaign_result,
    "draft_sns_post": draft_sns_post_skill,
    "check_sns_post": check_sns_post_skill,
    "cancel_campaign": cancel_campaign,
    "send_chat_message": send_chat_message,
    "list_chat_threads": list_chat_threads,
    "get_chat_thread": get_chat_thread,
    "close_chat_thread": close_chat_thread,
}
