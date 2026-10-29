"""상품 등록·수정은 admin 토큰만: 없으면 401, 다른 에이전트면 403, admin이면 통과."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

NEW = {"seller_id": "sel_0001", "name": "새 책", "category_code": "book", "price": 1, "stock": 1}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # 실제 모드 토큰 명부로 확인한다
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("SHOP_MOCK", raising=False)
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=adm-token,marketing=mkt-token")
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "operator:hum-token")
    events.reset_mock_bus()
    return TestClient(create_app(Db(":memory:")))


@pytest.mark.parametrize(
    ("token", "status"),
    [(None, 401), ("nobody", 401), ("hum-token", 403), ("mkt-token", 403), ("mock:admin", 401)],
)
def test_product_writes_reject_without_admin(client: TestClient, token, status) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    assert client.post("/api/shop/products", json=NEW, headers=headers).status_code == status
    patched = client.patch("/api/shop/products/prd_0001", json={"stock": 9}, headers=headers)
    assert patched.status_code == status
    assert client.get("/api/shop/products/prd_0001").json()["data"]["stock"] != 9


def test_product_writes_pass_with_admin(client: TestClient) -> None:
    admin = {"Authorization": "Bearer adm-token"}
    assert client.post("/api/shop/products", json=NEW, headers=admin).status_code == 201
    patched = client.patch("/api/shop/products/prd_0001", json={"stock": 9}, headers=admin)
    assert patched.status_code == 200
    assert patched.json()["data"]["stock"] == 9
