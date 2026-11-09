"""판매자 입점: 운영자(admin)만, 판매자·상품을 저장하고 `seller.registered`를 발행한다."""

import json

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

NEW = {
    "name": "새 서점",
    "category_codes": ["book"],
    "documents_verified": True,
    "products": [{"name": "싼 책", "category_code": "book", "price": 3000, "stock": 50}] * 3,
}
ADMIN = {"Authorization": "Bearer adm-token"}


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, db: Db) -> TestClient:
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("SHOP_MOCK", raising=False)
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=adm-token,marketing=mkt-token")
    events.reset_mock_bus()
    return TestClient(create_app(db))


def _registered(db: Db) -> list[dict]:
    with db.tx() as conn:
        rows = conn.execute("SELECT payload FROM outbox_events ORDER BY rowid").fetchall()
    found = [json.loads(r["payload"]) for r in rows]
    return [e for e in found if e["type"] == "seller.registered"]


@pytest.mark.parametrize(("token", "status"), [(None, 401), ("mkt-token", 403)])
def test_register_needs_admin(client: TestClient, db: Db, token, status) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    assert client.post("/api/shop/sellers", json=NEW, headers=headers).status_code == status
    assert _registered(db) == []


def test_register_saves_seller_products_and_event(client: TestClient, db: Db) -> None:
    before = len(client.get("/api/shop/sellers").json()["data"])
    res = client.post("/api/shop/sellers", json=NEW, headers=ADMIN)
    assert res.status_code == 201
    seller = res.json()["data"]
    assert seller["seller_id"].startswith("sel_") and seller["product_count"] == 3
    assert len(client.get("/api/shop/sellers").json()["data"]) == before + 1
    products = client.get(f"/api/shop/products?seller_id={seller['seller_id']}").json()["data"]
    assert len(products) == 3

    [event] = _registered(db)
    assert event["data"] == {
        "seller_id": seller["seller_id"],
        "category_codes": ["book"],
        "documents_verified": True,
    }
    assert "새 서점" not in json.dumps(event)  # 사람이 쓴 이름은 이벤트에 넣지 않는다

    again = client.post("/api/shop/sellers", json=NEW, headers=ADMIN).json()["data"]
    assert again["seller_id"] != seller["seller_id"]
