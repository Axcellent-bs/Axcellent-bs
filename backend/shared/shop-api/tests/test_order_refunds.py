"""주문 응답의 `refunds`(환불 기록 목록): 환불 시각을 재무가 실제 기준으로 쓰게 한다."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

SUPPORT = {"Authorization": "Bearer mock:support"}
KEYS = {"refund_id", "payment_id", "amount", "created_at"}
OLD_KEYS = {
    "order_id",
    "buyer_id",
    "status",
    "total_amount",
    "refunded_amount",
    "shipping_fee",
    "points_used",
    "coupon_id",
    "coupon_units",
    "coupon_discount",
    "promised_delivery_at",
    "shipped_at",
    "delivered_at",
    "created_at",
    "items",
    "payments",
}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(db: Db) -> TestClient:
    return TestClient(create_app(db))


def _paid(client: TestClient) -> dict:
    order = client.post(
        "/api/shop/orders",
        json={"buyer_id": "buy_0001", "items": [{"product_id": "prd_0001", "qty": 1}]},
    ).json()["data"]
    return client.post(f"/api/shop/orders/{order['order_id']}/payments", json={}).json()["data"]


def _refund(client: TestClient, payment_id: str, amount: int, key: str) -> str:
    body = {"refund_amount": amount, "idempotency_key": key, "context_id": "ctx_shop_aaa"}
    res = client.post(f"/api/shop/payments/{payment_id}/refund", json=body, headers=SUPPORT)
    assert res.status_code == 201
    return res.json()["data"]["refund_id"]


def _set_refund_time(db: Db, refund_id: str, at: str) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE refunds SET created_at = ? WHERE refund_id = ?", (at, refund_id))


def _get(client: TestClient, order_id: str) -> dict:
    return client.get(f"/api/shop/orders/{order_id}").json()["data"]


def _listed(client: TestClient, order_id: str, query: str = "") -> dict:
    rows = client.get(f"/api/shop/orders{query}").json()["data"]
    return next(o for o in rows if o["order_id"] == order_id)


def test_order_without_refund_has_empty_list(client: TestClient) -> None:
    paid = _paid(client)
    assert _get(client, paid["order_id"])["refunds"] == []
    assert _listed(client, paid["order_id"])["refunds"] == []


def test_single_refund_is_listed_with_its_record(client: TestClient) -> None:
    paid = _paid(client)
    payment_id = paid["payments"][0]["payment_id"]
    refund_id = _refund(client, payment_id, 5000, "k1")
    for order in (_get(client, paid["order_id"]), _listed(client, paid["order_id"])):
        assert len(order["refunds"]) == 1
        record = order["refunds"][0]
        assert set(record) == KEYS
        assert (record["refund_id"], record["payment_id"], record["amount"]) == (
            refund_id,
            payment_id,
            5000,
        )
        assert record["created_at"].endswith("Z") and record["created_at"][10] == "T"


def test_several_partial_refunds_are_in_time_order_and_sum_to_refunded_amount(
    client: TestClient, db: Db
) -> None:
    paid = _paid(client)
    payment_id = paid["payments"][0]["payment_id"]
    late = _refund(client, payment_id, 1000, "k1")  # 먼저 만들었지만 시각은 가장 늦다
    mid = _refund(client, payment_id, 2000, "k2")
    early = _refund(client, payment_id, 3000, "k3")
    _set_refund_time(db, late, "2026-11-09T03:00:00Z")
    _set_refund_time(db, mid, "2026-11-05T03:00:00Z")
    _set_refund_time(db, early, "2026-11-01T03:00:00Z")
    for order in (_get(client, paid["order_id"]), _listed(client, paid["order_id"])):
        assert [r["refund_id"] for r in order["refunds"]] == [early, mid, late]
        assert [r["created_at"] for r in order["refunds"]] == sorted(
            r["created_at"] for r in order["refunds"]
        )
        assert sum(r["amount"] for r in order["refunds"]) == order["refunded_amount"] == 6000


def test_refunds_are_attached_to_period_queries(client: TestClient) -> None:
    paid = _paid(client)
    _refund(client, paid["payments"][0]["payment_id"], 4000, "k1")
    query = "?since=2000-01-01T00:00:00Z&until=2999-01-01T00:00:00Z"
    order = _listed(client, paid["order_id"], query)
    assert [r["amount"] for r in order["refunds"]] == [4000]
    assert _listed(client, paid["order_id"], "?buyer_id=buy_0001")["refunds"][0]["amount"] == 4000


def test_existing_order_keys_are_kept_and_only_refunds_is_added(client: TestClient) -> None:
    paid = _paid(client)
    _refund(client, paid["payments"][0]["payment_id"], 1000, "k1")
    for order in (_get(client, paid["order_id"]), _listed(client, paid["order_id"])):
        assert set(order) == OLD_KEYS | {"refunds"}
        assert order["refunded_amount"] == 1000


def test_listing_reads_refunds_in_one_query_not_per_order(client: TestClient, db: Db) -> None:
    paid = [_paid(client) for _ in range(5)]
    for i, order in enumerate(paid):
        _refund(client, order["payments"][0]["payment_id"], 1000 + i, f"k{i}")
    seen: list[str] = []
    db._conn.set_trace_callback(seen.append)  # noqa: SLF001
    rows = client.get("/api/shop/orders").json()["data"]
    db._conn.set_trace_callback(None)  # noqa: SLF001
    assert len(rows) == 5 and all(len(o["refunds"]) == 1 for o in rows)
    refund_selects = [s for s in seen if "FROM refunds" in s]
    assert len(refund_selects) == 1, refund_selects
