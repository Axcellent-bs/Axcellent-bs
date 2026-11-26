"""회사마다 자기 DejaFix 연결만 쓴다 (11/25, 로드맵 6단계).

- 누비투어 로그인으로 불러도 캣북스 보관함·환경변수 DejaFix를 쓰지 않는다(연결 없으면 503)
- 누비투어가 저장한 연결은 누비투어만 쓰고, 로그인 토큰도 회사마다 따로 받는다
- 에이전트 토큰은 `X-AX-Company` 머리글의 회사, 없으면 캣북스(예전 그대로)
- 같이 설치된 DejaFix는 회사별을 알 때(`X-DejaFix-Company`)만 캣북스 아닌 회사에 넘긴다(11/26)
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ax_agent_template import company_scope, human_auth
from ax_agent_template import tool_connections as tc
from ax_dejafix_proxy import main
from ax_dejafix_proxy.main import create_app

pytestmark = pytest.mark.asyncio

AGENT_TOKEN = "agent-token-infra"
SECRET = "s" * 40
NUBI = "nubi-tour"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tc.FILE_ENV, str(tmp_path / "tool-connections.json"))  # 캣북스 보관함
    monkeypatch.setenv("AX_COMPANIES_DIR", str(tmp_path / "companies"))
    monkeypatch.delenv("AX_COMPANY_FOLDERS", raising=False)
    monkeypatch.delenv("DEJAFIX_USERNAME", raising=False)
    monkeypatch.delenv("DEJAFIX_PASSWORD", raising=False)
    monkeypatch.setenv("AX_AGENT_TOKENS", f"infra={AGENT_TOKEN}")
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, SECRET)
    monkeypatch.setenv(human_auth.REVOKED_FILE_ENV, str(tmp_path / "revoked.json"))
    monkeypatch.setenv(human_auth.GENERATIONS_FILE_ENV, str(tmp_path / "gen.json"))
    human_auth.reset_revoked_for_tests()
    tc._cache = None
    yield
    human_auth.reset_revoked_for_tests()
    tc._cache = None


def _save(user: str, password: str, company: str | None = None) -> None:
    def put() -> None:
        tc.upsert("dejafix", "장애", {"url": "http://10.0.0.5:8000", "username": user},
                  {"password": password}, conn_id=None, by="t")  # fmt: skip

    if company is None:
        put()
    else:
        with company_scope.using(company):
            put()


class Recorder:
    def __init__(self) -> None:
        self.logins: list[str] = []
        self.reads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            self.logins.append(json.loads(request.content)["username"])
            return httpx.Response(200, json={"data": {"token": f"t{len(self.logins)}"}})
        self.reads += 1
        return httpx.Response(200, json={"data": []})


def _session(company: str | None) -> str:
    token, _ = human_auth.issue_session(
        account_id=f"acc-{company}", name="u", role="admin", company_id=company
    )
    return token


def _client(rec: Recorder, token: str, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    upstream = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(rec))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(upstream)),
        base_url="http://proxy",
        headers={"Authorization": f"Bearer {token}", **(headers or {})},
    )


async def test_other_company_does_not_use_catbooks_vault() -> None:
    _save("cat-viewer", "pw-cat")
    rec = Recorder()
    async with _client(rec, _session(NUBI)) as c:
        res = await c.get("/api/incidents")
        status = (await c.get("/status")).json()
    assert res.status_code == 503 and res.json()["error"]["code"] == "DEJAFIX_NOT_CONNECTED"
    assert status["source"] == "none"
    assert rec.logins == [] and rec.reads == 0  # 캣북스 DejaFix에 로그인조차 하지 않는다


class Echo(Recorder):
    """회사별을 아는 DejaFix 흉내: 받은 `X-AX-Company`로 걸렀다고 `X-DejaFix-Company`로 돌려준다."""

    def __init__(self) -> None:
        super().__init__()
        self.companies: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        res = super().__call__(request)
        if request.url.path == "/api/auth/login":
            return res
        company = request.headers.get("X-AX-Company")
        self.companies.append(company)
        return httpx.Response(res.status_code, json=json.loads(res.content),
                              headers={"X-DejaFix-Company": company or ""})  # fmt: skip


def _builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEJAFIX_USERNAME", "builtin")
    monkeypatch.setenv("DEJAFIX_PASSWORD", "pw-builtin")


async def test_old_builtin_dejafix_is_catbooks_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """회사별을 모르는 같이 설치된 DejaFix(옛 버전)의 장애는 캣북스 것: 다른 회사에는 안 넘긴다.

    11/26 실서버: 누비투어 인프라 화면에 캣북스 Zabbix 장애가 보였다(#1880 뒤).
    """
    _builtin(monkeypatch)
    rec = Recorder()
    async with _client(rec, _session(NUBI)) as c:
        res = await c.get("/api/incidents")
        summary = await c.get("/api/dashboard/summary")
        status = (await c.get("/status")).json()
    assert res.status_code == 503 and res.json()["error"]["code"] == "DEJAFIX_NOT_CONNECTED"
    assert summary.status_code == 503
    assert "Zabbix" in res.json()["error"]["message"]
    assert status == {"source": "builtin", "reachable": None, "detail": main.NOT_SCOPED}
    async with _client(rec, _session("catbooks")) as c:  # 캣북스는 그대로
        assert (await c.get("/api/incidents")).status_code == 200
        assert (await c.get("/status")).json()["reachable"] is True


async def test_company_scoped_builtin_dejafix_serves_each_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """회사별을 아는 DejaFix: 하나가 회사마다 그 회사 것만 돌려준다(예진 님 방향, 11/26)."""
    _builtin(monkeypatch)
    rec = Echo()
    async with _client(rec, _session(NUBI)) as c:
        assert (await c.get("/api/incidents")).status_code == 200
        assert (await c.get("/status")).json()["reachable"] is True
    async with _client(rec, _session("catbooks")) as c:
        assert (await c.get("/api/incidents")).status_code == 200
    assert rec.companies == [NUBI, NUBI, "catbooks"]  # 요청마다 부르는 회사를 싣는다
    assert rec.logins == ["builtin", "builtin"]  # 로그인 토큰도 회사마다


async def test_company_header_cannot_override_session_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """로그인 세션이면 회사는 세션의 cid뿐: `X-AX-Company`를 캣북스로 바꿔 붙여도 누비투어다."""
    _builtin(monkeypatch)
    rec = Echo()
    async with _client(rec, _session(NUBI), {"X-AX-Company": "catbooks"}) as c:
        assert (await c.get("/api/incidents")).status_code == 200
    assert rec.companies == [NUBI]
    rec = Recorder()  # 옛 DejaFix + 위조 머리: 그래도 캣북스 장애는 안 넘긴다
    async with _client(rec, _session(NUBI), {"X-AX-Company": "catbooks"}) as c:
        assert (await c.get("/api/incidents")).status_code == 503


async def test_echo_for_another_company_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """DejaFix가 다른 회사로 걸렀다고 하면 넘기지 않는다."""
    _builtin(monkeypatch)

    def wrong(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"data": {"token": "t"}})
        return httpx.Response(200, json={"data": []}, headers={"X-DejaFix-Company": "catbooks"})

    upstream = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(wrong))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(upstream)),
        base_url="http://proxy",
        headers={"Authorization": f"Bearer {_session(NUBI)}"},
    ) as c:
        assert (await c.get("/api/incidents")).status_code == 503


async def test_each_company_uses_its_own_vault_and_login() -> None:
    _save("cat-viewer", "pw-cat")
    _save("nubi-viewer", "pw-nubi", company=NUBI)
    rec = Recorder()
    async with _client(rec, _session(NUBI)) as nubi:
        assert (await nubi.get("/api/incidents")).status_code == 200
        assert (await nubi.get("/api/incidents")).status_code == 200
    async with _client(rec, _session("catbooks")) as cat:
        assert (await cat.get("/api/incidents")).status_code == 200
    # 회사마다 한 번씩 로그인 (토큰을 섞어 쓰지 않는다)
    assert rec.logins == ["nubi-viewer", "cat-viewer"]


async def test_session_header_picks_company_when_authorization_is_not_a_session() -> None:
    """같은 출처 nginx가 읽기의 Authorization을 공용 토큰으로 바꿔 붙여도(11/25 실서버)
    화면이 같이 싣는
    X-AX-Session으로 회사를 고른다. 위조 머리는 무시(캣북스 그대로)."""
    _save("cat-viewer", "pw-cat")
    rec = Recorder()
    nubi = _session(NUBI)
    async with _client(rec, AGENT_TOKEN, {"X-AX-Session": nubi}) as c:
        assert (await c.get("/api/incidents")).status_code == 503  # 누비투어: 연결 없음
    prefix, body, sig = nubi.split(".")
    async with _client(
        rec, AGENT_TOKEN, {"X-AX-Session": f"{prefix}.{body}.{'A' * len(sig)}"}
    ) as c:
        # 위조: 회사를 못 고른다 = 회사 없음 = 연결 전(11/26부터 캣북스로 떨어지지 않는다)
        assert (await c.get("/api/incidents")).status_code == 503
    async with _client(rec, AGENT_TOKEN, {"X-AX-Session": _session("catbooks")}) as c:
        assert (await c.get("/api/incidents")).status_code == 200
    assert rec.logins == ["cat-viewer"]


async def test_agent_token_follows_company_header() -> None:
    _save("cat-viewer", "pw-cat")
    rec = Recorder()
    async with _client(rec, AGENT_TOKEN, {"X-AX-Company": NUBI}) as c:
        assert (await c.get("/api/incidents")).status_code == 503
    async with _client(rec, AGENT_TOKEN, {"X-AX-Company": "catbooks"}) as c:
        assert (await c.get("/api/incidents")).status_code == 200
    assert rec.logins == ["cat-viewer"]


@pytest.mark.parametrize("token", [AGENT_TOKEN, "human"])
async def test_no_company_is_not_connected_not_catbooks(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """회사 없이 부르면(머리글·세션 없음) 어느 회사 DejaFix도 안 쓴다: 캣북스로 안 떨어진다(11/26).

    DejaFix 데이터는 그 회사 회사 연결 설정의 Zabbix를 거쳐야 한다(예진 님)."""
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "yejin:human")
    _save("cat-viewer", "pw-cat")
    _builtin(monkeypatch)
    rec = Recorder()
    async with _client(rec, token) as c:
        res = await c.get("/api/incidents")
        status = (await c.get("/status")).json()
    assert res.status_code == 503 and res.json()["error"]["code"] == "DEJAFIX_NOT_CONNECTED"
    assert status["source"] == "none"
    assert rec.logins == [] and rec.reads == 0  # DejaFix에 로그인조차 하지 않는다


async def test_vault_without_company_folders_is_empty_for_other_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AX_COMPANIES_DIR", raising=False)
    _save("cat-viewer", "pw-cat")
    with company_scope.using(NUBI):
        assert tc.connections_file() == ""
        assert tc.find("dejafix") is None
    assert tc.find("dejafix") is not None  # 요청 밖·캣북스는 그대로
