"""시간 초과 결제(가짜 결제사): 장애 주입 lost_response → 시간 초과 → 다시 확인 → 결제됨."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

ADMIN = {"Authorization": "Bearer mock:admin"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv("PAYMENT_PROVIDER", "fake")
    events.reset_mock_bus()


def _client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _order(client: TestClient) -> str:
    product = client.get("/api/shop/products").json()["data"][0]
    body = {"buyer_id": "buy_0001", "items": [{"product_id": product["product_id"], "qty": 1}]}
    res = client.post("/api/shop/orders", json=body, headers={"Idempotency-Key": "k-unknown-1"})
    assert res.status_code in (200, 201), res.text
    return res.json()["data"]["order_id"]


def _pay(client: TestClient, order_id: str):
    return client.post(f"/api/shop/orders/{order_id}/payments", json={"method_code": "card"})


def test_lost_response_leaves_payment_unknown_and_blocks_paying_again() -> None:
    client = _client()
    client.post("/api/shop/admin/faults", json={"kind": "lost_response"}, headers=ADMIN)
    order_id = _order(client)
    data = _pay(client, order_id).json()["data"]
    assert data["status"] == "pending_payment"
    assert data["payments"][0]["status"] == "timeout"
    assert data["payments"][0]["failure_code"] == "PG_TIMEOUT"
    assert _pay(client, order_id).status_code == 409  # 두 번 청구될 수 있어 막는다
    listed = client.get("/api/shop/admin/payments/timeouts", headers=ADMIN).json()["data"]
    assert [r["order_id"] for r in listed] == [order_id]


def test_recheck_marks_fake_approved_payment_paid_once() -> None:
    client = _client()
    client.post("/api/shop/admin/faults", json={"kind": "lost_response"}, headers=ADMIN)
    order_id = _order(client)
    _pay(client, order_id)
    client.delete("/api/shop/admin/faults", headers=ADMIN)
    res = client.post("/api/shop/admin/payments/recheck", headers=ADMIN).json()["data"]
    assert [r["status"] for r in res["resolved"]] == ["succeeded"]
    assert res["left"] == 0
    order = client.get(f"/api/shop/orders/{order_id}").json()["data"]
    assert order["status"] == "paid"
    assert order["payments"][0]["status"] == "succeeded"
    again = client.post("/api/shop/admin/payments/recheck", headers=ADMIN).json()["data"]
    assert again["resolved"] == []  # 이미 정한 결제는 다시 바꾸지 않는다


def test_fault_state_shows_lost_response() -> None:
    client = _client()
    client.post("/api/shop/admin/faults", json={"kind": "lost_response"}, headers=ADMIN)
    state = client.get("/api/shop/admin/faults", headers=ADMIN).json()["data"]
    assert state["active"] is True and state["lost_response"] is True
    client.delete("/api/shop/admin/faults", headers=ADMIN)
    state = client.get("/api/shop/admin/faults", headers=ADMIN).json()["data"]
    assert state["active"] is False and state["lost_response"] is False
