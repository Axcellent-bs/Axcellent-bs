"""campaign_effect 상품 기준(products_effect, #646): 맞은 상품의 품목만, 이름은 정확히 같을 때만.

실제 shop-api를 부르지 않는다 (가짜 커넥터).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ax_contracts.schemas.data import CampaignChange, CampaignEffectInput, CampaignEffectOutput
from data_agent.connectors import CatalogProduct, ConnectorUnavailable
from data_agent.connectors.shop_api import ShopOrder
from data_agent.skills import campaign_effect as ce

START = datetime(2026, 10, 14, 14, tzinfo=UTC)
END = START + timedelta(hours=2)
CATALOG = [
    CatalogProduct(product_id="prd_0004", name="무지 노트 3권 세트"),
    CatalogProduct(product_id="prd_0007", name="소설 베스트 3종"),
    CatalogProduct(product_id="prd_0001", name="파이썬 입문서"),
]


def _order(oid: str, lines: list[tuple[str, int, int]], *, paid: bool = True) -> ShopOrder:
    gross = sum(q * p for _, q, p in lines)
    return ShopOrder.model_validate({
        "order_id": oid, "status": "paid" if paid else "pending_payment", "created_at": START,
        "items": [{"product_id": pid, "qty": q, "unit_price": p} for pid, q, p in lines],
        "payments": [{"amount": gross, "status": "succeeded"}] if paid else [],
    })  # fmt: skip


class FakeShop:
    def __init__(self, by_day: dict[int, list[ShopOrder]], fail_products: bool = False) -> None:
        self.by_day = by_day
        self.fail_products = fail_products

    async def orders(self, start: datetime, end: datetime) -> list[ShopOrder]:
        return self.by_day.get(round((START - start).total_seconds() / 86400), [])

    async def products(self) -> list[CatalogProduct]:
        if self.fail_products:
            raise ConnectorUnavailable("UNAVAILABLE", "down")
        return CATALOG


def _spans(n: int) -> list[ce.Span]:
    return [ce.Span(f"{d}일 전", START - timedelta(days=d), END - timedelta(days=d), {}, None, True)
            for d in range(1, n + 1)]  # fmt: skip


ORDERS = CampaignChange(status="known", direction="up", excess=10.0, baseline_days=3)


def _run(shop, products, spans=3, orders=ORDERS):  # noqa: ANN001, ANN202
    data = CampaignEffectInput(window_from=START, window_to=END, products=products)
    return asyncio.run(ce._products_effect(shop, data, orders, _spans(spans), ce.RULE_SHORT))


def _days() -> dict[int, list[ShopOrder]]:
    return {
        0: [  # 노트 2건(수량 3), 다른 상품만 든 주문, 노트 + 다른 상품, 결제 대기 노트
            _order("a", [("prd_0004", 1, 7500)]),
            _order("b", [("prd_0001", 1, 18000)]),
            _order("c", [("prd_0004", 2, 7500), ("prd_0001", 1, 18000)]),
            _order("d", [("prd_0004", 5, 7500)], paid=False),
        ],
        1: [_order("e", [("prd_0004", 1, 7500)])],
        2: [],
        3: [_order("f", [("prd_0001", 1, 18000)])],
    }


def test_only_matched_product_lines_of_paid_orders_count() -> None:
    p = _run(FakeShop(_days()), ["prd_0004"])
    assert p is not None and p.status == "known"
    assert [m.product_id for m in p.matched] == ["prd_0004"] and p.unmatched_products == []
    assert p.orders and p.units and p.gross_krw
    assert (p.orders.period, p.units.period, p.gross_krw.period) == (2, 3, 22500)  # 18000 안 셈
    assert p.gross_krw.baseline == round(7500 / 3, 4) and p.baseline_days == 3
    assert (p.gross_krw.incremental_low, p.gross_krw.incremental_high) == (15000, 22500)
    assert p.gross_krw.direction == "up" and "판매액이" in (p.gross_krw.direction_reason or "")


def test_names_match_only_when_normalized_names_are_equal() -> None:
    matched, unmatched = ce.match_products(
        [" 무지노트 3권세트", "소설 베스트 3종", "무지 노트", "prd_0007", "없는 상품"], CATALOG
    )
    assert [m.product_id for m in matched] == ["prd_0004", "prd_0007"]  # 같은 상품은 한 번
    assert matched[0].asked == " 무지노트 3권세트" and matched[0].name == "무지 노트 3권 세트"
    assert unmatched == ["무지 노트", "없는 상품"]  # 일부만 같은 이름은 추측하지 않는다


def test_no_match_gap_short_baseline_and_errors_are_unknown_with_reasons() -> None:
    none = _run(FakeShop(_days()), ["무지 노트"])
    assert none is not None and none.status == "unknown"
    assert ce.NO_MATCH in (none.unknown_reason or "") and none.unmatched_products == ["무지 노트"]
    gap = _run(FakeShop({0: [_order("x", [("prd_0004", 1, 7500)], paid=False)]}), ["prd_0004"])
    assert gap is not None and "결제 기록 공백" in (gap.unknown_reason or "")
    short = _run(FakeShop(_days()), ["prd_0004"], spans=2)
    assert short is not None and short.status == "unknown"
    assert "기준선 부족" in (short.unknown_reason or "") and short.orders is not None
    assert short.orders.period == 2 and short.orders.incremental is None  # 기간값만
    blocked = CampaignChange(status="unknown", unknown_reason="구간 주문·결제 기록이 하나도 없음")
    same = _run(FakeShop(_days()), ["prd_0004"], orders=blocked)
    assert same is not None and (same.unknown_reason or "").startswith("결제 기록 공백")
    failed = _run(FakeShop(_days(), fail_products=True), ["prd_0004"])
    assert failed is not None and "상품 목록을 읽지 못함" in (failed.unknown_reason or "")


def test_no_products_keeps_the_old_response() -> None:
    assert _run(FakeShop(_days()), []) is None


def test_product_numbers_are_facts() -> None:
    p = _run(FakeShop(_days()), ["prd_0004"])
    out = CampaignEffectOutput(
        window_from=START, window_to=END, orders=ORDERS,
        revenue=CampaignChange(status="unknown", unknown_reason="매출 지표 없음"),
        coupons={"status": "unknown", "unknown_reason": "campaign_id 없음"}, summary="",
        products_effect=p,
    )  # fmt: skip
    facts = ce.campaign_facts(out)
    labels = {f.label: f.value for f in facts}
    assert labels["상품 기준 판매액 구간 (무지 노트 3권 세트)"] == 22500
    assert labels["상품 기준 주문 수 증분 상한 (무지 노트 3권 세트)"] == 2
    assert labels["상품 기준 증분 판매액(원)"] == round(22500 - 7500 / 3)
    assert all(f.fact_id for f in facts)
    summary = ce._facts(out)
    assert summary["상품 기준 판매액 방향"] == "늘었음"
