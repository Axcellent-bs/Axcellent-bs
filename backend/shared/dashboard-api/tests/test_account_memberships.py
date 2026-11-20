"""계정의 회사 소속 (여러 회사 설계 1-3).

- 계정 줄에 `memberships: [{company_id, role}]`. 없는 옛 계정은 읽을 때 캣북스 소속으로 본다
  (관리자·일반회원 → 캣북스의 그 역할, 운영자 → 소속 없음). 파일은 고쳐 쓰지 않는다
- 로그인·내 계정 응답에 `companies`, 세션 토큰에 회사 `cid`·회사 역할 `crole`
- 회사가 캣북스 하나라 로그인·권한 규칙은 그대로다
"""

import base64
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
CATBOOKS_ADMIN = [{"company_id": "catbooks", "role": "admin"}]
CATBOOKS_MEMBER = [{"company_id": "catbooks", "role": "member"}]
AT = "2026-11-20T00:00:00Z"


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


def _login(client: TestClient, email: str = BOOT_EMAIL, pw: str = BOOT_PW) -> dict:
    res = client.post(BASE + "/login", json={"email": email, "password": pw})
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _make(client: TestClient, op: str, role: str, email: str) -> dict:
    pw = f"{role}-pass-1234"
    body = {"name": role, "email": email, "role": role, "password": pw}
    assert client.post(BASE + "/accounts", json=body, headers=_bearer(op)).status_code == 200
    return _login(client, email, pw)


def _payload(token: str) -> dict:
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def _rows(tmp_path: Path) -> list[dict]:
    return json.loads((tmp_path / accounts.ACCOUNTS_FILE).read_text(encoding="utf-8"))["accounts"]


def _set_field(tmp_path: Path, email: str, key: str, value) -> None:
    path = tmp_path / accounts.ACCOUNTS_FILE
    rows = _rows(tmp_path)
    for row in rows:
        if row["email"] == email:
            row[key] = value
    path.write_text(json.dumps({"accounts": rows}, ensure_ascii=False), encoding="utf-8")


def _me(client: TestClient, token: str) -> dict:
    res = client.get(BASE + "/me", headers=_bearer(token))
    assert res.status_code == 200, res.text
    return res.json()["data"]


@pytest.mark.parametrize("role", ["admin", "member"])
def test_an_old_customer_account_belongs_to_catbooks_with_its_role(client, tmp_path, role) -> None:
    op = _login(client)["token"]
    data = _make(client, op, role, f"{role}@example.com")
    want = [{"company_id": "catbooks", "role": role}]
    assert data["companies"] == want
    assert (_payload(data["token"])["cid"], _payload(data["token"])["crole"]) == ("catbooks", role)
    me = _me(client, data["token"])
    assert me["companies"] == want
    assert (me["company_id"], me["company_role"]) == ("catbooks", role)
    assert me["account"]["role"] == role  # 계정 역할은 그대로
    # 읽을 때만 캣북스로 본다. 파일에 소속을 써 넣지 않는다(되돌릴 수 있게)
    assert all("memberships" not in row for row in _rows(tmp_path))


def test_the_operator_has_no_company_membership_and_no_cid(client) -> None:
    data = _login(client)
    assert data["companies"] == []
    assert "cid" not in _payload(data["token"])
    me = _me(client, data["token"])
    # 1단계: 회사가 없는 토큰은 캣북스로 본다. 운영자는 회사 역할이 없다
    assert (me["company_id"], me["company_role"]) == ("catbooks", None)
    assert me["account"]["role"] == "operator"


def test_an_old_session_token_without_cid_keeps_working(client) -> None:
    op = _login(client)["token"]
    admin = _make(client, op, "admin", "ad@example.com")
    account_id = _me(client, admin["token"])["account"]["id"]
    old, _ = human_auth.issue_session(account_id=account_id, name="admin", role="admin")
    assert "cid" not in _payload(old)
    me = _me(client, old)
    assert (me["company_id"], me["company_role"]) == ("catbooks", "admin")
    assert client.get(BASE + "/accounts", headers=_bearer(old)).status_code == 200


