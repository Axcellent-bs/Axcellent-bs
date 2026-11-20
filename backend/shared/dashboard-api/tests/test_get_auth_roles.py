"""읽기까지 사람 토큰을 요구해도 로그인한 사람은 지금 쓰던 기능을 그대로 쓴다.

- 토큰 없이 열린 경로는 `auth.PUBLIC_ROUTES`뿐이다. 그 밖의 모든 GET은 토큰이 없으면 401
- 일반회원(member)·고객사 관리자(admin) 세션은 모든 GET에서 사람 검사를 지난다(401·403 없음).
  역할로 막는 것은 원래부터 역할 검사가 있던 계정 관리·연결 키 쓰기뿐이다
- 로그인 전 화면이 읽는 회사 공개 정보(`/auth/company`)에는 이름·사이트 주소만 있다
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth
from ax_dashboard_api import accounts, auth, connection_keys
from ax_dashboard_api.main import app

BOOT_EMAIL = "ops@example.com"
BOOT_PW = "boot-pass-for-tests"  # 테스트용 자리값
MEMBER_PW = "member-pass-for-tests"  # 테스트용 자리값
ADMIN_PW = "admin-pass-for-tests"  # 테스트용 자리값

# 원래부터 역할 검사가 있던 GET (시스템 관리 › 회원·서버 관리). 일반회원은 403이 맞다
ADMIN_ONLY_GETS = {
    "/api/dashboard/auth/accounts",
    # 비용 상한·정책 화면: 로그인한 운영자·관리자만 읽는다(일반회원·공용 토큰 403)
    "/api/dashboard/marketing/cost-limits",
    "/api/dashboard/cost-policy",
    # 키 관리 화면: 로그인한 운영자·관리자만(일반회원·공용 토큰 403)
    "/api/dashboard/secrets",
    # 회사 AI 설정(공통 AI 도구 연결): 고객사 관리자만. 회원은 /ai-connection/* 로 본다
    "/api/dashboard/company-ai",
}


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def _human_gets() -> list[str]:
    """사람이 읽는 GET 경로 전부 (공개·기계 경로 빼고). 숨긴 경로가 없어서 OpenAPI 목록이 전부다."""
    out = set()
    for path, ops in app.openapi()["paths"].items():
        if "get" not in ops:
            continue
        if ("GET", path) in auth.PUBLIC_ROUTES or ("GET", path) in auth.MACHINE_ROUTES:
            continue
        if path.startswith(auth.MACHINE_PREFIXES):
            continue
        out.add(path)
    return sorted(out)


HUMAN_GETS = _human_gets()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(accounts.ACCOUNTS_FILE_ENV, raising=False)
    monkeypatch.delenv(connection_keys.KEYS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.REVOKED_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.GENERATIONS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)  # 테스트용 자리값
    monkeypatch.setenv(accounts.BOOTSTRAP_ENV, f"{BOOT_EMAIL}:{BOOT_PW}")
    accounts.reset_failures_for_tests()
    human_auth.reset_revoked_for_tests()
    # 사람 검사를 지난 뒤 부서 AI 호출 설정이 없어 나는 예외는 500으로 받는다(401·403만 본다)
    c = TestClient(app, raise_server_exceptions=False)
    c.headers.pop("authorization")  # conftest의 공용 토큰을 빼고 시작
    return c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, email: str, pw: str) -> str:
    res = client.post("/api/dashboard/auth/login", json={"email": email, "password": pw})
    assert res.status_code == 200, res.text
    return res.json()["data"]["token"]


@pytest.fixture
def tokens(client: TestClient) -> dict[str, str]:
    op = _login(client, BOOT_EMAIL, BOOT_PW)
    for email, role, pw in (
        ("admin@example.com", "admin", ADMIN_PW),
        ("member@example.com", "member", MEMBER_PW),
    ):
        res = client.post(
            "/api/dashboard/auth/accounts",
            json={"name": role, "email": email, "role": role, "password": pw},
            headers=_bearer(op),
        )
        assert res.status_code == 200, res.text
    return {
        "operator": op,
        "admin": _login(client, "admin@example.com", ADMIN_PW),
        "member": _login(client, "member@example.com", MEMBER_PW),
    }


def test_the_sweep_sees_the_reads_the_screen_uses() -> None:
    for path in ("/api/dashboard/communications", "/api/dashboard/connection-keys"):
        assert path in HUMAN_GETS
    assert "/api/dashboard/auth/company" not in HUMAN_GETS


@pytest.mark.parametrize("path", HUMAN_GETS)
def test_every_human_read_needs_a_token(client, path) -> None:
    res = client.get(_fill(path))
    assert res.status_code == 401, (path, res.status_code, res.text[:200])


@pytest.mark.parametrize("path", HUMAN_GETS)
@pytest.mark.parametrize("role", ["member", "admin"])
def test_a_logged_in_member_or_admin_passes_every_read(client, tokens, path, role) -> None:
    """일반회원도 관리자와 같이 읽는다. 원래 역할 검사가 있던 계정 목록만 일반회원 403."""
    res = client.get(_fill(path), headers=_bearer(tokens[role]))
    if role == "member" and path in ADMIN_ONLY_GETS:
        assert res.status_code == 403 and res.json()["error"]["code"] == "ROLE_REQUIRED"
        return
    assert res.status_code not in (401, 403), (path, res.status_code, res.text[:200])


@pytest.mark.parametrize(
    ("path", "body", "passed"),
    [
        (
            "/api/dashboard/claims/999/approve",
            {},
            404,
        ),  # 사람 결정: 확인 권한은 화면 설정, 서버는 막지 않음
        ("/api/dashboard/plan-checks", {"draft": {"title": "x"}}, 422),
        ("/api/dashboard/handoffs/missing/complete", {}, None),
    ],
)
def test_a_member_session_can_still_write(client, tokens, path, body, passed) -> None:
    res = client.post(path, json=body, headers=_bearer(tokens["member"]))
    assert res.status_code not in (401, 403), res.text[:200]
    if passed is not None:
        assert res.status_code == passed


def test_only_the_admin_screens_check_the_role(client, tokens) -> None:
    """연결 키 연결·회사 정보는 고객사 관리자, 발급은 운영자 (원래부터 있던 검사)."""
    body = {"key": "AXC-NOPE", "name": "회사"}
    member = client.post(
        "/api/dashboard/connection-keys/connect", json=body, headers=_bearer(tokens["member"])
    )
    assert member.status_code == 403 and member.json()["error"]["code"] == "ROLE_REQUIRED"
    admin = client.post(
        "/api/dashboard/connection-keys/connect", json=body, headers=_bearer(tokens["admin"])
    )
    assert admin.status_code == 422  # 역할은 지나고 모르는 키에서 멈춘다
    issue = client.post(
        "/api/dashboard/connection-keys/issue", json={}, headers=_bearer(tokens["admin"])
    )
    assert issue.status_code == 403


@pytest.mark.parametrize(("method", "path"), sorted(auth.PUBLIC_ROUTES))
def test_public_routes_answer_without_a_token(client, method, path) -> None:
    if method == "GET":
        res = client.get(path)
        assert res.status_code == 200, res.text
    else:
        res = client.post(path, json={"email": "nobody@example.com", "password": "x"})
        assert res.status_code == 401 and res.json()["error"]["code"] == "LOGIN_FAILED"


def test_public_company_is_empty_before_connecting(client) -> None:
    res = client.get("/api/dashboard/auth/company")
    assert res.status_code == 200 and res.json()["data"] == {"company": None}


def test_public_company_shows_only_the_name_and_site(client, tmp_path) -> None:
    keys_file = tmp_path / connection_keys.KEYS_FILE
    keys_file.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "id": "k1",
                        "key": "AXC-SECRET-KEY-VALUE",
                        "memo": "운영 메모",
                        "issued_at": "t",
                        "status": "connected",
                    }
                ],
                "company": {
                    "id": "c-k1",
                    "name": "고양이책방",
                    "industry": "서점",
                    "network": "10.0.0.0/8",
                    "site_url": "https://cat.example",
                    "key_id": "k1",
                    "connected_at": "t",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    res = client.get("/api/dashboard/auth/company")
    assert res.status_code == 200
    assert res.json()["data"] == {
        "company": {"id": "c-k1", "name": "고양이책방", "site_url": "https://cat.example"}
    }
    text = res.text
    for secret in ("AXC-SECR", "운영 메모", "10.0.0.0", "서점", "key_id", "connected_at"):
        assert secret not in text
    # 토큰이 있어야 하는 전체 목록은 여전히 401
    assert client.get("/api/dashboard/connection-keys").status_code == 401
