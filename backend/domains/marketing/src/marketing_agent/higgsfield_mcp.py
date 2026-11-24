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

11/24 오류 종류 나누기 (운영 wr_aa0669b6f7):
`LocalProtocolError`·`tool_error`가 모두 "닿지 못했어요"로 보였다.
- 사유 코드: network(연결 실패)·timeout·http_<n>·refresh_failed·tool_error·bad_response·rpc_error·
  client_error(보내기 전 실패: httpx.LocalProtocolError·InvalidURL, 헤더 값에 CR·LF·제어문자)
- 예외에 단계(refresh·mcp_post·upload_put)·도구 이름·예외 클래스 이름·
  정리한 문장(`detail`)을 담는다.
  정리한 문장은 Higgsfield가 준 글에서 주소·토큰 모양·긴 16진수를 가리고
  120자로 자른 것이다(`clean_detail`)
- client_error는 어느 헤더인지 "헤더 이름"만 담는다(값은 담지 않는다)
"""

from __future__ import annotations

import itertools
import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

MCP_URL = "https://mcp.higgsfield.ai/mcp"
PROTOCOL_VERSION = "2025-06-18"
ACCEPT = "application/json, text/event-stream"
CLIENT_INFO = {"name": "axcellent-marketing", "version": "1.0"}
CREDIT_WORDS = ("insufficient", "not enough credit", "out of credit", "top up", "upgrade")


DETAIL_MAX = 120
STAGES = ("refresh", "mcp_post", "upload_put")


class McpError(Exception):
    """사유 코드(`str(오류)`)와 원인 칸. 값(토큰·프롬프트·본문)은 담지 않는다.

    코드: `unauthorized`·`http_<n>`·`bad_response`·`rpc_error`·`tool_error`·`network`·`timeout`·
    `client_error`·`credits_insufficient`·`unlim_choice`·`upload_http_<n>` 등.
    - stage: refresh·mcp_post·upload_put (모르면 None)
    - tool: 부른 도구 이름(initialize·balance·media_upload…)
    - detail: 정리한 문장(`clean_detail`) 또는 client_error의 헤더 이름·예외 종류
    - error_class: 원래 예외 클래스 이름(httpx 예외 등)
    - status: HTTP 응답 코드(있으면)
    """

    def __init__(self, code: str, *, stage: str | None = None, tool: str | None = None,
                 detail: str | None = None, error_class: str | None = None,
                 status: int | None = None) -> None:  # fmt: skip
        super().__init__(code)
        self.stage = stage
        self.tool = tool
        self.detail = detail
        self.error_class = error_class
        self.status = status

    @property
    def code(self) -> str:
        return str(self)


# ---------------------------------------------------------------- 문장 정리·헤더 검사 (순수)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_BEARER = re.compile(r"\b(Bearer)\s+\S+", re.IGNORECASE)
# 이름=값 모양 (token=…, api_key: …, secret=…, password=…)
_NAMED = re.compile(
    r"\b(access_token|refresh_token|token|api[_-]?key|secret|password)(\s*[:=]\s*)\S+",
    re.IGNORECASE,
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){1,2}")
_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_detail(text: object, limit: int = DETAIL_MAX) -> str | None:
    """Higgsfield가 준 문장을 로그·화면에 둘 수 있게 정리한다. 빈 글이면 None.

    https 주소 → <주소>, Bearer 뒤 값·token=값 같은 이름=값·JWT·긴 토큰 모양 → <가림>,
    긴 16진수 → <가림>,
    제어문자·줄바꿈 → 공백, `limit`자(기본 120)에서 자르고 "…"."""
    if text is None:
        return None
    s = _CONTROL.sub(" ", str(text))
    s = _URL.sub("<주소>", s)
    s = _BEARER.sub(lambda m: f"{m.group(1)} <가림>", s)
    s = _NAMED.sub(lambda m: f"{m.group(1)}{m.group(2)}<가림>", s)
    s = _JWT.sub("<가림>", s)
    s = _LONG_HEX.sub("<가림>", s)
    s = _LONG_TOKEN.sub("<가림>", s)
    s = " ".join(s.split())
    if not s:
        return None
    return s if len(s) <= limit else s[: limit - 1] + "…"


def bad_header(headers: dict[str, str]) -> str | None:
    """값에 CR·LF·제어문자가 든 첫 헤더의 이름(값은 돌려주지 않는다). 없으면 None."""
    for name, value in headers.items():
        if _CONTROL.search(name) or _CONTROL.search(str(value)):
            return name
    return None


def clean_value(value: object, field: str, *, stage: str, tool: str | None) -> str:
    """MCP가 준 칸(content_type·upload_url)의 앞뒤 공백을 지운다.
    공백·제어문자가 남으면 client_error(칸 이름만 담는다)."""
    s = str(value).strip()
    if not s or re.search(r"[\s\x00-\x1f\x7f]", s):
        raise McpError("client_error", stage=stage, tool=tool, detail=field)
    return s


def from_http_error(error: Exception, *, stage: str | None, tool: str | None) -> McpError:
    """httpx 예외 → 사유 코드. 보내기 전 실패(LocalProtocolError·InvalidURL 등)는 client_error."""
    name = type(error).__name__
    if isinstance(error, httpx.TimeoutException):
        code = "timeout"
    elif isinstance(error, httpx.LocalProtocolError | httpx.InvalidURL | httpx.UnsupportedProtocol):
        code = "client_error"
    else:
        code = "network"
    detail = name if code == "client_error" else None
    return McpError(code, stage=stage, tool=tool, detail=detail, error_class=name)


def describe(error: BaseException) -> dict[str, str | int | None]:
    """로그·화면용 원인 칸 (값 없이): reason·stage·tool·error·detail·status."""
    if isinstance(error, McpError) or hasattr(error, "stage"):
        return {
            "reason": str(error),
            "stage": getattr(error, "stage", None),
            "tool": getattr(error, "tool", None),
            "error": getattr(error, "error_class", None) or type(error).__name__,
            "detail": getattr(error, "detail", None),
            "status": getattr(error, "status", None),
        }
    if isinstance(error, httpx.HTTPError | httpx.InvalidURL):
        return describe(from_http_error(error, stage=None, tool=None))
    return {"reason": type(error).__name__, "stage": None, "tool": None,
            "error": type(error).__name__, "detail": None, "status": None}  # fmt: skip


def log_line(where: str, error: BaseException, ref: str | None = None) -> str:
    """실패 로그 한 줄: 시각(UTC)·사유·단계·도구·예외 종류·정리한 문장 (값 없이).

    서비스 로그 형식에 시각이 없어서(main._setup_app_logging) 이 줄에만 시각을 넣는다."""
    d = describe(error)
    parts = [f"{where} 실패", f"at={datetime.now(UTC).isoformat(timespec='seconds')}",
             f"reason={d['reason']}"]  # fmt: skip
    for key in ("stage", "tool", "error", "status"):
        if d[key] not in (None, ""):
            parts.append(f"{key}={d[key]}")
    if d["detail"]:
        parts.append(f'detail="{d["detail"]}"')
    if ref:
        parts.append(f"ref={ref}")
    return " ".join(parts)


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


def parse_message(text: str, content_type: str, want_id: int,
                  tool: str | None = None) -> dict[str, Any]:  # fmt: skip
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
    raise McpError("bad_response", stage="mcp_post", tool=tool)


def _tool_error(structured: dict[str, Any], texts: list[str],
                tool: str | None = None) -> McpError:  # fmt: skip
    message = " ".join([str(structured.get("error") or ""), *texts]).lower()
    # Higgsfield가 준 문장: structured.error, 없으면 첫 text (정리해서 담는다)
    said = structured.get("error") or next((t for t in texts if t.strip()), None)
    detail = clean_detail(said)
    if structured.get("upgrade_url") or any(w in message for w in CREDIT_WORDS):
        return McpError("credits_insufficient", stage="mcp_post", tool=tool, detail=detail)
    return McpError("tool_error", stage="mcp_post", tool=tool, detail=detail)


def tool_output(result: object, tool: str | None = None) -> dict[str, Any]:
    """`tools/call` 결과에서 구조화 출력. 도구가 실패했으면 McpError."""
    if not isinstance(result, dict):
        raise McpError("bad_response", stage="mcp_post", tool=tool)
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
        raise _tool_error(structured, texts, tool)
    if "unlim_choice" in structured:  # 무제한·크레딧 중 고르라는 질문(우리는 늘 크레딧을 고른다)
        raise McpError("unlim_choice", stage="mcp_post", tool=tool)
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

    async def _send(self, body: dict[str, Any], want_id: int | None,
                    tool: str | None = None) -> dict[str, Any] | None:  # fmt: skip
        stage = "mcp_post"
        headers = self._headers()
        if (name := bad_header(headers)) is not None:  # 보내기 전에: 헤더 이름만 남긴다
            raise McpError("client_error", stage=stage, tool=tool, detail=f"header {name}")
        try:
            res = await self._client.post(MCP_URL, json=body, headers=headers)
        except (httpx.HTTPError, httpx.InvalidURL) as error:
            raise from_http_error(error, stage=stage, tool=tool) from error
        if res.status_code in (401, 403):
            raise McpError("unauthorized", stage=stage, tool=tool, status=res.status_code)
        if res.status_code not in (200, 202):
            raise McpError(f"http_{res.status_code}", stage=stage, tool=tool,
                           status=res.status_code)  # fmt: skip
        sid = res.headers.get("mcp-session-id")
        if sid and len(sid) <= 200:
            self.session_id = sid
        if want_id is None:
            return None
        msg = parse_message(res.text, res.headers.get("content-type", ""), want_id, tool)
        if "error" in msg:
            err = msg.get("error")
            said = err.get("message") if isinstance(err, dict) else None
            raise McpError("rpc_error", stage=stage, tool=tool, detail=clean_detail(said))
        return msg

    async def open(self) -> None:
        rid = next(self._ids)
        params = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }
        await self._send(
            {"jsonrpc": "2.0", "id": rid, "method": "initialize", "params": params}, rid,
            "initialize",
        )  # fmt: skip
        self._opened = True
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"}, None,
                         "initialized")  # fmt: skip

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
        msg = await self._send(body, rid, name)
        return tool_output((msg or {}).get("result"), name)
