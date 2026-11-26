"""화면에서 Zabbix 연결 (`/settings/zabbix`, DejaFix 계약 #28·#29).

- 로그인 세션의 운영자·관리자만. 에이전트 토큰·공용 사람 토큰·일반회원은 거부
- 같이 설치된 DejaFix(캣북스 환경 변수 연결)만. 회사 보관함 연결은 409, 연결 없으면 503
- 캣북스 아닌 회사는 DejaFix가 회사별을 알 때(`X-DejaFix-Company`)만. 옛 DejaFix면 409(11/26)
- DejaFix에는 관리자 계정(`DEJAFIX_ADMIN_*`)으로 로그인한 세션으로 넘긴다(viewer 세션과 따로)
- Zabbix 토큰은 본문 그대로 DejaFix로만 가고, 응답에는 실리지 않는다
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ax_agent_template import company_scope, human_auth
from ax_agent_template import tool_connections as tc
from ax_dejafix_proxy.main import create_app

pytestmark = pytest.mark.asyncio

AGENT_TOKEN = "agent-token-infra"
HUMAN_TOKEN = "human-token-yejin"
SECRET = "s" * 40
ZBX_TOKEN = "cust-zabbix-read-token"
STATE = {
    "source": "file",
    "url": "https://zbx.customer.example/api_jsonrpc.php",
    "token_set": True,
    "verify_tls": True,
    "updated_at": "2026-11-25T09:00:00Z",
    "updated_by": "dfx-admin",
    "mock": False,
}


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tc.FILE_ENV, str(tmp_path / "tool-connections.json"))
    monkeypatch.setenv("AX_COMPANIES_DIR", str(tmp_path / "companies"))
    monkeypatch.delenv("AX_COMPANY_FOLDERS", raising=False)
    monkeypatch.setenv("DEJAFIX_USERNAME", "viewer")
    monkeypatch.setenv("DEJAFIX_PASSWORD", "pw")
    monkeypatch.setenv("DEJAFIX_ADMIN_USERNAME", "dfx-admin")
    monkeypatch.setenv("DEJAFIX_ADMIN_PASSWORD", "admin-pw")
    monkeypatch.setenv("AX_AGENT_TOKENS", f"infra={AGENT_TOKEN}")
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", f"yejin:{HUMAN_TOKEN}")
    for name in ("AGENT_TEMPLATE_MOCK", "DEJAFIX_PROXY_MOCK"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, SECRET)
    monkeypatch.setenv(human_auth.REVOKED_FILE_ENV, str(tmp_path / "revoked.json"))
    monkeypatch.setenv(human_auth.GENERATIONS_FILE_ENV, str(tmp_path / "gen.json"))
    human_auth.reset_revoked_for_tests()
    tc._cache = None
    yield
    human_auth.reset_revoked_for_tests()
    tc._cache = None


class DejaFix:
    """DejaFix 흉내: 로그인 계정별 토큰, /api/settings/zabbix는 관리자 토큰만."""

    def __init__(self) -> None:
        self.logins: list[str] = []
        self.calls: list[tuple[str, str, str | None, dict | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            user = json.loads(request.content)["username"]
            self.logins.append(user)
            return httpx.Response(200, json={"data": {"token": f"jwt-{user}"}})
        body = json.loads(request.content) if request.content else None
        auth = request.headers.get("authorization")
        self.calls.append((request.method, request.url.path, auth, body))
        if request.url.path != "/api/settings/zabbix":
            return httpx.Response(200, json={"data": []})
        if auth != "Bearer jwt-dfx-admin":
            return httpx.Response(403, json={"error": {"code": "FORBIDDEN", "message": "x"}})
        if request.method == "PUT" and body and body.get("token") == "bad":
            return httpx.Response(
                422,
                json={
                    "error": {
                        "code": "ZABBIX_CHECK_FAILED",
                        "message": "Zabbix에 연결하지 못했습니다",
                    }
                },
            )
        return httpx.Response(200, json={"data": dict(STATE), "meta": {}})


def _session(role: str = "admin", company: str | None = None) -> str:
    token, _ = human_auth.issue_session(
        account_id=f"acc-{role}", name=f"{role}-user", role=role, company_id=company
    )
    return token


def _client(dfx: DejaFix, token: str | None) -> httpx.AsyncClient:
    upstream = httpx.AsyncClient(base_url="http://dejafix.test", transport=httpx.MockTransport(dfx))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(upstream)),
        base_url="http://proxy",
        headers=headers,
    )


BODY = {"url": STATE["url"], "token": ZBX_TOKEN, "verify_tls": True}


@pytest.mark.parametrize("role", ["admin", "operator"])
async def test_admin_session_reads_and_saves_through_admin_account(role: str) -> None:
    dfx = DejaFix()
    async with _client(dfx, _session(role)) as c:
        got = await c.get("/settings/zabbix")
        saved = await c.put("/settings/zabbix", json=BODY)
        posted = await c.post("/settings/zabbix", json=BODY)  # nginx가 PUT을 못 열 때
    assert got.status_code == 200 and got.json()["data"]["url"] == STATE["url"]
    assert saved.status_code == 200 and posted.status_code == 200
    assert dfx.logins == ["dfx-admin"]  # viewer가 아니라 관리자 계정, 한 번만 로그인
    puts = [c for c in dfx.calls if c[0] == "PUT"]
    assert len(puts) == 2
    assert all(c[2] == "Bearer jwt-dfx-admin" and c[3] == BODY for c in puts)  # 본문 그대로
    for res in (got, saved, posted):
        assert ZBX_TOKEN not in res.text and "admin-pw" not in res.text


async def test_dejafix_check_failure_is_passed_through() -> None:
    dfx = DejaFix()
    async with _client(dfx, _session()) as c:
        res = await c.put("/settings/zabbix", json={**BODY, "token": "bad"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "ZABBIX_CHECK_FAILED"


@pytest.mark.parametrize(
    ("token", "status", "code"),
    [
        (None, 401, "AUTH_REQUIRED"),
        (AGENT_TOKEN, 403, "ADMIN_REQUIRED"),  # AI는 Zabbix 연결을 바꾸지 못한다
        (HUMAN_TOKEN, 403, "ADMIN_REQUIRED"),  # 공용 사람 토큰도 안 된다
        ("member", 403, "ADMIN_REQUIRED"),  # 일반회원
    ],
)
async def test_only_operator_or_admin_session(token: str | None, status: int, code: str) -> None:
    dfx = DejaFix()
    use = _session("member") if token == "member" else token
    async with _client(dfx, use) as c:
        for res in (await c.get("/settings/zabbix"), await c.put("/settings/zabbix", json=BODY)):
            assert res.status_code == status
            assert res.json()["error"]["code"] == code
    assert dfx.calls == [] and dfx.logins == []


async def test_admin_env_missing_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEJAFIX_ADMIN_PASSWORD")
    dfx = DejaFix()
    async with _client(dfx, _session()) as c:
        res = await c.put("/settings/zabbix", json=BODY)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "DEJAFIX_ADMIN_NOT_SET"
    assert "DEJAFIX_ADMIN_USERNAME" in res.json()["error"]["message"]
    assert dfx.calls == []


async def test_company_vault_dejafix_is_409() -> None:
    with company_scope.using("nubi-tour"):
        tc.upsert("dejafix", "장애", {"url": "http://10.0.0.5:8000", "username": "v"},
                  {"password": "p"}, conn_id=None, by="t")  # fmt: skip
    dfx = DejaFix()
    async with _client(dfx, _session(company="nubi-tour")) as c:
        res = await c.put("/settings/zabbix", json=BODY)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "DEJAFIX_NOT_BUILTIN"
    assert dfx.calls == []


async def test_company_without_dejafix_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """같이 설치된 DejaFix(환경 변수)도 보관함 DejaFix도 없으면 503."""
    monkeypatch.delenv("DEJAFIX_USERNAME")
    monkeypatch.delenv("DEJAFIX_PASSWORD")
    dfx = DejaFix()
    async with _client(dfx, _session(company="nubi-tour")) as c:
        res = await c.get("/settings/zabbix")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "DEJAFIX_NOT_CONNECTED"


async def test_old_builtin_dejafix_refuses_other_company_zabbix() -> None:
    """회사별을 모르는 DejaFix에 누비투어 Zabbix를 넣으면 캣북스 것을 덮는다: 막는다(11/26)."""
    dfx = DejaFix()
    async with _client(dfx, _session(company="nubi-tour")) as c:
        got = await c.get("/settings/zabbix")
        saved = await c.put("/settings/zabbix", json=BODY)
    for res in (got, saved):
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "DEJAFIX_NO_COMPANY"
        assert STATE["url"] not in res.text  # 캣북스 Zabbix 주소도 안 보인다
    assert [c for c in dfx.calls if c[0] == "PUT"] == []


async def test_company_scoped_dejafix_saves_each_company_zabbix() -> None:
    """회사별을 아는 DejaFix: 그 회사 관리자가 자기 Zabbix를 넣는다. 요청에 회사가 실린다."""
    seen: list[tuple[str, str | None]] = []
    dfx = DejaFix()

    def scoped(request: httpx.Request) -> httpx.Response:
        res = dfx(request)
        if request.url.path == "/api/auth/login":
            return res
        company = request.headers.get("X-AX-Company")
        seen.append((request.method, company))
        return httpx.Response(res.status_code, content=res.content,
                              headers={"X-DejaFix-Company": company or ""})  # fmt: skip

    upstream = httpx.AsyncClient(
        base_url="http://dejafix.test", transport=httpx.MockTransport(scoped)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(upstream)),
        base_url="http://proxy",
        headers={"Authorization": f"Bearer {_session(company='nubi-tour')}"},
    ) as c:
        saved = await c.put("/settings/zabbix", json=BODY)
    assert saved.status_code == 200
    assert ("PUT", "nubi-tour") in seen
    assert all(company == "nubi-tour" for _, company in seen)
    assert ZBX_TOKEN not in saved.text


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"url": 1, "token": ZBX_TOKEN},
        {"url": "https://z", "token": ZBX_TOKEN, "verify_tls": "yes"},
        {"url": "https://z", "token": "x" * 513},
    ],
)
async def test_bad_body_is_422_before_dejafix(body: object) -> None:
    dfx = DejaFix()
    async with _client(dfx, _session()) as c:
        res = await c.put("/settings/zabbix", json=body)
    assert res.status_code == 422
    assert dfx.calls == []


async def test_old_dejafix_without_settings_api_is_501() -> None:
    def old(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"data": {"token": "jwt-dfx-admin"}})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "x"}})

    upstream = httpx.AsyncClient(base_url="http://dejafix.test", transport=httpx.MockTransport(old))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(upstream)),
        base_url="http://proxy",
        headers={"Authorization": f"Bearer {_session()}"},
    ) as c:
        res = await c.get("/settings/zabbix")
    assert res.status_code == 501
    assert res.json()["error"]["code"] == "DEJAFIX_TOO_OLD"


async def test_old_dejafix_405_on_save_is_also_501() -> None:
    """옛 DejaFix는 저장 경로가 GET 전용 경로에 걸려 405를 준다. 405로 내지 않고 501로 안내한다"""

    def old(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"data": {"token": "jwt-dfx-admin"}})
        return httpx.Response(405, json={"detail": "Method Not Allowed"})

    upstream = httpx.AsyncClient(base_url="http://dejafix.test", transport=httpx.MockTransport(old))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(upstream)),
        base_url="http://proxy",
        headers={"Authorization": f"Bearer {_session()}"},
    ) as c:
        body = {"url": "http://z.test/api_jsonrpc.php", "token": "t"}
        res = await c.post("/settings/zabbix", json=body)
    assert res.status_code == 501
    assert res.json()["error"]["code"] == "DEJAFIX_TOO_OLD"


async def test_other_writes_are_still_refused() -> None:
    dfx = DejaFix()
    async with _client(dfx, _session()) as c:
        res = await c.put("/api/settings/zabbix", json=BODY)  # 일반 전달 경로는 GET만
    assert res.status_code == 405
    assert dfx.calls == []
