"""campaign_effect 판매액(sales): 결제 완료 주문만, 증분 범위로 방향, 공백·쿠폰 귀속 이유.

실제 shop-api를 부르지 않는다 (가짜 커넥터).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ax_contracts.schemas.data import CampaignChange, CampaignEffectInput
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.shop_api import CampaignCoupon, ShopOrder
from data_agent.skills import campaign_effect as ce

START = datetime(2026, 10, 14, 14, tzinfo=UTC)
END = START + timedelta(hours=2)


def _order(oid: str, at: datetime, price: int, *, paid: bool = True, coupon: str | None = None,
           discount: int = 0, refund: int = 0) -> ShopOrder:  # fmt: skip
    return ShopOrder.model_validate({
        "order_id": oid, "status": "paid" if paid else "pending_payment", "created_at": at,
        "coupon_id": coupon, "coupon_discount": discount, "refunded_amount": refund,
        "items": [{"qty": 1, "unit_price": price}],
        "payments": [{"amount": price - discount, "status": "succeeded"}] if paid else [],
    })  # fmt: skip


class FakeShop:
    def __init__(self, by_day: dict[int, list[ShopOrder]], coupons=None, fail=False) -> None:  # noqa: ANN001
        self.by_day = by_day
        self.coupons = coupons or []
        self.fail = fail

    async def orders(self, start: datetime, end: datetime) -> list[ShopOrder]:
        if self.fail:
            raise ConnectorUnavailable("UNAVAILABLE", "down")
        days = round((START - start).total_seconds() / 86400)
        return self.by_day.get(days, [])

    async def campaign_coupons(self, campaign_id: str) -> list[CampaignCoupon]:
        return self.coupons


def _spans(n: int) -> list[ce.Span]:
    return [ce.Span(f"{d}일 전", START - timedelta(days=d), END - timedelta(days=d), {}, None, True)
            for d in range(1, n + 1)]  # fmt: skip


ORDERS_UP = CampaignChange(status="known", direction="up", excess=10.0, baseline_days=3)


def _run(shop, spans=3, campaign_id=None, orders=ORDERS_UP):  # noqa: ANN001, ANN202
    data = CampaignEffectInput(campaign_id=campaign_id, window_from=START, window_to=END)
    return asyncio.run(ce._sales(shop, data, orders, _spans(spans), ce.RULE_SHORT))


def test_paid_orders_only_and_incremental_range() -> None:
    shop = FakeShop({
        0: [_order("a", START, 30000, discount=3000, refund=1000), _order("b", START, 20000),
            _order("c", START, 99999, paid=False)],  # 결제 대기는 세지 않는다
        1: [_order("d", START, 10000)],
        2: [_order("e", START, 20000)],
        3: [_order("f", START, 30000)],
    })  # fmt: skip
    s = _run(shop)
    assert s.status == "known" and s.period is not None and s.baseline is not None
    assert (s.period.orders, s.period.gross_krw, s.period.discount_krw) == (2, 50000, 3000)
    assert (s.period.paid_krw, s.period.refund_krw) == (47000, 1000)
    assert s.baseline.gross_krw == 20000 and s.baseline_days == 3
    assert (s.incremental_gross_krw, s.incremental_low_krw, s.incremental_high_krw) == (
        30000, 20000, 40000)  # fmt: skip
    assert s.direction == "up" and "주문 수 판정: 늘었음" in (s.direction_reason or "")


def test_overlapping_range_or_one_day_is_uncertain_and_below_is_down(monkeypatch) -> None:  # noqa: ANN001
    # 기준선 3일 (최소 3일): 범위에 걸치면 확실하지 않음, 가장 적은 날보다 적으면 줄었음
    three = {1: [_order("d", START, 10000)], 2: [_order("e", START, 30000)],
             3: [_order("f", START, 20000)]}  # fmt: skip
    mid = FakeShop({0: [_order("a", START, 25000)], **three})
    assert _run(mid, spans=3).direction == "uncertain"
    low = FakeShop({0: [_order("a", START, 5000)], **three})
    assert _run(low, spans=3).direction == "down"
    # 최소를 1일로 낮춘 회사 설정: 기준선 1일이면 범위를 낼 수 없어 확실하지 않음
    monkeypatch.setenv(ce.MIN_BASELINE_ENV, "1")
    one = FakeShop({0: [_order("a", START, 90000)], 1: [_order("d", START, 10000)]})
    assert _run(one, spans=1).direction == "uncertain"


def test_short_baseline_keeps_period_sales_but_not_increments(monkeypatch) -> None:  # noqa: ANN001
    coupons = [CampaignCoupon(coupon_id="cpn_1", count=3, used_units=1)]
    shop = FakeShop({0: [_order("a", START, 30000, coupon="cpn_1")], 1: [_order("d", START, 10000)],
                     2: [_order("e", START, 20000)]}, coupons=coupons)  # fmt: skip
    s = _run(shop, spans=2, campaign_id="cmp_1")
    assert s.status == "unknown" and s.unknown_reason == "기준선 부족(2일, 최소 3일)"
    assert s.period is not None and s.period.gross_krw == 30000  # 기간 판매액은 그대로
    assert s.baseline is not None and s.baseline_days == 2
    assert (s.incremental_gross_krw, s.incremental_low_krw, s.incremental_high_krw) == (
        None, None, None)  # fmt: skip
    assert s.direction is None and s.orders_direction == "up"
    assert s.direct_attributed_krw == 30000  # 쿠폰 귀속은 기간 숫자라 준다
    monkeypatch.setenv(ce.MIN_BASELINE_ENV, "2")  # 회사 설정으로 최소를 바꿀 수 있다
    assert _run(shop, spans=2, campaign_id="cmp_1").status == "known"
    for bad in ("0", "x"):
        monkeypatch.setenv(ce.MIN_BASELINE_ENV, bad)
        assert ce.min_baseline_days() == 3


def test_gaps_and_unknowns_say_why_without_zeros() -> None:
    gap = CampaignChange(status="unknown", unknown_reason="기록 공백 의심 (12칸): 결제 시도 0인 …")
    s = _run(FakeShop({}), orders=gap)
    assert s.status == "unknown" and s.unknown_reason.startswith("결제 기록 공백: 기록 공백 의심")
    assert s.incremental_gross_krw is None and s.period is None
    unpaid = FakeShop({0: [_order("a", START, 1000, paid=False)], 1: [_order("d", START, 1000)]})
    u = _run(unpaid, spans=1)
    assert u.status == "unknown" and "결제 대기" in (u.unknown_reason or "")
    down = _run(FakeShop({}, fail=True))
    assert down.status == "unknown" and "UNAVAILABLE" in (down.unknown_reason or "")

    class NoOrders:  # 주문 금액을 주지 않는 커넥터
        pass

    no = asyncio.run(ce._sales(NoOrders(), CampaignEffectInput(window_from=START, window_to=END),
                               ORDERS_UP, _spans(1), ce.RULE_SHORT))  # fmt: skip
    assert no.status == "unknown" and "주문 금액을 주지 않음" in (no.unknown_reason or "")


def test_coupon_attribution_sums_campaign_coupon_orders() -> None:
    coupons = [CampaignCoupon(coupon_id="cpn_1", count=3, used_units=1)]
    shop = FakeShop({0: [_order("a", START, 30000, coupon="cpn_1", discount=1000),
                         _order("b", START, 20000, coupon="cpn_other")],
                     1: [_order("d", START, 10000)]}, coupons=coupons)  # fmt: skip
    s = _run(shop, spans=1, campaign_id="cmp_1")
    assert (s.direct_attributed_krw, s.direct_orders, s.direct_reason) == (30000, 1, None)
    none = _run(shop, spans=1)
    assert none.direct_attributed_krw is None and "campaign_id가 없어" in (none.direct_reason or "")
    empty = _run(FakeShop({0: [_order("a", START, 1)], 1: [_order("d", START, 1)]}), spans=1,
                 campaign_id="cmp_x")  # fmt: skip
    assert empty.direct_attributed_krw is None and "쿠폰이 없음" in (empty.direct_reason or "")


def test_facts_carry_source_period_and_sample() -> None:
    shop = FakeShop({0: [_order("a", START, 30000)], 1: [_order("d", START, 10000)],
                     2: [_order("e", START, 20000)], 3: [_order("f", START, 15000)]})  # fmt: skip
    data = CampaignEffectInput(window_from=START, window_to=END)

    def out_of(spans: int) -> ce.CampaignEffectOutput:
        return ce.CampaignEffectOutput(
            window_from=START, window_to=END, orders=ORDERS_UP,
            revenue=CampaignChange(status="unknown", unknown_reason="x"),
            coupons=ce.CampaignCoupons(status="unknown", unknown_reason="x"), summary="",
            sales=asyncio.run(ce._sales(shop, data, ORDERS_UP, _spans(spans), ce.RULE_SHORT)),
        )  # fmt: skip

    facts = {f.label: f for f in ce.sales_facts(out_of(3))}
    gross = facts["증분 판매액 (주문 금액)"]
    assert gross.value == 15000 and gross.unit == "KRW" and gross.source == "shop-api"
    assert gross.sample == 3 and gross.period == "2026-10-14T14:00Z~2026-10-14T16:00Z"
    assert facts["구간 결제 완료 주문"].unit == "건"
    short = {f.label for f in ce.sales_facts(out_of(2))}  # 기준선 부족: 기간 숫자만, 0으로 안 채움
    assert "구간 주문 금액 합 (할인 전)" in short and "증분 판매액 (주문 금액)" not in short
