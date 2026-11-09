"""인기 검색어(Google Trends 공개 RSS) 출처: 커넥터·스냅샷 저장·수집·trend_candidates·추천 연결.

실제 Google·네이버를 부르지 않는다 (httpx MockTransport·메모리 커넥터).
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import (
    ContentRecommendRequest,
    TrendCandidatesRequest,
    TrendScanInput,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.google_trends import GoogleTrendsConnector, TrendingItem, parse_feed
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.connectors.youtube import VideoStat
from data_agent.explain import _Idea
from data_agent.skills.content_recommend import recommend
from data_agent.skills.trend_candidates import (
    CandidateConfig,
    find_candidates,
    load_config,
    pick_trending,
    trending_db,
)
from data_agent.skills.trend_scan import REQUEST_DAYS
from data_agent.trending_collect import collect_once, load_geo
from data_agent.trending_store import TrendingSnapshot, TrendingStore, latest_trending
from data_agent.youtube_store import Row, SnapshotStore

NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
SNAP_AT = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
END = date(2026, 11, 1)
START = END - timedelta(days=REQUEST_DAYS - 1)

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
<channel><title>Daily Search Trends</title>
<item><title>T1</title><ht:approx_traffic>500+</ht:approx_traffic>
<pubDate>Mon, 2 Nov 2026 05:40:00 -0800</pubDate></item>
<item><title>초록우산</title><ht:approx_traffic>200+</ht:approx_traffic></item>
<item><title>  </title></item>
<item><title>초록우산</title></item>
<item><title>대한민국</title><pubDate>not a date</pubDate></item>
</channel></rss>""".encode()


# ---------------------------------------------------------------- 커넥터


def test_feed_items_keep_order_traffic_text_and_time() -> None:
    items = parse_feed(FEED)
    assert [(i.keyword, i.rank, i.traffic) for i in items] == [
        ("T1", 1, "500+"),
        ("초록우산", 2, "200+"),
        ("대한민국", 3, None),  # 빈 제목·같은 검색어는 건너뛰고 순위를 다시 붙인다
    ]
    assert items[0].published_at == datetime(2026, 11, 2, 13, 40, tzinfo=UTC)
    assert items[2].published_at is None  # 못 읽은 시각은 비운다


@pytest.mark.parametrize(
    "body",
    [b"not xml", b"<html><body>hi</body></html>",
     b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel/></rss>'],
)  # fmt: skip
def test_bad_feeds_are_bad_response(body: bytes) -> None:
    with pytest.raises(ConnectorUnavailable) as error:
        parse_feed(body)
    assert error.value.code == "BAD_RESPONSE"


def test_empty_feed_is_an_empty_list_not_made_up() -> None:
    assert parse_feed(b'<rss version="2.0"><channel><title>x</title></channel></rss>') == []


@pytest.mark.parametrize(
    ("status", "code"), [(429, "RATE_LIMITED"), (503, "UNAVAILABLE"), (404, "BAD_REQUEST")]
)
def test_http_errors_become_connector_unavailable(status: int, code: str) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    connector = GoogleTrendsConnector({}, client=client)
    with pytest.raises(ConnectorUnavailable) as error:
        asyncio.run(connector.trending("KR"))
    assert error.value.code == code


def test_connector_asks_the_public_feed_with_the_region() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=FEED)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    items = asyncio.run(GoogleTrendsConnector({}, client=client).trending("KR"))
    assert len(items) == 3
    assert str(seen[0].url) == "https://trends.google.com/trending/rss?geo=KR"
    assert "authorization" not in {k.lower() for k in seen[0].headers}  # 키·로그인 없음


# ---------------------------------------------------------------- 저장·수집


def _items(*names: str) -> list[TrendingItem]:
    return [TrendingItem(n, i + 1, f"{(i + 1) * 100}+", None) for i, n in enumerate(names)]


