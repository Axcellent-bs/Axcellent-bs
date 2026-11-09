"""기획 쓰기 API 토큰: 없으면 401, 다른 종류면 403, 맞으면 통과 (개발 쪽은 domains/dev/tests)."""

import pytest
from fastapi.testclient import TestClient

from planning_agent.main import app

BASE = "/api/planning"
ADMIN = {"Authorization": "Bearer admin-test-token"}
AGENT = {"Authorization": "Bearer infra-test-token"}
HUMAN = {"Authorization": "Bearer human-test-token"}
UNKNOWN = {"Authorization": "Bearer nobody"}
NONE = {"Authorization": ""}  # conftest가 목 토큰을 붙이지 않게 빈 값으로 둔다


@pytest.fixture(autouse=True)
def _write_tokens(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=admin-test-token,infra=infra-test-token")
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "operator:human-test-token")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_simulator_intake_action_takes_agent_token(client) -> None:
    body = {"type": "inbound", "kind": "support_ticket"}
    assert client.post(f"{BASE}/actions", json=body, headers=NONE).status_code == 401
    assert client.post(f"{BASE}/actions", json=body, headers=AGENT).status_code == 200


def test_reads_stay_open(client) -> None:
    for path in ("/state", "/requests", "/projects", "/docs"):
        assert client.get(f"{BASE}{path}", headers=NONE).status_code == 200, path


def test_mock_tokens_work_only_in_mock_mode(client, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_admin = {"Authorization": "Bearer mock:admin"}
    mock_human = {"Authorization": "Bearer human:demo"}
    assert client.post(f"{BASE}/reset", headers=mock_admin).status_code == 200
    assert client.post(f"{BASE}/reset", headers=mock_human).status_code == 403
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("PLANNING_MOCK", raising=False)
    assert client.post(f"{BASE}/reset", headers=mock_admin).status_code == 401


def test_fail_closed_without_configured_tokens(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("PLANNING_MOCK", raising=False)
    monkeypatch.delenv("AX_AGENT_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    for headers in (ADMIN, HUMAN, AGENT):
        assert client.post(f"{BASE}/reset", headers=headers).status_code == 401
        res = client.post(f"{BASE}/actions", json={"type": "markRead"}, headers=headers)
        assert res.status_code == 401


def test_draft_reject_rejects_agent(client) -> None:
    path = f"{BASE}/requests/REQ-999/reject"
    assert client.post(path, json={}, headers=NONE).status_code == 401
    assert client.post(path, json={}, headers=AGENT).status_code == 403
    assert client.post(path, json={}, headers=HUMAN).status_code not in (401, 403)
