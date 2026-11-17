"""영향 분석이 시간 초과로 남은 결제(확인·환불 필요)를 따로 센다.

판매 서버 `GET /api/shop/failed-orders?status=timeout`(catbooks-demo 3a8a815)이 주는 결제다.
결제사 응답이 끊겨 승인됐는지 모르고 아직 다시 확인 전인 결제이고, 실패 주문·미결제·영향 고객과
섞지 않는다. 셀 수 없으면 숫자를 지어내지 않고 None + 근거에 이유 한 줄이다.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.data_access.shop_failed_orders import (
    FailedOrder,
    ShopFailedOrdersQuery,
    ShopFailedOrdersResult,
)
from ax_contracts.data_access.shop_metric_series import ShopMetricSeriesResult
from ax_contracts.schemas.data import ImpactAnalysisInput
from data_agent import explain
from data_agent.clients.shop_client import HttpShopClient
from data_agent.connectors.shop_api import ShopApi, ShopApiConnector, ShopUnavailable
from data_agent.impact import ImpactCounts
from data_agent.skills.impact_analysis import analyze_impact, impact_facts

EVAL = Path(__file__).resolve().parents[1] / "eval"
_NAME = "data_eval_fake_shop"  # test_eval_fake_shop·eval_world와 같은 이름
if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_NAME, EVAL / "fake_shop.py")
    assert _spec and _spec.loader
    sys.modules[_NAME] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules[_NAME])
fake_shop = sys.modules[_NAME]

T0 = datetime(2026, 10, 5, 1, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=30)
WINDOW = ImpactAnalysisInput(window_from=T0, window_to=T1, scope="payment")
BASE = "http://shop.test"


# ---------- 가짜 판매 서버 (판매 서버와 같은 규칙) ----------


def _world() -> Any:
    """구간 [T0, T0+30분) 안: 시간 초과 둘(21,000·27,000원)·장애 실패 하나(16,000원)·카드 거절 하나.
    구간 밖: 시간 초과 둘(시작 1분 전, 끝 시각 정각)."""
    clock = [T0 - timedelta(minutes=1)]
    shop = fake_shop.FakeShop(fake_shop.FakeShopDb(), now=lambda: clock[0])
    shop.seed()

    def pay(at: datetime, buyer: str, product: str, simulate: str | None = None) -> dict:
        clock[0] = at
        order = shop.create_order(buyer, [(product, 1)])
        return shop.pay(order["order_id"], "card", simulate)

    shop.inject_fault("lost_response")
    before = pay(T0 - timedelta(minutes=1), "buy_0009", "prd_0003")  # 구간 밖(앞)
    first = pay(T0 + timedelta(minutes=1), "buy_0001", "prd_0001")  # 18,000 + 배송 3,000
    second = pay(T0 + timedelta(minutes=2), "buy_0002", "prd_0002")  # 24,000 + 배송 3,000
    declined = pay(T0 + timedelta(minutes=4), "buy_0004", "prd_0003", "failure")  # 카드 거절
    at_end = pay(T1, "buy_0005", "prd_0003")  # 끝 시각 정각: 판매 서버는 [from, to)라 밖
    shop.release_fault()
    shop.inject_fault("failure")
    failed = pay(T0 + timedelta(minutes=3), "buy_0003", "prd_0003")  # 장애 실패 13,000 + 3,000
    shop.release_fault()
    for order in (before, first, second, at_end):
        assert order["status"] == "pending_payment"  # 시간 초과면 주문은 결제 대기로 둔다
        assert order["payments"][-1]["status"] == "timeout"
        assert order["payments"][-1]["failure_code"] == "PG_TIMEOUT"
    assert declined["status"] == failed["status"] == "payment_failed"
    return shop


def _connector(shop: Any, seen: list[httpx.Request] | None = None) -> HttpShopClient:
    transport = fake_shop.FakeShopTransport(shop, now=lambda: T1)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return transport.respond(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpShopClient(ShopApi(BASE, token="mock:data", client=client))


@pytest.mark.asyncio
async def test_two_timeouts_inside_the_window_are_counted_with_their_amount() -> None:
    shop = _world()
    seen: list[httpx.Request] = []
    connector = _connector(shop, seen)
    assert await connector.timeout_payments(T0, T1) == (2, 21_000 + 27_000)
    params = seen[-1].url.params
    assert seen[-1].url.path == "/api/shop/failed-orders" and params["status"] == "timeout"
    assert datetime.fromisoformat(params["from"]) == T0  # 영향 구간 그대로 보낸다
    assert datetime.fromisoformat(params["to"]) == T1


@pytest.mark.asyncio
async def test_impact_reports_timeouts_separately_and_keeps_the_other_numbers() -> None:
    shop = _world()
    result = await analyze_impact(WINDOW, "shop-demo", MockLLMClient(), shop=_connector(shop))
    out = result.output
    assert (out.timeout_count, out.timeout_amount) == (2, 48_000)
    # 실패 주문·영향 고객·미결제는 장애 실패 하나뿐이다 (시간 초과·카드 거절은 섞지 않는다)
    assert (out.failed_order_count, out.affected_customer_count) == (1, 1)
    assert out.affected_customer_ids == ["buy_0003"]
    assert out.damage_amount == out.unpaid_amount == 16_000
    assert [(c.category, c.failed_order_count, c.damage_amount) for c in out.by_category] == [
        ("book", 1, 16_000)
    ]
    lines = result.evidence.data_summary.split("\n")
    assert "시간 초과로 남은 결제 2건, 48000원" in lines


@pytest.mark.asyncio
async def test_timeouts_do_not_change_the_numbers_of_a_connector_without_them() -> None:
    shop = _world()
    with_timeouts = await analyze_impact(
        WINDOW, "shop-demo", MockLLMClient(), shop=_connector(shop)
    )

    class OldConnector(ShopApiConnector):  # 시간 초과를 모르는 커넥터 (같은 실패 주문)
        def __getattribute__(self, name: str) -> Any:
            if name == "timeout_payments":
                raise AttributeError(name)
            return super().__getattribute__(name)

    old = OldConnector(_connector(shop).api)
    without = await analyze_impact(WINDOW, "shop-demo", MockLLMClient(), connector=old)
    keys = (
        "failed_order_count",
        "affected_customer_count",
        "affected_customer_ids",
        "damage_amount",
        "unpaid_amount",
        "by_category",
        "excess_failed_order_count",
    )
    assert {k: getattr(with_timeouts.output, k) for k in keys} == {
        k: getattr(without.output, k) for k in keys
    }
    assert (without.output.timeout_count, without.output.timeout_amount) == (None, None)
    assert (
        "시간 초과로 남은 결제: 확인할 수 없음 (이 커넥터에 시간 초과 결제 조회가 없음)"
        in without.evidence.data_summary.split("\n")
    )


@pytest.mark.asyncio
async def test_fake_shop_rejects_an_unknown_status_like_the_shop_server() -> None:
    transport = fake_shop.FakeShopTransport(_world())
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        params = {"from": T0.isoformat(), "to": T1.isoformat()}
        bad = await client.get("/api/shop/failed-orders", params=params | {"status": "x"})
        assert bad.status_code == 422
        default = (await client.get("/api/shop/failed-orders", params=params)).json()["data"]
        same = await client.get("/api/shop/failed-orders", params=params | {"status": "failed"})
    assert default["orders"] == same.json()["data"]["orders"]
    assert [o["reason"] for o in default["orders"]] == ["PG_UNAVAILABLE", "PG_DECLINED"]


# ---------- 셀 수 없을 때: None + 이유 (숫자를 지어내지 않는다) ----------


def _row(reason: str, minute: int = 1) -> FailedOrder:
    return FailedOrder(
        order_id=f"ord_{reason}_{minute}",
        buyer_id="buy_0001",
        amount=10_000,
        category="book",
        failed_at=T0 + timedelta(minutes=minute),
        reason=reason,
    )


def _shop_api(timeout_response: httpx.Response) -> HttpShopClient:
    """실패 주문 하나(장애)를 주고, `status=timeout` 요청에는 `timeout_response`로 답한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if request.url.path.endswith("/metric-series"):
            empty = ShopMetricSeriesResult(metric=params["metric"], points=[])
            return httpx.Response(200, json={"data": empty.model_dump(mode="json"), "meta": {}})
        if params.get("status") == "timeout":
            return timeout_response
        failed = ShopFailedOrdersResult(orders=[_row("PG_UNAVAILABLE")], collected_at=T1)
        return httpx.Response(200, json={"data": failed.model_dump(mode="json"), "meta": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpShopClient(ShopApi(BASE, token="mock:data", client=client))


def _data(result: ShopFailedOrdersResult) -> httpx.Response:
    return httpx.Response(200, json={"data": result.model_dump(mode="json"), "meta": {}})


CANNOT_COUNT = [
    (  # 예전 판매 서버: status를 모르고 실패 주문을 그대로 준다
        _data(ShopFailedOrdersResult(orders=[_row("PG_UNAVAILABLE")], collected_at=T1)),
        "UNSUPPORTED",
        "판매 서버가 시간 초과 결제 조회를 지원하지 않음",
    ),
    (  # 시간 초과 줄과 실패 줄이 섞여도 예전 서버로 본다
        _data(ShopFailedOrdersResult(orders=[_row("PG_TIMEOUT"), _row("PG_DECLINED", 2)])),
        "UNSUPPORTED",
        "판매 서버가 시간 초과 결제 조회를 지원하지 않음",
    ),
    (  # status 값을 거절 (FastAPI 422, 오류 코드 없음)
        httpx.Response(422, json={"detail": [{"loc": ["query", "status"]}]}),
        "UNAVAILABLE",
        "shop-api 응답 422 (UNAVAILABLE)",
    ),
    (
        httpx.Response(422, json={"error": {"code": "INVALID_QUERY"}}),
        "INVALID_QUERY",
        "shop-api 응답 422 (INVALID_QUERY)",
    ),
    (  # 조회 한도에 걸려 잘림
        _data(ShopFailedOrdersResult(orders=[_row("PG_TIMEOUT")], truncated=True)),
        "TRUNCATED",
        "조회 한도 5000건에 걸려 잘려서 셀 수 없음",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("response", "code", "reason"), CANNOT_COUNT)
async def test_cannot_count_means_none_and_a_reason(
    response: httpx.Response, code: str, reason: str
) -> None:
    connector = _shop_api(response)
    with pytest.raises(ShopUnavailable) as caught:
        await connector.timeout_payments(T0, T1)
    assert (caught.value.code, str(caught.value)) == (code, reason)

    result = await analyze_impact(WINDOW, "shop-demo", MockLLMClient(), shop=_shop_api(response))
    out = result.output
    assert (out.timeout_count, out.timeout_amount) == (None, None)
    assert f"시간 초과로 남은 결제: 확인할 수 없음 ({reason})" in result.evidence.data_summary
    # 실패 주문 숫자는 그대로 낸다
    assert (out.failed_order_count, out.damage_amount, out.unpaid_amount) == (1, 10_000, 10_000)


@pytest.mark.asyncio
async def test_legacy_source_without_timeout_payments_gives_none() -> None:
    class LegacyShop:  # 예전 공급자 (실패 주문만 준다)
        async def list_failed_orders(self, query: ShopFailedOrdersQuery) -> ShopFailedOrdersResult:
            return ShopFailedOrdersResult(orders=[_row("PG_UNAVAILABLE")], collected_at=T1)

    result = await analyze_impact(WINDOW, "shop-demo", MockLLMClient(), shop=LegacyShop())
    assert (result.output.timeout_count, result.output.timeout_amount) == (None, None)
    assert result.output.failed_order_count == 1
    assert "시간 초과로 남은 결제: 확인할 수 없음" in result.evidence.data_summary


# ---------- 요청 모양 ----------


@pytest.mark.asyncio
async def test_default_failed_orders_request_has_no_status() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _data(ShopFailedOrdersResult())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = HttpShopClient(ShopApi(BASE, token="mock:data", client=client))
    await connector.failed_orders_raw(ShopFailedOrdersQuery(from_=T0, to=T1))
    await connector.failure_events(T0, T1)
    await connector.failed_orders_raw(ShopFailedOrdersQuery(from_=T0, to=T1, status="failed"))
    for request in seen:  # 기본 요청은 예전과 같다 (status를 붙이지 않는다)
        assert sorted(request.url.params) == ["from", "limit", "to"]
    assert len({str(request.url) for request in seen[::2]}) == 1  # failed를 적어도 같은 요청
    await connector.failed_orders_raw(ShopFailedOrdersQuery(from_=T0, to=T1, status="timeout"))
    assert seen[-1].url.params["status"] == "timeout"


# ---------- 설명 facts ----------


def test_timeout_numbers_are_allowed_in_the_explanation_only_when_counted() -> None:
    counts = ImpactCounts(failed_order_count=1, affected_customer_count=1, damage_amount=16_000)
    facts = impact_facts(counts, None, None, (2, 48_000))
    assert (facts["timeout_count"], facts["timeout_amount"]) == (2, 48_000)
    sentence = "실패 주문 1건, 시간 초과로 남은 결제 2건 48,000원입니다."
    assert explain._numbers_valid(sentence, facts)
    unknown = impact_facts(counts, None, None)
    assert "timeout_count" not in unknown and "timeout_amount" not in unknown
    assert not explain._numbers_valid(sentence, unknown)  # 세지 못했으면 그 숫자는 막는다
