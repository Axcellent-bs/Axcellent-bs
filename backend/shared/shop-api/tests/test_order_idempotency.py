"""주문 멱등 키: 같은 구매자의 같은 키는 주문 하나다."""

import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _body(key: str | None = None, **changes: object) -> dict:
    body: dict = {"buyer_id": "buy_0001", "items": [{"product_id": "prd_0001", "qty": 2}]}
    if key is not None:
        body["idempotency_key"] = key
    body.update(changes)
    return body


def _stock(client: TestClient, product_id: str = "prd_0001") -> int:
    return client.get(f"/api/shop/products/{product_id}").json()["data"]["stock"]


def _created_events() -> list[str]:
    return [e.type.value for e in events.MOCK_PUBLISHED if e.type.value == "order.created"]


def test_without_a_key_every_request_is_a_new_order(client: TestClient) -> None:
    first = client.post("/api/shop/orders", json=_body()).json()
    second = client.post("/api/shop/orders", json=_body()).json()
    assert first["data"]["order_id"] != second["data"]["order_id"]
    assert first["meta"]["replayed"] is False and second["meta"]["replayed"] is False


def test_same_key_returns_the_first_order_and_deducts_stock_once(client: TestClient) -> None:
    before = _stock(client)
    first = client.post("/api/shop/orders", json=_body("k-1"))
    again = client.post("/api/shop/orders", json=_body("k-1"))
    assert first.status_code == again.status_code == 201
    assert first.json()["meta"]["replayed"] is False
    assert again.json()["meta"]["replayed"] is True
    assert again.json()["data"] == first.json()["data"]
    assert before - _stock(client) == 2  # 재고는 한 번만 빠진다
    assert len(client.get("/api/shop/orders").json()["data"]) == 1
    assert len(_created_events()) == 1  # order.created도 한 번만 나간다


def test_item_order_does_not_matter_for_a_replay(client: TestClient) -> None:
    items = [{"product_id": "prd_0001", "qty": 1}, {"product_id": "prd_0002", "qty": 1}]
    first = client.post("/api/shop/orders", json=_body("k-2", items=items)).json()
    again = client.post("/api/shop/orders", json=_body("k-2", items=items[::-1])).json()
    assert again["data"]["order_id"] == first["data"]["order_id"]
    assert again["meta"]["replayed"] is True


@pytest.mark.parametrize(
    "changes",
    [
        {"items": [{"product_id": "prd_0001", "qty": 3}]},
        {"items": [{"product_id": "prd_0002", "qty": 2}]},
        {"points_used": 100},
    ],
)
def test_same_key_with_different_content_is_rejected(client: TestClient, changes: dict) -> None:
    client.post("/api/shop/orders", json=_body("k-3"))
    stock = _stock(client)
    response = client.post("/api/shop/orders", json=_body("k-3", **changes))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_MISMATCH"
    assert _stock(client) == stock  # 거절된 요청은 재고를 건드리지 않는다


def test_the_same_key_from_another_buyer_is_a_separate_order(client: TestClient) -> None:
    mine = client.post("/api/shop/orders", json=_body("shared-key")).json()["data"]
    theirs = client.post("/api/shop/orders", json=_body("shared-key", buyer_id="buy_0002")).json()[
        "data"
    ]
    assert mine["order_id"] != theirs["order_id"]
    assert (mine["buyer_id"], theirs["buyer_id"]) == ("buy_0001", "buy_0002")


def test_a_replay_shows_the_current_state_of_the_order(client: TestClient) -> None:
    first = client.post("/api/shop/orders", json=_body("k-4")).json()["data"]
    client.post(f"/api/shop/orders/{first['order_id']}/payments", json={"simulate": None})
    again = client.post("/api/shop/orders", json=_body("k-4")).json()
    assert again["meta"]["replayed"] is True
    assert again["data"]["status"] == "paid"


@pytest.mark.parametrize("key", ["", "x" * 129])
def test_an_empty_or_too_long_key_is_rejected(client: TestClient, key: str) -> None:
    assert client.post("/api/shop/orders", json=_body(key)).status_code == 422


def test_parallel_requests_with_one_key_make_one_order() -> None:
    app = create_app(Db(":memory:"))
    shop = app.state.shop
    results: list[tuple[str, bool]] = []

    def place() -> None:
        order, replayed = shop.place_order("buy_0001", [("prd_0001", 1)], 0, "race")
        results.append((order["order_id"], replayed))

    threads = [threading.Thread(target=place) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({order_id for order_id, _ in results}) == 1
    assert sorted(replayed for _, replayed in results) == [False] + [True] * 7


def test_an_old_database_file_gets_the_column_and_the_index(tmp_path: Path) -> None:
    path = str(tmp_path / "shop.db")
    app = create_app(Db(path))
    first = TestClient(app).post("/api/shop/orders", json=_body()).json()["data"]
    old = sqlite3.connect(path)  # 이 열이 생기기 전의 파일을 흉내 낸다
    old.execute("DROP INDEX idx_orders_buyer_idempotency")
    old.execute("ALTER TABLE orders DROP COLUMN idempotency_key")
    old.commit()
    old.close()

    reopened = Db(path)
    with reopened.tx() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
        indexes = {r["name"] for r in conn.execute("PRAGMA index_list(orders)")}
    assert "idempotency_key" in columns
    assert "idx_orders_buyer_idempotency" in indexes
    client = TestClient(create_app(reopened))
    assert (
        client.get(f"/api/shop/orders/{first['order_id']}").status_code == 200
    )  # 옛 주문은 그대로
    assert client.post("/api/shop/orders", json=_body("after")).status_code == 201
