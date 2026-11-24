"""운영자 화면에서 고객사 등록 + 그 회사 첫 관리자 만들기, 고객사 관리자는 자기 회사 계정만.

7단계(누비투어·스터디온 연결)를 실제 고객 첫 연결처럼 화면에서만 하려고 만든 경로.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import companies as company_list
from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth
from ax_dashboard_api import accounts
from ax_dashboard_api.main import app

BASE = "/api/dashboard/auth"
BOOT_EMAIL = "ops@example.com"
BOOT_PW = "boot-pass-for-tests"  # 테스트용 자리값
PW = "member-pass-1234"  # 테스트용 자리값


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(accounts.ACCOUNTS_FILE_ENV, raising=False)
    monkeypatch.delenv(company_list.COMPANIES_FILE_ENV, raising=False)
    monkeypatch.delenv(company_list.COMPANIES_DIR_ENV, raising=False)
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


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, email: str, password: str) -> str:
    res = client.post(BASE + "/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["data"]["token"]


def _op(client: TestClient) -> str:
    return _login(client, BOOT_EMAIL, BOOT_PW)


def _issue(client, token, **company):
    res = client.post(
        "/api/dashboard/connection-keys/issue", json={"memo": "", **company}, headers=_bearer(token)
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]["key"]


def _signup(client, key, name="누비관리", email="n@example.com"):
    body = {"key": key, "name": name, "email": email, "password": PW}
    return client.post("/api/dashboard/connection-keys/signup", json=body)


def _companies(tmp_path: Path) -> list[str]:
    path = tmp_path / "companies.json"
    if not path.exists():
        return ["catbooks"]
    return [c["company_id"] for c in json.loads(path.read_text(encoding="utf-8"))["companies"]]


def _account(client, token, name, email, role="admin"):
    body = {"name": name, "email": email, "role": role, "password": PW}
    return client.post(BASE + "/accounts", json=body, headers=_bearer(token))


def _rows(tmp_path: Path) -> list[dict]:
    return json.loads((tmp_path / accounts.ACCOUNTS_FILE).read_text(encoding="utf-8"))["accounts"]


def _id_of(tmp_path: Path, email: str) -> str:
    return next(r["id"] for r in _rows(tmp_path) if r["email"] == email)


def _check(client, key, token=None):
    path = "/check" if token else "/check-public"
    headers = _bearer(token) if token else {}
    res = client.post("/api/dashboard/connection-keys" + path, json={"key": key}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _connect(client, token, key, name="누비투어"):
    body = {"key": key, "name": name, "industry": "여행사", "network": ""}
    return client.post("/api/dashboard/connection-keys/connect", json=body, headers=_bearer(token))


def _nubi_admin(client, op) -> tuple[str, str]:
    """누비투어 전용 키를 발급하고 그 키로 첫 관리자 가입. (키, 관리자 세션 토큰)"""
    key = _issue(client, op, company_name="누비투어", industry="여행사", company_id="nubi")
    res = _signup(client, key)
    assert res.status_code == 200, res.text
    return key, res.json()["data"]["token"]


def test_issuing_a_company_key_registers_the_company_and_keeps_catbooks(client, tmp_path) -> None:
    op = _op(client)
    _issue(client, op, company_name="누비투어", industry="여행사", company_id="nubi")
    assert _companies(tmp_path) == ["catbooks", "nubi"]
    auto = _issue(client, op, company_name="스터디온에듀")  # 회사 ID를 비우면 자동
    assert len(_companies(tmp_path)) == 3
    assert _check(client, auto)["company_id"].startswith("c-")
    # 같은 회사 ID로 새 회사는 못 만들고, ID만 주면 있는 회사 전용 키
    res = client.post(
        "/api/dashboard/connection-keys/issue",
        json={"company_name": "다른", "company_id": "nubi"},
        headers=_bearer(op),
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "COMPANY_EXISTS"
    again = _issue(client, op, company_id="nubi")
    assert _check(client, again)["company_id"] == "nubi"
    res = client.post(
        "/api/dashboard/connection-keys/issue", json={"company_id": "nope"}, headers=_bearer(op)
    )
    assert res.json()["error"]["code"] == "UNKNOWN_COMPANY"
    # 예전처럼 회사 없이 발급한 키도 된다(캣북스 키 그대로)
    plain = _issue(client, op)
    assert "company_id" not in _check(client, plain, op)


def test_broken_company_file_is_not_overwritten(client, tmp_path) -> None:
    op = _op(client)
    broken = tmp_path / "companies.json"
    broken.write_text("{not json", encoding="utf-8")
    res = client.post(
        "/api/dashboard/connection-keys/issue",
        json={"company_name": "누비투어", "company_id": "nubi"},
        headers=_bearer(op),
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "COMPANIES_FILE_BROKEN"
    assert broken.read_text(encoding="utf-8") == "{not json"


def test_first_admin_signs_up_with_the_company_key_then_connects(client, tmp_path) -> None:
    op = _op(client)
    key = _issue(client, op, company_name="누비투어", industry="여행사", company_id="nubi")
    seen = _check(client, key)  # 로그인 전
    assert seen["ok"] and seen["signup"] and seen["company_id"] == "nubi"
    assert seen["company"]["name"] == "누비투어"
    res = _signup(client, key)
    assert res.status_code == 200, res.text
    token = res.json()["data"]["token"]
    row = next(r for r in _rows(tmp_path) if r["email"] == "n@example.com")
    assert row["role"] == "admin" and row["memberships"] == [
        {"company_id": "nubi", "role": "admin"}
    ]
    me = client.get(BASE + "/me", headers=_bearer(token)).json()["data"]
    assert me["company_id"] == "nubi"  # 계정 기준으로 누비투어에 들어간다
    # 키는 가입에 한 번만
    assert _check(client, key)["signup"] is False
    again = _signup(client, key, name="또", email="x@example.com")
    assert again.status_code == 409 and again.json()["error"]["code"] == "KEY_SIGNUP_USED"
    # 그 세션으로 연결하면 누비투어 줄에만 저장된다
    assert _connect(client, token, key).status_code == 200
    keys = json.loads((tmp_path / "connection_keys.json").read_text(encoding="utf-8"))
    assert keys["companies"]["nubi"]["name"] == "누비투어"
    assert keys.get("company") is None  # 캣북스 줄은 그대로(없던 그대로)


def test_signup_needs_a_company_key_and_no_admin_yet(client) -> None:
    op = _op(client)
    plain = _issue(client, op)
    res = _signup(client, plain)
    assert res.status_code == 422 and res.json()["error"]["code"] == "KEY_NOT_FOR_SIGNUP"
    res = _signup(client, "AXC-AAAA-BBBB-CCCC")
    assert res.status_code == 422
    _nubi_admin(client, op)
    second = _issue(client, op, company_id="nubi")
    res = _signup(client, second, name="둘째", email="two@example.com")
    assert res.status_code == 409 and res.json()["error"]["code"] == "COMPANY_HAS_ADMIN"


def test_catbooks_admin_cannot_connect_with_another_company_key(client, tmp_path) -> None:
    op = _op(client)
    plain = _issue(client, op)
    assert _account(client, op, "캣관리", "a@example.com").status_code == 200
    cat_admin = _login(client, "a@example.com", PW)
    assert _connect(client, cat_admin, plain, name="캣북스").status_code == 200
    before = json.loads((tmp_path / "connection_keys.json").read_text(encoding="utf-8"))["company"]
    nubi_key = _issue(client, op, company_name="누비투어", company_id="nubi")
    seen = _check(client, nubi_key, cat_admin)
    assert seen["ok"] is False and "다른 회사용" in seen["reason"]
    res = _connect(client, cat_admin, nubi_key)
    assert res.status_code == 422 and res.json()["error"]["code"] == "KEY_OTHER_COMPANY"
    after = json.loads((tmp_path / "connection_keys.json").read_text(encoding="utf-8"))["company"]
    assert after == before  # 캣북스 연결은 그대로


def test_admin_sees_and_touches_only_own_company_accounts(client, tmp_path) -> None:
    op = _op(client)
    assert _account(client, op, "캣관리", "a@example.com").status_code == 200
    assert _account(client, op, "캣회원", "m@example.com", role="member").status_code == 200
    _, nubi_admin = _nubi_admin(client, op)
    # 누비투어 관리자가 일반회원을 만들면 누비투어 소속
    res = _account(client, nubi_admin, "누비회원", "nm@example.com", role="member")
    assert res.status_code == 200
    row = next(r for r in _rows(tmp_path) if r["email"] == "nm@example.com")
    assert row["memberships"] == [{"company_id": "nubi", "role": "member"}]

    seen = client.get(BASE + "/accounts", headers=_bearer(nubi_admin)).json()["data"]
    assert {r["email"] for r in seen} == {"n@example.com", "nm@example.com"}
    cat_admin = _login(client, "a@example.com", PW)
    seen = client.get(BASE + "/accounts", headers=_bearer(cat_admin)).json()["data"]
    assert {r["email"] for r in seen} == {"a@example.com", "m@example.com"}

    cat_member = _id_of(tmp_path, "m@example.com")
    res = client.post(
        f"{BASE}/accounts/{cat_member}/reset-password",
        json={"new_password": "new-pass-12345"},
        headers=_bearer(nubi_admin),
    )
    assert res.status_code == 404
    res = client.post(
        f"{BASE}/accounts/{cat_member}/update", json={"dept": "영업"}, headers=_bearer(nubi_admin)
    )
    assert res.status_code == 404


def test_last_admin_is_counted_per_company(client, tmp_path) -> None:
    op = _op(client)
    assert _account(client, op, "캣관리1", "a1@example.com").status_code == 200
    assert _account(client, op, "캣관리2", "a2@example.com").status_code == 200
    _nubi_admin(client, op)
    nubi_admin = _id_of(tmp_path, "n@example.com")
    res = client.post(
        f"{BASE}/accounts/{nubi_admin}/update", json={"role": "member"}, headers=_bearer(op)
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "LAST_ADMIN"
    # 캣북스는 관리자가 둘이라 하나는 내릴 수 있다
    cat_admin = _id_of(tmp_path, "a2@example.com")
    res = client.post(
        f"{BASE}/accounts/{cat_admin}/update", json={"role": "member"}, headers=_bearer(op)
    )
    assert res.status_code == 200, res.text
