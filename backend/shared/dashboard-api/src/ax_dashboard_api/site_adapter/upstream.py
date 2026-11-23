"""고객사 자기 API 호출: 인증 헤더, 커서로 끝까지 읽기, 오류를 shop-api 오류 모양으로 바꾸기.

- 주소: 회사 연결 파일의 `adapter.base_url` (`kind=adapter`가 아니면 503 `NOT_CONFIGURED`)
- 인증: 회사 연결 화면에서 넣은 키(변환기 폴더의 `token` 파일,
  없으면 `.env`의 `AX_SITE_API_TOKEN`)를 짝 파일의 `auth_header`·`auth_scheme`으로 보낸다.
  키 값은 응답·로그·오류 문구에 넣지 않는다.
- 페이지: `paging.next_field`가 빌 때까지, 또는 `AX_SITE_ADAPTER_MAX_PAGES`(기본 50)까지 읽는다.
  상한에 닿으면 `truncated=True`.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from ax_agent_template import company_scope, pinned_http
from ax_agent_template.company_connection import read_connection
from ax_dashboard_api.site_adapter.mapping import Paging, ResourceMap, SiteMapping, adapter_dir

TOKEN_ENV = "AX_SITE_API_TOKEN"
TOKEN_FILE = "token"
MAX_PAGES_ENV = "AX_SITE_ADAPTER_MAX_PAGES"
DEFAULT_MAX_PAGES = 50
TIMEOUT = 10.0
# 같은 자원을 짧은 시간에 여러 번 받지 않는다(호출하는 쪽이 쪽마다 다시 부른다).
# 그동안 쪽 위치도 고정된다
CACHE_SECONDS_ENV = "AX_SITE_ADAPTER_CACHE_SECONDS"
DEFAULT_CACHE_SECONDS = 30.0
_cache: dict[tuple[str, str | None], tuple[float, list[dict[str, Any]], bool]] = {}
# 시험이 httpx.MockTransport로 바꾼다 (회사 연결 라우트와 같은 방식)
TRANSPORT: httpx.AsyncBaseTransport | None = None

logger = logging.getLogger("ax.site_adapter")


class UpstreamError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def clear_cache() -> None:
    _cache.clear()


def _cache_seconds() -> float:
    try:
        return max(0.0, float(os.getenv(CACHE_SECONDS_ENV, "") or DEFAULT_CACHE_SECONDS))
    except ValueError:
        return DEFAULT_CACHE_SECONDS


def base_url() -> str:
    found = read_connection()
    if not found or not found.enabled:
        raise UpstreamError(
            503,
            "NOT_CONFIGURED",
            "회사 사이트가 아직 연결돼 있지 않아요. 시스템 관리 › 회사 연결에서 연결해 주세요",
        )
    if found.kind != "adapter" or not found.adapter:
        raise UpstreamError(503, "NOT_CONFIGURED", "다른 형식 API로 연결돼 있지 않아요")
    return found.adapter["base_url"]


def _max_pages() -> int:
    try:
        return max(1, int(os.getenv(MAX_PAGES_ENV, "") or DEFAULT_MAX_PAGES))
    except ValueError:
        return DEFAULT_MAX_PAGES


def saved_token() -> str:
    """지금 회사의 고객사 API 키: 그 회사 변환기 폴더의 `token` 파일. 없으면 "".

    `.env`의 `AX_SITE_API_TOKEN`은 설치 하나에 하나라 캣북스일 때만 대신 쓴다
    (다른 회사 고객사에 캣북스 키가 가지 않게).
    """
    try:
        token = (adapter_dir() / TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token or company_scope.scope_id(company_scope.active()) != company_scope.CATBOOKS:
        return token
    return os.getenv(TOKEN_ENV, "").strip()


def save_token(token: str) -> None:
    """키를 변환기 폴더에만 쓴다(주인만 읽게). 응답·로그에는 넣지 않는다."""
    folder = adapter_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / TOKEN_FILE
    tmp = path.with_suffix(".tmp")
    # 처음부터 주인만 읽게 만든다(쓰고 나서 chmod하면 그 사이 다른 사용자가 읽을 틈이 있다)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    os.chmod(tmp, 0o600)  # 남아 있던 tmp(다른 권한)를 덮어쓴 경우
    os.replace(tmp, path)


def auth_headers(header: str, scheme: str, token: str) -> dict[str, str]:
    if not token:
        return {}
    return {header: f"{scheme} {token}".strip()}


def _headers(m: SiteMapping) -> dict[str, str]:
    return auth_headers(m.auth_header, m.auth_scheme, saved_token())


def _client() -> httpx.AsyncClient:
    # 고객사 주소는 검사한 IP로만 붙고(DNS 재바인딩 차단) 리다이렉트를 따라가지 않는다
    return httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=False,
        transport=TRANSPORT or pinned_http.guarded_transport(),
    )


def client() -> httpx.AsyncClient:
    """고객사 호출 클라이언트 (직접 쓰기 `direct`가 읽기와 같은 방식으로 쓴다)."""
    return _client()


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
    # 회사를 키에 넣는다: 두 회사가 같은 주소를 써도 키·짝이 다르면 응답을 나누지 않는다
    key = (f"{company_scope.scope_id(company_scope.active())}|{url}", since)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _cache_seconds():
        return list(hit[1]), hit[2]
    rows, truncated = await _fetch(m, res, url, since)
    _cache[key] = (time.monotonic(), rows, truncated)
    return list(rows), truncated


async def _fetch(
    m: SiteMapping, res: ResourceMap, url: str, since: str | None
) -> tuple[list[dict[str, Any]], bool]:
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


async def first_page(url: str, paging: Paging, headers: dict[str, str]) -> list[dict[str, Any]]:
    """한 쪽만 읽는다(설정 화면의 둘러보기·저장 전 확인). 캐시하지 않는다."""
    async with _client() as client:
        body = await _get(client, url, {paging.limit_param: paging.limit}, headers)
    items = body.get(paging.items_field) if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise UpstreamError(502, "MAPPING_BROKEN", f"응답에 {paging.items_field} 목록이 없어요")
    return [r for r in items if isinstance(r, dict)]


async def get_paths(base: str, headers: dict[str, str]) -> list[str]:
    """고객사 `openapi.json`에서 GET으로 읽는 목록 경로(`{}` 없는 것). 없으면 빈 목록."""
    try:
        async with _client() as client:
            body = await _get(client, base + "/openapi.json", {}, headers)
    except UpstreamError:
        return []
    paths = body.get("paths") if isinstance(body, dict) else None
    if not isinstance(paths, dict):
        return []
    return sorted(
        p
        for p, ops in paths.items()
        if isinstance(p, str) and "{" not in p and isinstance(ops, dict) and "get" in ops
    )


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