def _trending_db(path: Path, names: tuple[str, ...], at: datetime = SNAP_AT) -> Path:
    store = TrendingStore(path)
    store.save(at, "KR", _items(*names))
    store.close()
    return path


def test_store_keeps_one_row_per_time_and_rank_and_reads_the_latest(tmp_path: Path) -> None:
    path = tmp_path / "trending_snapshots.db"
    store = TrendingStore(path)
    assert store.save(SNAP_AT - timedelta(hours=1), "KR", _items("옛날")) == 1
    assert store.save(SNAP_AT, "KR", _items("가", "나")) == 2
    assert store.save(SNAP_AT, "KR", _items("다른")) == 0  # 같은 시각·순위는 한 줄
    store.close()
    snap = latest_trending(path)
    assert snap is not None and snap.collected_at == SNAP_AT
    assert [(i.keyword, i.rank, i.traffic) for i in snap.items] == [
        ("가", 1, "100+"),
        ("나", 2, "200+"),
    ]
    before = latest_trending(path, at_or_before=SNAP_AT - timedelta(minutes=1))
    assert before is not None and [i.keyword for i in before.items] == ["옛날"]
    assert latest_trending(path, geo="US") is None


def test_latest_trending_does_not_create_a_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "none.db"
    assert latest_trending(missing) is None
    assert not missing.exists()


class FakeFeed:
    def __init__(self, items: list[TrendingItem] | None = None, fail: str | None = None) -> None:
        self.items, self.fail = items or [], fail

    async def trending(self, geo: str = "KR") -> list[TrendingItem]:
        if self.fail:
            raise ConnectorUnavailable(self.fail, "피드 실패")
        return self.items


def test_collect_once_saves_items_and_logs_failures(tmp_path: Path) -> None:
    store = TrendingStore(tmp_path / "t.db")
    ok = asyncio.run(collect_once(FakeFeed(_items("가", "나")), store, "KR", now=SNAP_AT))  # type: ignore[arg-type]
    assert (ok.items, ok.saved, ok.errors) == (2, 2, [])
    bad = asyncio.run(
        collect_once(FakeFeed(fail="UNAVAILABLE"), store, "KR", now=SNAP_AT + timedelta(hours=1))  # type: ignore[arg-type]
    )
    assert (bad.items, bad.saved, bad.errors) == (0, 0, ["인기 검색어 [UNAVAILABLE]"])
    store.close()
    snap = latest_trending(tmp_path / "t.db")
    assert (
        snap is not None and snap.collected_at == SNAP_AT
    )  # 실패한 실행은 빈 스냅샷을 남기지 않는다


def test_collector_region_comes_from_company_config(tmp_path: Path) -> None:
    path = tmp_path / "trends.toml"
    path.write_text('[trending]\ngeo = "jp"\n', encoding="utf-8")
    assert load_geo(path) == "JP"
    assert load_geo(tmp_path / "none.toml") == "KR"


# ---------------------------------------------------------------- 후보 고르기


def test_trending_uses_the_same_cleaning_aliases_and_exclusions_as_tags() -> None:
    config = CandidateConfig(exclude_tags=frozenset({"게임"}), aliases={"리그오브레전드": ("lol",)})
    snap = TrendingSnapshot(SNAP_AT, "KR", tuple(_items("LoL", "게임", "리그오브레전드", " 날씨 ")))
    picks = pick_trending(snap, config)
    assert [(p.keyword, p.item_keyword, p.rank) for p in picks] == [
        ("리그오브레전드", "LoL", 1),  # 별칭 → 대표 이름, 같은 이름은 한 번(앞 순위)
        ("날씨", " 날씨 ", 4),
    ]


def test_trending_db_defaults_next_to_the_youtube_db(tmp_path: Path) -> None:
    youtube = tmp_path / "x" / "youtube.db"
    assert trending_db({"DATA_YOUTUBE_DB": str(youtube)}) == str(
        tmp_path / "x" / "trending_snapshots.db"
    )
    assert trending_db({"DATA_TRENDING_DB": "/a/b.db"}) == "/a/b.db"


