"""영향 분석 초과분 (`excess_failed_order_count`): 장애 때문에 평소보다 더 실패한 건수.

- 평소 = 지난 7일 같은 시각 ±1시간의 장애 쪽 실패. 결제가 기록된 5분 평균 × 구간 길이
- 초과분 = 구간 장애 쪽 실패 − 평소 (반올림, 0 미만이면 0)
- 지난 7일 자료가 없으면 None(확인할 수 없음)
- summary에 "평소보다 N건 많음"
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ax_agent_template.llm import MockLLMClient
from ax_contracts.data_access.shop_failed_orders import (
    FailedOrder,
    ShopFailedOrdersQuery,
    ShopFailedOrdersResult,
)
from ax_contracts.schemas.data import ImpactAnalysisInput, ImpactAnalysisOutput
from data_agent.impact import excess_failures, usual_fault_failures
from data_agent.skills.impact_analysis import analyze_impact

FIVE = timedelta(minutes=5)
START = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
WINDOW = ImpactAnalysisInput(window_from=START, window_to=START + timedelta(minutes=30))


def _order(at: datetime, n: int, reason: str = "PG_UNAVAILABLE") -> FailedOrder:
    return FailedOrder(
        order_id=f"ord_{at:%m%d%H%M}_{n}",
        buyer_id=f"buy_{n}",
        amount=10000,
        category="book",
        failed_at=at,
        reason=reason,
    )


class FakeShop:
    """shop-api 흉내: 구간 실패 + 지난 7일 결제 수·실패."""

    def __init__(
        self,
        window_failures: int,
        *,
        history_noise_per_day: int = 0,
        history_payments: float | None = 10.0,
    ) -> None:
        self.window = [_order(START + timedelta(minutes=i), i) for i in range(window_failures)]
        self.history: list[FailedOrder] = []
        for day in range(1, 8):
            base = START - timedelta(days=day)
            for i in range(history_noise_per_day):  # 매일 같은 시각 근처의 타임아웃 잡음
                self.history.append(
                    _order(base + timedelta(minutes=7 + 10 * i), 100 * day + i, "PG_TIMEOUT")
                )
            # 카드 거절은 평소 장애 쪽 실패에 넣지 않는다
            self.history.append(_order(base + timedelta(minutes=3), 900 + day, "PG_DECLINED"))
        self.history_payments = history_payments

    async def list_failed_orders(self, query: ShopFailedOrdersQuery) -> ShopFailedOrdersResult:
        pool = self.window + self.history
        return ShopFailedOrdersResult(
            orders=[o for o in pool if query.from_ <= o.failed_at < query.to]
        )

    async def payment_counts(self, window_from: datetime, window_to: datetime) -> dict:
        if self.history_payments is None:  # 지난 7일 자료 없음 (결제 0건으로만 기록)
            value = 0.0
        else:
            value = self.history_payments
        out, ts = {}, window_from
        while ts < window_to:
            out[ts] = value
            ts += FIVE
        return out


def _run(shop: object) -> ImpactAnalysisOutput:
    result = asyncio.run(
        analyze_impact(WINDOW, "shop-demo", MockLLMClient(responses={}), shop=shop)
    )
    return result.output


def test_no_noise_excess_equals_window_failures() -> None:
    out = _run(FakeShop(6))
    assert out.failed_order_count == 6
    assert out.excess_failed_order_count == 6
    assert "평소보다 6건 많음" in out.summary


def test_noise_is_subtracted_as_usual_failures() -> None:
    # 지난 7일 매일 ±1시간(150분 = 5분 30개)에 타임아웃 3건 → 5분당 0.1건, 30분이면 평소 0.6건
    out = _run(FakeShop(6, history_noise_per_day=3))
    assert out.failed_order_count == 6
    assert out.excess_failed_order_count == 5  # 6 − 0.6 = 5.4 → 5
    assert "평소보다 5건 많음" in out.summary


def test_no_history_is_null() -> None:
    out = _run(FakeShop(6, history_payments=None))
    assert out.excess_failed_order_count is None
    assert "평소보다" not in out.summary


def test_client_without_payment_counts_is_null() -> None:
    class Plain:
        async def list_failed_orders(self, query: ShopFailedOrdersQuery) -> ShopFailedOrdersResult:
            return ShopFailedOrdersResult(orders=[_order(START, 1)])

    assert _run(Plain()).excess_failed_order_count is None


def test_below_usual_is_zero() -> None:
    # 평소(5분당 1건 × 6 = 6건)보다 적게 실패하면 0
    out = _run(FakeShop(2, history_noise_per_day=30))
    assert out.failed_order_count == 2
    assert out.excess_failed_order_count == 0
    assert "평소보다 0건 많음" in out.summary


def test_usual_counts_only_buckets_with_payments() -> None:
    # 결제가 기록된 5분만 평균에 넣는다 (자료 없는 시간을 실패 0건으로 세지 않음)
    active = {START: 4.0, START + FIVE: 0.0, START + 2 * FIVE: 3.0}
    usual = usual_fault_failures(active, [START, START + 2 * FIVE], timedelta(minutes=10))
    assert usual == 2.0  # 결제 있는 5분 2개에 실패 2건 → 5분당 1건 × 2
    assert usual_fault_failures({START: 0.0}, [], timedelta(minutes=10)) is None
    assert excess_failures(3, 0.5) == 3  # 2.5 → 3 (반올림)
    assert excess_failures(3, None) is None
