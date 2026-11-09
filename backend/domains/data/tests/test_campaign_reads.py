"""campaign_effect 주문 읽기 (#705): 한 시간씩 나눠 읽기, 같은 구간은 한 번만, 상품 목록 먼저.

실제 shop-api를 부르지 않는다 (가짜 커넥터·MockTransport).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ax_contracts.schemas.data import CampaignEffectInput
from data_agent.connectors import CatalogProduct, ConnectorUnavailable
from data_agent.connectors.shop_api import ORDERS_CHUNK, ShopApi, ShopApiConnector, ShopOrder
from data_agent.explain import _campaign_template
from data_agent.skills import campaign_effect as ce

START = datetime(2026, 10, 14, 14, tzinfo=UTC)
END = START + timedelta(hours=2)
STEP = timedelta(minutes=5)


def _row(oid: str, at: datetime) -> dict[str, Any]:
    return {"order_id": oid, "status": "paid", "created_at": at.isoformat(),
            "items": [{"product_id": "prd_0004", "qty": 1, "unit_price": 7500}],
            "payments": [{"amount": 7500, "status": "succeeded"}]}  # fmt: skip


def _connector(handler: Any) -> ShopApiConnector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ShopApiConnector(ShopApi("http://shop", token="t", client=client))


def test_orders_are_read_one_hour_at_a_time_and_counted_once() -> None:
    seen: list[tuple[datetime, datetime]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        since, until = datetime.fromisoformat(p["since"]), datetime.fromisoformat(p["until"])
        seen.append((since, until))
        rows = [_row(f"o{since:%H}", since), _row("same", START)]  # 모든 조각에 같은 주문 하나
        return httpx.Response(200, json={"data": rows})

    end = START + timedelta(hours=2, minutes=30)
    got = asyncio.run(_connector(handler).orders(START, end))
    assert len(seen) == 3 and all(b - a <= ORDERS_CHUNK for a, b in seen)
    assert seen[0][0] == START and seen[-1][1] == end
    assert sorted(o.order_id for o in got) == ["o14", "o15", "o16", "same"]


def test_a_timeout_says_why_instead_of_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    try:
        asyncio.run(_connector(handler).products())
    except ConnectorUnavailable as error:
        reason = ce.read_error(error)
    assert "10초 안에 답하지 않음" in reason and "[UNAVAILABLE]" not in reason
    assert ce.read_error(ConnectorUnavailable("FORBIDDEN", "x")) == "[FORBIDDEN]"


class Shop:
    """지표는 날마다 100건, 주문·상품 읽기를 순서대로 기록한다."""

    def __init__(self, fail_products: bool = False) -> None:
        self.log: list[str] = []
        self.fail_products = fail_products

    async def metric_series(self, metric_id, window_from, window_to, bucket="5m"):  # noqa: ANN001, ANN201
        if metric_id != "order_count":
            return []
        slots = int((window_to - window_from) / STEP)
        return [(window_from + STEP * i, 100 / slots) for i in range(slots)]

    async def transactions_volume(self, window_from, window_to):  # noqa: ANN001, ANN201
        return None

    async def campaign_coupons(self, campaign_id: str) -> list:
        return []

    async def orders(self, start: datetime, end: datetime) -> list[ShopOrder]:
        self.log.append(f"orders {(START - start).days}")
        return [ShopOrder.model_validate(_row(f"o{start:%d}", start))]

    async def products(self) -> list[CatalogProduct]:
        self.log.append("products")
        if self.fail_products:
            raise ConnectorUnavailable("UNAVAILABLE", "shop-api가 10초 안에 답하지 않음")
        return [CatalogProduct(product_id="prd_0004", name="무지 노트 3권 세트")]

    async def attributed_orders(self, start, end, campaign_id=None, source=None):  # noqa: ANN001, ANN201
        self.log.append("attributed")
        return []


def _measure(shop: Shop) -> Any:
    data = CampaignEffectInput(campaign_id="cmp_1", window_from=START, window_to=END,
                               products=["prd_0004"])  # fmt: skip
    return asyncio.run(ce.measure(data, shop))


def test_products_first_and_each_span_of_orders_is_read_once() -> None:
    shop = Shop()
    out = _measure(shop)
    assert shop.log[0] == "products" and shop.log.count("products") == 1
    reads = [x for x in shop.log if x.startswith("orders")]
    assert len(reads) == len(set(reads))  # 판매액·상품 기준·유입 기준이 같은 구간을 다시 읽지 않음
    assert "orders 0" in reads
    assert out.sales is not None and out.sales.status == "known"
    assert out.products_effect is not None and out.products_effect.matched


def test_product_read_failure_is_unknown_with_the_reason() -> None:
    shop = Shop(fail_products=True)
    out = _measure(shop)
    assert shop.log.count("products") == 1  # 실패도 한 번만 묻는다
    p = out.products_effect
    assert p is not None and p.status == "unknown"
    assert p.unknown_reason == "상품 목록을 읽지 못함 (shop-api가 10초 안에 답하지 않음)"


def test_summary_says_the_order_count_is_the_whole_store() -> None:
    facts = {"주문 방향": "늘었음", "캠페인 기간 가게 전체 주문": 83191, "평소 주문": 65901.5,
             "주문 증분": 17289.5, "기준 일수": 7}  # fmt: skip
    assert "캠페인 기간 가게 전체 주문 83,191건" in _campaign_template(facts)
