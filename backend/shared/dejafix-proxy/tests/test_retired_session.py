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

    closes = 0
    real_aclose = main.DejaFixSession.aclose

    async def counting_aclose(self):
        nonlocal closes
        closes += 1
        await real_aclose(self)

    monkeypatch.setattr(main.DejaFixSession, "aclose", counting_aclose)

    app = main.create_app()
    caller = real_client(
        transport=httpx.ASGITransport(app=app),
        base_url="http://proxy",
        # 회사 없이 부르면 연결 전이다(11/26). 에이전트는 부르는 회사를 싣는다
        headers={"Authorization": f"Bearer {AGENT_TOKEN}", "X-AX-Company": "catbooks"},
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
    await asyncio.sleep(0)
    # 종료 뒤 진행 중이던 요청이 끝나도 닫기를 또 걸지 않는다 (세션 2개 = 2번)
    assert closes == 2
    await caller.aclose()
