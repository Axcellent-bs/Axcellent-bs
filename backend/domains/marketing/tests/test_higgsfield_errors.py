"""Higgsfield 회사 연결 오류 나누기 (11/24, 운영 wr_aa0669b6f7).

`LocalProtocolError`·`tool_error`가 모두 "닿지 못했어요"로 보여 원인이 가려졌다.
사유 코드·단계·도구 이름·정리한 문장을 예외·로그·상태 출력에 남긴다.
Higgsfield는 부르지 않는다(httpx MockTransport 가짜).
토큰 값은 테스트 고정값만 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest

from marketing_agent import creative_mcp
from marketing_agent import higgsfield_mcp as hm
from marketing_agent import higgsfield_mcp_auth as auth
from marketing_agent import higgsfield_status as hs

ACCESS = "DUMMY-HF-ACCESS-0001"
REFRESH = "DUMMY-HF-REFRESH-0001"


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _rpc(body: dict[str, Any], result: dict[str, Any]) -> httpx.Response:
    msg = {"jsonrpc": "2.0", "id": body["id"], "result": result}
    return httpx.Response(200, json=msg)


def _mcp(tool_result: dict[str, Any] | None = None, *, raise_on: str | None = None,
         error: Exception | None = None, status: int | None = None) -> Any:  # fmt: skip
    """initialize → initialized → tools/call. `raise_on` 단계에서 예외·HTTP 코드."""

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if raise_on == method and error is not None:
            raise type(error)(str(error), request=request)  # type: ignore[call-arg]
        if raise_on == method and status is not None:
            return httpx.Response(status)
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            return _rpc(body, {"protocolVersion": hm.PROTOCOL_VERSION})
        return _rpc(body, tool_result or {"structuredContent": {"credits": 5}})

    return handle


async def _call(handler: Any, tool: str = "balance") -> dict[str, Any]:
    async with _client(handler) as client:
        return await hm.McpSession(client, ACCESS).call(tool, {})


def _raises(handler: Any, tool: str = "balance") -> hm.McpError:
    with pytest.raises(hm.McpError) as got:
        asyncio.run(_call(handler, tool))
    return got.value


# ---------------------------------------------------------------- 사유 코드 나누기


def test_network_timeout_and_http_codes() -> None:
    net = _raises(_mcp(raise_on="tools/call", error=httpx.ConnectError("x")))
    assert (net.code, net.stage, net.tool, net.error_class) == ("network", "mcp_post", "balance",
                                                                "ConnectError")  # fmt: skip
    slow = _raises(_mcp(raise_on="initialize", error=httpx.ReadTimeout("x")))
    assert (slow.code, slow.stage, slow.tool) == ("timeout", "mcp_post", "initialize")
    err = _raises(_mcp(raise_on="tools/call", status=500))
    assert (err.code, err.status, err.tool) == ("http_500", 500, "balance")


def test_local_protocol_error_is_client_error() -> None:
    got = _raises(_mcp(raise_on="tools/call", error=httpx.LocalProtocolError("Illegal header")))
    assert (got.code, got.stage, got.tool) == ("client_error", "mcp_post", "balance")
    assert (got.detail, got.error_class) == ("LocalProtocolError", "LocalProtocolError")


def test_tool_error_keeps_cleaned_sentence() -> None:
    said = "Internal error at https://api.example/v1?token=abc for Bearer sk-live-zz " + "x" * 200
    result = {"isError": True, "content": [{"type": "text", "text": said}]}
    got = _raises(_mcp(result))
    assert (got.code, got.tool, got.stage) == ("tool_error", "balance", "mcp_post")
    assert got.detail is not None and len(got.detail) <= hm.DETAIL_MAX
    assert "https://" not in got.detail and "sk-live" not in got.detail
    assert got.detail.startswith("Internal error at <주소> for Bearer <가림>")
    # structured.error가 있으면 그것을 쓴다
    got = _raises(_mcp({"structuredContent": {"error": "account suspended"}}))
    assert (got.code, got.detail) == ("tool_error", "account suspended")


def test_bad_response_and_rpc_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "initialize":
            return _rpc(body, {})
        return httpx.Response(200, text="not json")

    got = _raises(handle)
    assert (got.code, got.tool) == ("bad_response", "balance")

    def rpc(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "initialize":
            return _rpc(body, {})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "error": {"code": -1, "message": "no such tool"}})

    got = _raises(rpc)
    assert (got.code, got.detail) == ("rpc_error", "no such tool")


def test_refresh_failed_keeps_status_and_network_is_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "stored_refresh_token", lambda cid: REFRESH)
    monkeypatch.setattr(auth, "client_id", lambda: "app")
    auth.forget()

    async def token(handler: Any) -> str:
        async with _client(handler) as client:
            return await auth.access_token(client, "catbooks")

    with pytest.raises(auth.McpAuthError) as got:
        asyncio.run(token(lambda r: httpx.Response(403)))
    assert (str(got.value), got.value.status, got.value.stage) == ("refresh_failed", 403, "refresh")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x", request=request)

    with pytest.raises(auth.McpAuthError) as got:
        asyncio.run(token(down))
    assert (str(got.value), got.value.error_class) == ("network", "ConnectError")


# ---------------------------------------------------------------- 문장 정리·헤더 검사 (순수)


def test_clean_detail_hides_urls_tokens_hex_and_cuts() -> None:
    text = ("fail https://cdn.x/a?sig=1 token Bearer abc.def eyJhbGciOi.eyJzdWIi.sig "
            "id deadbeefdeadbeefdeadbeef key AbCdEfGhIjKlMnOpQrStUvWxYz0123456789\nnext")
    out = hm.clean_detail(text)
    assert out == ("fail <주소> token Bearer <가림> <가림> id <가림> key <가림> next")
    # 이름=값 모양도 가린다 (짧은 값이라도)
    assert hm.clean_detail("bad token=ab12 and api_key: zz9 secret=q") == (
        "bad token=<가림> and api_key: <가림> secret=<가림>"
    )
    assert hm.clean_detail("token expired, please retry") == "token expired, please retry"
    long = hm.clean_detail("too many requests " * 20)
    assert long is not None and len(long) == hm.DETAIL_MAX and long.endswith("…")
    assert hm.clean_detail("a" * 300) == "<가림>"  # 긴 토큰 모양은 통째로 가린다
    assert hm.clean_detail("   \n ") is None and hm.clean_detail(None) is None


def test_bad_header_returns_only_the_name() -> None:
    assert hm.bad_header({"Content-Type": "image/jpeg", "Accept": "x"}) is None
    assert hm.bad_header({"Content-Type": "image/jpeg\r\nX-Evil: 1"}) == "Content-Type"
    assert hm.bad_header({"If-None-Match": "*\x00"}) == "If-None-Match"


def test_bad_authorization_header_is_client_error_with_name_only() -> None:
    async def run() -> None:
        async with _client(_mcp()) as client:
            await hm.McpSession(client, "DUMMY\nTOKEN").call("balance", {})

    with pytest.raises(hm.McpError) as got:
        asyncio.run(run())
    assert (got.value.code, got.value.detail) == ("client_error", "header Authorization")
    assert "DUMMY" not in str(hm.describe(got.value))


# ---------------------------------------------------------------- presigned 업로드


class _Upload:
    """media_upload·media_confirm을 흉내 내고 PUT 헤더를 남긴다."""

    def __init__(self, first: dict[str, Any], put_status: int = 200, put_text: str = "") -> None:
        self.first = first
        self.put_status = put_status
        self.put_text = put_text
        self.put_headers: dict[str, str] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            self.put_headers = dict(request.headers)
            return httpx.Response(self.put_status, text=self.put_text)
        body = json.loads(request.content)
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "initialize":
            return _rpc(body, {})
        name = body["params"]["name"]
        out = {"uploads": [self.first]} if name == "media_upload" else {"results": []}
        return _rpc(body, {"structuredContent": out})


class _Hf:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self._s = hm.McpSession(client, ACCESS)

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._s.call(name, arguments)


def _upload(fake: _Upload) -> str:
    async def run() -> str:
        async with _client(fake.handle) as client:
            return await creative_mcp.upload(_Hf(client), b"img", "a.jpg", "image/jpeg")  # type: ignore[arg-type]

    return asyncio.run(run())


SIGNED = (
    "https://up.example/put?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-SignedHeaders=content-type%3Bhost%3Bif-none-match&X-Amz-Signature=abc"
)


def test_if_none_match_is_added_when_signed() -> None:
    fake = _Upload({"upload_url": SIGNED, "media_id": "m-1", "content_type": " image/jpeg "})
    assert _upload(fake) == "m-1"
    assert fake.put_headers["if-none-match"] == "*"
    assert fake.put_headers["content-type"] == "image/jpeg"  # 앞뒤 공백을 지운다
    assert "authorization" not in fake.put_headers


def test_if_none_match_is_not_added_when_not_signed() -> None:
    url = "https://up.example/put?X-Amz-SignedHeaders=content-type%3Bhost"
    fake = _Upload({"upload_url": url, "media_id": "m-1", "content_type": "image/jpeg"})
    _upload(fake)
    assert "if-none-match" not in fake.put_headers


def test_result_header_field_wins_and_is_allow_listed() -> None:
    fake = _Upload({"upload_url": SIGNED, "media_id": "m-1", "content_type": "image/jpeg",
                    "headers": {"If-None-Match": "abc", "x-amz-meta-a": "1",
                                "Authorization": "nope", "Host": "evil"}})  # fmt: skip
    _upload(fake)
    assert fake.put_headers["if-none-match"] == "abc"  # 결과 헤더 칸이 먼저
    assert fake.put_headers["x-amz-meta-a"] == "1"
    assert "authorization" not in fake.put_headers  # 허용 목록 밖은 무시
    assert fake.put_headers["host"] == "up.example"


def test_newline_in_content_type_is_client_error_with_field_name() -> None:
    fake = _Upload({"upload_url": SIGNED, "media_id": "m-1", "content_type": "image/jpeg\nX: 1"})
    with pytest.raises(hm.McpError) as got:
        _upload(fake)
    assert (got.value.code, got.value.stage, got.value.detail) == ("client_error", "upload_put",
                                                                   "content_type")  # fmt: skip
    assert fake.put_headers == {}  # 보내지 않았다


def test_upload_put_failure_keeps_status_and_cleaned_body() -> None:
    body = "<Error><Code>SignatureDoesNotMatch</Code><Msg>see https://s3.example/x</Msg></Error>"
    fake = _Upload({"upload_url": SIGNED, "media_id": "m-1", "content_type": "image/jpeg"},
                   put_status=403, put_text=body)  # fmt: skip
    with pytest.raises(hm.McpError) as got:
        _upload(fake)
    assert (got.value.code, got.value.status, got.value.stage) == ("upload_http_403", 403,
                                                                   "upload_put")  # fmt: skip
    assert got.value.detail is not None and "SignatureDoesNotMatch" in got.value.detail
    assert "https://" not in got.value.detail and len(got.value.detail) <= hm.DETAIL_MAX


# ---------------------------------------------------------------- 상태 출력·로그


@pytest.mark.parametrize(("error", "connection", "reason", "words"), [
    (hm.McpError("network", stage="mcp_post", tool="balance"), "unreachable", "network",
     "네트워크·시간 초과"),
    (hm.McpError("http_502", stage="mcp_post", tool="balance", status=502), "unreachable",
     "http_502", "502 오류"),
    (auth.McpAuthError("refresh_failed", status=403), "unreachable", "refresh_failed",
     "토큰을 새로 받지 못했어요(403)"),
    (hm.McpError("tool_error", stage="mcp_post", tool="balance", detail="quota exceeded"),
     "unreachable", "tool_error", 'balance가 오류로 답했어요: "quota exceeded"'),
    (hm.McpError("bad_response", stage="mcp_post", tool="balance"), "unreachable",
     "bad_response", "응답을 읽지 못했어요"),
    (hm.McpError("client_error", stage="mcp_post", tool="balance", detail="LocalProtocolError"),
     "unreachable", "client_error", "보내기 전에 막혔어요(mcp_post: LocalProtocolError)"),
    (hm.McpError("unauthorized", stage="mcp_post", tool="balance"), "invalid_key",
     "unauthorized", "다시 연결"),
    (auth.McpAuthError("reconnect_needed"), "invalid_key", "reconnect_needed", "다시 연결"),
])  # fmt: skip
def test_status_output_has_reason_and_detail(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    connection: str,
    reason: str,
    words: str,
) -> None:
    async def fail(_hf: Any) -> tuple[float | None, str | None]:
        raise error

    monkeypatch.setattr(creative_mcp, "balance", fail)
    monkeypatch.setattr(hs, "higgsfield_key", lambda: "")
    caplog.set_level(logging.WARNING, logger="marketing.higgsfield_status")
    out = asyncio.run(hs.probe_mcp("catbooks", _client(lambda r: httpx.Response(500))))
    assert (out.connection, out.reason_code, out.source) == (connection, reason, "mcp")
    assert words in out.message_ko and len(out.message_ko) <= 200
    if reason == "tool_error":
        assert out.detail_ko == "quota exceeded"
    # 로그: 시각·사유·단계 (값 없이)
    line = caplog.records[-1].getMessage()
    assert "at=" in line and f"reason={reason}" in line and "stage=" in line


def test_success_has_no_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok(_hf: Any) -> tuple[float | None, str | None]:
        return 12.0, "ultra"

    monkeypatch.setattr(creative_mcp, "balance", ok)
    monkeypatch.setattr(hs, "higgsfield_key", lambda: "")
    out = asyncio.run(hs.probe_mcp("catbooks", _client(lambda r: httpx.Response(500))))
    assert (out.connection, out.reason_code, out.detail_ko) == ("ok", None, None)


def test_log_line_has_no_values() -> None:
    error = hm.McpError("tool_error", stage="mcp_post", tool="balance", detail="bad Bearer <가림>")
    line = hm.log_line("creative_render: Higgsfield sheet_image", error, "CR-3195e81bb400")
    assert line.startswith("creative_render: Higgsfield sheet_image 실패 at=")
    assert "reason=tool_error stage=mcp_post tool=balance error=McpError" in line
    assert 'detail="bad Bearer <가림>" ref=CR-3195e81bb400' in line
    raw = hm.log_line("x", httpx.LocalProtocolError("Illegal header value b'a\\nb'"))
    assert "reason=client_error" in raw and "a\\nb" not in raw
