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
from marketing_agent import shop_client
from marketing_agent.campaign import record_campaign_result, start_campaign
from marketing_agent.coupons import CouponLedger, SequenceGap, total_value
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
        self.sns = SnsStore()


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


HANDLERS = {
    "draft_notice": draft_notice,
    "publish_notice": publish_notice,
    "get_notice": get_notice,
    "list_active_notices": list_active_notices,
    "issue_coupon": issue_coupon,
    "start_campaign": start_campaign,
    "record_campaign_result": record_campaign_result,
    "draft_sns_post": draft_sns_post_skill,
    "check_sns_post": check_sns_post_skill,
}
