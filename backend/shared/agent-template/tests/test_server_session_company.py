"""화면용 HTTP 경로는 로그인 세션의 회사로 읽는다 (11/24 누비투어 연결)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ax_agent_template import company_scope
from ax_agent_template import human_auth as ha


@pytest.fixture
def client(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(ha.SESSION_SECRET_ENV, "s" * 40)  # 테스트용 자리값

    @app.get("/api/whose")
    def whose() -> dict:
        return {"data": {"company": company_scope.active()}}

    return TestClient(app)


def _session(company_id: str | None = None, **kwargs) -> dict[str, str]:
    token, _ = ha.issue_session(
        account_id="acc-1", name="관리자", role="admin", company_id=company_id, **kwargs
    )
    return {"Authorization": f"Bearer {token}"}


def test_a_session_of_another_company_reads_that_company(client: TestClient) -> None:
    res = client.get("/api/whose", headers=_session("nubi", company_role="admin"))
    assert res.json()["data"]["company"] == "nubi"


def test_catbooks_session_scopes_catbooks_shared_and_anonymous_stay_unscoped(
    client: TestClient,
) -> None:
    # 11/27: 캣북스 세션도 캣북스를 건다(예전에는 회사 없음 → 다른 회사 건까지 보였다)
    for headers in (
        _session("catbooks", company_role="admin"),
        _session("shop-demo", company_role="admin"),  # 별칭도 캣북스
    ):
        assert client.get("/api/whose", headers=headers).json()["data"]["company"] == "catbooks"
    for headers in (
        {"Authorization": "Bearer shared-human-token"},
        {"Authorization": "Bearer axs1.broken.token"},
        {},
    ):
        assert client.get("/api/whose", headers=headers).json()["data"]["company"] is None


def test_no_company_session_does_not_fall_back_to_catbooks_scope(client: TestClient) -> None:
    headers = {"Authorization": _session(no_company=True)["Authorization"]}
    # 소속 없는 세션은 회사를 걸지 않는다(업무 경로는 각 경로의 인증 검사가 403으로 막는다)
    assert client.get("/api/whose", headers=headers).json()["data"]["company"] is None
