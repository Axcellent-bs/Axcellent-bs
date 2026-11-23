"""클레임 판정 규칙 (취소·반품·교환). 프레임워크에 의존하지 않는다.

- 금액·기간·횟수는 모두 코드가 주문 사실에서 계산한다. LLM과 요청 값을 쓰지 않는다.
  예외: 고객사 사이트가 자기 규칙으로 계산한 예상 환불(`SiteRefund`, 사이트 변환기 신청 받은함)은
  환불 금액으로 쓴다. 받은 금액 기준 환불 가능액을 넘지 않게 막는다.
- 규칙 값은 정책 문서(refund-1 수령 후 7일 단순 변심 / refund-2 불량·오배송 판매자 부담)의 시드다.
  memory 정책 문서 API가 생기면 거기서 읽도록 바꾼다.
- 결과 `outcome`: auto_rejected, needs_info, needs_review, approved_pending_return, auto_approved
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from support_agent.orders import Order

RETURN_WINDOW_DAYS = 7
AUTO_APPROVE_LIMIT = 50000  # 이 금액 이상은 사람이 본다
ABUSE_REVIEW_COUNT = 3  # 같은 고객의 이전 클레임이 이 수 이상이면 사람이 본다
ABUSE_POINTS_PER_CLAIM = 30

SELLER_FAULT = {"defect", "damaged", "wrong_item", "missing_item"}
Level = Literal["block", "review", "warn", "info"]


@dataclass(frozen=True)
class ReasonHit:
    code: str
    level: Level
    message: str
    rule: str | None = None


@dataclass(frozen=True)
class Line:
    order_item_id: str
    title: str
    qty: int
    requested_qty: int
    unit_price: int
    refund_cash: int
    points_back: int
    product_id: str | None = None  # 상품 ID. 옛 기록·시드 주문은 None


@dataclass(frozen=True)
class SiteRefund:
    """고객사 사이트가 자기 규칙(예: 여행 취소 수수료)으로 계산한 예상 환불.

    사이트 변환기 신청 받은함이 사이트 값을 그대로 옮긴 것이다. LLM·고객 글에서 오지 않는다.
    """

    refund: int  # 사이트 예상 환불액 (원)
    fee: int | None = None  # 사이트 수수료 (원)
    fee_rate: float | None = None  # 사이트 수수료율 (0.1 = 10%)


def site_rule_text(site: SiteRefund) -> str:
    """사람이 보는 사이트 규칙 근거. 숫자는 사이트 값 그대로 코드가 채운다."""
    parts = []
    if site.fee_rate is not None:
        parts.append(f"수수료율 {site.fee_rate * 100:g}%")
    if site.fee is not None:
        parts.append(f"수수료 {site.fee:,}원")
    parts.append(f"환불 {site.refund:,}원")
    return "사이트 규칙: " + ", ".join(parts)


@dataclass
class Decision:
    outcome: str
    resolution: str
    requires_return: bool
    reasons: list[ReasonHit] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    deductions: list[tuple[str, int, str]] = field(default_factory=list)  # (code, 금액, 설명)
    shipping_refund: int = 0
    expected_refund: int = 0
    points_back: int = 0
    abuse_score: int = 0


def _lines(order: Order, wanted: dict[int, int] | None) -> list[Line]:
    lines: list[Line] = []
    for item in order.items:
        requested = item.qty if wanted is None else wanted.get(item.id, 0)
        if requested <= 0:
            continue
        refundable = item.qty - item.refunded_qty
        qty = min(requested, refundable)
        unit = item.paid_amount // item.qty if item.qty else 0
        points = item.points_share * qty // item.qty if item.qty else 0
        lines.append(
            Line(item.id, item.title, qty, requested, unit, unit * qty, points, item.product_id)
        )
    return lines


def _apply_site_refund(decision: Decision, site: SiteRefund) -> int:
    """사이트 예상 환불액을 판정 금액으로 쓴다. 근거는 사실 값으로 남긴다.

    `decision.expected_refund`는 책 쇼핑몰 규칙 금액(받은 금액 기준 환불 가능액)이다. 사이트 값이
    그보다 크면 그 금액으로 막고 review로 사람에게 넘긴다(받은 것보다 많이 내보내지 않는다).
    """
    limit = decision.expected_refund
    decision.reasons.append(
        ReasonHit("SITE_REFUND_RULE", "info", site_rule_text(site), "site_rule")
    )
    if site.refund > limit:
        decision.reasons.append(
            ReasonHit(
                "SITE_REFUND_OVER_PAID",
                "review",
                f"사이트 환불액 {site.refund:,}원이 받은 금액 기준 환불 가능액 {limit:,}원보다 많아"
                f" {limit:,}원으로 막았다",
                "site_rule",
            )
        )
        return limit
    if site.refund < limit:
        decision.deductions.append(
            (
                "SITE_CANCEL_FEE",
                limit - site.refund,
                "사이트 규칙 수수료 (" + site_rule_text(site) + ")",
            )
        )
    return site.refund


def decide(
    order: Order,
    claim_type: str,
    reason: str,
    *,
    items: dict[str, int] | None,
    evidence_count: int,
    unsealed: bool,
    prior_claims: int,
    now: datetime,
    site_refund: SiteRefund | None = None,
) -> Decision:
    """주문 사실과 요청(유형·사유·품목)으로 판정한다.

    `site_refund`가 있으면(고객사 사이트가 자기 규칙으로 계산한 예상 환불) 환불 금액은 그 값이다.
    책 쇼핑몰 규칙으로 계산한 금액(받은 금액 기준 환불 가능액)을 넘으면 그 금액으로 막고 사람이
    보게 한다(`SITE_REFUND_OVER_PAID`, review). 없으면 책 쇼핑몰 규칙 그대로다.
    """
    requires_return = claim_type in ("return", "exchange")
    lines = _lines(order, items)
    decision = Decision(
        outcome="",
        resolution={"cancel": "취소", "return": "환불", "exchange": "교환"}.get(claim_type, ""),
        requires_return=requires_return,
        lines=lines,
        abuse_score=prior_claims * ABUSE_POINTS_PER_CLAIM,
    )
    hits = decision.reasons

    if not lines or all(line.qty == 0 for line in lines):
        hits.append(ReasonHit("NOTHING_REFUNDABLE", "block", "처리할 수 있는 품목이 없다"))

    if claim_type == "cancel":
        if order.status in ("shipped", "delivered"):
            hits.append(
                ReasonHit(
                    "CANCEL_AFTER_SHIPPING", "block", "발송 후에는 취소할 수 없다", "cancel_window"
                )
            )
    elif order.delivered_at is None:
        hits.append(ReasonHit("NOT_DELIVERED", "block", "배송 완료 전에는 반품·교환할 수 없다"))
    else:
        days = (now - order.delivered_at).days
        if reason == "change_of_mind":
            if days > RETURN_WINDOW_DAYS:
                hits.append(
                    ReasonHit(
                        "RETURN_WINDOW",
                        "block",
                        f"수령 후 {RETURN_WINDOW_DAYS}일이 지났다 ({days}일)",
                        "refund-1",
                    )
                )
            elif order.shipping_fee:
                decision.deductions.append(
                    ("RETURN_SHIPPING", order.shipping_fee * 2, "단순 변심 왕복 배송비 고객 부담")
                )
            if unsealed:
                hits.append(
                    ReasonHit("UNSEALED", "review", "개봉 상품이라 상태 확인 필요", "refund-1")
                )
        elif reason in SELLER_FAULT:
            if evidence_count == 0:
                hits.append(
                    ReasonHit("EVIDENCE_REQUIRED", "info", "증빙 사진이 필요하다", "refund-2")
                )
            else:
                hits.append(
                    ReasonHit("DEFECT_EVIDENCE", "review", "사진 증빙 확인 필요", "refund-2")
                )
        else:
            hits.append(ReasonHit("OTHER_REASON", "review", "사유 확인이 필요하다"))

    if prior_claims >= ABUSE_REVIEW_COUNT:
        hits.append(
            ReasonHit("ABUSE_FREQUENT", "review", f"이전 클레임 {prior_claims}건 (잦은 요청)")
        )

    # 금액: 교환은 돈이 오가지 않는다. 판매자 귀책·취소는 배송비도 돌려준다
    if claim_type != "exchange":
        decision.shipping_refund = (
            order.shipping_fee if claim_type == "cancel" or reason in SELLER_FAULT else 0
        )
        deducted = sum(amount for _, amount, _ in decision.deductions)
        decision.expected_refund = max(
            sum(line.refund_cash for line in lines) + decision.shipping_refund - deducted, 0
        )
        if site_refund is not None:
            decision.expected_refund = _apply_site_refund(decision, site_refund)
        decision.points_back = sum(line.points_back for line in lines)
        if decision.expected_refund >= AUTO_APPROVE_LIMIT:
            hits.append(
                ReasonHit("HIGH_AMOUNT", "review", f"{AUTO_APPROVE_LIMIT}원 이상은 사람이 확인한다")
            )

    levels = {hit.level for hit in hits}
    if "block" in levels:
        decision.outcome = "auto_rejected"
    elif any(hit.code == "EVIDENCE_REQUIRED" for hit in hits):
        decision.outcome = "needs_info"
    elif "review" in levels:
        decision.outcome = "needs_review"
    elif requires_return:
        decision.outcome = "approved_pending_return"
    else:
        decision.outcome = "auto_approved"
    return decision
