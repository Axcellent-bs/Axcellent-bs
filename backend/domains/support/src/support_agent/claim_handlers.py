"""클레임(취소·반품·교환) skill 핸들러.

- 판정은 `claim_rules.decide`가 주문 사실에서 코드로 한다. 입력에는 금액이 없고 LLM도 쓰지 않는다.
  예외: 사이트 변환기 신청의 사이트 규칙 예상 환불(`site_refund`)은 판정·환불 승인 금액이 된다.
- 읽기 skill은 상태를 바꾸지 않는다. 쓰기 skill은 `handlers._write`(멱등 확인 → `execute()` →
  저장소 반영)를 거친다. 실행되지 않으면(보류·검증 중·거부) 저장소를 바꾸지 않는다.
- 환불 지급(`process_claim_queue`)은 safety 레지스트리의 `refund_request`(A, 안전 관문의 한도
  검사, 기준 이상 C 보류)로 실행한다. 실행되면 shop-api 환불 API(SHOP-12)로 현금을 환불한다
  (`shop_client.py`).
- 보상 쿠폰은 클레임이 아니라 `request_compensation`(DEP-CS-02)으로 요청한다.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from ax_agent_template.execute import execute
from ax_agent_template.server import error_result
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from support_agent import book_issue_stats, claim_rules, events, persistence, shop_client
from support_agent import handlers as h
from support_agent.book_issue_schemas import PERIOD_DAYS, BookIssueInput, InquiryOrderCounts
from support_agent.book_issue_stats import OrderInquiry, OrderStatus
from support_agent.claim_schemas import (
    BuyerOrder,
    BuyerOrderItem,
    BuyerOrders,
    Claim,
    ClaimReasonHit,
    CreateClaimInput,
    DecideClaimInput,
    GetClaimInput,
    InspectReturnInput,
    ListBuyerOrdersInput,
    ListClaimsInput,
    ProcessClaimQueueInput,
    QueueRun,
    QueueRunResult,
    ReceiveReturnInput,
    SiteRefund,
)
from support_agent.claims import now_iso, refund_rejection_hit
from support_agent.schemas import RecordClaimActivityInput

CLAIMS = persistence.build_claim_store()


async def _record_activity(request: AgentRequest, claim: Any, kind: str, **extra: Any) -> None:
    """클레임이 문의에서 접수된 것이면 그 처리 결과를 문의의 AI 처리 내력에 남긴다.

    문의와 같은 프로세스면 바로 남기고, 아니면(서비스가 나뉘어 있으면) 문의 서비스에 알린다.
    내력은 부가 기록이라 실패해도 클레임 처리는 그대로 끝난다.
    """
    if claim.conversation_id is None:
        return
    payload = {
        "inquiry_id": claim.conversation_id,
        "kind": kind,
        "claim_id": claim.id,
        "claim_type": claim.type,
        "reason": claim.reason,
        "status": claim.status,
        "outcome": claim.outcome,
        "amount": claim.expected_refund,
        **{k: v for k, v in extra.items() if v is not None},
    }
    try:
        record = RecordClaimActivityInput.model_validate(payload)
        if h.STORE.get(claim.conversation_id) is not None:
            h.log_claim_activity(claim.conversation_id, record)
            return
        meta = request.meta.model_copy(
            update={
                "idempotency_key": (
                    f"{claim.context_id}:record_claim_activity:claim:{claim.id}:{kind}"
                    f":{claim.updated_at}"
                )
            }
        )
        reply = await h.SEND(
            "support",
            AgentRequest(meta=meta, skill="record_claim_activity", input=record.model_dump()),
        )
        if reply.status != TaskStatus.COMPLETED:
            h.logger.warning("클레임 결과를 문의 내력에 남기지 못했다: %s", reply.outcome)
    except Exception:  # noqa: BLE001 - 내력은 부가 기록이다
        h.logger.warning(
            "클레임 결과를 문의 내력에 남기지 못했다 (claim %s)", claim.id, exc_info=True
        )


# 지급을 실행하지 못한 이유 (safety 판정 상태 → 화면에 보일 문장)
HELD_MESSAGE = {
    "verifying": "재무 교차검증 중",
    "held": "안전 레이어 보류 (사람 확인 대기)",
    "rejected": "실행이 거부됨",
    "failed": "실행에 실패함",
    "shop_refund_failed": "shop-api 환불 실패 (다시 처리하면 같은 키로 재시도)",
    "shop_refund_read_only": "관찰 모드(읽기 전용)라 환불을 내지 않음 (쓰기 권한 설치에서 처리)",
    "shop_refund_pending_human": "사이트 반영 대기 (사람이 사이트에서 환불 처리, 할 일함)",
}


def _note(request: AgentRequest) -> str | None:
    """상담원·검수 메모는 `untrusted.note`로만 받는다. 저장만 하고 지시로 쓰지 않는다."""
    note = request.untrusted.get("note")
    return note.strip() or None if isinstance(note, str) else None


def _claim_not_found(request: AgentRequest, claim_id: int) -> AgentResult:
    return h._done(request, {"message": f"클레임 {claim_id}를 찾을 수 없다"}, outcome="not_found")


def _invalid_state(request: AgentRequest, claim_id: int, status: str, wanted: str) -> AgentResult:
    return h._done(
        request,
        {"message": f"클레임 {claim_id}는 {status} 상태라 {wanted}할 수 없다", "status": status},
        outcome="invalid_state",
    )


# ---------------------------------------------------------------- 읽기
async def list_claims(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, ListClaimsInput)
    if isinstance(parsed, AgentResult):
        return parsed
    items = CLAIMS.list(parsed.status)
    return h._done(request, {"items": [c.model_dump(mode="json") for c in items]})


async def get_claim(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, GetClaimInput)
    if isinstance(parsed, AgentResult):
        return parsed
    claim = CLAIMS.get(parsed.claim_id)
    if claim is None:
        return _claim_not_found(request, parsed.claim_id)
    return h._done(request, claim.model_dump(mode="json"))


async def list_buyer_orders(request: AgentRequest) -> AgentResult:
    """구매자 한 명의 결제가 끝난 주문 목록(읽기). 환불·교환 접수 전에 어느 주문인지 고르게 한다.

    `buyer_id`가 요청한 사람의 것인지는 여기서 알 수 없다.
    총괄이 로그인 계정의 연결에서 정한 값만 넣는다.
    """
    parsed = h._parse(request, ListBuyerOrdersInput)
    if isinstance(parsed, AgentResult):
        return parsed
    try:
        found, truncated = await shop_client.load_buyer_orders(parsed.buyer_id, parsed.limit)
    except shop_client.ShopError as exc:
        return error_result(request.meta, TaskStatus.FAILED, "shop_unavailable", str(exc))
    result = BuyerOrders(
        buyer_id=parsed.buyer_id,
        items=[
            BuyerOrder(
                order_id=o.id,
                status=o.status,
                paid_at=o.paid_at.isoformat(),
                delivered_at=o.delivered_at.isoformat() if o.delivered_at else None,
                shipping_fee=o.shipping_fee,
                total_paid=sum(i.paid_amount for i in o.items),
                items=[
                    BuyerOrderItem(
                        order_item_id=i.id, title=i.title, qty=i.qty, paid_amount=i.paid_amount
                    )
                    for i in o.items
                ],
            )
            for o in found
        ],
        truncated=truncated,
    )
    return h._done(request, result.model_dump(mode="json"))


async def claim_stats(request: AgentRequest) -> AgentResult:
    return h._done(request, CLAIMS.stats().model_dump(mode="json"))


ORDER_READ_CONCURRENCY = 8
DEADLINE_MARGIN = 3  # 요청 마감 몇 초 전에 선택 근거(문의·판매 수량) 읽기를 멈추는가 (최대치)
MARGIN_SHARE = (
    0.3  # 마감이 짧으면 남은 시간의 이 비율만 여유로 남긴다 (재무가 3초 마감으로 부를 때)
)


def _stop_at(deadline: datetime, now: datetime | None = None) -> datetime:
    """선택 근거(문의·판매 수량) 읽기를 멈출 시각. 여유는 `min(3초, 남은 시간 × 30%)`다.

    마감이 넉넉하면(30초) 3초 전에 멈추고, 재무처럼 3초 마감으로 부르면 남은 시간의 30%만 남겨
    시작하자마자 마감이 지나 있지 않게 한다(11/17 태윤 님 검토). 이미 지난 마감은 여유 0이다.
    """
    current = now or datetime.now(UTC)
    remaining = max((deadline - current).total_seconds(), 0.0)
    margin = min(float(DEADLINE_MARGIN), remaining * MARGIN_SHARE)
    return deadline - timedelta(seconds=margin)


async def _skipped[T](value: T) -> T:
    """읽지 않기로 한 근거 (`include_inquiries`·`include_sales`가 False)."""
    return value


class InquiryEvidence(NamedTuple):
    """문의 서비스에서 읽은 주문별 문의와 그 주문의 상품. `orders`가 None이면 읽지 못한 것이다."""

    orders: list[OrderInquiry] | None = None
    total: int | None = None
    truncated: bool = False
    notes: tuple[str, ...] = ()


class SalesEvidence(NamedTuple):
    sold: dict[str, int] | None = None
    scanned: int | None = None
    notes: tuple[str, ...] = ()


async def _inquiry_evidence(request: AgentRequest, period: str) -> InquiryEvidence:
    """문의 서비스에서 주문별 문의 건수를 읽고, 그 주문들의 상품을 판매 서버에서 읽는다.

    문의는 선택 근거라서 어떤 이유로든 못 읽으면 `orders=None`과 이유를 돌려주고,
    클레임 집계는 그대로 나간다. 문의 건수를 0으로 채우지 않는다.
    """
    meta = request.meta.model_copy(update={"idempotency_key": None})
    try:
        reply = await h.SEND(
            "support",
            AgentRequest(meta=meta, skill="inquiry_order_counts", input={"period": period}),
        )
        if reply.status != TaskStatus.COMPLETED:
            return InquiryEvidence(notes=(f"문의 건수를 읽지 못했다: {reply.outcome}",))
        counts = InquiryOrderCounts.model_validate(reply.output)
    except Exception:  # noqa: BLE001 - 문의는 부가 근거라 클레임 집계는 그대로 낸다
        h.logger.warning("주문별 문의 건수를 읽지 못했다", exc_info=True)
        return InquiryEvidence(notes=("문의 건수를 읽지 못했다 (문의 서비스에 닿지 못함)",))

    stop_at = _stop_at(request.meta.deadline)
    gate = asyncio.Semaphore(ORDER_READ_CONCURRENCY)

    async def read(
        order_id: str, http: Any
    ) -> tuple[OrderStatus, tuple[tuple[str, str], ...], bool]:
        """(상태, (상품 ID, 제목)들, 상품 ID 없는 품목이 있는가)."""
        async with gate:
            if datetime.now(UTC) >= stop_at:  # 마감이 가까우면 더 읽지 않는다
                return "unread", (), False
            try:
                order = await shop_client.load_order(order_id, client=http)
            except shop_client.ShopError:
                return "unread", (), False
            except Exception:  # noqa: BLE001 - 응답 모양이 이상해도 클레임 집계는 그대로 낸다
                h.logger.warning("주문 %s을 읽다가 실패했다", order_id, exc_info=True)
                return "unread", (), False
        if order is None:  # 결제 전이거나 없는 주문 (결제 실패 문의는 상품 불만이 아니라서 뺀다)
            return "unpaid", (), False
        known = {i.product_id: i.title for i in order.items if i.product_id}
        unknown = any(not i.product_id for i in order.items)
        if not known:
            return "no_product", (), unknown
        return "ok", tuple(known.items()), unknown

    async with shop_client.shared_client() as http:
        loaded = await asyncio.gather(*(read(o.order_id, http) for o in counts.orders))
    notes: list[str] = []
    unread = sum(1 for status, _, _ in loaded if status == "unread")
    if unread:
        notes.append(
            f"사이트 서버에서 주문 {unread}개를 읽지 못했거나 마감 때문에 건너뛰어 "
            "그 문의는 상품에 붙이지 못했다"
        )
    if counts.truncated:
        notes.append("문의가 있는 주문이 많아 문의 많은 순 200개 주문까지만 붙였다")
    orders = [
        OrderInquiry(o.order_id, o.inquiry_count, o.categories, products, status, unknown)
        for o, (status, products, unknown) in zip(counts.orders, loaded, strict=True)
    ]
    return InquiryEvidence(orders, counts.total_inquiries, counts.truncated, tuple(notes))


async def _sales_evidence(request: AgentRequest, now: datetime, period: str) -> SalesEvidence:
    """판매 서버에서 기간 안 상품별 판매 수량을 읽는다(판매 집계 읽기 경로, 없으면 주문 목록).

    끝까지 읽지 못하면(연결 실패·페이지 상한·마감·응답 모양 이상) 모자란 수량으로 비율을 내지 않게
    `sold=None`과 이유를 돌려준다. 판매 수량은 선택 근거라서 클레임 집계는 그대로 나간다.
    """
    if shop_client.order_source() == "seed":
        return SalesEvidence(notes=("주문 출처가 시드라 판매 수량을 읽지 않았다",))
    since = now - timedelta(days=PERIOD_DAYS[period])
    try:
        read = await shop_client.fetch_sold_qty(since, now, stop_at=_stop_at(request.meta.deadline))
    except shop_client.ShopError as exc:
        return SalesEvidence(notes=(f"판매 수량을 읽지 못했다: {exc}",))
    except Exception:  # noqa: BLE001 - 응답 모양이 이상해도 클레임 집계는 그대로 낸다
        h.logger.warning("판매 수량을 읽다가 실패했다", exc_info=True)
        return SalesEvidence(notes=("판매 수량을 읽지 못했다 (응답을 해석하지 못함)",))
    if not read.complete:
        if read.source == "summary":
            note = "마감 때문에 판매 집계를 끝까지 읽지 못해 판매 수량을 내지 않았다"
        else:
            note = f"주문이 많아 {read.orders_scanned}건까지만 읽어서 판매 수량을 내지 않았다"
        return SalesEvidence(notes=(f"{note} (모자란 수량으로 비율을 내면 틀린다)",))
    return SalesEvidence(read.qty_by_product, read.orders_scanned)


async def book_issue_stats_skill(request: AgentRequest) -> AgentResult:
    """책(상품)별 클레임·불량·반품·환불·문의 집계. 이벤트 후보에서 불만 많은 책을 거를 근거다.

    요청한 고객사(`meta.tenant_id`)의 클레임만 센다. 판매 수량은 연결된 판매 서버(고객사 하나)
    것이다.
    """
    parsed = h._parse(request, BookIssueInput)
    if isinstance(parsed, AgentResult):
        return parsed
    now = datetime.now(UTC)
    inquiry_task = (
        _inquiry_evidence(request, parsed.period)
        if parsed.include_inquiries
        else _skipped(InquiryEvidence())
    )
    sales_task = (
        _sales_evidence(request, now, parsed.period)
        if parsed.include_sales
        else _skipped(SalesEvidence())
    )
    inquiry, sales = await asyncio.gather(inquiry_task, sales_task)
    tenant = request.meta.tenant_id
    report = book_issue_stats.book_issue_report(
        [c for c in CLAIMS.list() if c.tenant_id == tenant],
        now,
        parsed,
        inquiry.orders,
        total_inquiries=inquiry.total,
        inquiries_truncated=inquiry.truncated,
        sold=sales.sold,
        sales_orders_scanned=sales.scanned,
        notes=[*inquiry.notes, *sales.notes],
    )
    return h._done(request, report.model_dump(mode="json"))


async def claim_queue(request: AgentRequest) -> AgentResult:
    return h._done(request, CLAIMS.queue().model_dump(mode="json"))


def _site_refund(site: SiteRefund | None) -> claim_rules.SiteRefund | None:
    """사이트 규칙 예상 환불을 판정 규칙 모양으로 (없으면 책 쇼핑몰 규칙)."""
    if site is None:
        return None
    return claim_rules.SiteRefund(refund=site.refund_amount, fee=site.fee, fee_rate=site.fee_rate)


def _refund_summary(claim: Claim) -> str:
    """보류되면 담당자가 보는 요약. 코드가 아는 사실(금액·ID·유형·사유 코드·사이트 규칙)만 쓴다."""
    summary = (
        f"환불 {claim.expected_refund:,}원 · 클레임 {claim.id} · 주문 {claim.order_id}"
        f" · {claim.type}/{claim.reason}"
    )
    site = _site_refund(claim.site_refund)
    if site is not None:
        summary += f" · {claim_rules.site_rule_text(site)}"
    return summary


# ---------------------------------------------------------------- 쓰기 (execute 경유)
async def create_claim(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, CreateClaimInput)
    if isinstance(parsed, AgentResult):
        return parsed
    try:
        order = await shop_client.load_order(parsed.order_id)
    except shop_client.ShopError as exc:
        return error_result(request.meta, TaskStatus.FAILED, "shop_unavailable", str(exc))
    if order is None or order.buyer_id != parsed.buyer_id:
        return h._done(
            request, {"message": f"주문 {parsed.order_id}를 찾을 수 없다"}, outcome="not_found"
        )
    key = request.meta.idempotency_key or (
        f"{request.meta.context_id}:create_claim:order:{order.id}:{parsed.type}"
    )
    if key in h.STORE.results:  # 같은 요청의 재시도는 중복 검사보다 먼저 이전 결과를 돌려준다
        return h._done(request, h.STORE.results[key])
    wanted = None if parsed.items is None else {i.order_item_id: i.qty for i in parsed.items}
    item_ids = {i.id for i in order.items} if wanted is None else set(wanted)
    blocking = CLAIMS.blocking_claim(order.id, item_ids)
    if blocking is not None:
        message = (
            "같은 품목에 환불이 거절된 클레임이 있다"
            if blocking.status == "rejected"
            else "같은 품목에 진행 중인 클레임이 있다"
        )
        return h._done(request, {"message": message}, outcome="duplicate_claim")
    decision = claim_rules.decide(
        order,
        parsed.type,
        parsed.reason,
        items=wanted,
        evidence_count=parsed.evidence_count,
        unsealed=parsed.unsealed,
        prior_claims=CLAIMS.count_for_buyer(parsed.buyer_id),
        now=datetime.now(UTC),
        site_refund=_site_refund(parsed.site_refund),
    )

    async def apply() -> tuple[dict[str, Any], bool]:
        claim = CLAIMS.create(
            context_id=request.meta.context_id,
            tenant_id=request.meta.tenant_id,
            order=order,
            customer=parsed.customer_name,
            claim_type=parsed.type,
            reason=parsed.reason,
            decision=decision,
            evidence_count=parsed.evidence_count,
            unsealed=parsed.unsealed,
            conversation_id=parsed.conversation_id,
            site_refund=parsed.site_refund,
        )
        await _record_activity(request, claim, "claim_created")
        return claim.model_dump(mode="json"), False

    return await h._write(
        request,
        "create_claim",
        f"order:{order.id}:{parsed.type}",
        {"order_id": order.id, "type": parsed.type, "reason": parsed.reason},
        apply,
    )


async def decide_claim(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, DecideClaimInput)
    if isinstance(parsed, AgentResult):
        return parsed
    claim = CLAIMS.get(parsed.claim_id)
    if claim is None:
        return _claim_not_found(request, parsed.claim_id)
    # 안전 장치가 환불을 닫은 입고 클레임: 상담원이 사유와 함께 종료하거나,
    # 만료로 닫힌 것만 다시 요청한다
    refund_closed = bool(claim.refund_rejected) and claim.status in ("approved", "return_received")
    if parsed.decision == "retry_refund":
        if claim.refund_rejected != "review_timeout" or not refund_closed or claim.refund_paid > 0:
            return h._done(
                request,
                {
                    "message": f"클레임 {claim.id}는 만료로 닫힌 환불이 아니라 다시 요청할 수 없다",
                    "status": claim.status,
                },
                outcome="invalid_state",
            )
    else:
        allowed = (
            ("needs_review",) if parsed.decision == "approve" else ("needs_review", "needs_info")
        )
        closing_refund = (
            parsed.decision == "reject" and claim.status not in allowed and refund_closed
        )
        if claim.status not in allowed and not closing_refund:
            return _invalid_state(request, claim.id, claim.status, "결정")
        if closing_refund and not _note(request):
            return h._done(
                request,
                {
                    "message": "환불이 거절된 클레임을 종료하려면 사유를 적어야 한다",
                    "status": claim.status,
                },
                outcome="invalid_state",
            )

    async def apply() -> tuple[dict[str, Any], bool]:
        if parsed.decision == "retry_refund":
            status = claim.status
            claim.refund_rejected = None
            claim.refund_attempt += 1
        elif parsed.decision == "approve":
            status = "awaiting_return" if claim.requires_return else "approved"
        else:
            status = "rejected"
        claim.decided_by = request.meta.requester or "agent_console"
        claim.agent_note = _note(request)
        CLAIMS.set_status(
            claim,
            status,
            parsed.decision,
            note=claim.agent_note or "",
            system_outcome=claim.outcome,
            agent_action=parsed.decision,
        )
        await _record_activity(request, claim, "claim_decided", decision=parsed.decision)
        return claim.model_dump(mode="json"), False

    return await h._write(
        request,
        "decide_claim",
        f"claim:{claim.id}:{parsed.decision}",
        {"claim_id": claim.id, "decision": parsed.decision},
        apply,
    )


async def receive_return(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, ReceiveReturnInput)
    if isinstance(parsed, AgentResult):
        return parsed
    claim = CLAIMS.get(parsed.claim_id)
    if claim is None:
        return _claim_not_found(request, parsed.claim_id)
    if claim.status != "awaiting_return":
        return _invalid_state(request, claim.id, claim.status, "입고 처리")

    async def apply() -> tuple[dict[str, Any], bool]:
        claim.return_received_at = now_iso()
        claim.inspection = parsed.inspection
        claim.inspection_note = _note(request) or claim.inspection_note
        CLAIMS.set_status(claim, "return_received", "receive", inspection=parsed.inspection)
        await _record_activity(
            request, claim, "claim_return_received", inspection=parsed.inspection
        )
        return claim.model_dump(mode="json"), False

    return await h._write(
        request, "receive_return", f"claim:{claim.id}", {"claim_id": claim.id}, apply
    )


async def inspect_return(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, InspectReturnInput)
    if isinstance(parsed, AgentResult):
        return parsed
    claim = CLAIMS.get(parsed.claim_id)
    if claim is None:
        return _claim_not_found(request, parsed.claim_id)
    if claim.status != "return_received":
        return _invalid_state(request, claim.id, claim.status, "검수")

    async def apply() -> tuple[dict[str, Any], bool]:
        claim.inspection = parsed.inspection
        claim.inspection_note = _note(request) or claim.inspection_note
        CLAIMS.set_status(claim, "return_received", "inspection", inspection=parsed.inspection)
        await _record_activity(request, claim, "claim_inspected", inspection=parsed.inspection)
        return claim.model_dump(mode="json"), False

    return await h._write(
        request,
        "inspect_return",
        f"claim:{claim.id}:{parsed.inspection}",
        {"claim_id": claim.id, "inspection": parsed.inspection},
        apply,
    )


QUEUE_PROCESS_MAX_ENV = "CLAIM_QUEUE_PROCESS_MAX"
QUEUE_PROCESS_MAX_DEFAULT = 20


def _queue_process_cap() -> int:
    """한 번에 지급을 시도할 수 있는 클레임 수(dashboard-api 상한과 별개의 안전망)."""
    try:
        raw = os.getenv(QUEUE_PROCESS_MAX_ENV) or os.getenv("DASHBOARD_QUEUE_PROCESS_MAX") or ""
        value = int(raw.strip() or QUEUE_PROCESS_MAX_DEFAULT)
    except ValueError:
        return QUEUE_PROCESS_MAX_DEFAULT
    return value if value >= 1 else QUEUE_PROCESS_MAX_DEFAULT


async def process_claim_queue(request: AgentRequest) -> AgentResult:
    """준비된 큐 항목을 `refund_request`로 지급한다. 항목마다 따로 `execute()`를 거친다."""
    parsed = h._parse(request, ProcessClaimQueueInput)
    if isinstance(parsed, AgentResult):
        return parsed
    results: list[QueueRunResult] = []
    paid = held = 0
    attempted = 0  # 지급을 시도한(`execute` 호출) 건수. 건너뛴 항목은 세지 않는다
    cap = _queue_process_cap()
    for entry in CLAIMS.queue().queue:
        if parsed.claim_ids is not None and entry.claim_id not in parsed.claim_ids:
            continue
        if parsed.limit is not None and len(results) >= parsed.limit:
            break
        claim = CLAIMS.get(entry.claim_id)
        if claim is None:
            continue
        if not entry.ready:
            results.append(
                QueueRunResult(
                    claim_id=claim.id,
                    result="skipped",
                    detail=", ".join(b.message for b in entry.blockers),
                    blockers=entry.blockers,
                )
            )
            continue
        # 키와 사건은 클레임 단위로 고정한다. 안전 레이어는 C 보류 건을 승인한 뒤 같은 키로 다시
        # 요청해야 `executed`를 돌려주는데, 호출마다 새 키를 쓰면 승인이 반영되지 않는다.
        if claim.refund_paid > 0:  # 이미 지급한 클레임은 다시 요청하지 않는다
            results.append(
                QueueRunResult(claim_id=claim.id, result="skipped", detail="already_paid")
            )
            continue
        if attempted >= cap:  # 호출 쪽이 상한을 걸지 않았어도 한 번에 지급하는 수는 제한한다
            break
        attempted += 1
        # 만료로 닫힌 환불을 다시 요청하면 시도 번호가 붙은 새 키로 관문을 다시 지난다
        attempt = f":retry{claim.refund_attempt}" if claim.refund_attempt else ""
        meta = request.meta.model_copy(
            update={
                "context_id": claim.context_id,
                "idempotency_key": f"{claim.context_id}:refund_request:claim:{claim.id}{attempt}",
            }
        )
        # 보류되면 담당자가 이 요약을 본다 (safety가 앞 200자를 보류 기록에 담는다). 고객 이름·메모
        # 같은 외부 입력은 넣지 않고 코드가 아는 사실(금액·ID·유형·사유 코드·사이트 규칙)만 쓴다.
        # 사이트 규칙 신청이면 `expected_refund`가 사이트 예상 환불액이라 관문 금액도 같다.
        summary = _refund_summary(claim)
        decision = await execute(
            "refund_request",
            {
                "claim_id": claim.id,
                "order_id": claim.order_id,
                "buyer_id": claim.buyer_id,
                "refund_amount": claim.expected_refund,
                "summary": summary,
            },
            meta,
            Evidence(
                queries=["claim_rules"],
                data_summary=f"claim {claim.id}: 환불 {claim.expected_refund}원",
                ref_ids=[
                    f"claim:{claim.id}",
                    f"order:{claim.order_id}",
                    f"request:{request.meta.context_id}",
                ],
            ),
        )
        if decision.status == "rejected" and decision.hold_reason is not None:
            # 사람이 거절했거나 보류가 만료됐거나 검증이 거부한 환불이다.
            # 감사 기록 실패 같은 일시 오류는 사유가 없어 여기에 오지 않는다.
            # 같은 키로 다시 불러도 같은 거절이 오므로 기억해 큐에서 뺀다
            claim.refund_rejected = str(decision.hold_reason)
            CLAIMS.set_status(claim, claim.status, "refund_rejected", reason=claim.refund_rejected)
            results.append(
                QueueRunResult(
                    claim_id=claim.id,
                    result="skipped",
                    detail=f"refund_rejected:{claim.refund_rejected}",
                    blockers=[refund_rejection_hit(claim.refund_rejected)],
                )
            )
            continue
        if decision.status != "executed":
            held += 1
            results.append(
                QueueRunResult(
                    claim_id=claim.id,
                    result="held",
                    detail=decision.status,
                    blockers=[
                        ClaimReasonHit(
                            code="ON_HOLD",
                            level="block",
                            message=HELD_MESSAGE.get(decision.status, decision.status),
                        )
                    ],
                )
            )
            continue
        shop_refund: dict[str, Any] | None = None
        if claim.payment_id and shop_client.order_source() == "shop-api":
            # 같은 키로 다시 불러도 shop-api가 이전 환불을 돌려주므로 실패했다면 다시 처리하면 된다
            try:
                shop_refund = await shop_client.refund(
                    claim.payment_id,
                    claim.expected_refund,
                    meta.idempotency_key or "",
                    claim.context_id,
                    cancel_order=claim.type == "cancel",  # 배송 전 취소면 주문도 취소한다
                )
            except shop_client.ShopError as exc:
                held += 1
                # 관찰 설치는 환불이 늘 막힌다. 재시도 안내 대신 이유를 적는다
                kind = "shop_refund_read_only" if exc.code == "READ_ONLY" else "shop_refund_failed"
                results.append(
                    QueueRunResult(
                        claim_id=claim.id,
                        result="held",
                        detail=f"{kind}:{exc.code}",
                        blockers=[
                            ClaimReasonHit(
                                code="ON_HOLD",
                                level="block",
                                message=f"{HELD_MESSAGE[kind]} ({exc.code})",
                            )
                        ],
                    )
                )
                continue
            if shop_refund.get("applied") == "pending_human":
                # 사이트 변환기가 환불을 할 일함에 넣었다: 아직 돈이 나가지 않았다.
                # 지급 완료로 적지 않고 보류로 둔다(다시 처리해도 같은 키라 같은 할 일이다)
                held += 1
                results.append(
                    QueueRunResult(
                        claim_id=claim.id,
                        result="held",
                        detail=f"shop_refund_pending_human:{shop_refund.get('handoff_task_id')}",
                        blockers=[
                            ClaimReasonHit(
                                code="ON_HOLD",
                                level="block",
                                message=HELD_MESSAGE["shop_refund_pending_human"],
                            )
                        ],
                    )
                )
                continue
        claim.refund_paid = claim.expected_refund
        claim.processed_at = now_iso()
        order_detail: dict[str, Any] = {}
        if shop_refund is not None and "order_cancelled" in shop_refund:
            # 판매 서버가 주문도 취소했는지 (취소 클레임만 true가 될 수 있다)
            order_detail["order_cancelled"] = bool(shop_refund["order_cancelled"])
        CLAIMS.set_status(
            claim,
            "completed",
            "pay",
            amount=claim.expected_refund,
            refund_cash=claim.expected_refund,
            compensation=0,
            execution=decision.execution_id,
            requested_by=parsed.requested_by,
            **order_detail,
        )
        await _record_activity(
            request, claim, "claim_paid", amount=claim.refund_paid, requested_by=parsed.requested_by
        )
        # 문의를 이미 닫았다면 재무가 환불 0원으로 남기지 않게 금액을 채워 한 번 더 알린다
        if claim.conversation_id is not None:
            conversation = await h.load_inquiry(request, claim.conversation_id)
            await events.emit_refund_completed(request, conversation, claim)
        paid += 1
        results.append(QueueRunResult(claim_id=claim.id, result="paid", refund=claim.refund_paid))
    run = QueueRun(results=results, paid=paid, held=held, queue=CLAIMS.queue().queue)
    return h._done(request, run.model_dump(mode="json"))


CLAIM_HANDLERS = {
    "list_claims": list_claims,
    "get_claim": get_claim,
    "list_buyer_orders": list_buyer_orders,
    "claim_stats": claim_stats,
    "book_issue_stats": book_issue_stats_skill,
    "claim_queue": claim_queue,
    "create_claim": create_claim,
    "decide_claim": decide_claim,
    "receive_return": receive_return,
    "inspect_return": inspect_return,
    "process_claim_queue": process_claim_queue,
}
