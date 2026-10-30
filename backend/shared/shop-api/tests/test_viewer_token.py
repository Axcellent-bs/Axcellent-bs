"""읽기 전용 열람 토큰(`SHOP_VIEWER_TOKEN`): 총괄 읽기 중계와 장애 상태 읽기만 열고 닫는다."""

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_agent_template.server import verify_token
from ax_shop_api import orchestrator_relay
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

VIEWER_TOKEN = "viewer-token-for-tests-0123456789"
VIEWER = {"Authorization": f"Bearer {VIEWER_TOKEN}"}
ADMIN = {"Authorization": "Bearer mock:admin"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv("SHOP_VIEWER_TOKEN", VIEWER_TOKEN)
    events.reset_mock_bus()
    monkeypatch.setattr(
        orchestrator_relay,
        "TRANSPORT",
        httpx.MockTransport(lambda r: httpx.Response(200, json={"data": [], "meta": {}})),
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


@pytest.mark.parametrize(
    "path",
    [
        "/api/shop/admin/orchestrator/incidents",
        "/api/shop/admin/orchestrator/incidents/ctx_inc_abc123",
        "/api/shop/admin/orchestrator/departments",
        "/api/shop/admin/faults",
    ],
)
def test_viewer_can_read_orchestrator_and_fault_state(client, path) -> None:
    assert client.get(path, headers=VIEWER).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/shop/admin/faults", {"kind": "failure"}),
        ("DELETE", "/api/shop/admin/faults", None),
        ("POST", "/api/shop/admin/orders/ord_0001/shipped", {}),
        ("POST", "/api/shop/admin/orders/ord_0001/delivered", {}),
        ("POST", "/api/shop/admin/claims/1/return-received", {}),
        ("POST", "/api/shop/products", {}),
        ("PATCH", "/api/shop/products/prd_0001", {"stock": 1}),
        ("GET", "/api/shop/coupons", None),
        ("GET", "/api/shop/sellers/sel_0001/profile", None),
    ],
)
def test_viewer_is_refused_everywhere_else(client, method, path, body) -> None:
    response = client.request(method, path, headers=VIEWER, json=body)
    assert response.status_code == 403, (method, path, response.text)


def test_viewer_cannot_stand_in_for_a_write_that_admin_can_do(client) -> None:
    assert client.post(
        "/api/shop/admin/faults", headers=ADMIN, json={"kind": "failure"}
    ).status_code in (
        200,
        201,
    )
    assert client.delete("/api/shop/admin/faults", headers=VIEWER).status_code == 403
    assert client.get("/api/shop/admin/faults", headers=VIEWER).json()["data"] is not None


def test_a_wrong_token_is_still_unauthorized(client) -> None:
    response = client.get(
        "/api/shop/admin/orchestrator/incidents", headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


def test_viewer_is_off_when_unset(client, monkeypatch) -> None:
    monkeypatch.delenv("SHOP_VIEWER_TOKEN")
    response = client.get("/api/shop/admin/orchestrator/incidents", headers=VIEWER)
    assert response.status_code == 401


def test_a_short_viewer_token_is_not_accepted(client, monkeypatch) -> None:
    monkeypatch.setenv("SHOP_VIEWER_TOKEN", "short")
    response = client.get(
        "/api/shop/admin/orchestrator/incidents", headers={"Authorization": "Bearer short"}
    )
    assert response.status_code == 401


def test_viewer_token_is_not_an_agent_token(monkeypatch) -> None:
    """에이전트 명부에 들어가지 않는다 — 명부 전체를 받아 주는 다른 서버로 통하지 않게."""
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=some-admin-token-0123456789abcd")
    with pytest.raises(PermissionError):
        verify_token(VIEWER_TOKEN)
