"""검색광고 키워드 도구·HUB 블로그·카페·뉴스 커넥터. httpx MockTransport만 (실제 API 호출 없음)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json

import httpx
import pytest

from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_search import NOT_ENABLED, NaverSearchConnector
from data_agent.connectors.naver_searchad import (
    JsonDailyCache,
    NaverSearchAdConnector,
    parse,
    signature,
)

KEYS = {"NAVER_SEARCHAD_API_KEY": "test-api-key", "NAVER_SEARCHAD_SECRET": "test-secret",
        "NAVER_SEARCHAD_CUSTOMER_ID": "1234"}  # fmt: skip
HUB = {"NAVER_HUB_CLIENT_ID": "test-id", "NAVER_HUB_CLIENT_SECRET": "test-hub-secret"}


def _client(handler) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- 검색광고


def test_signature_matches_official_sample_formula() -> None:
    expected = base64.b64encode(
        hmac.new(b"test-secret", b"1700000000000.GET./keywordstool", hashlib.sha256).digest()
    ).decode()
    assert signature("test-secret", "1700000000000", "GET", "/keywordstool") == expected


def test_request_path_params_headers_and_below_10() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"keywordList": [
            {"relKeyword": "그림책", "monthlyPcQcCnt": "1,200", "monthlyMobileQcCnt": 8800,
             "compIdx": "높음"},
            {"relKeyword": "희귀 키워드", "monthlyPcQcCnt": "< 10", "monthlyMobileQcCnt": "<10",
             "compIdx": "low"},
        ]})  # fmt: skip

    conn = NaverSearchAdConnector(KEYS, client=_client(handler), cache=JsonDailyCache(),
                                  clock=lambda: 1700000000.0)  # fmt: skip
    stats = asyncio.run(conn.keywords(["그림책", "그림책", "동화", "a", "b", "c", "d"]))
    req = seen[0]
    assert req.url.path == "/keywordstool" and req.method == "GET"
    assert req.url.params["hintKeywords"] == "그림책,동화,a,b,c"  # 중복 없이 최대 5개
    assert req.url.params["showDetail"] == "1"
    assert req.headers["X-Timestamp"] == "1700000000000"
    assert req.headers["X-API-KEY"] == "test-api-key" and req.headers["X-Customer"] == "1234"
    assert req.headers["X-Signature"] == signature("test-secret", "1700000000000", "GET",
                                                   "/keywordstool")  # fmt: skip
    first, rare = stats
    assert (first.pc, first.mobile, first.total, first.competition) == (1200, 8800, 10000, "high")
    assert rare.pc is None and rare.pc_below_10 and rare.mobile_below_10 and rare.total is None
    # 하루 캐시: 같은 힌트는 다시 부르지 않는다
    asyncio.run(conn.keywords(["그림책", "동화", "a", "b", "c"]))
    assert len(seen) == 1 and conn.calls_today == 1
    assert "test-secret" not in repr(conn) and "test-api-key" not in repr(conn)


def test_bare_list_response_is_also_read() -> None:
    stats = parse([{"relKeyword": "책", "monthlyPcQcCnt": "10", "monthlyMobileQcCnt": "20"}])
    assert stats[0].total == 30 and stats[0].competition is None


def test_no_keys_is_unauthorized_with_register_message() -> None:
    conn = NaverSearchAdConnector({}, client=_client(lambda r: httpx.Response(200, json=[])),
                                  cache=JsonDailyCache())  # fmt: skip
    with pytest.raises(ConnectorUnavailable) as err:
        asyncio.run(conn.keywords(["책"]))
    assert err.value.code == "UNAUTHORIZED" and "키 등록 후 가능" in str(err.value)


def test_daily_limit_and_429() -> None:
    env = {**KEYS, "NAVER_SEARCHAD_DAILY_LIMIT": "1"}
    conn = NaverSearchAdConnector(env, client=_client(lambda r: httpx.Response(200, json=[])),
                                  cache=JsonDailyCache())  # fmt: skip
    asyncio.run(conn.keywords(["a"]))
    with pytest.raises(ConnectorUnavailable) as err:
        asyncio.run(conn.keywords(["b"]))
    assert err.value.code == "RATE_LIMITED"
    busy = NaverSearchAdConnector(KEYS, client=_client(lambda r: httpx.Response(429, json={})),
                                  cache=JsonDailyCache())  # fmt: skip
    with pytest.raises(ConnectorUnavailable) as err:
        asyncio.run(busy.keywords(["c"]))
    assert err.value.code == "RATE_LIMITED" and "test-secret" not in str(err.value)


# ---------------------------------------------------------------- HUB 블로그·카페·뉴스


def _hub(request: httpx.Request) -> httpx.Response:
    kind = request.url.path.rsplit("/", 1)[-1]
    if kind == "blog":
        items = [{"title": "t", "postdate": "20261117", "bloggername": "누군가",
                  "description": "본문"}, {"postdate": "20261001"}]  # fmt: skip
    elif kind == "cafearticle":
        items = [{"title": "카페 글", "cafename": "어떤 카페"}]
    else:
        items = [{"title": "<b>그림책</b> &amp; 연말", "originallink": "https://news.example.com/a/1",
                  "link": "https://n.news.naver.com/x",
                  "pubDate": "Tue, 17 Nov 2026 09:00:00 +0900",
                  "description": "요약"}]  # fmt: skip
    return httpx.Response(200, json={"total": 1234, "items": items})


def test_hub_paths_params_and_parsed_fields() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _hub(request)

    cache = JsonDailyCache()
    conn = NaverSearchConnector(HUB, client=_client(handler), cache=cache)
    blog = asyncio.run(conn.search("blog", "그림책"))
    cafe = asyncio.run(conn.search("cafearticle", "그림책", 1, "sim"))
    news = asyncio.run(conn.search("news", "그림책", 3))
    assert [r.url.path for r in seen] == ["/search/v1/blog", "/search/v1/cafearticle",
                                          "/search/v1/news"]  # fmt: skip
    assert seen[0].url.params["display"] == "100" and seen[0].url.params["sort"] == "date"
    assert seen[0].headers["X-NCP-APIGW-API-KEY-ID"] == "test-id"
    assert blog.total == 1234 and [d.isoformat() for d in blog.dates] == ["2026-11-17",
                                                                          "2026-10-01"]  # fmt: skip
    assert cafe.dates == ()  # 카페글 응답에는 날짜가 없다
    n = news.news[0]
    assert n.title == "그림책 & 연말" and n.source == "news.example.com"
    assert n.published_at is not None and n.published_at.hour == 0  # UTC
    # 캐시에는 작성자·본문 요약을 남기지 않는다
    stored = json.dumps(cache._hits, ensure_ascii=False)  # noqa: SLF001
    assert "누군가" not in stored and "본문" not in stored and "어떤 카페" not in stored
    asyncio.run(conn.search("blog", "그림책"))
    assert len(seen) == 3  # 하루 캐시


def test_hub_api_not_enabled_says_how_to_turn_on() -> None:
    conn = NaverSearchConnector(HUB, client=_client(lambda r: httpx.Response(401, json={})),
                                cache=JsonDailyCache())  # fmt: skip
    with pytest.raises(ConnectorUnavailable) as err:
        asyncio.run(conn.search("blog", "책"))
    assert err.value.code == "NOT_ENABLED" and NOT_ENABLED in str(err.value)
