"""NAVER API HUB 검색 API 커넥터: 블로그·카페글·뉴스 (확산 정도만).

공식 문서로 확인한 것 (2026-11-18, https://api.ncloud-docs.com/docs/naver-api-hub-overview):
- 주소 `https://naverapihub.apigw.ntruss.com` + `GET /search/v1/blog` · `/search/v1/cafearticle` ·
  `/search/v1/news`. 쿼리 `query`(필수) · `display`(1~100) · `start`(1~1000) · `sort`(sim·date) ·
  `format`(json)
- 헤더 `X-NCP-APIGW-API-KEY-ID` · `X-NCP-APIGW-API-KEY`
  (데이터랩과 같은 HUB 앱 키 `NAVER_HUB_CLIENT_*`)
- 응답 `total` · `items[]`. 블로그 `postdate`(yyyyMMdd), 뉴스 `pubDate`(RFC 822),
  `originallink`·`link`. **카페글 항목에는 날짜가 없다** (최근 7일 수를 셀 수 없음).
  **뉴스 항목에 언론사 이름 칸이 없다** (원문 주소 도메인을 출처로 쓴다)
- 401 = 인증 실패 또는 애플리케이션에 API 권한이 없음, 429 = 하루 허용량 초과.
  검색 API 하루 25,000회

저장하는 것: 전체 건수(total)와 결과 날짜만. 뉴스는 제목·출처(도메인)·주소까지.
본문 요약(description)·블로그 이름·카페 이름 같은 작성자 정보는 읽어도 남기지 않는다.

- 하루 캐시(한국 날짜, 같은 요청), 하루 상한 `NAVER_SEARCH_DAILY_LIMIT`(기본 500)
- 앱에서 API를 켜지 않았으면(401·403) `NOT_ENABLED` + "HUB에서 블로그·카페·뉴스 API를 켜면 보여요"
"""

from __future__ import annotations

import html
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import CLIENT_ID_ENV, CLIENT_SECRET_ENV
from data_agent.connectors.naver_searchad import JsonDailyCache, limit_of
from data_agent.stage_timing import stage

DEFAULT_URL = "https://naverapihub.apigw.ntruss.com/search/v1"
URL_ENV = "AX_NAVER_SEARCH_URL"
DAILY_LIMIT_ENV = "NAVER_SEARCH_DAILY_LIMIT"
DEFAULT_DAILY_LIMIT = 500
CACHE_DB_ENV = "DATA_NAVER_SEARCH_CACHE_DB"
MAX_DISPLAY = 100  # 문서: display 1~100
TIMEOUT_SECONDS = 10.0
NOT_ENABLED = "HUB에서 블로그·카페·뉴스 API를 켜면 보여요"
_TAG = re.compile(r"<[^>]+>")

Kind = Literal["blog", "cafearticle", "news"]


@dataclass(frozen=True)
class SearchNews:
    title: str
    source: str | None  # 원문 주소 도메인
    url: str | None
    published_at: datetime | None


@dataclass(frozen=True)
class SearchResult:
    kind: Kind
    total: int
    dates: tuple[date, ...]  # 결과 날짜 (블로그만, 카페글은 응답에 날짜가 없다)
    news: tuple[SearchNews, ...] = ()


_cache = JsonDailyCache(os.environ.get(CACHE_DB_ENV) or None)


def clear_cache() -> None:
    _cache.clear()


def _clean(text: Any) -> str:
    return html.unescape(_TAG.sub("", str(text or ""))).strip()


def _parse(kind: Kind, payload: Any) -> SearchResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ConnectorUnavailable("BAD_RESPONSE", "네이버 검색 응답 형식이 예상과 다르다")
    try:
        total = max(0, int(payload.get("total") or 0))
    except (TypeError, ValueError):
        total = 0
    dates: list[date] = []
    news: list[SearchNews] = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            continue
        if kind == "blog":
            raw = str(item.get("postdate") or "")
            try:
                dates.append(datetime.strptime(raw, "%Y%m%d").date())
            except ValueError:
                pass
        elif kind == "news":
            try:
                at = parsedate_to_datetime(str(item.get("pubDate") or "")).astimezone(UTC)
            except (TypeError, ValueError):
                at = None
            url = str(item.get("originallink") or item.get("link") or "") or None
            host = urlparse(url).hostname if url else None
            news.append(SearchNews(_clean(item.get("title")), host, url, at))
    return SearchResult(kind, total, tuple(dates), tuple(news))


