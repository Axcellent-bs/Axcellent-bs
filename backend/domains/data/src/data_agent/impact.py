"""장애 영향 분석의 숫자 집계 (DEP-DATA-02).

영향 원천 숫자의 유일한 출처다. 숫자는 코드로만 계산하고 LLM을 쓰지 않는다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ax_contracts.data_access.shop_failed_orders import FailedOrder, ShopFailedOrdersResult

# 장애(결제 서버·결제사 쪽) 사유. 이 코드만 장애 피해로 센다 (shop-api `failure_code`)
FAULT_REASONS = frozenset(
    {
        "PG_UNAVAILABLE",  # shop-api 장애 주입(결제 서버 5xx)
        "PG_TIMEOUT",
        "PG_ERROR",
        "PG_5XX",
        "GATEWAY_TIMEOUT",
        "PAYMENT_GATEWAY_TIMEOUT",
        "TIMEOUT",
        "SERVER_ERROR",
        "NETWORK_ERROR",
    }
)
# 결제한 사람 쪽 사유. 장애와 무관하므로 장애 피해에서 뺀다
USER_SIDE_REASONS: dict[str, str] = {
    "PG_DECLINED": "카드 거절",
    "CARD_DECLINED": "카드 거절",
    "DECLINED": "카드 거절",
    "LIMIT_EXCEEDED": "한도 초과",
    "INSUFFICIENT_FUNDS": "잔액 부족",
    "CARD_EXPIRED": "카드 만료",
    "INVALID_CARD": "카드 정보 오류",
    "USER_CANCELED": "결제 취소",
}
# 실패 사유가 없다는 표시 (shop-api는 failure_code가 비면 "UNKNOWN"을 준다)
MISSING_REASONS = frozenset({"", "UNKNOWN"})


@dataclass(frozen=True)
class FailureSplit:
    """실패 결제를 장애 피해와 제외 대상으로 나눈 결과."""

    fault: list[FailedOrder]  # 장애로 실패한 결제 (`FAULT_REASONS`)
    excluded: dict[str, int]  # 사용자 쪽 사유 이름(카드 거절 등) → 건수
    missing_reason: int  # 실패 사유가 없는 결제 수 (있으면 장애인지 알 수 없다)
    unseen: dict[str, int] = field(default_factory=dict)  # 처음 보는 실패 코드 → 건수


def split_failures(result: ShopFailedOrdersResult) -> FailureSplit:
    """실패 사유로 장애 피해와 사용자 쪽 실패를 나눈다.

    장애 쪽 사유(`FAULT_REASONS`)만 장애로 세고, 사용자 쪽 사유(`USER_SIDE_REASONS`)는 뺀다.
    둘 다 아닌 처음 보는 코드와 사유가 없는 결제는 어느 쪽인지 알 수 없어 따로 센다
    (있으면 영향 분석은 숫자를 내지 않는다).
    """
    fault: list[FailedOrder] = []
    excluded: Counter[str] = Counter()
    unseen: Counter[str] = Counter()
    missing = 0
    for order in result.orders:
        code = (order.reason or "").strip().upper()
        if code in MISSING_REASONS:
            missing += 1
        elif code in USER_SIDE_REASONS:
            excluded[USER_SIDE_REASONS[code]] += 1
        elif code in FAULT_REASONS:
            fault.append(order)
        else:
            unseen[code] += 1
    return FailureSplit(
        fault=fault, excluded=dict(excluded), missing_reason=missing, unseen=dict(unseen)
    )


@dataclass(frozen=True)
class ImpactCounts:
    failed_order_count: int
    affected_customer_count: int
    damage_amount: int  # 정수 원


def aggregate_impact(result: ShopFailedOrdersResult | list[FailedOrder]) -> ImpactCounts:
    """실패 주문 수, 영향 고객 수(같은 buyer_id는 한 번), 피해 금액 합계를 계산한다.

    주문 목록(장애로 실패한 것만 고른 목록)도 받는다.
    금액이 음수인 주문이 있으면 ValueError를 낸다 (음수 피해 금액은 없다).
    """
    orders = result if isinstance(result, list) else result.orders
    for order in orders:
        if order.amount < 0:
            raise ValueError(f"negative amount: order_id={order.order_id}")
    return ImpactCounts(
        failed_order_count=len(orders),
        affected_customer_count=len({o.buyer_id for o in orders}),
        damage_amount=sum(o.amount for o in orders),
    )
