"""구매자별 주문 목록 skill(`list_buyer_orders`): 계정에 연결한 구매자의 주문만 읽는다."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from support_agent import claim_handlers, handlers, shop_client
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.store import InquiryStore


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())
    yield
    shop_client.use_client(None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def call(
    client: TestClient, input: dict[str, Any], requester: str = "orchestrator"
) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_req_0001",
            "token": f"mock:{requester}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "list_buyer_orders",
        "input": input,
        "untrusted": {},
    }
    return client.post("/a2a", json=body).json()


def shop_order(order_id: str, buyer: str, paid_at: str, **changes: Any) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "buyer_id": buyer,
        "status": "paid",
        "created_at": paid_at,
        "shipping_fee": 3000,
        "payments": [
            {"payment_id": f"pay_{order_id}", "status": "succeeded", "created_at": paid_at}
        ],
        "items": [
            {
                "order_item_id": f"{order_id}_1",
                "title": "고양이의 하루",
                "qty": 1,
                "paid_amount": 15000,
            },
            {
                "order_item_id": f"{order_id}_2",
                "title": "탁상 달력",
                "qty": 2,
                "paid_amount": 20000,
            },
        ],
        **changes,
    }


def fake_shop(
    orders: list[dict[str, Any]], seen: list[Any] | None = None, honor_filter: bool = True
) -> None:
    """판매 서버 주문 목록·단건 읽기를 흉내 낸다. `honor_filter=False`면 조건을 무시한다."""

    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((request.url.path, dict(request.url.params)))
        path = request.url.path
        if path == "/api/shop/orders":
            buyer = request.url.params.get("buyer_id")
            rows = [o for o in orders if not honor_filter or o["buyer_id"] == buyer]
            # 목록 줄에는 구매자가 없을 수 있다
            lite = [{"order_id": o["order_id"], "status": o["status"]} for o in rows]
            return httpx.Response(200, json={"data": lite, "meta": {"next_cursor": None}})
        order = next((o for o in orders if path == f"/api/shop/orders/{o['order_id']}"), None)
        if order is None:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "없음"}})
        return httpx.Response(200, json={"data": order})

    shop_client.use_client(httpx.AsyncClient(transport=httpx.MockTransport(handle)))


def test_returns_only_the_buyers_paid_orders_newest_first(client: TestClient) -> None:
    fake_shop(
        [
            shop_order("ord_1", "buy_0001", "2026-11-01T00:00:00Z"),
            shop_order(
                "ord_2", "buy_0001", "2026-11-10T00:00:00Z", delivered_at="2026-11-12T00:00:00Z"
            ),
            shop_order("ord_3", "buy_0002", "2026-11-05T00:00:00Z"),
            shop_order("ord_4", "buy_0001", "2026-11-08T00:00:00Z", status="payment_failed"),
        ]
    )
    body = call(client, {"buyer_id": "buy_0001"})
    assert body["status"] == "completed", body
    out = body["output"]
    assert out["buyer_id"] == "buy_0001" and out["truncated"] is False
    assert [o["order_id"] for o in out["items"]] == [
        "ord_2",
        "ord_1",
    ]  # 최근 순, 결제 안 된 주문 제외
    first = out["items"][0]
    assert first["status"] == "delivered" and first["total_paid"] == 35000
    assert first["shipping_fee"] == 3000
    assert first["items"][0] == {
        "order_item_id": "ord_2_1",
        "title": "고양이의 하루",
        "qty": 1,
        "paid_amount": 15000,
    }
    assert "payment_id" not in str(out)  # 결제 번호 같은 내부 값은 내보내지 않는다


def test_other_buyers_orders_never_leak_even_if_the_shop_ignores_the_filter(
    client: TestClient,
) -> None:
    fake_shop(
        [
            shop_order("ord_1", "buy_0001", "2026-11-01T00:00:00Z"),
            shop_order("ord_3", "buy_0002", "2026-11-05T00:00:00Z"),
        ],
        honor_filter=False,
    )
    out = call(client, {"buyer_id": "buy_0001"})["output"]
    assert [o["order_id"] for o in out["items"]] == ["ord_1"]


def test_asks_the_shop_for_that_buyer_with_the_limit(client: TestClient) -> None:
    seen: list[Any] = []
    fake_shop([shop_order("ord_1", "buy_0001", "2026-11-01T00:00:00Z")], seen)
    call(client, {"buyer_id": "buy_0001", "limit": 5})
    assert seen[0] == ("/api/shop/orders", {"buyer_id": "buy_0001", "limit": "5"})


def test_a_buyer_without_orders_gets_an_empty_list(client: TestClient) -> None:
    fake_shop([shop_order("ord_3", "buy_0002", "2026-11-05T00:00:00Z")])
    out = call(client, {"buyer_id": "buy_0009"})["output"]
    assert out["items"] == [] and out["truncated"] is False


def test_bad_buyer_ids_and_limits_are_refused(client: TestClient) -> None:
    fake_shop([])
    for bad in ({"buyer_id": "7"}, {"buyer_id": "usr_0007"}, {"buyer_id": "buy_００７７"}, {}):
        assert call(client, bad)["status"] == "failed", bad
    for limit in (0, 51):
        assert call(client, {"buyer_id": "buy_0001", "limit": limit})["status"] == "failed", limit


def test_shop_outage_is_a_clean_failure(client: TestClient) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    shop_client.use_client(httpx.AsyncClient(transport=httpx.MockTransport(down)))
    body = call(client, {"buyer_id": "buy_0001"})
    assert body["status"] == "failed" and body["outcome"] == "shop_unavailable"


def test_only_the_orchestrator_may_ask(client: TestClient) -> None:
    fake_shop([shop_order("ord_1", "buy_0001", "2026-11-01T00:00:00Z")])
    assert call(client, {"buyer_id": "buy_0001"}, requester="orchestrator")["status"] == "completed"
    assert call(client, {"buyer_id": "buy_0001"}, requester="marketing")["status"] == "rejected"


def test_seed_mode_reads_the_seed_orders(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    shop_client.use_client(None)
    monkeypatch.setenv("SUPPORT_ORDER_SOURCE", "seed")
    out = call(client, {"buyer_id": "buy_0001"})["output"]
    assert [o["order_id"] for o in out["items"]] == ["101"]
    assert call(client, {"buyer_id": "buy_0777"})["output"]["items"] == []
