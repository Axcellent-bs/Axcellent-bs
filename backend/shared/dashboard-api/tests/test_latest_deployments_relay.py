"""도구 연결 › 배포: 서비스별 최근 배포 읽기 중계 (`GET /api/dashboard/deployments/latest`).

화면은 사람 토큰으로 dashboard-api를 부르고, dashboard-api는 자기 에이전트 토큰으로 인프라
(`GET /api/infra/deployments/latest`)를 읽는다.
인프라가 안 되면 오류로 돌려준다(빈 목록으로 숨기지 않는다).
"""

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_dashboard_api import relay
from ax_dashboard_api.main import app

PATH = "/api/dashboard/deployments/latest"
UPSTREAM = "/api/infra/deployments/latest"
ROW = {
    "service": "shop-api",
    "version": "v3.0.1",
    "deployed_at": "2026-11-18T05:05:00Z",
    "source": "github_actions",
    "is_rollback": False,
    "version_from_sha": False,
}


class Infra:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.body: Any = {"data": [ROW]}
        self.down: Exception | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise self.down
        if request.url.path != UPSTREAM:
            return httpx.Response(404, json={"detail": "x"})
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def infra() -> Any:
    fake = Infra()
    relay.use_client(httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))
    yield fake
    relay.use_client(None)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, infra: Infra) -> TestClient:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("AX_AGENT_TOKENS", "dashboard-api=dash-agent-token")
    monkeypatch.setenv("AX_INCIDENT_URL", "http://infra.test")
    return TestClient(app)


def test_relays_rows_with_the_dashboard_agent_token(live: TestClient, infra: Infra) -> None:
    body = live.get(PATH).json()
    assert body["data"] == [ROW] and body["meta"]["total"] == 1
    [req] = infra.requests
    assert (req.method, req.url.host, req.url.path) == ("GET", "infra.test", UPSTREAM)
    # 사람 토큰은 넘기지 않고 dashboard-api 에이전트 토큰으로 읽는다
    assert req.headers["authorization"] == "Bearer dash-agent-token"


def test_keeps_only_known_fields(live: TestClient, infra: Infra) -> None:
    infra.body = {"data": [{**ROW, "change_summary": "github_actions 실행 12 · alice"}]}
    assert live.get(PATH).json()["data"] == [ROW]


def test_needs_a_human_token(live: TestClient, infra: Infra) -> None:
    live.headers.pop("authorization")
    assert live.get(PATH).status_code == 401
    agent = live.get(PATH, headers={"Authorization": "Bearer mock:infra"})
    assert agent.status_code == 403
    assert infra.requests == []


def test_upstream_failures_are_errors_not_empty(live: TestClient, infra: Infra) -> None:
    infra.down = httpx.ConnectError("refused")
    down = live.get(PATH)
    assert down.status_code == 502 and down.json()["error"]["code"] == "UPSTREAM_ERROR"
    infra.down = None
    infra.status = 403  # 인프라가 토큰을 거부
    assert live.get(PATH).status_code == 502
    infra.status = 200
    infra.body = {"data": {"not": "a list"}}
    assert live.get(PATH).status_code == 502


def test_mock_mode_has_no_made_up_rows(monkeypatch: pytest.MonkeyPatch, infra: Infra) -> None:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    assert TestClient(app).get(PATH).json()["data"] == []
    assert infra.requests == []


def _session(company: str) -> str:
    from ax_agent_template import human_auth

    token, _ = human_auth.issue_session(
        account_id="acc_1", name="예진", role="admin", company_id=company, company_role="admin"
    )
    return token


def test_sends_the_session_company_to_infra(
    live: TestClient, infra: Infra, monkeypatch: pytest.MonkeyPatch
) -> None:
    """화면 세션의 회사를 실어 인프라가 그 회사 배포 이력만 주게 한다(11/26 회사 분리).

    도구 연결 화면은 공용 토큰 + `X-AX-Session`(로그인 세션)으로 부른다."""
    monkeypatch.setenv("AX_SESSION_SECRET", "s" * 40)
    live.get(PATH, headers={"X-AX-Session": _session("c-cf2f0d")})
    live.get(PATH, headers={"X-AX-Session": _session("catbooks")})
    live.get(PATH)  # 세션 없음: 싣지 않는다(예전 그대로)
    sent = [r.headers.get("x-ax-company") for r in infra.requests]
    assert sent == ["c-cf2f0d", "catbooks", None]
    assert all(r.headers["authorization"] == "Bearer dash-agent-token" for r in infra.requests)


def test_forged_session_header_is_ignored(live: TestClient, infra: Infra) -> None:
    live.get(PATH, headers={"X-AX-Session": "not-a-session"})
    assert infra.requests[-1].headers.get("x-ax-company") is None
