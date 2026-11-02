"""YouTube 커넥터·스냅샷 저장·수집 테스트. 실제 API 없이 httpx MockTransport 메모리 응답으로 돈다.

응답 모양은 2026-11-02 실제 호출(videos.list mostPopular KR, search.list)에서 본 것과 같다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.youtube import DEFAULT_URL, YouTubeConnector
from data_agent.youtube_collect import (
    YouTubeConfig,
    collect_once,
    config_path,
    load_config,
    load_env,
)
from data_agent.youtube_store import SnapshotStore

KEY = "test-yt-key-9876"
ENV = {"YOUTUBE_API_KEY": KEY}
NOW = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)  # 한국 10시
Handler = Callable[[httpx.Request], httpx.Response]
CATBOOKS_YOUTUBE = (
    Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "youtube.toml"
)


def _video(
    vid: str, views: int | None = 1000, likes: int | None = 10, comments: int | None = 1, **extra
) -> dict:  # noqa: ANN003
    stats = {"favoriteCount": "0"}
    if views is not None:
        stats["viewCount"] = str(views)
    if likes is not None:
        stats["likeCount"] = str(likes)
    if comments is not None:
        stats["commentCount"] = str(comments)
    snippet = {
        "publishedAt": "2026-11-01T09:00:06Z",
        "channelId": f"ch_{vid}",
        "title": f"영상 {vid}",
        "categoryId": "10",
        "tags": ["고양이", " ", "책"],
        **extra,
    }
    return {"kind": "youtube#video", "id": vid, "snippet": snippet, "statistics": stats}


class Fake:
    """요청을 기록하고 경로별로 답한다."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _connector(handler: Handler, env: dict | None = None) -> tuple[YouTubeConnector, Fake]:
    fake = Fake(handler)
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return YouTubeConnector(ENV if env is None else env, client=client), fake


