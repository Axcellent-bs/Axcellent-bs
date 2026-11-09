"""campaign_effect 유입경로 기준 (#648): 이 캠페인 링크 주문만, 표시 없으면 unknown, 겹침은 따로.

실제 shop-api를 부르지 않는다 (가짜 커넥터).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from ax_contracts.schemas.data import CampaignEffectInput, PendingSource
from data_agent.connectors import CatalogProduct, ConnectorUnavailable
from data_agent.connectors.shop_api import AttributedOrder, ShopApi, ShopApiConnector, ShopOrder
from data_agent.skills import campaign_effect as ce
from data_agent.skills.data_sources import list_sources

START = datetime(2026, 10, 14, 14, tzinfo=UTC)
END = START + timedelta(hours=2)


def _att(oid: str, source: str | None, campaign: str | None, lines, status="paid"):  # noqa: ANN001, ANN202
    return AttributedOrder.model_validate({
        "order_id": oid, "created_at": START, "status": status, "src_source": source,
        "src_medium": "social" if source else None, "src_campaign": campaign,
        "items": [{"product_id": p, "qty": q, "unit_price": u} for p, q, u in lines],
    })  # fmt: skip


def _paid(oid: str) -> ShopOrder:
    return ShopOrder.model_validate({
        "order_id": oid, "status": "paid", "created_at": START,
        "items": [{"qty": 1, "unit_price": 1}], "payments": [{"amount": 1, "status": "succeeded"}],
    })  # fmt: skip


class Shop:
    def __init__(self, tagged, window=10, fail=False) -> None:  # noqa: ANN001
        self.tagged, self.window, self.fail = tagged, window, fail

    async def attributed_orders(self, start, end, campaign_id=None, source=None):  # noqa: ANN001, ANN201
        if self.fail:
            raise ConnectorUnavailable("FORBIDDEN", "data 토큰이 아님")
        return self.tagged

    async def orders(self, start, end):  # noqa: ANN001, ANN201
        return [_paid(f"w{i}") for i in range(self.window)]

    async def products(self):  # noqa: ANN201
        return [CatalogProduct(product_id="prd_0004", name="무지 노트 3권 세트")]


def _run(shop, campaign="cmp_1", products=None):  # noqa: ANN001, ANN202
    data = CampaignEffectInput(campaign_id=campaign, window_from=START, window_to=END,
                               products=products or [])  # fmt: skip
    pe = None
    if products:
        matched, unmatched = ce.match_products(products, asyncio.run(shop.products()))
        pe = ce.CampaignProductsEffect(status="unknown", unknown_reason="x", matched=matched,
                                       unmatched_products=unmatched)  # fmt: skip
    return asyncio.run(ce._attribution(shop, data, pe))


def test_only_this_campaign_paid_orders_by_source() -> None:
    shop = Shop([
        _att("a", "instagram", "cmp_1", [("prd_0004", 2, 7500)]),
        _att("b", "instagram", "cmp_1", [("prd_0001", 1, 18000)]),
        _att("c", "google", "cmp_1", [("prd_0001", 1, 18000)]),
        _att("d", "naver", "cmp_other", [("prd_0001", 1, 18000)]),  # 다른 캠페인: 안 셈
        _att("e", "google", "cmp_1", [("prd_0001", 1, 18000)], status="payment_failed"),
    ])  # fmt: skip
    a, both = _run(shop)
    assert a is not None and a.status == "known" and both is None
    assert (a.orders, a.gross_krw) == (3, 51000)
    assert [(r.source, r.orders, r.gross_krw) for r in a.by_source] == [
        ("instagram", 2, 33000), ("google", 1, 18000)]  # fmt: skip
    assert (a.window_orders, a.tagged_orders, a.tagged_share) == (10, 4, 0.4)  # 결제 완료 태그 4건
    assert a.note and "꼬리표" in a.note


def test_no_tags_or_errors_are_unknown_and_campaign_id_is_required() -> None:
    a, both = _run(Shop([]), products=["무지 노트 3권 세트"])
    assert a is not None and a.status == "unknown" and ce.NO_TAGS in (a.unknown_reason or "")
    assert both is not None and both.status == "unknown"
    failed, _ = _run(Shop([], fail=True))
    assert failed is not None
    assert "유입 주문을 읽지 못함 [FORBIDDEN]" in (failed.unknown_reason or "")
    assert _run(Shop([]), campaign=None) == (None, None)  # campaign_id가 없으면 칸 없음


def test_attribution_and_products_counts_only_the_matched_lines() -> None:
    shop = Shop([
        _att("a", "instagram", "cmp_1", [("prd_0004", 2, 7500), ("prd_0001", 1, 18000)]),
        _att("b", "instagram", "cmp_1", [("prd_0001", 1, 18000)]),
        _att("c", "naver", "cmp_other", [("prd_0004", 5, 7500)]),
    ])  # fmt: skip
    a, both = _run(shop, products=["무지 노트 3권 세트"])
    assert a is not None and a.orders == 2
    assert both is not None and both.status == "known" and both.matched == ["prd_0004"]
    assert (both.orders, both.units, both.gross_krw) == (1, 2, 15000)


def test_connector_splits_the_window_when_truncated() -> None:
    calls: list[tuple[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        calls.append((p["from"], p["to"]))
        whole = len(calls) == 1  # 처음(전체 구간)만 잘림
        order = {"order_id": f"o{len(calls)}", "created_at": p["from"], "status": "paid",
                 "items": [], "src_campaign": "cmp_1"}  # fmt: skip
        body = {"orders": [] if whole else [order], "truncated": whole}
        return httpx.Response(200, json={"data": body, "meta": {}})

    async def go() -> list[AttributedOrder]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            conn = ShopApiConnector(ShopApi("http://shop", token="t", client=client))
            return await conn.attributed_orders(START, END)

    got = asyncio.run(go())
    assert len(calls) == 3 and len(got) == 2  # 전체 1번 + 반씩 2번


def test_ad_data_is_a_pending_source_not_a_plan_source() -> None:
    out = asyncio.run(list_sources({}, prober=None)).output
    assert PendingSource(name="광고 데이터", reason="#647 광고 부분 대기") in out.pending_sources
    assert all(s.source_id != "ad_data" for s in out.sources)


def test_attribution_numbers_are_facts() -> None:
    shop = Shop([_att("a", "instagram", "cmp_1", [("prd_0004", 2, 7500)])])
    a, both = _run(shop, products=["prd_0004"])
    out = ce.CampaignEffectOutput(
        window_from=START, window_to=END, summary="",
        orders={"status": "unknown", "unknown_reason": "x"},
        revenue={"status": "unknown", "unknown_reason": "x"},
        coupons={"status": "unknown", "unknown_reason": "x"},
        attribution_effect=a, attribution_and_products=both,
    )  # fmt: skip
    labels = {f.label: f.value for f in ce.campaign_facts(out)}
    assert labels["유입 주문 (cmp_1)"] == 1 and labels["유입 판매액 · instagram"] == 15000
    assert labels["유입 + 관련 상품 판매액"] == 15000
    assert labels["유입 표시가 있는 주문 비율"] == 0.1
