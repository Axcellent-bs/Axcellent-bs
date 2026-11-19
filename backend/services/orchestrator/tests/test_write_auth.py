"""총괄 쓰기 API 토큰: 없으면 401, 다른 종류면 403, 맞으면 통과."""

import pytest
from fastapi.testclient import TestClient

from ax_orchestrator.main import app, state

ADMIN = {"Authorization": "Bearer admin-test-token"}
AGENT = {"Authorization": "Bearer shop-test-token"}
HUMAN = {"Authorization": "Bearer human-test-token"}
UNKNOWN = {"Authorization": "Bearer nobody"}

EVENT = {
    "event_id": "evt_auth",
    "type": "order.created",
    "occurred_at": "2026-10-16T01:00:00Z",
    "source": "shop-api",
    "idempotency_key": "evt_auth",
    "data": {"component": "payment"},
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(state, "mock", True)
    monkeypatch.setattr(state, "latency", 0.0)
    monkeypatch.setattr(state, "manager", None)
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    ("headers", "status"),
    [(None, 401), (UNKNOWN, 401), (HUMAN, 403), (AGENT, 200), (ADMIN, 200)],
)
def test_events_need_agent_token(client, headers, status) -> None:
    assert client.post("/api/events", json=EVENT, headers=headers).status_code == status


@pytest.mark.parametrize(
    ("headers", "status"),
    [(None, 401), (UNKNOWN, 401), (AGENT, 403), (ADMIN, 403), (HUMAN, 404)],
)
def test_hold_decision_needs_human_token(client, headers, status) -> None:
    # 맞는 토큰이면 관문을 통과하고, 대기 중인 보류가 없어 404가 된다
    response = client.post(
        "/api/incidents/ctx_x/holds/rollback", json={"approved": True}, headers=headers
    )
    assert response.status_code == status


@pytest.mark.parametrize(
    ("headers", "status"),
    [(None, 401), (UNKNOWN, 401), (AGENT, 403), (HUMAN, 403), (ADMIN, 200)],
)
def test_demo_incident_needs_admin_token(client, headers, status) -> None:
    assert client.post("/api/demo/incidents", json={}, headers=headers).status_code == status


def test_mock_tokens_work_only_in_mock_mode(client, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_admin = {"Authorization": "Bearer mock:admin"}
    mock_human = {"Authorization": "Bearer human:demo"}
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("ORCHESTRATOR_MOCK", "1")
    assert client.post("/api/demo/incidents", json={}, headers=mock_admin).status_code == 200
    held = client.post("/api/incidents/x/holds/y", json={"approved": True}, headers=mock_human)
    assert held.status_code == 404
    monkeypatch.setenv("ORCHESTRATOR_MOCK", "0")
    assert client.post("/api/demo/incidents", json={}, headers=mock_admin).status_code == 401
    held = client.post("/api/incidents/x/holds/y", json={"approved": True}, headers=mock_human)
    assert held.status_code == 401


@pytest.mark.parametrize(
    ("headers", "status"), [(None, 401), (AGENT, 403), (ADMIN, 403), (HUMAN, 404)]
)
def test_request_step_decision_needs_human_token(client, headers, status) -> None:
    """사람 확인·승인은 사람 토큰만. 맞는 토큰이어도 없는 의뢰면 404."""
    r = client.post("/api/requests/wr_x/steps/c/decision", json={"approved": True}, headers=headers)
    assert r.status_code == status


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/requests"),
        ("get", "/api/requests/wr_x"),
        ("post", "/api/requests/wr_x/rerun"),
    ],
)
def test_request_screen_api_needs_a_human_token(client, method, path) -> None:
    """고객 글이 든 의뢰 조회·다시 돌리기는 사람 토큰만. 토큰 없음 401, 에이전트 403."""
    send = getattr(client, method)
    kw = {"json": {"task": "sales_analysis"}} if method == "post" else {}
    assert send(path, **kw).status_code == 401
    assert send(path, headers=AGENT, **kw).status_code == 403
    ok = send(path, headers=HUMAN, **kw).status_code
    assert ok in (200, 404)  # 맞는 토큰이면 통과(없는 의뢰는 404)
