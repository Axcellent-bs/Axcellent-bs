"""보관함 `dejafix` 연결을 요청마다 읽는 프록시."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ax_agent_template import tool_connections as tc
from ax_dejafix_proxy.main import create_app

pytestmark = pytest.mark.asyncio

AGENT_TOKEN = "agent-token-infra"  # 호출자 토큰 (AX_AGENT_TOKENS)


@pytest.fixture(autouse=True)
def _vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tc.FILE_ENV, str(tmp_path / "tool-connections.json"))
    monkeypatch.delenv("DEJAFIX_USERNAME", raising=False)
    monkeypatch.delenv("DEJAFIX_PASSWORD", raising=False)
    monkeypatch.setenv("AX_AGENT_TOKENS", f"infra={AGENT_TOKEN}")
    tc._cache = None


def _save(url: str = "http://10.0.0.5:8000", user: str = "viewer", password: str = "pw1") -> None:
    tc.upsert(
        "dejafix",
        "장애",
        {"url": url, "username": user},
        {"password": password},
        conn_id="dejafix-1" if tc.find("dejafix") else None,
        by="t",
    )


class Recorder:
    def __init__(self) -> None:
        self.logins: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            import json

            self.logins.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"token": f"t{len(self.logins)}"}})
        return httpx.Response(200, json={"data": []})


def _client(handler: Recorder) -> httpx.AsyncClient:
    upstream = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler))
    app = create_app(upstream)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://proxy",
        headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
    )


async def test_proxy_uses_vault_target() -> None:
    _save()
    rec = Recorder()
    async with _client(rec) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 200
    assert rec.logins == [{"username": "viewer", "password": "pw1"}]


async def test_proxy_relogins_when_vault_changes() -> None:
    _save()
    rec = Recorder()
    async with _client(rec) as c:
        await c.get("/api/incidents")
        await c.get("/api/incidents")
        assert len(rec.logins) == 1
        _save(password="pw2")
        await c.get("/api/incidents")
    assert [x["password"] for x in rec.logins] == ["pw1", "pw2"]


async def test_proxy_503_without_any_connection() -> None:
    async with _client(Recorder()) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "DEJAFIX_NOT_CONNECTED"


async def test_proxy_refuses_blocked_vault_host_without_leaking_password() -> None:
    _save(url="http://169.254.169.254", password="topsecretpw")
    rec = Recorder()
    async with _client(rec) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 502
    assert rec.logins == []
    assert "topsecretpw" not in res.text


async def test_old_session_closed_only_after_inflight_request_finishes() -> None:
    import asyncio

    from ax_dejafix_proxy.main import DejaFixSession

    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"data": {"token": "t"}})
        await gate.wait()
        return httpx.Response(200, json={"data": []})

    http = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler))
    session = DejaFixSession(http, "u", "p")
    task = asyncio.create_task(session.get("/api/incidents", ""))
    await asyncio.sleep(0.05)
    session.retire()  # 대상이 바뀌어 내보내는 중
    await asyncio.sleep(0.05)
    assert not http.is_closed
    gate.set()
    res = await task
    assert res.status_code == 200
    await asyncio.sleep(0.05)
    assert http.is_closed


async def test_retire_without_inflight_closes() -> None:
    import asyncio

    from ax_dejafix_proxy.main import DejaFixSession

    http = httpx.AsyncClient(
        base_url="http://x", transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    DejaFixSession(http, "u", "p").retire()
    await asyncio.sleep(0.05)
    assert http.is_closed


async def test_link_local_vault_target_is_rejected() -> None:
    _save(url="http://169.254.169.254")
    rec = Recorder()
    async with _client(rec) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "DEJAFIX_UNREACHABLE"
    assert rec.logins == []
