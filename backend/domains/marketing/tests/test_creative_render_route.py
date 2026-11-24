"""의뢰 영상 그림이 회사 계정(MCP) 대신 키 길로 가던 문제 (11/24, 운영 wr_aa0669b6f7·wr_cb45ce73c4).

- 의뢰 흐름(`request_creative._ensure_images`)이 `_render_image`에 경로를 넘기지 않아
  기본값(API)으로 갔다. 안전 기록 provider가 "higgsfield"였다.
  회사 id를 요청 메타에서 한 번 정해 끝까지 넘긴다
- 그 회사가 연결했으면 언제나 MCP, 연결이 없을 때만 키 길. 다른 회사 키·토큰은 쓰지 않는다
- 키 길도 원인이 보이게: 헤더 검사(client_error, 헤더 이름만), httpx 예외에 단계

가짜 Higgsfield·가짜 안전 관문만 쓴다(실제로 부르지 않는다). 키·토큰은 테스트 고정값.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_agent_template import company_scope
from ax_contracts.schemas import RequestMeta
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import higgsfield_status as hs
from marketing_agent import request_creative as rc
from marketing_agent.higgsfield_mcp import McpError


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


M = _load("creative_render_mcp_helpers_route", "test_creative_render_mcp.py")
env = M.env  # 같은 준비(캣북스만 회사 계정 연결, 설치 기본 키 있음)
NUBI_KEY = "DUMMY-NUBI-KEY:SECRET"


def _meta(tenant: str) -> RequestMeta:
    return M.META.model_copy(update={"tenant_id": tenant})


def _record(company: str) -> tuple[str, dict[str, Any]]:
    with company_scope.using(company):
        rid = cr.store().add_sheet(M.SHEET, None, None)
        return rid, cr.store().get(rid) or {}


def _ensure(env: dict[str, Any], company: str, rid: str, rec: dict[str, Any]) -> str | None:
    """의뢰 흐름의 그림 만들기. company_scope를 걸지 않는다(뒤에서 도는 작업과 같다)."""
    request = type("Req", (), {"meta": _meta(company)})()

    async def go() -> str | None:
        out = await rc._ensure_images(request, env["ledger"], rid, rec)  # type: ignore[arg-type]
        await rr.wait_idle()
        return out

    return asyncio.run(go())


def _sheet_part(company: str, rid: str) -> dict[str, Any]:
    with company_scope.using(company):
        return cr.store().get_render(rid).get("sheet_image") or {}


# ---------------------------------------------------------------- 경로 고르기


def test_request_flow_without_scope_uses_the_connected_company_account(
    env: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="marketing.creative_render")
    rid, rec = _record("catbooks")
    assert company_scope.active() is None  # 뒤에서 도는 작업처럼 요청 회사가 비었다
    _ensure(env, "catbooks", rid, rec)
    part = _sheet_part("catbooks", rid)
    assert part["status"] == "done" and part["route"] == "mcp"
    assert part["company_id"] == "catbooks"
    assert env["hf"].api_calls == []  # 키 길은 부르지 않았다
    action, params = env["gate"].calls[0]
    # 예전에는 provider "higgsfield"(키 길)로 안전 관문에 갔다
    assert action == "generate_media" and params["provider"] == rr.MCP_PROVIDER == "higgsfield_mcp"
    assert params["billing"] == "unlim"  # 11/24: 그림은 무제한
    assert "creative_render: route=mcp cid=catbooks part=sheet_image" in caplog.text


def test_route_follows_the_given_company_not_the_scope(env: dict[str, Any]) -> None:
    assert rr.route("catbooks") == rr.MCP  # 연결된 회사: 키가 있어도 MCP
    assert rr.route("nubi") == rr.API  # 연결 없는 회사만 키 길
    with company_scope.using("nubi"):
        assert rr.route("catbooks") == rr.MCP  # 요청 범위가 달라도 넘긴 회사로 본다
        assert rr.route() == rr.API  # 인자 없으면 예전 그대로(요청 회사)
    assert rr.request_company(_meta("nubi")) == "nubi"
    with company_scope.using("catbooks"):
        assert rr.request_company(_meta("nubi")) == "catbooks"  # 서버가 건 요청 회사가 먼저


# ---------------------------------------------------------------- 키 길 (연결 없는 회사)


class ApiFake:
    """키 길(api.higgsfield.ai)·업로드·결과 파일을 흉내 내고 Authorization을 남긴다."""

    def __init__(self, raise_on: str | None = None) -> None:
        self.auth: list[str | None] = []
        self.raise_on = raise_on

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if self.raise_on and self.raise_on in url:
            raise httpx.LocalProtocolError("Illegal header value", request=request)
        if url.startswith("https://api.higgsfield.ai"):
            self.auth.append(request.headers.get("authorization"))
        if url.endswith("/files/generate-upload-url"):
            return httpx.Response(200, json={"public_url": "https://cdn.example/ref.jpg",
                                             "upload_url": "https://up.example/put"})
        if url == "https://up.example/put":
            return httpx.Response(200)
        if url.endswith("/status"):
            return httpx.Response(200, json={"status": "completed",
                                             "images": [{"url": "https://cdn.example/o.jpg"}]})
        if url.startswith("https://api.higgsfield.ai/"):
            return httpx.Response(200, json={"request_id": "req-0001-abcd"})
        if url.startswith("https://cdn.example/"):
            return httpx.Response(200, content=M.JPEG)
        return httpx.Response(404)


def _api_env(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, fake: ApiFake) -> None:
    monkeypatch.setattr(
        rr, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    )
    monkeypatch.setenv("MARKETING_QUOTA_IMAGE_COUNT_MONTHLY", "10")


def test_unconnected_company_uses_only_its_own_key(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    env["svc"].import_value("nubi", "MARKETING_HIGGSFIELD_API_KEY", NUBI_KEY, "t")
    fake = ApiFake()
    _api_env(env, monkeypatch, fake)
    rid, rec = _record("nubi")
    with company_scope.using("nubi"):  # 제작 기록이 누비투어 칸에 있다
        _ensure(env, "nubi", rid, rec)
        part = cr.store().get_render(rid)["sheet_image"]
    assert part["route"] == "api" and part["status"] == "done", part
    assert fake.auth and set(fake.auth) == {f"Key {NUBI_KEY}"}  # 캣북스 토큰·키를 쓰지 않았다
    assert env["gate"].calls[0][1]["provider"] == rr.PROVIDER
    assert env["hf"].names() == []  # 캣북스 회사 계정(MCP)을 부르지 않았다


def test_key_with_trailing_newline_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hs, "higgsfield_key", lambda cid=None: "kid:ksecret\n")
    assert rr._auth("api_submit", "nubi") == {"Authorization": "Key kid:ksecret"}  # noqa: SLF001


def test_blank_key_is_key_missing(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hs, "higgsfield_key", lambda cid=None: "  \n ")
    assert rr.configured("image", rr.API, "nubi") == "higgsfield_key_missing"
    with pytest.raises(rr.RenderError, match="higgsfield_key_missing"):
        rr._auth("api_submit", "nubi")  # noqa: SLF001


def test_control_char_in_key_is_client_error_with_header_name_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hs, "higgsfield_key", lambda cid=None: "kid:\x01secret")
    with pytest.raises(McpError) as got:
        rr._auth("api_upload", "nubi")  # noqa: SLF001
    assert (got.value.code, got.value.stage, got.value.detail) == (
        "client_error", "api_upload", "header Authorization"
    )
    assert "secret" not in str(got.value) and "secret" not in str(got.value.detail)


def test_api_path_local_protocol_error_has_stage(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env["svc"].import_value("nubi", "MARKETING_HIGGSFIELD_API_KEY", NUBI_KEY, "t")
    # 참조 그림이 없어 만들기 요청에서 난다
    fake = ApiFake(raise_on="/google/nano-banana/edit")
    _api_env(env, monkeypatch, fake)
    caplog.set_level(logging.WARNING, logger="marketing.creative_render")
    rid, rec = _record("nubi")
    with company_scope.using("nubi"):
        _ensure(env, "nubi", rid, rec)
        part = cr.store().get_render(rid)["sheet_image"]
    assert part["status"] == "failed" and part["error"] == "mcp_client_error"
    line = next(r.getMessage() for r in caplog.records if "실패" in r.getMessage())
    assert "reason=client_error stage=api_submit error=LocalProtocolError" in line
    assert NUBI_KEY not in caplog.text


def test_download_error_has_stage() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x", request=request)

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
            await rr._download(client, " https://cdn.example/o.jpg ", Path("never"))  # noqa: SLF001

    with pytest.raises(McpError) as got:
        asyncio.run(go())
    assert (got.value.code, got.value.stage) == ("network", "download")
