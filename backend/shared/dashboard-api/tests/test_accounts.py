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
    monkeypatch.delenv(human_auth.GENERATIONS_FILE_ENV, raising=False)
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


# ---- 화면 로그인 (사람별 토큰 2단계) ----


def test_status_tells_the_screen_whether_server_login_is_on(client, monkeypatch) -> None:
    assert client.get(BASE + "/status").json()["data"] == {"login_enabled": True}
    monkeypatch.delenv(human_auth.SESSION_SECRET_ENV)
    assert client.get(BASE + "/status").json()["data"] == {"login_enabled": False}


def _make(client: TestClient, op: str, role: str, email: str) -> str:
    pw = f"{role}-pass-1234"
    body = {"name": role, "email": email, "role": role, "password": pw}
    assert client.post(BASE + "/accounts", json=body, headers=_bearer(op)).status_code == 200
    return _login(client, email, pw).json()["data"]["token"]


def test_connection_keys_check_the_role_of_a_session_token(client) -> None:
    op = _op_token(client)
    admin = _make(client, op, "admin", "ad@example.com")
    member = _make(client, op, "member", "m@example.com")
    keys = "/api/dashboard/connection-keys"
    # 키 발급은 운영자만
    for token in (admin, member):
        res = client.post(keys + "/issue", json={"memo": ""}, headers=_bearer(token))
        assert res.status_code == 403 and res.json()["error"]["code"] == "ROLE_REQUIRED"
    issued = client.post(keys + "/issue", json={"memo": ""}, headers=_bearer(op)).json()["data"]
    # 연결은 고객사 관리자만
    connect = {"key": issued["key"], "name": "새벽커피", "network": "10.0.0.0/24"}

    def post(path: str, token: str, body: dict | None = None) -> int:
        return client.post(keys + path, json=body, headers=_bearer(token)).status_code

    assert post("/connect", op, connect) == 403
    assert post("/connect", member, connect) == 403
    assert post("/connect", admin, connect) == 200
    assert post("/disconnect", member) == 403
    assert post("/revoke", admin, {"id": issued["id"]}) == 403


def test_the_shared_token_still_uses_connection_keys(client) -> None:
    res = client.post(
        "/api/dashboard/connection-keys/issue",
        json={"memo": ""},
        headers=_bearer("test-human-token"),
    )
    assert res.status_code == 200, res.text


# ---- 비밀번호 바꾸기·초기화 ----

NEW_PW = "new-pass-for-tests"  # 테스트용 자리값


def _change(client: TestClient, token: str, current: str, new: str = NEW_PW):
    body = {"current_password": current, "new_password": new}
    return client.post(BASE + "/password", json=body, headers=_bearer(token))


def _write_ok(client: TestClient, token: str) -> int:
    # 사람 토큰 검사를 지나면 업무 처리에서 404 (없는 클레임)
    res = client.post("/api/dashboard/claims/999/approve", json={}, headers=_bearer(token))
    return res.status_code


def test_changing_the_password_ends_old_sessions_and_returns_a_new_one(client) -> None:
    old = _op_token(client)
    other_old = _op_token(client)  # 다른 기기에서 로그인한 세션
    res = _change(client, old, BOOT_PW)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["account"]["email"] == BOOT_EMAIL and "password_hash" not in data["account"]
    for token in (old, other_old):
        me = client.get(BASE + "/me", headers=_bearer(token))
        assert me.status_code == 401 and me.json()["error"]["code"] == "SESSION_ENDED"
        assert _write_ok(client, token) == 401
    assert client.get(BASE + "/me", headers=_bearer(data["token"])).status_code == 200
    assert _write_ok(client, data["token"]) == 404
    assert _login(client).status_code == 401
    assert _login(client, pw=NEW_PW).status_code == 200