def _popular_pages(total: int = 200) -> Handler:
    """mostPopular를 50개씩 쪽으로 나눠 준다. 마지막 쪽엔 nextPageToken이 없다."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params.get("chart") == "mostPopular":
            page = int(params.get("pageToken") or 0)
            items = [_video(f"p{i}") for i in range(page * 50, min(total, page * 50 + 50))]
            body: dict = {"items": items, "pageInfo": {"totalResults": total}}
            if page * 50 + 50 < total:
                body["nextPageToken"] = str(page + 1)
            return httpx.Response(200, json=body)
        ids = params.get("id", "").split(",")
        return httpx.Response(200, json={"items": [_video(i) for i in ids if i]})

    return handler


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


# ---------------------------------------------------------------- 커넥터: 정상


def test_popular_reads_four_pages_with_ranks_and_header_key() -> None:
    connector, fake = _connector(_popular_pages())
    videos = _run(connector.popular("KR"))
    assert len(videos) == 200
    assert [v.rank for v in videos[:3]] == [1, 2, 3] and videos[-1].rank == 200
    assert len(fake.requests) == 4 and connector.quota_used == 4
    for request in fake.requests:
        assert str(request.url).startswith(f"{DEFAULT_URL}/videos")
        assert request.headers["X-Goog-Api-Key"] == KEY
        assert KEY not in str(request.url)  # 키는 URL에 넣지 않는다
    assert videos[0].tags == ("고양이", "책")  # 빈 태그는 뺀다
    assert videos[0].published_at == datetime(2026, 11, 1, 9, 0, 6, tzinfo=UTC)


def test_popular_stops_when_there_is_no_next_page() -> None:
    connector, fake = _connector(_popular_pages(total=70))
    assert len(_run(connector.popular())) == 70
    assert len(fake.requests) == 2


def test_videos_are_read_fifty_at_a_time() -> None:
    connector, fake = _connector(_popular_pages())
    ids = [f"v{i}" for i in range(120)] + ["v0"]  # 겹친 id는 한 번만
    videos = _run(connector.videos(ids))
    assert len(videos) == 120
    assert [len(r.url.params["id"].split(",")) for r in fake.requests] == [50, 50, 20]
    assert connector.quota_used == 3


def test_missing_videos_stay_missing() -> None:
    """지워졌거나 비공개인 영상은 응답에 없고 결과에도 없다(0으로 만들지 않는다)."""
    connector, _ = _connector(lambda r: httpx.Response(200, json={"items": [_video("a")]}))
    assert [v.video_id for v in _run(connector.videos(["a", "gone"]))] == ["a"]


def test_search_then_reads_statistics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            items = [
                {"id": {"kind": "youtube#video", "videoId": "s1"}},
                {"id": {"kind": "youtube#channel"}},
            ]
            return httpx.Response(200, json={"items": items})
        return httpx.Response(200, json={"items": [_video("s1", views=1596534)]})

    connector, fake = _connector(handler)
    found = _run(connector.search("고양이", NOW - timedelta(days=7)))
    assert [(v.video_id, v.view_count) for v in found] == [("s1", 1596534)]
    assert fake.requests[0].url.params["q"] == "고양이"
    assert fake.requests[0].url.params["publishedAfter"] == "2026-10-26T01:00:00Z"
    assert connector.quota_used == 101  # 검색 100 + 통계 1


# ---------------------------------------------------------------- 숫자 필드 누락


def test_hidden_counts_are_none_not_zero() -> None:
    body = {"items": [_video("liveless", likes=None, comments=None), _video("noviews", views=None)]}
    connector, _ = _connector(lambda r: httpx.Response(200, json=body))
    a, b = _run(connector.videos(["liveless", "noviews"]))
    assert (a.view_count, a.like_count, a.comment_count) == (1000, None, None)
    assert b.view_count is None
    no_stats = {
        "items": [{"id": "x", "snippet": _video("x")["snippet"]}]
    }  # statistics 칸 자체가 없음
    connector, _ = _connector(lambda r: httpx.Response(200, json=no_stats))
    (c,) = _run(connector.videos(["x"]))
    assert (c.view_count, c.like_count, c.comment_count) == (None, None, None)


# ---------------------------------------------------------------- 실패·빈 응답·할당량


def _google_error(status: int, reason: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "message": f"{reason} 메시지",
                                                  "errors": [{"reason": reason}]}})  # fmt: skip


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (_google_error(403, "quotaExceeded"), "QUOTA_EXCEEDED"),
        (_google_error(403, "forbidden"), "UNAUTHORIZED"),
        (_google_error(400, "keyInvalid"), "BAD_REQUEST"),
        (_google_error(429, "rateLimitExceeded"), "RATE_LIMITED"),
        (httpx.Response(500), "UNAVAILABLE"),
        (httpx.Response(503, text="<html>"), "UNAVAILABLE"),
    ],
    ids=["quota", "forbidden", "bad-request", "rate-limit", "500", "503"],
)
def test_http_errors_are_unavailable(response: httpx.Response, code: str) -> None:
    connector, _ = _connector(lambda r: response)
    with pytest.raises(ConnectorUnavailable) as caught:
        _run(connector.popular())
    assert caught.value.code == code
    assert connector.quota_used == 1  # 실패한 호출도 할당량을 쓴다


def test_network_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    connector, _ = _connector(handler)
    with pytest.raises(ConnectorUnavailable) as caught:
        _run(connector.videos(["a"]))
    assert caught.value.code == "UNAVAILABLE"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b""),
        httpx.Response(200, text="<html>gateway</html>"),
        httpx.Response(200, json={}),  # items 없음
        httpx.Response(200, json={"items": [{"id": "a"}]}),  # snippet 없음
        httpx.Response(200, json={"items": [_video("a") | {"statistics": {"viewCount": "많음"}}]}),
        httpx.Response(200, json={"items": [_video("a") | {"statistics": {"viewCount": "-5"}}]}),
    ],
    ids=["empty-body", "html", "no-items", "no-snippet", "bad-number", "negative"],
)
def test_malformed_responses_are_bad_response(response: httpx.Response) -> None:
    connector, _ = _connector(lambda r: response)
    with pytest.raises(ConnectorUnavailable) as caught:
        _run(connector.videos(["a"]))
    assert caught.value.code == "BAD_RESPONSE"


def test_empty_items_is_an_empty_list() -> None:
    connector, _ = _connector(lambda r: httpx.Response(200, json={"items": []}))
    assert _run(connector.popular()) == []
    assert _run(connector.search("아무도 안 찾는 말", NOW)) == []
    assert connector.quota_used == 101  # 검색 결과가 없으면 통계를 부르지 않는다


def test_missing_key_does_not_call() -> None:
    connector, fake = _connector(_popular_pages(), env={})
    with pytest.raises(ConnectorUnavailable) as caught:
        _run(connector.popular())
    assert caught.value.code == "UNAUTHORIZED"
    assert fake.requests == [] and connector.quota_used == 0


def test_key_never_appears_in_errors_or_repr() -> None:
    connector, _ = _connector(lambda r: httpx.Response(403, text=f"bad {KEY}"))
    with pytest.raises(ConnectorUnavailable) as caught:
        _run(connector.popular())
    assert KEY not in str(caught.value) and KEY not in repr(connector)


# ---------------------------------------------------------------- 저장


def test_same_time_same_video_is_saved_once(tmp_path: Path) -> None:
    connector, _ = _connector(_popular_pages(total=3))
    videos = _run(connector.popular())
    store = SnapshotStore(tmp_path / "yt.db")
    from data_agent.youtube_store import Row

    assert store.save(NOW, [Row(v, ("popular",)) for v in videos]) == 3
    assert (
        store.save(NOW, [Row(v, ("popular",)) for v in videos]) == 0
    )  # 같은 시각 다시 넣으면 건너뜀
    assert store.save(NOW + timedelta(hours=1), [Row(videos[0], ("tracked",))]) == 1
    assert store.count("p0") == 2 and store.count() == 4


def test_hidden_counts_are_stored_as_null() -> None:
    connector, _ = _connector(
        lambda r: httpx.Response(200, json={"items": [_video("h", likes=None)]})
    )
    (video,) = _run(connector.videos(["h"]))
    store = SnapshotStore(":memory:")
    from data_agent.youtube_store import Row

    store.save(NOW, [Row(video, ("tracked",))])
    (row,) = store.rows("h")
    assert row["like_count"] is None and row["view_count"] == 1000
    assert row["popular_rank"] is None
    assert json.loads(row["tags"]) == ["고양이", "책"]
    assert row["collected_at"] == "2026-11-02T01:00:00+00:00"


# ---------------------------------------------------------------- 수집


def _collect_handler(log: list[str]) -> Handler:
    popular = _popular_pages(total=3)

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200, json={"items": [{"id": {"videoId": "s1"}}, {"id": {"videoId": "p0"}}]}
            )
        return popular(request)

    return handler


def test_collect_saves_popular_every_time_and_searches_once_a_day() -> None:
    store = SnapshotStore(":memory:")
    config = YouTubeConfig(queries=("고양이",))
    calls: list[str] = []
    connector, _ = _connector(_collect_handler(calls))
    first = _run(collect_once(connector, store, config, now=NOW))
    assert first.searched and first.popular == 3 and first.searched_videos == 2
    assert first.quota_units == 1 + 100 + 1  # 인기 1쪽 + 검색 + 검색 결과 통계
    assert first.saved == 4  # p0은 인기·검색 둘 다지만 한 줄
    (p0,) = store.rows("p0")
    assert p0["popular_rank"] == 1 and p0["sources"] == "popular,search:고양이"
    assert "할당량 102단위" in first.line()

    calls.clear()
    connector, _ = _connector(_collect_handler(calls))
    second = _run(collect_once(connector, store, config, now=NOW + timedelta(hours=1)))
    assert not second.searched and "search" not in calls  # 같은 한국 날짜엔 검색 안 함
    assert "검색 안 함(오늘 이미 함)" in second.line()
    assert second.tracked == 1  # 지난번에 본 s1 (이번 인기 목록에 없음)
    assert second.quota_units == 2  # 인기 1 + 추적 1

    connector, _ = _connector(_collect_handler(calls))
    next_day = _run(collect_once(connector, store, config, now=NOW + timedelta(days=1)))
    assert next_day.searched


def test_quota_exceeded_stops_the_run_but_keeps_what_was_read() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return _google_error(403, "quotaExceeded")
        return _popular_pages(total=3)(request)

    store = SnapshotStore(":memory:")
    connector, fake = _connector(handler)
    result = _run(
        collect_once(connector, store, YouTubeConfig(queries=("고양이", "고양이 책")), now=NOW)
    )
    assert result.saved == 3  # 인기 목록은 저장
    assert result.errors == ["검색 '고양이' [QUOTA_EXCEEDED]"]
    assert (
        sum(r.url.path.endswith("/search") for r in fake.requests) == 1
    )  # 두 번째 키워드는 안 부름
    assert result.quota_units == 101


def test_failed_popular_is_logged_without_zeros() -> None:
    store = SnapshotStore(":memory:")
    connector, _ = _connector(lambda r: httpx.Response(500))
    result = _run(collect_once(connector, store, YouTubeConfig(), now=NOW))
    assert result.saved == 0 and store.count() == 0
    assert result.errors == ["인기 목록 [UNAVAILABLE]"]
    assert "검색 안 함(회사 키워드 없음)" in result.line()
    assert "실패: 인기 목록 [UNAVAILABLE]" in result.line()


# ---------------------------------------------------------------- 설정


def test_company_keywords_come_from_config(tmp_path: Path) -> None:
    assert load_config(CATBOOKS_YOUTUBE).queries  # 캣북스 키워드가 있다
    path = tmp_path / "youtube.toml"
    path.write_text('region = "KR"\n[[queries]]\nq = "그림책"\n', encoding="utf-8")
    assert load_config(path) == YouTubeConfig(region="KR", queries=("그림책",))
    assert load_config(tmp_path / "none.toml") == YouTubeConfig()
    assert config_path({"DATA_YOUTUBE_CONFIG": str(path)}) == path
    assert config_path({}).name == "youtube.toml" and config_path({}).parent.name == "catbooks"


def test_env_file_fills_only_missing_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("YOUTUBE_API_KEY=from-file\nOTHER=x # 주석\n", encoding="utf-8")
    assert load_env({}, env_file)["YOUTUBE_API_KEY"] == "from-file"
    assert load_env({"YOUTUBE_API_KEY": "from-env"}, env_file)["YOUTUBE_API_KEY"] == "from-env"
    assert load_env({}, env_file)["OTHER"] == "x"
