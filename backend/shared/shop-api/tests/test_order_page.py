"""`GET /api/shop/orders/page`: 주문 관리 화면의 페이지 목록.

최신순·탭별 건수·부분/전체 환불 구분을 본다.
"""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

SUPPORT = {"Authorization": "Bearer mock:support"}
PAGE = "/api/shop/orders/page"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _order(client: TestClient, buyer: str = "buy_0001") -> dict:
    body = {"buyer_id": buyer, "items": [{"product_id": "prd_0001", "qty": 1}]}
    res = client.post("/api/shop/orders", json=body)
    assert res.status_code == 201
    return res.json()["data"]


def _pay(client: TestClient, order_id: str) -> dict:
    """결제하고 결제 한 건을 돌려준다 (응답은 주문이다)"""
    res = client.post(f"/api/shop/orders/{order_id}/payments", json={})
    return res.json()["data"]["payments"][-1]


def _refund(client: TestClient, payment_id: str, amount: int, key: str) -> None:
    body = {"refund_amount": amount, "idempotency_key": key, "context_id": "ctx_shop_aaa"}
    res = client.post(f"/api/shop/payments/{payment_id}/refund", json=body, headers=SUPPORT)
    assert res.status_code == 201


@pytest.fixture
def orders(client: TestClient) -> dict[str, str]:
    """결제 대기·결제 완료·부분 환불·전체 환불·취소 한 건씩 (만든 순서대로)"""
    pending = _order(client)["order_id"]
    paid = _order(client)
    _pay(client, paid["order_id"])
    partial = _order(client)
    payment = _pay(client, partial["order_id"])
    _refund(client, payment["payment_id"], 1000, "rf-partial")
    full = _order(client, "buy_0002")
    payment = _pay(client, full["order_id"])
    _refund(client, payment["payment_id"], full["total_amount"], "rf-full")
    cancelled = _order(client)["order_id"]
    assert client.post(f"/api/shop/orders/{cancelled}/cancel", json={}).status_code == 200
    return {
        "pending": pending,
        "paid": paid["order_id"],
        "partial": partial["order_id"],
        "full": full["order_id"],
        "cancelled": cancelled,
    }


def _ids(body: dict) -> list[str]:
    return [o["order_id"] for o in body["data"]]


def test_newest_first_with_counts_per_tab(client: TestClient, orders: dict[str, str]) -> None:
    body = client.get(PAGE).json()
    made = list(orders.values())
    assert _ids(body) == made[::-1]  # 나중에 만든 주문이 먼저
    assert body["meta"]["total"] == 5
    assert body["meta"]["counts"] == {
        "all": 5,
        "pending_payment": 1,
        "paid": 1,
        "partial_refund": 1,
        "full_refund": 1,
        "cancelled": 1,
    }


@pytest.mark.parametrize(
    ("tab", "key"),
    [
        ("partial_refund", "partial"),
        ("full_refund", "full"),
        ("cancelled", "cancelled"),
        ("paid", "paid"),  # 환불된 결제 완료 주문은 결제 완료에 넣지 않는다
        ("pending_payment", "pending"),
    ],
)
def test_each_tab_lists_only_its_orders(
    client: TestClient, orders: dict[str, str], tab: str, key: str
) -> None:
    body = client.get(PAGE, params={"status": tab}).json()
    assert _ids(body) == [orders[key]]
    assert body["meta"]["total"] == 1
    assert body["meta"]["counts"]["all"] == 5  # 건수는 탭과 상관없이 같다


def test_partial_refund_keeps_amount_and_refund_rows(
    client: TestClient, orders: dict[str, str]
) -> None:
    [row] = client.get(PAGE, params={"status": "partial_refund"}).json()["data"]
    assert row["refunded_amount"] == 1000 < row["total_amount"]
    assert [r["amount"] for r in row["refunds"]] == [1000]


def test_paging_with_limit_and_offset(client: TestClient, orders: dict[str, str]) -> None:
    made = list(orders.values())[::-1]
    first = client.get(PAGE, params={"limit": 2}).json()
    second = client.get(PAGE, params={"limit": 2, "offset": 2}).json()
    last = client.get(PAGE, params={"limit": 2, "offset": 4}).json()
    assert _ids(first) + _ids(second) + _ids(last) == made
    assert first["meta"]["total"] == 5 and len(_ids(last)) == 1


def test_buyer_and_order_number_filters_also_change_counts(
    client: TestClient, orders: dict[str, str]
) -> None:
    by_buyer = client.get(PAGE, params={"buyer_id": "buy_0002"}).json()
    assert _ids(by_buyer) == [orders["full"]]
    assert by_buyer["meta"]["counts"] == {"all": 1, "full_refund": 1}
    by_number = client.get(PAGE, params={"q": orders["partial"][-3:]}).json()
    assert orders["partial"] in _ids(by_number)
    assert client.get(PAGE, params={"q": "%"}).json()["data"] == []  # LIKE 와일드카드가 아니다


def test_period_filter(client: TestClient, orders: dict[str, str]) -> None:
    future = client.get(PAGE, params={"since": "2999-01-01T00:00:00Z"}).json()
    assert future["data"] == [] and future["meta"]["counts"] == {"all": 0}
    assert client.get(PAGE, params={"since": "어제"}).status_code == 400


def test_bad_query_values_are_rejected(client: TestClient) -> None:
    assert client.get(PAGE, params={"status": "shipped"}).status_code == 422
    assert client.get(PAGE, params={"limit": 0}).status_code == 422
    assert client.get(PAGE, params={"limit": 101}).status_code == 422
    assert client.get(PAGE, params={"offset": -1}).status_code == 422


def test_page_route_is_not_taken_as_an_order_id(client: TestClient) -> None:
    res = client.get(PAGE)
    assert res.status_code == 200 and res.json()["data"] == []
