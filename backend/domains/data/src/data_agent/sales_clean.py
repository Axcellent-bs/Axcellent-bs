"""판매 기록 정제 규칙 (코드). 수집기가 저장하기 전에, 분석이 매출을 셀 때 쓴다.

- 금액이 음수인 주문(상품 금액·배송비·포인트·쿠폰 할인)은 주문째 버린다
- 상품 줄: 수량이 0 이하이거나 금액(단가·쿠폰 몫·포인트 몫)이 음수면 그 줄만 버린다
- 환불: 금액이 음수면 그 환불만 버린다
- 버린 것은 (종류, 위치, 이유)로 남겨 품질 보고에서 센다
- 매출에 넣는 주문: 성공한 결제가 있고(`payment_status == succeeded`) 취소·결제 실패·결제 대기
  상태가 아닌 주문. 취소된 주문은 환불도 매출에서 빼지 않는다(주문째 빠지므로 두 번 빼지 않게)
- 시각은 UTC 그대로 둔다. 한국 시간으로 바꾸는 것은 집계할 때(`sales_period`)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRefund, SalesOrderRow

NEGATIVE = "금액이 음수"
NON_POSITIVE_QTY = "수량이 0 이하"

# 매출에서 빼는 주문 상태 (결제가 성공했어도 뺀다). 철자가 다른 회사도 받는다
EXCLUDED_STATUSES = frozenset(
    {"cancelled", "canceled", "payment_failed", "pending_payment", "refunded_full"}
)
PAID = "succeeded"


@dataclass(frozen=True)
class Dropped:
    kind: str  # order | line | refund
    ref: int  # 줄·환불 순번 (주문은 0)
    reason: str


@dataclass
class Cleaned:
    order: SalesOrderRow | None  # None = 주문째 버림
    lines: list[SalesOrderLine] = field(default_factory=list)
    refunds: list[SalesOrderRefund] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)


def clean(row: SalesOrderRow) -> Cleaned:
    if min(row.items_amount, row.shipping_fee, row.points_used, row.coupon_discount) < 0:
        return Cleaned(order=None, dropped=[Dropped("order", 0, NEGATIVE)])
    result = Cleaned(order=row)
    for i, line in enumerate(row.lines):
        if line.qty <= 0:
            result.dropped.append(Dropped("line", i, NON_POSITIVE_QTY))
        elif min(line.unit_price, line.coupon_share, line.points_share) < 0:
            result.dropped.append(Dropped("line", i, NEGATIVE))
        else:
            result.lines.append(line)
    for i, refund in enumerate(row.refunds):
        if refund.amount < 0:
            result.dropped.append(Dropped("refund", i, NEGATIVE))
        else:
            result.refunds.append(refund)
    return result


def counts_as_revenue(status: str, payment_status: str | None) -> bool:
    """매출에 넣는 주문인지 (결제 완료, 취소·결제 실패·대기 아님)."""
    return payment_status == PAID and status.lower() not in EXCLUDED_STATUSES