def test_company_config_reads_the_trending_section(tmp_path: Path) -> None:
    path = tmp_path / "trends.toml"
    path.write_text("[trending]\nmax_candidates = 3\nmax_age_hours = 6\n", encoding="utf-8")
    config = load_config({"DATA_TREND_CONFIG": str(path)})
    assert (config.trending_max, config.trending_max_age_hours, config.trending_geo) == (
        3,
        6.0,
        "KR",
    )


# ---------------------------------------------------------------- trend_candidates


def _calm(day: date) -> float:
    return 50.0 * (1 + 0.05 * math.sin(day.toordinal() * 2.1))


def _points(spike: float = 1.0) -> tuple[tuple[date, float], ...]:
    out, day = [], START
    while day <= END:
        out.append((day, _calm(day) * (spike if day > END - timedelta(days=3) else 1.0)))
        day += timedelta(days=1)
    top = max(v for _, v in out)
    return tuple((d, round(v * 100 / top, 5)) for d, v in out)


class Naver:
    def __init__(self, spikes: dict[str, float], fail: frozenset[str] = frozenset()) -> None:
        self.spikes, self.fail = spikes, fail
        self.queries: list[TrendScanInput] = []
        self.calls_today = 0

    async def search_trend(self, query: TrendScanInput) -> TrendResponse:
        self.queries.append(query)
        self.calls_today += 1
        name = query.keyword_groups[0].group_name
        if name in self.fail:
            raise ConnectorUnavailable("UNAVAILABLE", "연결 실패")
        series = TrendSeries(name, (name,), _points(self.spikes.get(name, 1.0)))
        return TrendResponse(query.start_date, query.end_date, query.time_unit, (series,))


def _stat(video_id: str, rank: int, tags: tuple[str, ...]) -> VideoStat:
    return VideoStat(
        video_id=video_id, channel_id="ch", category_id="20", published_at=NOW - timedelta(days=1),
        view_count=1000, like_count=10, comment_count=1, tags=tags, rank=rank,
    )  # fmt: skip


def _env(tmp_path: Path, trending: tuple[str, ...] | None, videos: bool = True) -> dict[str, str]:
    youtube = tmp_path / "youtube.db"
    if videos:
        store = SnapshotStore(youtube)
        store.save(SNAP_AT, [Row(_stat("v1", 1, ("t1", "젠지")), ("popular",)),
                             Row(_stat("v2", 2, ("t1", "젠지")), ("popular",))])  # fmt: skip
        store.close()
    if trending is not None:
        _trending_db(tmp_path / "trending_snapshots.db", trending)
    config = tmp_path / "trends.toml"
    config.write_text(
        '[[groups]]\nname = "그림책"\nkeywords = ["그림책"]\n'
        '[candidates]\nexclude_tags = ["shorts"]\n'
        "[trending]\nmax_candidates = 2\n",
        encoding="utf-8",
    )
    return {"DATA_YOUTUBE_DB": str(youtube), "DATA_TREND_CONFIG": str(config)}


def _find(env: dict[str, str], naver: Naver):  # noqa: ANN202
    return asyncio.run(
        find_candidates(TrendCandidatesRequest(end_date=END), naver, env=env, now=NOW)
    )


