"""회사 고르기 (여러 회사 설계 1-8, 2026-11-20).

- `POST /api/dashboard/auth/company {company_id}`: 로그인 세션 본인만, 그 계정 소속 회사로만
  세션 토큰을 다시 발급한다(같은 계정, 새 `cid`·`crole`, 만료 시각은 그대로)
- 소속이 아닌 회사·모르는 회사·형식이 틀린 값은 403 `TENANT_MISMATCH`(계약 `ErrorCode`)
- 공용 사람 토큰·에이전트 토큰·토큰 없음은 거절. 별칭 `shop-demo`는 `catbooks`로
- 로그인·내 계정 응답의 `company_list`: 소속 회사의 이름·사이트 주소(회사 목록에서). 화면 고르기용
"""

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import companies as company_list
from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth
from ax_contracts.schemas.message import ErrorCode
from ax_dashboard_api import accounts
from ax_dashboard_api.main import app

BASE = "/api/dashboard/auth"
BOOT_EMAIL = "ops@example.com"
BOOT_PW = "boot-pass-for-tests"  # 테스트용 자리값
AT = "2026-11-20T00:00:00Z"
BOTH = [{"company_id": "nubi", "role": "admin"}, {"company_id": "catbooks", "role": "member"}]


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


def _set_field(tmp_path: Path, email: str, key: str, value) -> None:
    path = tmp_path / accounts.ACCOUNTS_FILE
    rows = json.loads(path.read_text(encoding="utf-8"))["accounts"]
    for row in rows:
        if row["email"] == email:
            row[key] = value
    path.write_text(json.dumps({"accounts": rows}, ensure_ascii=False), encoding="utf-8")


def _two_companies(tmp_path: Path) -> None:
    (tmp_path / "companies.json").write_text(
        json.dumps(
            {
                "companies": [
                    {
                        "company_id": "catbooks",
                        "name": "캣북스",
                        "site_url": "https://catbooks.example.com",
                        "created_at": AT,
                    },
                    {"company_id": "nubi", "name": "누비투어", "created_at": AT},
                    {"company_id": "studyon", "name": "스터디온", "created_at": AT},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _member_of_both(client: TestClient, tmp_path: Path) -> dict:
    _two_companies(tmp_path)
    op = _login(client)["token"]
    _make(client, op, "member", "m@example.com")
    _set_field(tmp_path, "m@example.com", "memberships", BOTH)
    return _login(client, "m@example.com", "member-pass-1234")


def _switch(client: TestClient, token: str | None, company_id) -> object:
    headers = _bearer(token) if token else {}
    return client.post(BASE + "/company", json={"company_id": company_id}, headers=headers)


def _error_code(res) -> str:
    return res.json()["error"]["code"]


def test_a_member_of_two_companies_switches_to_the_other_one(client, tmp_path) -> None:
    first = _member_of_both(client, tmp_path)
    assert _payload(first["token"])["cid"] == "nubi"
    res = _switch(client, first["token"], "catbooks")
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    claims = _payload(data["token"])
    assert (claims["cid"], claims["crole"]) == ("catbooks", "member")
    assert claims["sub"] == _payload(first["token"])["sub"]  # 같은 계정
    assert claims["role"] == "member"  # 계정 역할은 그대로
    assert (data["company_id"], data["company_role"]) == ("catbooks", "member")
    assert data["companies"] == BOTH
    assert data["account"]["email"] == "m@example.com"
    # 회사를 바꿔도 로그인 시간은 늘지 않는다
    assert data["expires_at"] == first["expires_at"]
    me = client.get(BASE + "/me", headers=_bearer(data["token"])).json()["data"]
    assert (me["company_id"], me["company_role"]) == ("catbooks", "member")


def test_a_company_outside_the_memberships_is_refused(client, tmp_path) -> None:
    first = _member_of_both(client, tmp_path)
    for target in ("studyon", "nowhere", "Bad Id", "", 3):
        res = _switch(client, first["token"], target)
        assert res.status_code == 403, (target, res.text)
        assert _error_code(res) == ErrorCode.TENANT_MISMATCH.value


def test_the_alias_shop_demo_means_catbooks(client, tmp_path) -> None:
    first = _member_of_both(client, tmp_path)
    res = _switch(client, first["token"], "shop-demo")
    assert res.status_code == 200, res.text
    assert _payload(res.json()["data"]["token"])["cid"] == "catbooks"


def test_only_a_login_session_can_switch(client, tmp_path) -> None:
    _member_of_both(client, tmp_path)
    assert _switch(client, None, "catbooks").status_code == 401
    shared = client.post(
        BASE + "/company",
        json={"company_id": "catbooks"},
        headers=_bearer("test-human-token"),
    )
    assert shared.status_code == 403, shared.text
    agent = client.post(
        BASE + "/company", json={"company_id": "catbooks"}, headers=_bearer("mock:orchestrator")
    )
    assert agent.status_code == 403, agent.text


def test_the_operator_has_no_company_to_switch_to(client) -> None:
    op = _login(client)["token"]
    res = _switch(client, op, "catbooks")
    assert res.status_code == 403
    assert _error_code(res) == ErrorCode.TENANT_MISMATCH.value


def test_a_single_company_account_can_pick_its_own_company(client) -> None:
    op = _login(client)["token"]
    admin = _make(client, op, "admin", "ad@example.com")
    res = _switch(client, admin["token"], "catbooks")
    assert res.status_code == 200, res.text
    claims = _payload(res.json()["data"]["token"])
    assert (claims["cid"], claims["crole"]) == ("catbooks", "admin")
    # 관리자 권한 판단은 계정 역할 그대로
    headers = _bearer(res.json()["data"]["token"])
    assert client.get(BASE + "/accounts", headers=headers).status_code == 200


def test_login_and_me_list_the_member_companies_with_names(client, tmp_path) -> None:
    first = _member_of_both(client, tmp_path)
    want = [
        {"company_id": "nubi", "role": "admin", "name": "누비투어", "site_url": ""},
        {
            "company_id": "catbooks",
            "role": "member",
            "name": "캣북스",
            "site_url": "https://catbooks.example.com",
        },
    ]
    assert first["company_list"] == want
    assert (first["company_id"], first["company_role"]) == ("nubi", "admin")
    me = client.get(BASE + "/me", headers=_bearer(first["token"])).json()["data"]
    assert me["company_list"] == want


def test_a_password_change_keeps_the_picked_company(client, tmp_path) -> None:
    first = _member_of_both(client, tmp_path)
    picked = _switch(client, first["token"], "catbooks").json()["data"]["token"]
    body = {"current_password": "member-pass-1234", "new_password": "member-new-pass-1"}
    res = client.post(BASE + "/password", json=body, headers=_bearer(picked))
    assert res.status_code == 200, res.text
    assert _payload(res.json()["data"]["token"])["cid"] == "catbooks"
