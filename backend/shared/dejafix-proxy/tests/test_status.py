"""`GET /status`: 연결 출처(builtin·custom·none)와 응답 여부. 비밀번호·토큰은 싣지 않는다."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ax_agent_template import tool_connections as tc
from ax_dejafix_proxy.main import create_app

pytestmark = pytest.mark.asyncio

ENV_PW = "env-secret-pw"
VAULT_PW = "vault-secret-pw"
TOKEN = "tok-secret-123"
CALLER = "caller-human-token"


@pytest.fixture(autouse=True)
def _vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tc.FILE_ENV, str(tmp_path / "tool-connections.json"))
    monkeypatch.delenv("DEJAFIX_USERNAME", raising=False)
    monkeypatch.delenv("DEJAFIX_PASSWORD", raising=False)
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", f"yejin:{CALLER}")
    monkeypatch.delenv("AX_ALLOW_SHARED_HUMAN_TOKEN", raising=False)
    tc._cache = None


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEJAFIX_USERNAME", "viewer")
    monkeypatch.setenv("DEJAFIX_PASSWORD", ENV_PW)


def _save_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    # 보관함 주소는 사내·허용 목록 정책을 거친다. 여기서는 정책 자체가 아니라 출처 판정만 본다
    monkeypatch.setattr("ax_agent_template.company_connection.check_host", lambda _url: None)
    tc.upsert(
        "dejafix",
        "장애",
        {"url": "http://10.0.0.5:8000", "username": "viewer2"},
        {"password": VAULT_PW},
        conn_id=None,
        by="t",
    )


def _handler(incidents_status: int = 200):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"data": {"token": TOKEN}})
        return httpx.Response(incidents_status, json={"data": []})

    return handle


def _client(handler) -> httpx.AsyncClient:
    upstream = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(upstream)),
        base_url="http://proxy",
        headers={"Authorization": f"Bearer {CALLER}"},  # /status도 호출자 토큰을 확인한다
    )


def _no_secret(res: httpx.Response) -> None:
    for secret in (ENV_PW, VAULT_PW, TOKEN):
        assert secret not in res.text
    assert set(res.json()) == {"source", "reachable", "detail"}


async def test_status_none() -> None:
    async with _client(_handler()) as c:
        res = await c.get("/status")
    assert res.status_code == 200
    assert res.json()["source"] == "none"
    assert res.json()["reachable"] is None
    _no_secret(res)


async def test_status_builtin_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    async with _client(_handler()) as c:
        res = await c.get("/status")
    assert res.json()["source"] == "builtin"
    assert res.json()["reachable"] is True
    _no_secret(res)


async def test_status_builtin_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)

    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with _client(down) as c:
        res = await c.get("/status")
    assert res.json()["source"] == "builtin"
    assert res.json()["reachable"] is False
    _no_secret(res)


async def test_status_builtin_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    async with _client(_handler(500)) as c:
        res = await c.get("/status")
    assert res.json()["reachable"] is False
    assert res.json()["detail"] == "HTTP 500"


async def test_status_custom_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    _save_vault(monkeypatch)
    async with _client(_handler()) as c:
        res = await c.get("/status")
    assert res.json()["source"] == "custom"
    assert res.json()["reachable"] is True
    _no_secret(res)
    assert "10.0.0.5" not in res.text  # 주소도 싣지 않는다 (화면은 도구 연결 목록에서 읽는다)


async def test_status_custom_blocked_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _save_vault(monkeypatch)

    def refuse(_url: str) -> None:
        raise ValueError("허용 목록 밖")

    monkeypatch.setattr("ax_agent_template.company_connection.check_host", refuse)
    async with _client(_handler()) as c:
        res = await c.get("/status")
    assert res.json() == {"source": "custom", "reachable": False, "detail": "주소를 쓸 수 없어요"}
