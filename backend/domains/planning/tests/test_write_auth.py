"""기획개발 쓰기 API 토큰: 없으면 401, 다른 종류면 403, 맞으면 통과. 형상관리 토큰은 숨긴다."""

import pytest
from fastapi.testclient import TestClient

from planning_agent import vcs
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
    vcs._screen = None
    vcs.set_vcs(None)
    yield
    vcs._screen = None
    vcs.set_vcs(None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize(
    ("headers", "status"),
    [(NONE, 401), (UNKNOWN, 401), (AGENT, 403), (ADMIN, 403), (HUMAN, 404)],
)
def test_deploy_approve_needs_human_token(client, headers, status) -> None:
    # 맞는 토큰이면 관문을 통과하고, 그런 배포가 없어 404가 된다
    res = client.post(f"{BASE}/deployments/DEP-999/approve", json={}, headers=headers)
    assert res.status_code == status
    assert res.json()["error"]["code"]


@pytest.mark.parametrize(
    ("headers", "status"),
    [(NONE, 401), (UNKNOWN, 401), (AGENT, 403), (ADMIN, 403), (HUMAN, 200)],
)
def test_human_actions_need_human_token(client, headers, status) -> None:
    res = client.post(f"{BASE}/actions", json={"type": "markRead", "to": "x"}, headers=headers)
    assert res.status_code == status


@pytest.mark.parametrize("path", ["/dev-tasks/T-999/retry", "/requests/REQ-999/reject"])
def test_other_human_decisions_reject_agent(client, path) -> None:
    assert client.post(f"{BASE}{path}", json={}, headers=NONE).status_code == 401
    assert client.post(f"{BASE}{path}", json={}, headers=AGENT).status_code == 403
    assert client.post(f"{BASE}{path}", json={}, headers=HUMAN).status_code not in (401, 403)


@pytest.mark.parametrize(
    ("headers", "status"),
    [(NONE, 401), (UNKNOWN, 401), (AGENT, 403), (HUMAN, 403), (ADMIN, 200)],
)
def test_reset_needs_admin_token(client, headers, status) -> None:
    assert client.post(f"{BASE}/reset", headers=headers).status_code == status


@pytest.mark.parametrize(
    ("headers", "status"),
    [(NONE, 401), (UNKNOWN, 401), (AGENT, 403), (HUMAN, 403), (ADMIN, 200)],
)
def test_vcs_writes_need_admin_token(client, headers, status) -> None:
    res = client.post(f"{BASE}/vcs/connections", json={"kind": "mock"}, headers=headers)
    assert res.status_code == status
    assert client.post(f"{BASE}/vcs/test", headers=headers).status_code == status


@pytest.mark.parametrize(("headers", "status"), [(HUMAN, 403), (AGENT, 403), (ADMIN, 200)])
def test_auto_policy_change_needs_admin_token(client, headers, status) -> None:
    res = client.post(f"{BASE}/actions", json={"type": "rejectPromotion"}, headers=headers)
    assert res.status_code == status


@pytest.mark.parametrize(
    ("headers", "status"),
    [(NONE, 401), (UNKNOWN, 401), (AGENT, 400), (HUMAN, 400), (ADMIN, 400)],
)
def test_intake_takes_human_or_agent_token(client, headers, status) -> None:
    # 맞는 토큰이면 관문을 통과하고, 본문이 비어 400이 된다
    for path in ("/dev-tasks", "/deployments"):
        res = client.post(f"{BASE}{path}", json={}, headers=headers)
        assert res.status_code == status, path


def test_simulator_intake_action_takes_agent_token(client) -> None:
    body = {"type": "inbound", "kind": "support_ticket"}
    assert client.post(f"{BASE}/actions", json=body, headers=NONE).status_code == 401
    assert client.post(f"{BASE}/actions", json=body, headers=AGENT).status_code == 200


def test_reads_stay_open(client) -> None:
    for path in ("/state", "/vcs", "/vcs/connections", "/deployments", "/dev-chat"):
        assert client.get(f"{BASE}{path}", headers=NONE).status_code == 200, path


def test_mock_tokens_work_only_in_mock_mode(client, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_admin = {"Authorization": "Bearer mock:admin"}
    mock_human = {"Authorization": "Bearer human:demo"}
    assert client.post(f"{BASE}/reset", headers=mock_admin).status_code == 200
    assert client.post(f"{BASE}/reset", headers=mock_human).status_code == 403
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("PLANNING_MOCK", raising=False)
    assert client.post(f"{BASE}/reset", headers=mock_admin).status_code == 401
    res = client.post(f"{BASE}/deployments/DEP-1/approve", json={}, headers=mock_human)
    assert res.status_code == 401


def test_fail_closed_without_configured_tokens(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("PLANNING_MOCK", raising=False)
    monkeypatch.delenv("AX_AGENT_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    for headers in (ADMIN, HUMAN, AGENT):
        assert client.post(f"{BASE}/reset", headers=headers).status_code == 401
        res = client.post(f"{BASE}/actions", json={"type": "markRead"}, headers=headers)
        assert res.status_code == 401


def test_vcs_token_never_in_responses(client) -> None:
    secret = "ghp_planningSecret0000abcd"
    saved = client.post(
        f"{BASE}/vcs/connections",
        json={"id": "shop", "kind": "github", "repo": "acme/shop", "token": secret},
        headers=ADMIN,
    )
    assert saved.status_code == 200
    legacy = client.post(
        f"{BASE}/vcs", json={"kind": "github", "repo": "acme/web", "token": secret}, headers=ADMIN
    )
    assert legacy.status_code == 200
    bad = client.post(
        f"{BASE}/vcs/connections",
        json={"id": "bad id!", "kind": "github", "repo": "acme/x", "token": secret},
        headers=ADMIN,
    )
    assert bad.status_code == 400
    responses = [saved, legacy, bad]
    for path in ("/vcs", "/vcs/connections", "/state", "/projects"):
        responses.append(client.get(f"{BASE}{path}"))
    for res in responses:
        assert secret not in res.text
    rows = client.get(f"{BASE}/vcs/connections").json()["data"]
    shop = next(r for r in rows if r["id"] == "shop")
    assert shop["token_set"] is True and shop["token_hint"].endswith("abcd")
    assert "token" not in shop
