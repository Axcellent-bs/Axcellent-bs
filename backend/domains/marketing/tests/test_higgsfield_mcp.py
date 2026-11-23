"""Higgsfield 원격 MCP 클라이언트·토큰 갱신. 가짜 Higgsfield·더미 토큰만 쓴다(실제 호출 없음)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from ax_secret_store import resolver
from ax_secret_store.crypto import generate_master_key
from marketing_agent import higgsfield_mcp_auth as auth
from marketing_agent.higgsfield_mcp import MCP_URL, McpError, McpSession, parse_message

OLD = "DUMMY-HF-REFRESH-OLD-0001"
NEW = "DUMMY-HF-REFRESH-NEW-0002"
ACCESS = "DUMMY-HF-ACCESS-0001"


def _sse(msg: dict[str, Any]) -> httpx.Response:
    text = f"event: message\nid: 1\ndata: {json.dumps(msg)}\n\n"
    return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})


# ---------------------------------------------------------------- 본문 읽기
def test_sse_body_picks_the_answer_with_the_same_id() -> None:
    text = (
        'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
        'data: {"jsonrpc":"2.0","id":7,\ndata: "result":{"ok":true}}\n\n'
    )
    assert parse_message(text, "text/event-stream", 7)["result"] == {"ok": True}
    plain = '{"jsonrpc":"2.0","id":3,"result":{}}'
    assert parse_message(plain, "application/json", 3)["result"] == {}
    with pytest.raises(McpError, match="bad_response"):
        parse_message(text, "text/event-stream", 8)
    with pytest.raises(McpError, match="bad_response"):
        parse_message("not json", "application/json", 1)


class Server:
    """initialize → initialized → tools/call 를 흉내 낸다. 세션 ID를 주고, 받은 헤더를 남긴다."""

    def __init__(self, tool: Any = None, status: int = 200) -> None:
        self.seen: list[tuple[str, dict[str, str]]] = []
        self.tool = tool or {
            "result": {"structuredContent": {"credits": 5, "subscription_plan_type": "pro"}}
        }
        self.status = status

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == MCP_URL
        body = json.loads(request.content)
        self.seen.append((body.get("method"), dict(request.headers)))
        if self.status != 200:
            return httpx.Response(self.status)
        if body["method"] == "initialize":
            res = {"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}}
            return httpx.Response(200, json=res, headers={"mcp-session-id": "sess-1"})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        tool = self.tool if not callable(self.tool) else self.tool(body)
        return _sse({"jsonrpc": "2.0", "id": body["id"], **tool})


def _call(server: Server, name: str = "balance") -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(server.handle)) as client:
            return await McpSession(client, ACCESS).call(name, {})

    return asyncio.run(go())


def test_session_opens_keeps_the_session_id_and_reads_structured_output() -> None:
    server = Server()
    assert _call(server) == {"credits": 5, "subscription_plan_type": "pro"}
    methods = [m for m, _ in server.seen]
    assert methods == ["initialize", "notifications/initialized", "tools/call"]
    first, last = server.seen[0][1], server.seen[-1][1]
    assert "mcp-session-id" not in first and last["mcp-session-id"] == "sess-1"
    assert last["authorization"] == f"Bearer {ACCESS}"
    assert "text/event-stream" in last["accept"] and "application/json" in last["accept"]


def test_text_content_is_read_when_there_is_no_structured_output() -> None:
    server = Server({"result": {"content": [{"type": "text", "text": '{"credits": 9}'}]}})
    assert _call(server) == {"credits": 9}


@pytest.mark.parametrize(
    ("tool", "code"),
    [
        ({"error": {"code": -32601, "message": "no"}}, "rpc_error"),
        ({"result": {"isError": True, "content": [{"type": "text", "text": "bad"}]}}, "tool_error"),
        (
            {"result": {"isError": True, "structuredContent": {"error": "x", "upgrade_url": "u"}}},
            "credits_insufficient",
        ),
        (
            {"result": {"structuredContent": {"error": "Insufficient credits for this model"}}},
            "credits_insufficient",
        ),
        ({"result": {"structuredContent": {"unlim_choice": {"q": "?"}}}}, "unlim_choice"),
        ({"result": "nope"}, "bad_response"),
    ],
)
def test_tool_failures_become_reason_codes(tool: dict[str, Any], code: str) -> None:
    with pytest.raises(McpError) as caught:
        _call(Server(tool))
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("status", "code"), [(401, "unauthorized"), (403, "unauthorized"), (500, "http_500")]
)
def test_http_failures_become_reason_codes(status: int, code: str) -> None:
    with pytest.raises(McpError) as caught:
        _call(Server(status=status))
    assert caught.value.code == code and ACCESS not in str(caught.value)


# ---------------------------------------------------------------- 토큰 갱신
@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Any]:
    monkeypatch.setenv("AX_SECRET_STORE", "db")
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("AX_SECRET_CACHE_TTL", "0")
    monkeypatch.setenv(auth.CLIENT_ID_ENV, "DUMMY-PUBLIC-CLIENT")
    resolver.reset_for_tests()
    auth.forget()
    svc = resolver.service()
    svc.store_oauth_token("catbooks", auth.TOKEN_NAME, OLD, "test")
    yield svc
    auth.forget()
    resolver.reset_for_tests()


class TokenServer:
    def __init__(self) -> None:
        self.posts: list[dict[str, str]] = []
        self.answers: list[httpx.Response] = []
        self.before: Any = None  # 응답 직전에 할 일(다른 곳이 먼저 바꾸는 흉내)

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == auth.TOKEN_URL
        self.posts.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        if self.before:
            self.before()
        if self.answers:
            return self.answers.pop(0)
        return httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3600})


def _token(server: TokenServer, *, force: bool = False) -> str:
    async def go() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(server.handle)) as client:
            return await auth.access_token(client, "catbooks", force=force)

    return asyncio.run(go())


def _stored(svc: Any, tenant: str = "catbooks") -> str | None:
    return svc.reveal_for_runtime(tenant, auth.TOKEN_NAME)


def test_refresh_sends_the_public_client_and_resource_and_caches(store: Any) -> None:
    server = TokenServer()
    assert _token(server) == ACCESS and _token(server) == ACCESS
    assert len(server.posts) == 1  # 두 번째는 메모리에서
    sent = server.posts[0]
    assert sent == {
        "grant_type": "refresh_token",
        "refresh_token": OLD,
        "client_id": "DUMMY-PUBLIC-CLIENT",
        "resource": auth.RESOURCE,
    }
    _token(server, force=True)
    assert len(server.posts) == 2
    assert auth.connected("catbooks") and not auth.connected("nubi")  # 회사마다 따로


def test_rotated_refresh_token_is_stored_with_compare_and_swap(store: Any) -> None:
    server = TokenServer()
    server.answers = [
        httpx.Response(200, json={"access_token": ACCESS, "refresh_token": NEW, "expires_in": 60})
    ]
    assert _token(server) == ACCESS
    assert _stored(store) == NEW
    history = [h["action"] for h in store.history(["catbooks"])]
    assert "oauth_rotate" in history


def test_stale_token_is_not_overwritten_and_the_access_token_is_still_used(
    store: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    other = "DUMMY-HF-REFRESH-OTHER-03"
    server = TokenServer()
    # 갱신하는 사이 다른 프로세스가 먼저 토큰을 바꿨다
    server.before = lambda: store.store_oauth_token("catbooks", auth.TOKEN_NAME, other, "x")
    server.answers = [
        httpx.Response(200, json={"access_token": ACCESS, "refresh_token": NEW, "expires_in": 60})
    ]
    assert _token(server) == ACCESS
    assert _stored(store) == other  # 남의 새 토큰을 덮지 않았다
    for value in (OLD, NEW, other, ACCESS):
        assert value not in caplog.text


def test_refused_refresh_retries_once_with_a_token_rotated_elsewhere(store: Any) -> None:
    other = "DUMMY-HF-REFRESH-OTHER-03"
    server = TokenServer()
    server.answers = [httpx.Response(400, json={"error": "invalid_grant"})]
    server.before = lambda: store.store_oauth_token("catbooks", auth.TOKEN_NAME, other, "x")
    assert _token(server) == ACCESS
    assert [p["refresh_token"] for p in server.posts] == [OLD, other]


def test_refused_refresh_without_a_newer_token_means_reconnect(store: Any) -> None:
    server = TokenServer()
    server.answers = [httpx.Response(400, json={"error": "invalid_grant"})]
    with pytest.raises(auth.McpAuthError, match="reconnect_needed"):
        _token(server)
    assert len(server.posts) == 1
    server.answers = [httpx.Response(503)]
    with pytest.raises(auth.McpAuthError, match="refresh_failed"):
        _token(server)
    server.answers = [httpx.Response(200, json={"token_type": "Bearer"})]
    with pytest.raises(auth.McpAuthError, match="refresh_failed"):
        _token(server)


def test_not_connected_and_missing_client_id(store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(auth.CLIENT_ID_ENV)
    with pytest.raises(auth.McpAuthError, match="client_id_missing"):
        _token(TokenServer())
    store.stage("delete", "catbooks", auth.TOKEN_NAME, None, "t")
    req = store.list_requests(["catbooks"])[0]
    store.apply(req["id"], "t")
    with pytest.raises(auth.McpAuthError, match="not_connected"):
        _token(TokenServer())


def test_env_mode_has_no_company_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 모드에서는 설치 하나의 환경값을 여러 회사가 같이 쓰게 되므로 연결이 없는 것으로 본다."""
    monkeypatch.setenv("AX_SECRET_STORE", "env")
    monkeypatch.setenv(auth.TOKEN_NAME, OLD)
    resolver.reset_for_tests()
    assert auth.stored_refresh_token("catbooks") == ""
    assert auth.connected("catbooks") is False
    resolver.reset_for_tests()
