"""로그인 세션 토큰(`axs1.`)으로 총괄 사람 결정: 통과·위조·로그아웃·공용 토큰·이름."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_orchestrator.auth import require_admin, require_human
from ax_orchestrator.main import app, state

SECRET = "s" * 40  # 테스트용 자리값 (실제 키 아님)
HOLD = "/api/incidents/ctx_x/holds/rollback"


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, SECRET)
    monkeypatch.setenv(human_auth.REVOKED_FILE_ENV, str(tmp_path / "revoked.json"))
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "공용:shared-secret")
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MOCK", raising=False)
    human_auth.reset_revoked_for_tests()
    return human_auth.issue_session(account_id="acc-1", name="예진", role="operator")[0]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(state, "mock", True)
    monkeypatch.setattr(state, "latency", 0.0)
    monkeypatch.setattr(state, "manager", None)
    with TestClient(app) as c:
        yield c


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_a_session_token_decides_with_the_account_name(client, session: str) -> None:
    assert require_human(f"Bearer {session}") == "예진"
    # 관문을 통과하고, 대기 중인 보류가 없어 404
    assert client.post(HOLD, json={"approved": True}, headers=_h(session)).status_code == 404
    # 사람 토큰이라 에이전트(admin) 자리는 403
    assert client.post("/api/demo/incidents", json={}, headers=_h(session)).status_code == 403
    with pytest.raises(HTTPException):
        require_admin(f"Bearer {session}")


def test_forged_and_logged_out_session_tokens_are_refused(client, session: str) -> None:
    prefix, body, sig = session.split(".")
    forged = f"{prefix}.{body}.{'A' * len(sig)}"
    assert client.post(HOLD, json={"approved": True}, headers=_h(forged)).status_code == 403
    human_auth.revoke_session(human_auth.verify_session(session))
    assert client.post(HOLD, json={"approved": True}, headers=_h(session)).status_code == 401


def test_shared_token_still_works_until_switched_off(
    client, session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert require_human("Bearer shared-secret") == "공용"
    shared = _h("shared-secret")
    assert client.post(HOLD, json={"approved": True}, headers=shared).status_code == 404
    monkeypatch.setenv(human_auth.ALLOW_SHARED_ENV, "0")
    assert client.post(HOLD, json={"approved": True}, headers=shared).status_code == 401
