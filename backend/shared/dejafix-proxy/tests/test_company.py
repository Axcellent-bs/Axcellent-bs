"""회사마다 자기 DejaFix 연결만 쓴다 (11/25, 로드맵 6단계).

- 누비투어 로그인으로 불러도 캣북스 보관함·환경변수 DejaFix를 쓰지 않는다(연결 없으면 503)
- 누비투어가 저장한 연결은 누비투어만 쓰고, 로그인 토큰도 회사마다 따로 받는다
- 에이전트 토큰은 `X-AX-Company` 머리글의 회사, 없으면 캣북스(예전 그대로)
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


async def test_other_company_does_not_use_builtin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEJAFIX_USERNAME", "builtin")
    monkeypatch.setenv("DEJAFIX_PASSWORD", "pw-builtin")
    rec = Recorder()
    async with _client(rec, _session(NUBI)) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 503
    assert rec.logins == []


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


async def test_agent_token_follows_company_header() -> None:
    _save("cat-viewer", "pw-cat")
    rec = Recorder()
    async with _client(rec, AGENT_TOKEN, {"X-AX-Company": NUBI}) as c:
        assert (await c.get("/api/incidents")).status_code == 503
    async with _client(rec, AGENT_TOKEN) as c:  # 머리글 없음 = 캣북스 (예전 그대로)
        assert (await c.get("/api/incidents")).status_code == 200
    assert rec.logins == ["cat-viewer"]


async def test_vault_without_company_folders_is_empty_for_other_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AX_COMPANIES_DIR", raising=False)
    _save("cat-viewer", "pw-cat")
    with company_scope.using(NUBI):
        assert tc.connections_file() == ""
        assert tc.find("dejafix") is None
    assert tc.find("dejafix") is not None  # 요청 밖·캣북스는 그대로
