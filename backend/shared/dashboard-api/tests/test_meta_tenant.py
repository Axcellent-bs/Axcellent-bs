"""응답 meta.tenant_id가 로그인 세션의 회사를 따른다 (5단계 최소 범위)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_dashboard_api.envelope import SessionCompanyMiddleware, ok


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AX_SESSION_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.delenv("TENANT_ID", raising=False)
    app = FastAPI()
    app.add_middleware(SessionCompanyMiddleware)

    @app.get("/probe")
    def probe() -> dict:
        return ok({})

    return TestClient(app)


def _session(company: str) -> str:
    token, _ = human_auth.issue_session(
        account_id="acc_1", name="tester", role="admin", company_id=company, company_role="admin"
    )
    return token


def _tenant(client: TestClient, auth: str | None = None) -> str:
    headers = {"Authorization": f"Bearer {auth}"} if auth else {}
    return client.get("/probe", headers=headers).json()["meta"]["tenant_id"]


def test_no_session_keeps_the_legacy_value(client: TestClient) -> None:
    assert _tenant(client) == "shop-demo"


def test_catbooks_session_keeps_the_legacy_value(client: TestClient) -> None:
    assert _tenant(client, _session("catbooks")) == "shop-demo"


def test_other_company_session_shows_its_own_id(client: TestClient) -> None:
    assert _tenant(client, _session("nubitour")) == "nubitour"


def test_requests_do_not_leak_into_each_other(client: TestClient) -> None:
    assert _tenant(client, _session("nubitour")) == "nubitour"
    assert _tenant(client) == "shop-demo"


def test_broken_token_is_ignored_here(client: TestClient) -> None:
    assert _tenant(client, "not-a-token") == "shop-demo"
