"""장애 영향 분석의 숫자 집계 (DEP-DATA-02).

영향 원천 숫자의 유일한 출처다. 숫자는 코드로만 계산하고 LLM을 쓰지 않는다.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ax_contracts.data_access.shop_failed_orders import FailedOrder, ShopFailedOrdersResult
from data_agent import catalog
from data_agent.connectors import FailureEvent, FailureEvents, as_events

# 장애(결제 서버·결제사 쪽) 사유. 이 코드만 장애 피해로 센다 (shop-api `failure_code`).
# 코드 목록은 회사 카탈로그(`catalogs/<회사>.toml`의 지표 `fault_codes`)에 있다.
# 이 이름은 예전 코드가 읽던 값(시작할 때의 카탈로그)이고, 판정은 `catalog.fault_codes()`를 쓴다
FAULT_REASONS = catalog.fault_codes()
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

    fault: list[FailureEvent]  # 장애로 실패한 결제 (카탈로그 장애 쪽 코드)
    excluded: dict[str, int]  # 사용자 쪽 사유 이름(카드 거절 등) → 건수
    missing_reason: int  # 실패 사유가 없는 결제 수 (있으면 장애인지 알 수 없다)
    unseen: dict[str, int] = field(default_factory=dict)  # 처음 보는 실패 코드 → 건수


def split_failures(result: ShopFailedOrdersResult | FailureEvents | list) -> FailureSplit:
    """실패 사유로 장애 피해와 사용자 쪽 실패를 나눈다.

    장애 쪽 사유(`FAULT_REASONS`)만 장애로 세고, 사용자 쪽 사유(`USER_SIDE_REASONS`)는 뺀다.
    둘 다 아닌 처음 보는 코드와 사유가 없는 결제는 어느 쪽인지 알 수 없어 따로 센다
    (있으면 영향 분석은 숫자를 내지 않는다).

    커넥터의 실패 이벤트(`FailureEvents`·목록)와 shop-api 실패 주문 조회 결과를 모두 받는다.
    """
    fault: list[FailureEvent] = []
    excluded: Counter[str] = Counter()
    unseen: Counter[str] = Counter()
    missing = 0
    for order in as_events(result):
        code = (order.code or "").strip().upper()
        if code in MISSING_REASONS:
            missing += 1
        elif code in USER_SIDE_REASONS:
            excluded[USER_SIDE_REASONS[code]] += 1
        elif code in catalog.fault_codes():
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


def aggregate_impact(
    result: ShopFailedOrdersResult | list[FailedOrder] | list[FailureEvent],
) -> ImpactCounts:
    """실패 주문 수, 영향 고객 수(같은 buyer_id는 한 번), 피해 금액 합계를 계산한다.

    주문 목록(장애로 실패한 것만 고른 목록)도 받는다.
    금액이 음수인 주문이 있으면 ValueError를 낸다 (음수 피해 금액은 없다).
    """
    orders = as_events(result)
    for order in orders:
        if order.amount < 0:
            raise ValueError(f"negative amount: order_id={order.event_id}")
    return ImpactCounts(
        failed_order_count=len(orders),
        affected_customer_count=len({o.customer_id for o in orders}),
        damage_amount=sum(o.amount for o in orders),
    )


# ---------------------------------------------------------------- 초과분 (평소보다 더 실패한 건수)

EXCESS_DAYS = 7  # 지난 7일
EXCESS_MARGIN = timedelta(hours=1)  # 같은 시각 ±1시간
_BUCKET = timedelta(minutes=5)


def _floor_5m(ts: datetime) -> datetime:
    ts = ts.astimezone(UTC)
    return ts.replace(minute=ts.minute - ts.minute % 5, second=0, microsecond=0)


def usual_fault_failures(
    payments: dict[datetime, float], fault_times: list[datetime], window: timedelta
) -> float | None:
    """지난 7일 같은 시각 ±1시간의 평소 장애 쪽 실패를 구간 길이만큼으로 환산한다.

    `payments`는 5분 구간별 결제 수, `fault_times`는 그 기간 장애 쪽 실패 시각이다. 결제가 1건
    이상 기록된 5분만 평균에 넣는다(자료가 없는 시간을 실패 0건으로 세지 않는다). 그런 5분이
    하나도 없으면 None(확인할 수 없음)이다.
    """
    active = {_floor_5m(b) for b, n in payments.items() if n and n > 0}
    if not active:
        return None
    fails = sum(1 for t in fault_times if _floor_5m(t) in active)
    return fails / len(active) * (window / _BUCKET)


def excess_failures(window_faults: int, usual: float | None) -> int | None:
    """초과분 = 구간 장애 쪽 실패 − 평소 실패 (반올림, 0 미만이면 0). 평소를 모르면 None."""
    if usual is None:
        return None
    return max(0, math.floor(window_faults - usual + 0.5))
