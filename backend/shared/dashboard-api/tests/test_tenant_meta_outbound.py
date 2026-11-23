"""에이전트를 부를 때 요청 meta에 세션 회사가 실린다 (5단계 최소 범위, 보내는 쪽)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_dashboard_api import support_client
from ax_dashboard_api.envelope import SessionCompanyMiddleware, tenant_meta


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AX_SESSION_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.setattr(support_client.config, "agent_token", lambda name: "test-agent-token")
    app = FastAPI()
    app.add_middleware(SessionCompanyMiddleware)

    @app.get("/meta")
    def meta() -> dict:
        return {
            "plain": tenant_meta(),
            "explicit": tenant_meta("nubitour"),
            "support": dict(support_client._meta("ctx_x", "k"))["tenant_id"],
        }

    return TestClient(app)


def _auth(company: str) -> dict[str, str]:
    token, _ = human_auth.issue_session(
        account_id="acc_1", name="t", role="admin", company_id=company, company_role="admin"
    )
    return {"Authorization": f"Bearer {token}"}


def test_no_session_adds_nothing(probe: TestClient) -> None:
    body = probe.get("/meta").json()
    assert body["plain"] == {}
    assert body["explicit"] == {"tenant_id": "nubitour"}  # 명시한 값은 그대로
    assert body["support"] == "shop-demo"  # 기본값이 그대로


def test_catbooks_session_keeps_the_default(probe: TestClient) -> None:
    body = probe.get("/meta", headers=_auth("catbooks")).json()
    assert body["plain"] == {}
    assert body["support"] == "shop-demo"


def test_other_company_session_is_carried(probe: TestClient) -> None:
    body = probe.get("/meta", headers=_auth("nubitour")).json()
    assert body["plain"] == {"tenant_id": "nubitour"}
    assert body["support"] == "nubitour"


def test_explicit_wins_over_the_session(probe: TestClient) -> None:
    # 개인 MCP 창구가 넘기는 토큰의 회사는 세션 회사보다 앞선다
    body = probe.get("/meta", headers=_auth("studyon")).json()
    assert body["explicit"] == {"tenant_id": "nubitour"}
