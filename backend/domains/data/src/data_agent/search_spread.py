"""키워드 하나의 블로그·카페·뉴스 확산 (keyword_trend 급상승 상위·trend_memes 상위가 같이 쓴다).

- 블로그: 날짜순 최대 100개를 받아 최근 7일(한국 날짜) 글 수를 결과 날짜로 센다.
  100개가 모두 7일 안이면 실제는 더 많을 수 있어 `blog_7d_capped`. 전체 건수(total)도 남긴다
- 카페글: 응답에 날짜가 없어 최근 7일 수는 못 센다 → 전체 건수만 + 이유
- 뉴스: 날짜순 3개(제목·출처 도메인·주소)
- 키워드 하나에 3번(블로그·카페·뉴스). 앱에서 API를 켜지 않았으면 unavailable + 안내 문장
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ax_contracts.schemas.data import KeywordNews, KeywordSpread
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_search import MAX_DISPLAY, NOT_ENABLED

CAFE_NO_DATE = "카페글 검색 응답에 글 날짜가 없어 최근 7일 수는 셀 수 없음 (전체 건수만)"
NEWS_SHOWN = 3


def _why(error: ConnectorUnavailable) -> tuple[str, str]:
    if error.code in ("NOT_ENABLED", "UNAUTHORIZED"):
        return "unavailable", NOT_ENABLED
    if error.code == "RATE_LIMITED":
        return "not_checked", f"확인 안 함(한도): {error}"
    return "unavailable", f"네이버 검색을 읽지 못함 [{error.code}]"


async def spread(connector: Any, keyword: str, today: date) -> KeywordSpread:
    """블로그·카페·뉴스 확산 한 키워드. 커넥터가 없으면 not_checked."""
    if connector is None:
        return KeywordSpread(status="not_checked", reason="네이버 검색 커넥터가 없음")
    if not getattr(connector, "configured", True):
        return KeywordSpread(status="unavailable", reason=NOT_ENABLED)
    try:
        blog = await connector.search("blog", keyword, MAX_DISPLAY, "date")
        cafe = await connector.search("cafearticle", keyword, 1, "sim")
        news = await connector.search("news", keyword, NEWS_SHOWN, "date")
    except ConnectorUnavailable as error:
        status, why = _why(error)
        return KeywordSpread(status=status, reason=why)  # type: ignore[arg-type]
    since = today - timedelta(days=6)
    recent = [d for d in blog.dates if since <= d <= today]
    return KeywordSpread(
        status="known",
        blog_total=blog.total,
        blog_7d=len(recent),
        blog_7d_capped=bool(blog.dates) and len(recent) == len(blog.dates) == MAX_DISPLAY,
        cafe_total=cafe.total,
        cafe_7d=None,
        cafe_7d_reason=CAFE_NO_DATE,
        news=[
            KeywordNews(title=n.title, source=n.source, url=n.url, published_at=n.published_at)
            for n in news.news[:NEWS_SHOWN]
        ],  # fmt: skip
    )
