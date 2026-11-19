"""대상이 바뀌어 물러난 세션도 앱 종료 때 반드시 닫힌다."""

import asyncio

import httpx
import pytest

from ax_dejafix_proxy import main

pytestmark = pytest.mark.asyncio

AGENT_TOKEN = "agent-token-infra"


async def test_shutdown_closes_retired_session_still_in_flight(monkeypatch):
    monkeypatch.setenv("AX_AGENT_TOKENS", f"infra={AGENT_TOKEN}")
    monkeypatch.setenv("DEJAFIX_USERNAME", "viewer")
    monkeypatch.setenv("DEJAFIX_PASSWORD", "pw")
    monkeypatch.setenv("DEJAFIX_PROXY_TARGET", "http://a.test")
    monkeypatch.setattr(main.tool_connections, "find", lambda _name: None)

    release = asyncio.Event()
    started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"data": {"token": "t"}})
        if request.url.host == "a.test":
            started.set()
            await release.wait()
        return httpx.Response(200, json={"data": []})

    made: list[httpx.AsyncClient] = []
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        http = real_client(**kwargs)
        made.append(http)
        return http

    app = main.create_app()
    caller = real_client(
        transport=httpx.ASGITransport(app=app),
        base_url="http://proxy",
        headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
    )
    monkeypatch.setattr(main.httpx, "AsyncClient", factory)

    async with app.router.lifespan_context(app):
        slow = asyncio.create_task(caller.get("/api/incidents?status=problem"))
        await started.wait()
        monkeypatch.setenv("DEJAFIX_PROXY_TARGET", "http://b.test")
        res = await caller.get("/api/incidents?status=problem")
        assert res.status_code == 200
        assert len(made) == 2
        assert not made[0].is_closed  # 진행 중이면 아직 닫지 않는다
    # 종료: 현재 세션뿐 아니라 물러난 세션도 닫는다
    assert all(http.is_closed for http in made)
    release.set()
    await asyncio.gather(slow, return_exceptions=True)
    await caller.aclose()
