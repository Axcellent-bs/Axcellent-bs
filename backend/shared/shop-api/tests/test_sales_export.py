"""판매 기록 내보내기 `GET /api/shop/sales-export` (계약 `data_access.shop_sales`).

커서 이어 받기, 상태가 바뀐 주문·환불된 주문의 재노출, 권한(401·403), limit 경계, 개인정보 없음.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.data_access.shop_sales import ShopSalesExportResult
from ax_shop_api.db import Db
from ax_shop_api.main import create_app
from ax_shop_api.sales_export import encode_cursor

DATA = {"Authorization": "Bearer mock:data"}
ADMIN = {"Authorization": "Bearer mock:admin"}
MARKETING = {"Authorization": "Bearer mock:marketing"}
SUPPORT = {"Authorization": "Bearer mock:support"}
URL = "/api/shop/sales-export"


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


def _order(client: TestClient, qty: int = 2, pay: bool = True, buyer: str = "buy_0001") -> dict:
    order = client.post(
        "/api/shop/orders",
        json={"buyer_id": buyer, "items": [{"product_id": "prd_0001", "qty": qty}]},
    ).json()["data"]
    if pay:
        return client.post(f"/api/shop/orders/{order['order_id']}/payments", json={}).json()["data"]
    return order


def _set_updated(db: Db, order_id: str, at: str) -> None:
    """주문 바뀐 시각을 과거로 옮긴다(내보내기는 5초 지난 주문만 준다)."""
    with db.tx() as conn:
        conn.execute("UPDATE orders SET updated_at = ? WHERE order_id = ?", (at, order_id))


def _page(client: TestClient, cursor: str | None = None, limit: int = 500) -> ShopSalesExportResult:
    params: dict = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    res = client.get(URL, params=params, headers=DATA)
    assert res.status_code == 200, res.text
    return ShopSalesExportResult.model_validate(res.json()["data"])


def test_export_row_has_amounts_lines_payment_and_no_personal_fields(
    client: TestClient, db: Db
) -> None:
    paid = _order(client, qty=2)
    _set_updated(db, paid["order_id"], "2026-11-01T00:00:00Z")
    res = client.get(URL, headers=DATA)
    raw = res.json()["data"]["orders"]
    assert len(raw) == 1
    row = raw[0]
    assert row["order_id"] == paid["order_id"] and row["buyer_id"] == "buy_0001"
    assert row["status"] == "paid" and row["payment_status"] == "succeeded"
    assert row["items_amount"] == 2 * 18000
    assert row["lines"] == [
        {"product_id": "prd_0001", "category_code": "book", "title": "파이썬 입문서", "qty": 2,
         "unit_price": 18000, "coupon_share": 0, "points_share": 0},
    ]  # fmt: skip
    assert row["refunds"] == []
    text = res.text
    for personal in ("name", "phone", "address", "email"):
        assert f'"{personal}"' not in text and f"buyer_{personal}" not in text


def test_cursor_continues_without_gaps_or_repeats(client: TestClient, db: Db) -> None:
    ids = [_order(client)["order_id"] for _ in range(5)]
    for i, order_id in enumerate(ids):
        _set_updated(db, order_id, f"2026-11-01T00:00:0{i}Z")
    first = _page(client, limit=2)
    assert [o.order_id for o in first.orders] == ids[:2] and first.has_more
    second = _page(client, first.next_cursor, limit=2)
    assert [o.order_id for o in second.orders] == ids[2:4] and second.has_more
    third = _page(client, second.next_cursor, limit=2)
    assert [o.order_id for o in third.orders] == ids[4:] and not third.has_more
    # 받을 것이 없으면 같은 커서를 돌려준다(다음 수집이 그 자리부터)
    empty = _page(client, third.next_cursor, limit=2)
    assert empty.orders == [] and empty.next_cursor == third.next_cursor and not empty.has_more


def test_same_timestamp_is_ordered_by_order_id(client: TestClient, db: Db) -> None:
    ids = sorted(_order(client)["order_id"] for _ in range(3))
    for order_id in ids:
        _set_updated(db, order_id, "2026-11-01T00:00:00Z")
    first = _page(client, limit=1)
    rest = _page(client, first.next_cursor, limit=5)
    assert [o.order_id for o in first.orders + rest.orders] == ids


def test_status_change_reappears_after_cursor(client: TestClient, db: Db) -> None:
    unpaid = _order(client, pay=False)
    other = _order(client)
    _set_updated(db, unpaid["order_id"], "2026-11-01T00:00:00Z")
    _set_updated(db, other["order_id"], "2026-11-01T00:00:01Z")
    first = _page(client)
    assert {o.order_id: o.status for o in first.orders}[unpaid["order_id"]] == "pending_payment"
    client.post(f"/api/shop/orders/{unpaid['order_id']}/cancel", json={})
    _set_updated(db, unpaid["order_id"], "2026-11-01T01:00:00Z")
    again = _page(client, first.next_cursor)
    assert [(o.order_id, o.status) for o in again.orders] == [(unpaid["order_id"], "cancelled")]


def test_refund_bumps_updated_at_and_reappears_with_refund(client: TestClient, db: Db) -> None:
    paid = _order(client)
    _set_updated(db, paid["order_id"], "2026-11-01T00:00:00Z")
    first = _page(client)
    payment_id = paid["payments"][0]["payment_id"]
    res = client.post(
        f"/api/shop/payments/{payment_id}/refund",
        json={"refund_amount": 5000, "idempotency_key": "k1", "context_id": "ctx_shop_aaa"},
        headers=SUPPORT,
    )
    assert res.status_code == 201
    with db.tx() as conn:
        bumped = conn.execute(
            "SELECT updated_at, status FROM orders WHERE order_id = ?", (paid["order_id"],)
        ).fetchone()
    assert bumped["updated_at"] > "2026-11-01T00:00:00Z" and bumped["status"] == "paid"
    _set_updated(db, paid["order_id"], "2026-11-01T02:00:00Z")
    again = _page(client, first.next_cursor)
    assert len(again.orders) == 1
    assert [r.amount for r in again.orders[0].refunds] == [5000]
    assert again.orders[0].refunds[0].refund_id


def test_unsettled_recent_change_waits(client: TestClient) -> None:
    _order(client)  # 방금 바뀐 주문(5초 안)은 아직 주지 않는다
    assert _page(client).orders == []


def test_failed_payment_status(client: TestClient, db: Db) -> None:
    order = _order(client, pay=False)
    client.post(f"/api/shop/orders/{order['order_id']}/payments", json={"simulate": "failure"})
    _set_updated(db, order["order_id"], "2026-11-01T00:00:00Z")
    row = _page(client).orders[0]
    assert row.status == "payment_failed" and row.payment_status == "failed"


def test_permissions_401_and_403(client: TestClient) -> None:
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={"Authorization": "Bearer "}).status_code == 401
    assert client.get(URL, headers=MARKETING).status_code == 403
    assert client.get(URL, headers=SUPPORT).status_code == 403
    assert client.get(URL, headers=DATA).status_code == 200
    assert client.get(URL, headers=ADMIN).status_code == 200


@pytest.mark.parametrize(("limit", "status"), [(0, 400), (1, 200), (1000, 200), (1001, 400)])
def test_limit_bounds(client: TestClient, limit: int, status: int) -> None:
    assert client.get(URL, params={"limit": limit}, headers=DATA).status_code == status


def test_bad_cursor_is_400(client: TestClient) -> None:
    for bad in ("!!!", "bm90LWEtY3Vyc29y", encode_cursor("2026-11-01", "ord_1")):
        res = client.get(URL, params={"cursor": bad}, headers=DATA)
        assert res.status_code == 400, bad


def test_export_does_not_write(client: TestClient, db: Db) -> None:
    order = _order(client)
    _set_updated(db, order["order_id"], "2026-11-01T00:00:00Z")
    with db.tx() as conn:
        before = conn.total_changes
    _page(client)
    with db.tx() as conn:
        assert conn.total_changes == before


def test_updated_at_index_exists_on_existing_file(tmp_path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)  # 인덱스가 없던 옛 DB 파일
    conn.execute(
        "CREATE TABLE orders (order_id TEXT PRIMARY KEY, buyer_id TEXT NOT NULL, "
        "status TEXT NOT NULL, total_amount INTEGER NOT NULL, tenant_id TEXT NOT NULL, "
        "context_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    Db(str(path))
    conn = sqlite3.connect(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    plan = " ".join(
        str(r[-1])
        for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT order_id FROM orders WHERE tenant_id = 'x' "
            "AND (updated_at, order_id) > ('a', 'b') ORDER BY updated_at, order_id"
        )
    )
    conn.close()
    assert "idx_orders_updated_at" in names
    assert "idx_orders_updated_at" in plan
