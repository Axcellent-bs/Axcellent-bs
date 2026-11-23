"""Higgsfield 원격 MCP 클라이언트 (회사 계정 연결, 구독 크레딧, 2026-11-23).

회사 관리자가 dashboard-api에서 자기 Higgsfield 계정을 연결하면(`higgsfield_mcp_oauth.py`), 마케팅은
API 키 대신 이 길로 그림·영상을 만든다. 갱신 토큰·access token은 `higgsfield_mcp_auth.py`.

원격 MCP(2026-11-23 실제 서버로 확인, 읽기 도구만 불러 봤다):
- `POST https://mcp.higgsfield.ai/mcp`, JSON-RPC 2.0(streamable HTTP). `Accept: application/json,
  text/event-stream`을 보내야 하고, 답은 JSON이거나 SSE(`data:` 줄)다
- `initialize` → `notifications/initialized` → `tools/call`. 서버가 `Mcp-Session-Id`를 주면 다음
  요청에 붙인다(확인할 때는 주지 않았다. 주면 쓰고, 안 주면 없이 보낸다)
- 도구 결과는 `structuredContent`(없으면 첫 text를 JSON으로 읽는다).
  실패는 `isError`와 `error` 문장. 크레딧이 모자라면 `upgrade_url`이 같이 온다
- 인증 `Authorization: Bearer <access token>`. 401·403이면 `unauthorized`
  (부르는 쪽이 한 번 갱신한다)

값(토큰·프롬프트·응답 본문)은 예외·로그에 넣지 않는다. 예외에는 사유 코드만 담는다.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import httpx

MCP_URL = "https://mcp.higgsfield.ai/mcp"
PROTOCOL_VERSION = "2025-06-18"
ACCEPT = "application/json, text/event-stream"
CLIENT_INFO = {"name": "axcellent-marketing", "version": "1.0"}
CREDIT_WORDS = ("insufficient", "not enough credit", "out of credit", "top up", "upgrade")


class McpError(Exception):
    """사유 코드만 담는다(`unauthorized`·`http_<n>`·`bad_response`·`rpc_error`·`tool_error`·
    `credits_insufficient`·`unlim_choice`)."""

    @property
    def code(self) -> str:
        return str(self)


def _events(text: str) -> list[str]:
    """SSE 본문의 이벤트마다 data 줄을 이은 문자열."""
    out: list[str] = []
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line:
            if lines:
                out.append("\n".join(lines))
                lines = []
            continue
        if line.startswith("data:"):
            lines.append(line[5:].removeprefix(" "))
    if lines:
        out.append("\n".join(lines))
    return out


def parse_message(text: str, content_type: str, want_id: int) -> dict[str, Any]:
    """JSON 또는 SSE 본문에서 그 id의 JSON-RPC 응답을 꺼낸다. 없으면 `bad_response`."""
    sse = "text/event-stream" in content_type.lower() or text.lstrip().startswith(
        ("event:", "data:", "id:", ":")
    )
    chunks = _events(text) if sse else [text]
    for chunk in chunks:
        try:
            value = json.loads(chunk)
        except ValueError:
            continue
        for msg in value if isinstance(value, list) else [value]:
            if (
                isinstance(msg, dict)
                and msg.get("id") == want_id
                and ("result" in msg or "error" in msg)
            ):
                return msg
    raise McpError("bad_response")


def _tool_error(structured: dict[str, Any], texts: list[str]) -> McpError:
    message = " ".join([str(structured.get("error") or ""), *texts]).lower()
    if structured.get("upgrade_url") or any(w in message for w in CREDIT_WORDS):
        return McpError("credits_insufficient")
    return McpError("tool_error")


def tool_output(result: object) -> dict[str, Any]:
    """`tools/call` 결과에서 구조화 출력. 도구가 실패했으면 McpError."""
    if not isinstance(result, dict):
        raise McpError("bad_response")
    texts = [
        str(c.get("text") or "")
        for c in result.get("content") or []
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        structured = {}
        for text in texts:
            try:
                value = json.loads(text)
            except ValueError:
                continue
            if isinstance(value, dict):
                structured = value
                break
    if result.get("isError") or structured.get("error"):
        raise _tool_error(structured, texts)
    if "unlim_choice" in structured:  # 무제한·크레딧 중 고르라는 질문(우리는 늘 크레딧을 고른다)
        raise McpError("unlim_choice")
    return structured


class McpSession:
    """한 번 쓰는 MCP 연결. `open()` 뒤 `call()`. HTTP 클라이언트는 부르는 쪽이 닫는다."""

    def __init__(self, client: httpx.AsyncClient, access_token: str) -> None:
        self._client = client
        self._token = access_token
        self._ids = itertools.count(1)
        self.session_id: str | None = None
        self._opened = False

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": ACCEPT,
        }
        if self._opened:
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _send(self, body: dict[str, Any], want_id: int | None) -> dict[str, Any] | None:
        res = await self._client.post(MCP_URL, json=body, headers=self._headers())
        if res.status_code in (401, 403):
            raise McpError("unauthorized")
        if res.status_code not in (200, 202):
            raise McpError(f"http_{res.status_code}")
        sid = res.headers.get("mcp-session-id")
        if sid and len(sid) <= 200:
            self.session_id = sid
        if want_id is None:
            return None
        msg = parse_message(res.text, res.headers.get("content-type", ""), want_id)
        if "error" in msg:
            raise McpError("rpc_error")
        return msg

    async def open(self) -> None:
        rid = next(self._ids)
        params = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }
        await self._send(
            {"jsonrpc": "2.0", "id": rid, "method": "initialize", "params": params}, rid
        )
        self._opened = True
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._opened:
            await self.open()
        rid = next(self._ids)
        body = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        msg = await self._send(body, rid)
        return tool_output((msg or {}).get("result"))
