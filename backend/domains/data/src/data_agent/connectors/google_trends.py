"""Google Trends 인기 검색어 공개 RSS 커넥터. 읽기만 한다 (로그인·스크래핑 없음).

- 주소: `https://trends.google.com/trending/rss?geo=<지역>` (공개 RSS, 키 없음).
  `AX_GOOGLE_TRENDS_RSS_URL`로 바꿀 수 있다.
- 2026-11-02 확인: 이 주소는 200(`text/xml`, 항목 10개), 예전 일별 주소
  `trends.google.com/trends/trendingsearches/daily/rss`는 404다. 그래서 이 주소 하나만 읽는다
- 항목마다 검색어(`title`), 피드 안 순서(1부터, 순위), 올라온 시각(`pubDate`),
  피드가 주는 트래픽 표시값(`ht:approx_traffic`, 예: "500+").
  트래픽 표시값은 검색 건수가 아니라 Google이 붙인 대략 표시라 숫자로 바꾸지 않고 글자 그대로 둔다
- 못 읽으면 `ConnectorUnavailable`: 연결 실패·5xx `UNAVAILABLE`, 404·400 `BAD_REQUEST`,
  429 `RATE_LIMITED`, XML 아님·형식 다름 `BAD_RESPONSE`.
  항목이 0개인 피드는 빈 목록(지어내지 않음)
- DTD(`<!DOCTYPE`)가 든 응답은 읽지 않는다(`BAD_RESPONSE`, XML 엔티티 공격 방지)
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from data_agent.connectors import ConnectorUnavailable

DEFAULT_URL = "https://trends.google.com/trending/rss"


def _http_url(raw: str | None) -> str | None:
    """http(s) 주소만 (그 밖은 버린다)."""
    value = (raw or "").strip()
    return value if value.startswith(("https://", "http://")) else None


MAX_NEWS = 3  # 검색어마다 남기는 관련 뉴스 제목 수
URL_ENV = "AX_GOOGLE_TRENDS_RSS_URL"
TIMEOUT_SECONDS = 15.0
HT = "{https://trends.google.com/trending/rss}"  # 피드의 ht: 이름공간
SOURCE = "google_trends_rss"

_client: httpx.AsyncClient | None = None  # 테스트가 `use_client`로 바꿔 끼운다


def use_client(client: httpx.AsyncClient | None) -> None:
    """HTTP 클라이언트를 바꿔 끼운다 (테스트용). None이면 호출마다 새로 만든다."""
    global _client
    _client = client


@dataclass(frozen=True)
class TrendingItem:
    """인기 검색어 하나 (피드에 있는 그대로)."""

    keyword: str
    rank: int  # 피드 안 순서 (1부터)
    traffic: str | None  # 피드의 트래픽 표시값 (예: "500+"). 없으면 None
    published_at: datetime | None  # 피드의 pubDate (UTC). 없거나 못 읽으면 None
    # 피드가 함께 주는 관련 뉴스 제목 (`ht:news_item_title`, 최대 MAX_NEWS개). 없으면 ()
    news_titles: tuple[str, ...] = ()
    # 관련 뉴스 (제목, 출처, 주소) 피드 값 그대로
    # (`ht:news_item_source`·`ht:news_item_url`, 최대 MAX_NEWS)
    news: tuple[tuple[str, str | None, str | None], ...] = ()


def parse_feed(body: bytes) -> list[TrendingItem]:
    """RSS 본문 → 항목 목록. 형식이 다르면 `ConnectorUnavailable(BAD_RESPONSE)`."""
    if b"<!DOCTYPE" in body[:2048].upper():
        raise ConnectorUnavailable("BAD_RESPONSE", "인기 검색어 피드에 DTD가 있어 읽지 않음")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as error:
        raise ConnectorUnavailable("BAD_RESPONSE", "인기 검색어 피드가 XML이 아님") from error
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ConnectorUnavailable("BAD_RESPONSE", "인기 검색어 피드 형식이 RSS가 아님")
    out: list[TrendingItem] = []
    seen: set[str] = set()
    for item in channel.findall("item"):
        keyword = (item.findtext("title") or "").strip()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        traffic = (item.findtext(f"{HT}approx_traffic") or "").strip() or None
        published: datetime | None = None
        raw = (item.findtext("pubDate") or "").strip()
        if raw:
            try:
                published = parsedate_to_datetime(raw).astimezone(UTC)
            except (TypeError, ValueError):
                published = None
        news = tuple(
            t
            for t in (
                (n.findtext(f"{HT}news_item_title") or "").strip()
                for n in item.findall(f"{HT}news_item")
            )
            if t
        )[:MAX_NEWS]
        links = tuple(
            (
                title,
                (n.findtext(f"{HT}news_item_source") or "").strip() or None,
                _http_url(n.findtext(f"{HT}news_item_url")),
            )
            for n in item.findall(f"{HT}news_item")
            if (title := (n.findtext(f"{HT}news_item_title") or "").strip())
        )[:MAX_NEWS]
        out.append(TrendingItem(keyword, len(out) + 1, traffic, published, news, links))
    return out


class GoogleTrendsConnector:
    """인기 검색어 공개 RSS 읽기. 주소는 `env`(기본 `os.environ`)에서 읽는다."""

    def __init__(
        self, env: Mapping[str, str] | None = None, *, client: httpx.AsyncClient | None = None
    ) -> None:
        env = os.environ if env is None else env
        self.url = (env.get(URL_ENV) or DEFAULT_URL).rstrip("/")
        self._client = client
        self.calls = 0

    def __repr__(self) -> str:
        return f"GoogleTrendsConnector(url={self.url!r}, calls={self.calls})"

    async def trending(self, geo: str = "KR") -> list[TrendingItem]:
        """지역의 인기 검색어 (피드 순서 = 순위)."""
        self.calls += 1
        client = self._client or _client
        try:
            if client is not None:
                response = await client.get(self.url, params={"geo": geo})
            else:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as fresh:
                    response = await fresh.get(self.url, params={"geo": geo})
        except httpx.HTTPError as error:
            raise ConnectorUnavailable(
                "UNAVAILABLE", f"인기 검색어 피드 연결 실패: {error.__class__.__name__}"
            ) from error
        status = response.status_code
        if status == 429:
            raise ConnectorUnavailable("RATE_LIMITED", "인기 검색어 피드 요청 한도 (429)")
        if status >= 500:
            raise ConnectorUnavailable("UNAVAILABLE", f"인기 검색어 피드 서버 오류 ({status})")
        if status >= 400:
            raise ConnectorUnavailable("BAD_REQUEST", f"인기 검색어 피드 요청 실패 ({status})")
        return parse_feed(response.content)