class NaverSearchConnector:
    """블로그·카페글·뉴스 검색. 키는 데이터랩과 같은 HUB 앱 키."""

    def __init__(self, env: Mapping[str, str] | None = None, *,
                 client: httpx.AsyncClient | None = None,
                 cache: JsonDailyCache | None = None) -> None:  # fmt: skip
        env = os.environ if env is None else env
        self.url = (env.get(URL_ENV) or DEFAULT_URL).rstrip("/")
        self.daily_limit = limit_of(env, DAILY_LIMIT_ENV, DEFAULT_DAILY_LIMIT)
        self._id = env.get(CLIENT_ID_ENV) or ""
        self._secret = env.get(CLIENT_SECRET_ENV) or ""
        self._client = client
        self._cache = cache or _cache

    def __repr__(self) -> str:  # 키를 담지 않는다
        return f"NaverSearchConnector(url={self.url!r})"

    @property
    def configured(self) -> bool:
        return bool(self._id and self._secret)

    async def search(self, kind: Kind, query: str, display: int = MAX_DISPLAY,
                     sort: Literal["sim", "date"] = "date") -> SearchResult:  # fmt: skip
        """한 번 부른다 (같은 요청은 그날 캐시). 못 읽으면 `ConnectorUnavailable`."""
        params = {"query": query, "display": str(max(1, min(MAX_DISPLAY, display))),
                  "start": "1", "sort": sort, "format": "json"}  # fmt: skip
        key = f"{self.url}/{kind}|{sorted(params.items())}"
        cached = self._cache.get(key)
        if cached is not None:
            return _parse(kind, cached)
        if not self.configured:
            why = f"{CLIENT_ID_ENV}·{CLIENT_SECRET_ENV}가 없어 네이버 검색을 부를 수 없다"
            raise ConnectorUnavailable("UNAUTHORIZED", why)
        self._cache.count_call(self.daily_limit, "네이버 블로그·카페·뉴스 검색")
        headers = {"X-NCP-APIGW-API-KEY-ID": self._id, "X-NCP-APIGW-API-KEY": self._secret}
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            with stage("external"):
                response = await client.get(f"{self.url}/{kind}", params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(
                "UNAVAILABLE", f"네이버 검색에 연결할 수 없다 ({type(exc).__name__})"
            ) from exc
        finally:
            if client is not self._client:
                await client.aclose()
        status = response.status_code
        if status in (401, 403):
            raise ConnectorUnavailable("NOT_ENABLED", f"네이버 검색 응답 {status}: {NOT_ENABLED}")
        if status != 200:
            code = {400: "BAD_REQUEST", 429: "RATE_LIMITED"}.get(status, "UNAVAILABLE")
            raise ConnectorUnavailable(code, f"네이버 검색 응답 {status} ({code})")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorUnavailable("BAD_RESPONSE", "네이버 검색 응답이 JSON이 아니다") from exc
        result = _parse(kind, payload)
        # 캐시에도 남길 것만 (작성자·본문 요약은 버린다)
        self._cache.put(key, _slim(kind, payload))
        return result


def _slim(kind: Kind, payload: dict[str, Any]) -> dict[str, Any]:
    keep = {"blog": ("postdate",), "cafearticle": (), "news": ("title", "originallink", "link",
                                                               "pubDate")}[kind]  # fmt: skip
    items = [{k: i.get(k) for k in keep if k in i} for i in payload.get("items", [])
             if isinstance(i, dict)]  # fmt: skip
    return {"total": payload.get("total"), "items": items}
