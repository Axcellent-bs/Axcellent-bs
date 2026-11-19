"""로그인 세션 토큰(`axs1.`)으로 기획 쓰기(`ax_workflow.auth`): 통과·위조·로그아웃·공용·이름."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_workflow.auth import AuthError, check_admin, check_caller, check_human
from planning_agent.main import app

SECRET = "s" * 40  # 테스트용 자리값 (실제 키 아님)
REJECT = "/api/planning/requests/REQ-999/reject"


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, SECRET)
    monkeypatch.setenv(human_auth.REVOKED_FILE_ENV, str(tmp_path / "revoked.json"))
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "공용:shared-secret")
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=admin-test-token")
    # 실제 모드: 목 스위치를 끈다 (`human:<이름>`은 안 된다)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("PLANNING_MOCK", raising=False)
    human_auth.reset_revoked_for_tests()
    return human_auth.issue_session(account_id="acc-1", name="예진", role="admin")[0]


def _auth(token: str) -> str:
    return f"Bearer {token}"


def _forged(token: str) -> str:
    prefix, body, sig = token.split(".")
    return f"{prefix}.{body}.{'A' * len(sig)}"


def test_a_session_token_is_a_human_with_the_account_name(session: str) -> None:
    assert check_human(_auth(session)) == "예진"
    assert check_caller(_auth(session)) == "예진"
    with pytest.raises(AuthError) as err:  # 사람이라 운영자(admin 에이전트) 자리는 아니다
        check_admin(_auth(session))
    assert err.value.status_code == 403
    client = TestClient(app)
    res = client.post(REJECT, json={}, headers={"Authorization": _auth(session)})
    assert res.status_code not in (401, 403)


def test_forged_and_logged_out_session_tokens_are_refused(session: str) -> None:
    with pytest.raises(AuthError) as err:
        check_human(_auth(_forged(session)))
    assert err.value.status_code == 403
    human_auth.revoke_session(human_auth.verify_session(session))
    with pytest.raises(AuthError) as err:
        check_caller(_auth(session))
    assert err.value.status_code == 401
    client = TestClient(app)
    res = client.post(REJECT, json={}, headers={"Authorization": _auth(session)})
    assert res.status_code == 401


def test_shared_token_still_works_until_switched_off(
    session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert check_human(_auth("shared-secret")) == "공용"
    monkeypatch.setenv(human_auth.ALLOW_SHARED_ENV, "0")
    with pytest.raises(AuthError):
        check_human(_auth("shared-secret"))
