"""Higgsfield 연결 상태 (skill `higgsfield_status`, read). 실제 Higgsfield는 부르지 않는다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from marketing_agent import handlers
from marketing_agent import higgsfield_status as hs

KEY = "kid:ksecret"


def _client(code: int | None) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Key {KEY}"
        assert request.method == "GET" and request.url.path.endswith("/status")
        if code is None:
            raise httpx.ConnectTimeout("timeout", request=request)
        return httpx.Response(code, json={"detail": "x"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_API_KEY", KEY)
    monkeypatch.setenv("MARKETING_HIGGSFIELD_IMAGE_MODEL", "nano-banana/edit")
    hs.reset_cache()


@pytest.mark.parametrize(
    ("code", "connection"),
    [(404, "ok"), (422, "ok"), (401, "invalid_key"), (403, "invalid_key"), (502, "unreachable")],
)
def test_http_code_decides_the_connection(code: int, connection: str) -> None:
    out = asyncio.run(hs.probe(_client(code)))
    assert out.connection == connection and out.key_configured and out.http_status == code
    assert out.image_model == "nano-banana/edit"
    assert KEY not in out.model_dump_json()  # 키 값은 결과에 없다


def test_network_error_is_unreachable() -> None:
    out = asyncio.run(hs.probe(_client(None)))
    assert out.connection == "unreachable" and out.http_status is None


def test_without_key_nothing_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKETING_HIGGSFIELD_API_KEY")
    monkeypatch.delenv("MARKETING_HIGGSFIELD_IMAGE_MODEL")

    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError("키가 없으면 부르지 않는다")

    out = asyncio.run(hs.probe(httpx.AsyncClient(transport=httpx.MockTransport(boom))))
    assert out.connection == "not_configured" and not out.key_configured and out.image_model is None


def test_status_is_remembered_for_a_short_while(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    async def fake_probe() -> object:
        calls.append(1)
        return hs._out(True, "ok", "연결됨", 404)

    monkeypatch.setattr(hs, "probe", fake_probe)
    asyncio.run(hs.status(now=100.0))
    asyncio.run(hs.status(now=100.0 + hs.CACHE_S - 1))
    assert len(calls) == 1
    asyncio.run(hs.status(now=100.0 + hs.CACHE_S + 1))
    assert len(calls) == 2


def test_handler_returns_the_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_status() -> object:
        return hs._out(True, "invalid_key", "거절", 401)

    monkeypatch.setattr(hs, "status", fake_status)
    request = AgentRequest(
        meta=RequestMeta(
            context_id="ctx_test_hf",
            token="t",
            idempotency_key="k1",
            deadline=datetime.now(UTC) + timedelta(seconds=60),
        ),
        skill="higgsfield_status",
        input={},
    )
    result = asyncio.run(handlers.HANDLERS["higgsfield_status"](request))
    assert result.status == TaskStatus.COMPLETED
    assert result.output["connection"] == "invalid_key"