def test_stored_memberships_win_and_aliases_become_catbooks(client, tmp_path) -> None:
    op = _login(client)["token"]
    _make(client, op, "admin", "ad@example.com")
    stored = [
        {"company_id": "shop-demo", "role": "member"},
        {"company_id": "catbooks", "role": "admin"},  # 같은 회사 두 번째 줄은 버린다
        {"company_id": "Bad Id", "role": "admin"},
        {"company_id": "catbooks", "role": "operator"},
        "garbage",
    ]
    _set_field(tmp_path, "ad@example.com", "memberships", stored)
    data = _login(client, "ad@example.com", "admin-pass-1234")
    assert data["companies"] == CATBOOKS_MEMBER
    assert _payload(data["token"])["crole"] == "member"
    # 권한 규칙은 계정 역할로 그대로(관리자는 회원 목록을 본다)
    assert client.get(BASE + "/accounts", headers=_bearer(data["token"])).status_code == 200


def test_memberships_of_unknown_companies_are_dropped(client, tmp_path) -> None:
    op = _login(client)["token"]
    _make(client, op, "admin", "ad@example.com")
    _set_field(tmp_path, "ad@example.com", "memberships", [{"company_id": "nubi", "role": "admin"}])
    data = _login(client, "ad@example.com", "admin-pass-1234")
    assert data["companies"] == []
    assert "cid" not in _payload(data["token"])


def test_a_broken_memberships_field_means_no_company(client, tmp_path) -> None:
    op = _login(client)["token"]
    _make(client, op, "admin", "ad@example.com")
    _set_field(tmp_path, "ad@example.com", "memberships", {"company_id": "catbooks"})
    data = _login(client, "ad@example.com", "admin-pass-1234")
    assert data["companies"] == []  # 깨진 값은 캣북스로 채우지 않는다


def test_with_several_companies_the_token_takes_the_first_membership(client, tmp_path) -> None:
    (tmp_path / "companies.json").write_text(
        json.dumps(
            {
                "companies": [
                    {"company_id": "catbooks", "name": "캣북스", "created_at": AT},
                    {"company_id": "nubi", "name": "누비투어", "created_at": AT},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    op = _login(client)["token"]
    _make(client, op, "member", "m@example.com")
    both = [{"company_id": "nubi", "role": "admin"}, {"company_id": "catbooks", "role": "member"}]
    _set_field(tmp_path, "m@example.com", "memberships", both)
    data = _login(client, "m@example.com", "member-pass-1234")
    assert data["companies"] == both
    assert (_payload(data["token"])["cid"], _payload(data["token"])["crole"]) == ("nubi", "admin")


def test_a_role_change_moves_the_stored_catbooks_membership_too(client, tmp_path) -> None:
    op = _login(client)["token"]
    _make(client, op, "admin", "ad@example.com")
    _make(client, op, "admin", "ad2@example.com")
    _set_field(tmp_path, "ad2@example.com", "memberships", CATBOOKS_ADMIN)
    target = next(r["id"] for r in _rows(tmp_path) if r["email"] == "ad2@example.com")
    res = client.post(
        BASE + f"/accounts/{target}/update", json={"role": "member"}, headers=_bearer(op)
    )
    assert res.status_code == 200, res.text
    row = next(r for r in _rows(tmp_path) if r["email"] == "ad2@example.com")
    assert row["memberships"] == CATBOOKS_MEMBER
    assert _login(client, "ad2@example.com", "admin-pass-1234")["companies"] == CATBOOKS_MEMBER


def test_a_password_change_keeps_the_company_in_the_new_token(client) -> None:
    op = _login(client)["token"]
    member = _make(client, op, "member", "m@example.com")
    body = {"current_password": "member-pass-1234", "new_password": "member-new-pass-1"}
    res = client.post(BASE + "/password", json=body, headers=_bearer(member["token"]))
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["companies"] == CATBOOKS_MEMBER
    assert (_payload(data["token"])["cid"], _payload(data["token"])["crole"]) == (
        "catbooks",
        "member",
    )