def test_trending_keywords_join_youtube_candidates_or_stand_alone(tmp_path: Path) -> None:
    naver = Naver({"날씨": 3.0})
    out = _find(_env(tmp_path, ("T1", "날씨", "shorts", "환율", "주가")), naver).output
    t1, zen = out.candidates
    # 이름이 겹치는 YouTube 후보에 근거를 붙인다 (네이버는 한 번만)
    assert t1.keyword == "t1" and t1.origins == ["youtube_tag", "trending_search"]
    assert t1.trending is not None and (t1.trending.rank, t1.trending.traffic) == (1, "100+")
    assert t1.trending.keyword == "T1" and t1.trending.snapshot_at == SNAP_AT
    assert zen.origins == ["youtube_tag"] and zen.trending is None
    # 겹치지 않는 것은 따로 (제외 목록 shorts는 빠지고, 상한 2개)
    assert [(c.keyword, c.trending.rank, c.origins) for c in out.trending_candidates] == [
        ("날씨", 2, ["trending_search"]),
        ("환율", 4, ["trending_search"]),
    ]
    assert out.trending_candidates[0].naver.status == "rising"  # trend_scan과 같은 판정 함수
    assert out.trending_status == "known" and out.trending_snapshot_at == SNAP_AT
    assert [q.keyword_groups[0].group_name for q in naver.queries] == ["t1", "젠지", "날씨", "환율"]
    assert out.naver_calls == 4
    assert out.summary.endswith(
        "인기 검색어 후보 2개(YouTube 후보와 겹친 1개 제외), 그중 네이버 급상승 1개 (날씨)."
    )


def test_without_trending_snapshot_youtube_candidates_are_as_before(tmp_path: Path) -> None:
    naver = Naver({})
    out = _find(_env(tmp_path, None), naver).output
    assert out.status == "known" and [c.keyword for c in out.candidates] == ["t1", "젠지"]
    assert out.trending_status == "unknown" and out.trending_candidates == []
    assert out.trending_reason and "인기 검색어 스냅샷이 없음" in out.trending_reason
    assert out.summary == "YouTube 인기 영상 2개의 태그에서 후보 2개, 네이버 급상승 0개."


def test_old_trending_snapshot_is_unknown_with_reason(tmp_path: Path) -> None:
    env = _env(tmp_path, None)
    _trending_db(tmp_path / "trending_snapshots.db", ("날씨",), at=NOW - timedelta(hours=30))
    out = asyncio.run(find_candidates(TrendCandidatesRequest(), Naver({}), env=env, now=NOW)).output
    assert out.trending_status == "unknown" and "오래됨" in (out.trending_reason or "")
    assert out.trending_candidates == []


def test_trending_still_works_when_youtube_is_unknown(tmp_path: Path) -> None:
    naver = Naver({"날씨": 3.0})
    out = _find(_env(tmp_path, ("날씨",), videos=False), naver).output
    assert out.status == "unknown" and out.candidates == []  # YouTube 쪽은 예전처럼 이유와 함께
    assert [c.keyword for c in out.trending_candidates] == ["날씨"]
    assert out.naver_calls == 1 and (out.start_date, out.end_date) == (START, END)


def test_a_failed_naver_call_makes_only_that_trending_candidate_unknown(tmp_path: Path) -> None:
    out = _find(_env(tmp_path, ("날씨", "환율")), Naver({}, fail=frozenset({"날씨"}))).output
    weather, money = out.trending_candidates
    assert weather.naver.status == "unknown" and "읽지 못함" in (weather.naver.unknown_reason or "")
    assert money.naver.status == "normal"


# ---------------------------------------------------------------- content_recommend


def test_rising_trending_keywords_become_recommendations_with_their_origin(tmp_path: Path) -> None:
    llm = MockLLMClient(responses={_Idea: _Idea(idea="날씨 영상을 만들어 보세요.", format="숏폼")})  # type: ignore[arg-type]
    env = _env(tmp_path, ("날씨", "T1"))
    out = asyncio.run(
        recommend(
            ContentRecommendRequest(end_date=END),
            Naver({"날씨": 3.0, "t1": 3.0}),
            llm,
            env=env,
            now=NOW,
        )
    ).output
    by_name = {i.keyword: i for i in out.items}
    assert by_name["날씨"].origins == ["trending_search"]
    assert by_name["날씨"].sources == ["naver"] and not by_name["날씨"].score.two_sources
    assert by_name["t1"].origins == ["youtube_tag", "trending_search"]
    assert by_name["t1"].score.two_sources  # 점수 규칙은 그대로 (검색 급상승 + YouTube)
