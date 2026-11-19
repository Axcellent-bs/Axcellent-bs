"""계정·로그인 (사람별 토큰 1단계).

부트스트랩 운영자, 비밀번호 확인, 실패 횟수 제한, 세션 토큰으로 쓰기.

공용 사람 토큰은 계속 받는다(지금 동작 그대로).
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth
from ax_dashboard_api import accounts, auth
from ax_dashboard_api.main import app

BASE = "/api/dashboard/auth"
BOOT_EMAIL = "ops@example.com"
BOOT_PW = "boot-pass-for-tests"  # 테스트용 자리값


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(accounts.ACCOUNTS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.REVOKED_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)  # 테스트용 자리값
    monkeypatch.setenv(accounts.BOOTSTRAP_ENV, f"{BOOT_EMAIL}:{BOOT_PW}")
    accounts.reset_failures_for_tests()
    human_auth.reset_revoked_for_tests()
    c = TestClient(app)
    c.headers.pop("authorization")  # conftest의 공용 토큰을 빼고 시작
    return c


def _login(client: TestClient, email: str = BOOT_EMAIL, pw: str = BOOT_PW):
    return client.post(BASE + "/login", json={"email": email, "password": pw})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _op_token(client: TestClient) -> str:
    res = _login(client)
    assert res.status_code == 200, res.text
    return res.json()["data"]["token"]


def test_bootstrap_operator_logs_in_and_the_file_keeps_only_a_hash(client, tmp_path) -> None:
    res = _login(client, email="OPS@Example.com ")
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["account"]["role"] == "operator" and data["account"]["email"] == BOOT_EMAIL
    assert data["token"].startswith("axs1.")
    me = client.get(BASE + "/me", headers=_bearer(data["token"])).json()["data"]
    assert me["account"]["name"] == "ops"
    text = (tmp_path / accounts.ACCOUNTS_FILE).read_text(encoding="utf-8")
    assert BOOT_PW not in text and "scrypt$" in text
    assert "password_hash" not in json.dumps(me)


def test_bootstrap_is_ignored_once_accounts_exist(client, monkeypatch) -> None:
    _op_token(client)
    monkeypatch.setenv(accounts.BOOTSTRAP_ENV, "other@example.com:another-password")
    assert _login(client, "other@example.com", "another-password").status_code == 401


def test_a_wrong_password_or_unknown_email_fails_the_same_way(client) -> None:
    for email, pw in ((BOOT_EMAIL, "wrong-password"), ("nobody@example.com", BOOT_PW)):
        res = _login(client, email, pw)
        assert res.status_code == 401 and res.json()["error"]["code"] == "LOGIN_FAILED"


def test_five_failures_in_a_row_lock_the_account_even_for_the_right_password(client) -> None:
    for _ in range(accounts.MAX_FAILURES):
        assert _login(client, pw="wrong-password").status_code == 401
    res = _login(client)
    assert res.status_code == 429 and res.json()["error"]["code"] == "LOGIN_LOCKED"


def test_a_success_resets_the_failure_count(client) -> None:
    for _ in range(accounts.MAX_FAILURES - 1):
        _login(client, pw="wrong-password")
    assert _login(client).status_code == 200
    for _ in range(accounts.MAX_FAILURES - 1):
        _login(client, pw="wrong-password")
    assert _login(client).status_code == 200


def test_the_lock_lifts_after_the_wait(client, monkeypatch) -> None:
    for _ in range(accounts.MAX_FAILURES):
        _login(client, pw="wrong-password")
    real = accounts.time.time
    monkeypatch.setattr(accounts.time, "time", lambda: real() + accounts.LOCK_SECONDS + 1)
    assert _login(client).status_code == 200


def test_login_is_off_without_a_session_secret(client, monkeypatch) -> None:
    monkeypatch.delenv(human_auth.SESSION_SECRET_ENV)
    assert _login(client).json()["error"]["code"] == "LOGIN_DISABLED"


def test_a_session_token_passes_the_write_check(client) -> None:
    token = _op_token(client)
    # 사람 토큰 검사를 지나 업무 처리에서 404 (없는 클레임)
    res = client.post("/api/dashboard/claims/999/approve", json={}, headers=_bearer(token))
    assert res.status_code == 404
    assert auth.human_from_authorization(f"Bearer {token}") == "ops"


def test_the_shared_human_token_still_writes(client) -> None:
    res = client.post(
        "/api/dashboard/claims/999/approve", json={}, headers=_bearer("test-human-token")
    )
    assert res.status_code == 404


def test_expired_and_forged_session_tokens_cannot_write(client) -> None:
    expired, _ = human_auth.issue_session(account_id="a", name="x", role="member", now=0)
    forged = _op_token(client)[:-3] + "AAA"
    for token, code in ((expired, "SESSION_EXPIRED"), (forged, "HUMAN_TOKEN_INVALID")):
        res = client.post("/api/dashboard/queue-entries/process", json={}, headers=_bearer(token))
        assert res.json()["error"]["code"] == code


def test_logout_ends_the_session_for_writes_and_me(client) -> None:
    token = _op_token(client)
    assert client.post(BASE + "/logout", headers=_bearer(token)).status_code == 200
    assert client.get(BASE + "/me", headers=_bearer(token)).json()["error"]["code"] == (
        "SESSION_REVOKED"
    )
    res = client.post("/api/dashboard/claims/999/approve", json={}, headers=_bearer(token))
    assert res.status_code == 401


def test_operator_creates_accounts_and_the_new_person_logs_in(client) -> None:
    token = _op_token(client)
    body = {
        "name": "태윤",
        "email": "ty@example.com",
        "role": "operator",
        "password": "ty-pass-1234",
    }
    res = client.post(BASE + "/accounts", json=body, headers=_bearer(token))
    assert res.status_code == 200, res.text
    assert "password_hash" not in res.json()["data"]
    again = client.post(BASE + "/accounts", json=body, headers=_bearer(token))
    assert again.json()["error"]["code"] == "EMAIL_TAKEN"
    login = _login(client, "ty@example.com", "ty-pass-1234").json()["data"]
    assert login["account"]["name"] == "태윤"
    listed = client.get(BASE + "/accounts", headers=_bearer(token)).json()["data"]
    assert {r["email"] for r in listed} == {BOOT_EMAIL, "ty@example.com"}


def test_admin_creates_only_members_and_cannot_list(client) -> None:
    op = _op_token(client)
    admin = {
        "name": "관리자",
        "email": "ad@example.com",
        "role": "admin",
        "password": "ad-pass-1234",
    }
    client.post(BASE + "/accounts", json=admin, headers=_bearer(op))
    token = _login(client, admin["email"], admin["password"]).json()["data"]["token"]
    member = {"name": "회원", "email": "m@example.com", "role": "member", "password": "m-pass-1234"}
    assert client.post(BASE + "/accounts", json=member, headers=_bearer(token)).status_code == 200
    other_admin = {**admin, "email": "ad2@example.com"}
    res = client.post(BASE + "/accounts", json=other_admin, headers=_bearer(token))
    assert res.status_code == 403
    assert client.get(BASE + "/accounts", headers=_bearer(token)).status_code == 403


def test_the_shared_token_cannot_manage_accounts(client) -> None:
    body = {"name": "x", "email": "x@example.com", "role": "member", "password": "x-pass-1234"}
    res = client.post(BASE + "/accounts", json=body, headers=_bearer("test-human-token"))
    assert res.status_code == 403 and res.json()["error"]["code"] == "SESSION_REQUIRED"
    assert client.get(BASE + "/me").status_code == 401


def test_short_passwords_and_bad_emails_are_refused(client) -> None:
    token = _op_token(client)
    short = {"name": "x", "email": "x@example.com", "role": "member", "password": "short"}
    bad = {**short, "email": "not-an-email", "password": "long-enough-pw"}
    res = client.post(BASE + "/accounts", json=short, headers=_bearer(token))
    assert res.json()["error"]["code"] == "PASSWORD_TOO_SHORT"
    assert client.post(BASE + "/accounts", json=bad, headers=_bearer(token)).status_code == 422