def test_a_wrong_current_password_is_401_and_counts_toward_the_lock(client) -> None:
    token = _op_token(client)
    res = _change(client, token, "wrong-password")
    assert res.status_code == 401 and res.json()["error"]["code"] == "PASSWORD_WRONG"
    for _ in range(accounts.MAX_FAILURES - 1):
        _login(client, pw="wrong-password")
    # 비밀번호 바꾸기 1번 + 로그인 4번 = 5번 → 맞는 비밀번호도 막힌다
    assert _login(client).json()["error"]["code"] == "LOGIN_LOCKED"
    locked = _change(client, token, BOOT_PW)
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "LOGIN_LOCKED"
    assert _write_ok(client, token) == 404  # 틀렸을 때는 세션을 끊지 않는다


def test_a_short_or_unchanged_new_password_is_422(client) -> None:
    token = _op_token(client)
    short = _change(client, token, BOOT_PW, "short")
    assert short.status_code == 422 and short.json()["error"]["code"] == "PASSWORD_TOO_SHORT"
    same = _change(client, token, BOOT_PW, BOOT_PW)
    assert same.status_code == 422 and same.json()["error"]["code"] == "PASSWORD_SAME"
    assert _login(client).status_code == 200


def test_the_shared_token_cannot_change_or_reset_passwords(client) -> None:
    shared = _bearer("test-human-token")
    body = {"current_password": BOOT_PW, "new_password": NEW_PW}
    res = client.post(BASE + "/password", json=body, headers=shared)
    assert res.status_code == 403 and res.json()["error"]["code"] == "SESSION_REQUIRED"
    res = client.post(
        BASE + "/accounts/acc-x/reset-password", json={"new_password": NEW_PW}, headers=shared
    )
    assert res.status_code == 403 and res.json()["error"]["code"] == "SESSION_REQUIRED"
    assert client.post(BASE + "/password", json=body).status_code == 401


def _account_id(client: TestClient, token: str) -> str:
    return client.get(BASE + "/me", headers=_bearer(token)).json()["data"]["account"]["id"]


def _reset(client: TestClient, token: str, account_id: str, pw: str = NEW_PW):
    return client.post(
        BASE + f"/accounts/{account_id}/reset-password",
        json={"new_password": pw},
        headers=_bearer(token),
    )


def test_operator_resets_any_account_and_its_old_sessions_end(client) -> None:
    op = _op_token(client)
    admin = _make(client, op, "admin", "ad@example.com")
    admin_id = _account_id(client, admin)
    res = _reset(client, op, admin_id)
    assert res.status_code == 200 and "password_hash" not in res.json()["data"]
    assert client.get(BASE + "/me", headers=_bearer(admin)).json()["error"]["code"] == (
        "SESSION_ENDED"
    )
    assert _write_ok(client, admin) == 401
    assert _login(client, "ad@example.com", "admin-pass-1234").status_code == 401
    assert _login(client, "ad@example.com", NEW_PW).status_code == 200
    assert _write_ok(client, op) == 404  # 초기화한 운영자의 세션은 그대로
    assert _reset(client, op, "acc-none").status_code == 404
    short = _reset(client, op, admin_id, "short")
    assert short.json()["error"]["code"] == "PASSWORD_TOO_SHORT"


def test_admin_resets_only_members_and_members_reset_nobody(client) -> None:
    op = _op_token(client)
    admin = _make(client, op, "admin", "ad@example.com")
    other_admin = _make(client, op, "admin", "ad2@example.com")
    member = _make(client, op, "member", "m@example.com")
    op_id, member_id = _account_id(client, op), _account_id(client, member)
    for target in (op_id, _account_id(client, other_admin)):
        res = _reset(client, admin, target)
        assert res.status_code == 403 and res.json()["error"]["code"] == "ROLE_REQUIRED"
    assert _reset(client, member, member_id).status_code == 403
    assert _reset(client, admin, member_id).status_code == 200
    assert _write_ok(client, member) == 401
    assert _write_ok(client, other_admin) == 404  # 다른 계정은 그대로
    assert _login(client, "m@example.com", NEW_PW).status_code == 200


def test_passwords_never_reach_the_logs(client, caplog) -> None:
    caplog.set_level("DEBUG")
    token = _op_token(client)
    _change(client, token, BOOT_PW)
    assert BOOT_PW not in caplog.text and NEW_PW not in caplog.text
