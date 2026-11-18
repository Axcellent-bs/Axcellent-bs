"""고객사 자기 API 호출: 인증 헤더, 커서로 끝까지 읽기, 오류를 shop-api 오류 모양으로 바꾸기.

- 주소: 회사 연결 파일의 `adapter.base_url` (`kind=adapter`가 아니면 503 `NOT_CONFIGURED`)
- 인증: `.env`의 `AX_SITE_API_TOKEN`을 짝 파일의 `auth_header`·`auth_scheme`으로 보낸다.
  키 값은 응답·로그·오류 문구에 넣지 않는다.
- 페이지: `paging.next_field`가 빌 때까지, 또는 `AX_SITE_ADAPTER_MAX_PAGES`(기본 50)까지 읽는다.
  상한에 닿으면 `truncated=True`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from ax_agent_template.company_connection import read_connection
from ax_dashboard_api.site_adapter.mapping import ResourceMap, SiteMapping

TOKEN_ENV = "AX_SITE_API_TOKEN"
MAX_PAGES_ENV = "AX_SITE_ADAPTER_MAX_PAGES"
DEFAULT_MAX_PAGES = 50
TIMEOUT = 10.0
# 시험이 httpx.MockTransport로 바꾼다 (회사 연결 라우트와 같은 방식)
TRANSPORT: httpx.AsyncBaseTransport | None = None

logger = logging.getLogger("ax.site_adapter")


class UpstreamError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def base_url() -> str:
    found = read_connection()
    if not found or found.kind != "adapter" or not found.adapter:
        raise UpstreamError(503, "NOT_CONFIGURED", "다른 형식 API로 연결돼 있지 않아요")
    return found.adapter["base_url"]


def _max_pages() -> int:
    try:
        return max(1, int(os.getenv(MAX_PAGES_ENV, "") or DEFAULT_MAX_PAGES))
    except ValueError:
        return DEFAULT_MAX_PAGES


def _headers(m: SiteMapping) -> dict[str, str]:
    token = os.getenv(TOKEN_ENV, "").strip()
    if not token:
        return {}
    return {m.auth_header: f"{m.auth_scheme} {token}".strip()}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, transport=TRANSPORT)


async def _get(
    client: httpx.AsyncClient, url: str, params: dict[str, Any], headers: dict[str, str]
) -> Any:
    try:
        resp = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("고객사 API에 닿지 못했다: %s (%s)", url, type(exc).__name__)
        raise UpstreamError(503, "UPSTREAM_UNAVAILABLE", "고객사 API에 닿지 못했어요") from exc
    if resp.status_code in (401, 403):
        raise UpstreamError(
            503, "UPSTREAM_AUTH", "고객사 API가 키를 받지 않았어요. 키를 다시 넣어 주세요"
        )
    if resp.status_code >= 400:
        raise UpstreamError(
            503, "UPSTREAM_UNAVAILABLE", f"고객사 API가 {resp.status_code}로 답했어요"
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise UpstreamError(502, "MAPPING_BROKEN", "고객사 API 응답이 JSON이 아니에요") from exc


async def fetch_all(
    m: SiteMapping, res: ResourceMap, *, since: str | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """자원 하나를 끝까지 읽는다. (원본 행들, 최대 쪽 수에서 멈췄는지)."""
    url = base_url() + res.path
    paging = res.paging
    params: dict[str, Any] = {paging.limit_param: paging.limit}
    if since and paging.since_param:
        params[paging.since_param] = since
    rows: list[dict[str, Any]] = []
    async with _client() as client:
        for _ in range(_max_pages()):
            body = await _get(client, url, params, _headers(m))
            items = body.get(paging.items_field) if isinstance(body, dict) else None
            if not isinstance(items, list):
                raise UpstreamError(
                    502, "MAPPING_BROKEN", f"{res.path} 응답에 {paging.items_field} 목록이 없어요"
                )
            rows.extend(r for r in items if isinstance(r, dict))
            cursor = body.get(paging.next_field)
            if not cursor:
                return rows, False
            params = {**params, paging.cursor_param: cursor}
    return rows, True


async def ping() -> bool:
    """고객사 API 주소가 답하는지. 연결 안 됐거나 오류면 False."""
    try:
        url = base_url()
    except UpstreamError:
        return False
    try:
        async with _client() as client:
            resp = await client.get(url)
    except httpx.HTTPError:
        return False
    return resp.status_code < 400
