"""트렌드 감지 "밈 소스" (11/24): 인스타 밈·크리에이터 계정 묶음 + 유튜브 쇼츠 키워드 검색.

11/24 레이더 161곳은 기업 공식 계정이라 뜬 게시물 12개 중 11개가 "광고 재료 주의" 브랜드 홍보,
밈은 1개뿐이었다. 유튜브 "요즘 유행"은 이슈 모음 채널의 #수능·#베스티였다.
- 설정 읽기(빈 목록 = 지금과 같음), source_kind 붙이기, 팔로워 대비 반응 계산
- 쇼츠 검색: 쿼터 30%를 넘으면 건너뜀, 구독자 대비 배수·10만 이상 표시, 실존 인물·사건 분류 적용
- 보고서 재료 우선순위, 데이터 대화 종류 나누기
외부 호출은 전부 가짜다.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import tenant
from ax_contracts.schemas.data import (
    InstagramRadarPost,
    TrendMaterial,
    TrendReportBrief,
    TrendReportEvidence,
)
from data_agent import data_chat as dc
from data_agent import instagram_store as st
from data_agent import meme_shorts as ms
from data_agent import youtube_quota
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors import instagram_graph as ig
from data_agent.connectors.youtube import VideoStat
from data_agent.skills import instagram as igs
from data_agent.skills import trend_report as tr

NOW = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)  # 한국 12:00


@pytest.fixture(autouse=True)
def no_saved_lists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """화면 저장 파일은 tmp (없으면 회사 설정 파일). 자동 목록은 없음."""
    path = tmp_path / "instagram_radar.json"
    monkeypatch.setenv(tenant.IG_RADAR_FILE_ENV, str(path))
    monkeypatch.setenv(tenant.YT_MEME_KEYWORDS_FILE_ENV, str(tmp_path / "yt_meme.json"))
    monkeypatch.setattr(igs, "ig_radar_auto_accounts", lambda: None)
    return path


def _trends(tmp_path: Path, body: str) -> dict[str, str]:
    path = tmp_path / "trends.toml"
    path.write_text(body, encoding="utf-8")
    return {"DATA_TREND_CONFIG": str(path)}


# ---------------------------------------------------------------- 1. 설정 읽기


def test_empty_meme_list_is_the_same_as_now(tmp_path: Path) -> None:
    env = _trends(tmp_path, '[instagram]\nradar_accounts = ["brand_a", "brand_b"]\n')
    config = igs.load_config(env)
    assert config.radar_accounts == ("brand_a", "brand_b") and config.meme_accounts == ()
    assert config.kind_of("brand_a") == "benchmark"


def test_meme_accounts_from_the_trends_file_join_the_radar(tmp_path: Path) -> None:
    env = _trends(tmp_path, '[instagram]\nradar_accounts = ["brand_a"]\n'
                  'radar_meme_accounts = ["@meme_one", "brand_a", "meme_two"]\n')  # fmt: skip
    config = igs.load_config(env)
    assert config.meme_accounts == ("meme_one", "meme_two")  # 벤치마크와 겹치면 벤치마크
    assert config.radar_accounts == ("brand_a", "meme_one", "meme_two")
    assert config.kind_of("meme_two") == "meme"


def test_saved_meme_accounts_win_and_the_total_stays_200(tmp_path: Path,
                                                        no_saved_lists: Path) -> None:  # fmt: skip
    env = _trends(tmp_path, '[instagram]\nradar_meme_accounts = ["from_file"]\n')
    bench = [f"brand{i}" for i in range(195)]
    memes = [f"meme{i}" for i in range(10)]
    no_saved_lists.write_text(json.dumps({"accounts": bench, "meme_accounts": memes}), "utf-8")
    config = igs.load_config(env)
    assert len(config.radar_accounts) == igs.RADAR_MAX_ACCOUNTS
    assert config.meme_accounts == tuple(memes[:5])  # 합계 200곳까지만


def test_only_meme_accounts_still_run_the_radar(tmp_path: Path) -> None:
    env = _trends(tmp_path, '[instagram]\nradar_meme_accounts = ["meme_one"]\n')
    config = igs.load_config(env)
    assert config.radar_accounts == ("meme_one",) and config.radar_missing == ""


# ---------------------------------------------------------------- source_kind · 팔로워 대비


def _media(account: str, hot: int, followers: int | None = 10_000) -> list[dict[str, Any]]:
    out = []
    for i in range(10):
        at = (NOW - timedelta(days=1 if i == 0 else 10 + i)).isoformat()
        m = {"id": f"{account}-{i}", "media_product_type": "FEED", "media_type": "IMAGE",
             "timestamp": at, "like_count": hot if i == 0 else 100, "comments_count": 20,
             "caption": "#같이"}  # fmt: skip
        if followers is not None:
            m[ig.OWNER_FOLLOWERS] = followers
        out.append(m)
    return out


def test_bursts_carry_the_source_kind_and_follower_engagement(tmp_path: Path) -> None:
    env = _trends(tmp_path, '[instagram]\nradar_accounts = ["brand_a"]\n'
                  'radar_meme_accounts = ["meme_one"]\n')  # fmt: skip
    config = igs.load_config(env)
    media = {"brand_a": _media("brand_a", 400), "meme_one": _media("meme_one", 980),
             "nobody": _media("nobody", 900, followers=None)}  # fmt: skip
    posts = {p.account: p for p in igs.bursts(media, NOW, config)}
    assert posts["brand_a"].source_kind == "benchmark"
    meme = posts["meme_one"]
    assert meme.source_kind == "meme" and meme.followers == 10_000 and meme.comments == 20
    assert meme.follower_engagement == 0.1  # (980 + 20) ÷ 10,000
    assert posts["nobody"].follower_engagement is None  # 팔로워를 모르면 0이 아니라 None
    assert [p.account for p in igs.meme_posts(list(posts.values()))] == ["meme_one"]


def test_follower_engagement_needs_followers_and_likes() -> None:
    assert igs.follower_engagement({"like_count": 50, ig.OWNER_FOLLOWERS: 1000}) == 0.05
    assert igs.follower_engagement({"like_count": 50, ig.OWNER_FOLLOWERS: 0}) is None
    assert igs.follower_engagement({ig.OWNER_FOLLOWERS: 1000}) is None


def test_followers_are_kept_in_the_radar_store(tmp_path: Path) -> None:
    path = tmp_path / "ig_radar.db"
    store = st.RadarStore(path)
    store.save(date(2026, 11, 24), "meme_one", _media("meme_one", 980))
    store.save(date(2026, 11, 24), "old_style", _media("old_style", 100, followers=None))
    store.complete(date(2026, 11, 24), NOW, 2)
    store.close()
    _day, media, _failed = st.radar_latest(path)
    assert media["meme_one"][0][ig.OWNER_FOLLOWERS] == 10_000
    assert ig.OWNER_FOLLOWERS not in media["old_style"][0]


def test_business_media_attaches_followers() -> None:
    import httpx

    from data_agent.connectors.naver_searchad import JsonDailyCache

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"business_discovery": {
            "followers_count": 5200, "media": {"data": [{"id": "1"}]}}})  # fmt: skip

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    graph = ig.InstagramGraph({ig.TOKEN_ENV: "t", ig.USER_ENV: "1"}, client=client,
                              calls=JsonDailyCache())  # fmt: skip
    got = asyncio.run(graph.business_media("meme_one", 20))
    assert got[0][ig.OWNER_FOLLOWERS] == 5200


# ---------------------------------------------------------------- 2. 쇼츠 검색


def _stat(vid: str, channel: str, views: int | None) -> VideoStat:
    return VideoStat(video_id=vid, channel_id=channel, category_id=None,
                     published_at=NOW - timedelta(days=1), view_count=views, like_count=None,
                     comment_count=None, tags=(), title=f"제목 {vid}",
                     channel_title=f"채널 {channel}")  # fmt: skip


@dataclass
class FakeYouTube:
    results: dict[str, list[VideoStat]]
    subs: dict[str, int | None]
    ledger: youtube_quota.Ledger
    quota_used: int = 0
    calls: list[tuple[str, Any]] | None = None

    async def search_query_shorts(self, query: str, published_after: datetime,
                                  region: str = "KR", language: str | None = None,
                                  max_results: int = 50) -> list[VideoStat]:  # fmt: skip
        if self.calls is not None:
            self.calls.append(("search", (query, region, max_results)))
        assert published_after == NOW - timedelta(days=7)
        self.quota_used += 101
        self.ledger.add(ms.CALLER, 101, search=True)
        return self.results.get(query, [])

    async def channels(self, ids: list[str]) -> dict[str, int | None]:
        self.quota_used += 1
        self.ledger.add(ms.CALLER, 1)
        return {i: self.subs.get(i) for i in ids}


def _shorts_env(tmp_path: Path, words: str = '["밈", "공감"]') -> dict[str, str]:
    env = _trends(tmp_path, f"[youtube]\nmeme_keywords = {words}\n")
    env[ms.DB_ENV] = str(tmp_path / "meme_shorts.db")
    env[youtube_quota.CAP_ENV] = "8000"
    return env


def test_keywords_come_from_the_screen_then_the_trends_file(tmp_path: Path) -> None:
    env = _shorts_env(tmp_path, '["밈", " 밈 ", "공감", "짤"]')
    assert ms.load_keywords(env) == ["밈", "공감", "짤"]
    path = tenant.yt_meme_keywords_file()
    assert path is not None
    path.write_text(json.dumps({"keywords": ["책추천"]}), "utf-8")
    assert ms.load_keywords(env) == ["책추천"]  # 화면 저장이 먼저


def test_no_keywords_means_no_search(tmp_path: Path) -> None:
    env = _shorts_env(tmp_path, "[]")
    yt = FakeYouTube({}, {}, youtube_quota.Ledger(None), calls=[])
    got = asyncio.run(ms.collect(env, yt, now=NOW))
    assert got.keywords == 0 and yt.calls == []
    out = asyncio.run(ms.shorts_search(env, NOW, llm=None))
    assert out.status == "not_set" and out.reason and "밈 키워드가 없어" in out.reason


def test_quota_over_30_percent_skips_and_keeps_the_reason(tmp_path: Path) -> None:
    assert ms.quota_reason(2297, 8000) is None  # 2,297 + 102 = 2,399 ≤ 2,400
    assert ms.quota_reason(2299, 8000) is not None
    env = _shorts_env(tmp_path)
    ledger = youtube_quota.Ledger(None)
    ledger.add("youtube_collect", 2200)
    yt = FakeYouTube({"밈": [_stat("v1", "c1", 5000)]}, {"c1": 1000}, ledger, calls=[])
    got = asyncio.run(ms.collect(env, yt, now=NOW, ledger=ledger))
    assert got.searched == ["밈"] and got.skipped == ["공감"]  # 2,302 + 102 > 2,400
    assert len(yt.calls or []) == 1
    out = asyncio.run(ms.shorts_search(env, NOW, llm=None))
    assert out.searched == ["밈"] and "30%" in out.limits[0] and out.units == 102
    # 다음 회차(같은 날)는 남은 키워드만, 쿼터가 되면 마저 찾는다
    ledger.clear()
    again = asyncio.run(ms.collect(env, yt, now=NOW, ledger=ledger))
    assert again.searched == ["공감"] and not again.skipped
    assert asyncio.run(ms.collect(env, yt, now=NOW, ledger=ledger)).searched == []


def test_search_params_and_views_per_subscriber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from data_agent.skills import topic_category

    async def none(*a: Any, **k: Any) -> dict:  # 분류는 다 모름 (분류 대기 → 보여 준다)
        return {}

    monkeypatch.setattr(topic_category, "classify_topics", none)
    env = _shorts_env(tmp_path, '["밈"]')
    ledger = youtube_quota.Ledger(None)
    yt = FakeYouTube({"밈": [_stat("small", "c1", 30_000), _stat("big", "c2", 500_000),
                             _stat("hidden", "c3", 9_000)]},
                     {"c1": 1_000, "c2": 2_000_000, "c3": None}, ledger, calls=[])  # fmt: skip
    asyncio.run(ms.collect(env, yt, now=NOW, ledger=ledger))
    assert yt.calls == [("search", ("밈", "KR", ms.PER_KEYWORD))]
    out = asyncio.run(ms.shorts_search(env, NOW, llm=None))
    assert out.status == "known" and out.day == date(2026, 11, 24)
    assert [v.video_id for v in out.items] == ["small", "big", "hidden"]  # 구독자 대비 배수 순
    small, big, hidden = out.items
    assert small.views_per_subscriber == 30.0 and not small.big_channel
    assert big.views_per_subscriber == 0.25 and big.big_channel  # 10만 이상은 따로 표시
    assert hidden.views_per_subscriber is None and hidden.subscribers is None
    assert small.video_url == "https://www.youtube.com/shorts/small" and small.keyword == "밈"


def test_real_person_and_event_videos_are_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from data_agent.skills import topic_category
    from data_agent.skills.topic_category import CategoryResult

    async def classify(inputs: Any, llm: Any, **k: Any) -> dict:
        out = {}
        for t in inputs:
            if "person" in t.keyword:
                out[t.keyword] = CategoryResult("연예·스포츠", None, 1, is_person=True)
            elif "news" in t.keyword:
                out[t.keyword] = CategoryResult("뉴스·사건", None, 1, is_person=False)
            else:
                out[t.keyword] = CategoryResult("유머", None, 1, is_person=False)
        return out

    monkeypatch.setattr(topic_category, "classify_topics", classify)
    env = _shorts_env(tmp_path, '["밈"]')
    ledger = youtube_quota.Ledger(None)
    found = [_stat("person", "c1", 9000), _stat("news", "c2", 8000), _stat("ok", "c3", 7000)]
    yt = FakeYouTube({"밈": found}, {"c1": 10, "c2": 10, "c3": 10}, ledger)
    asyncio.run(ms.collect(env, yt, now=NOW, ledger=ledger))
    out = asyncio.run(ms.shorts_search(env, NOW, llm=None))
    assert [v.video_id for v in out.items] == ["ok"]
    assert {r.kind for r in out.removed} == {"person", "category"}


def test_search_failure_stops_and_records_the_code(tmp_path: Path) -> None:
    class Broken(FakeYouTube):
        async def search_query_shorts(self, *a: Any, **k: Any) -> list[VideoStat]:
            raise ConnectorUnavailable("QUOTA_EXCEEDED", "한도")

    env = _shorts_env(tmp_path)
    ledger = youtube_quota.Ledger(None)
    got = asyncio.run(ms.collect(env, Broken({}, {}, ledger), now=NOW, ledger=ledger))
    assert got.searched == [] and got.skipped == ["밈", "공감"]
    out = asyncio.run(ms.shorts_search(env, NOW, llm=None))
    assert "QUOTA_EXCEEDED" in out.limits[0]


# ---------------------------------------------------------------- 3. 보고서 재료 우선순위


def _post(account: str, ratio: float, kind: str = "benchmark") -> InstagramRadarPost:
    return InstagramRadarPost(account=account, media_id=f"m_{account}", metric="like_count",
                              value=500, account_median=100.0, ratio=ratio,
                              source_kind=kind)  # type: ignore[arg-type]  # fmt: skip


def _brief(**kw: Any) -> TrendReportBrief:
    sources = {k: "input" for k in ("product", "target", "format", "competitors", "keywords")}
    return TrendReportBrief(sources=sources, **kw)  # type: ignore[arg-type]


def test_meme_words_in_the_brief() -> None:
    assert tr.wants_memes(_brief(format="밈 쇼츠"))
    assert tr.wants_memes(_brief(product="책", keywords=["요즘 유행"]))
    assert not tr.wants_memes(_brief(product="책", format="30초 쇼츠"))


def test_content_puts_meme_source_posts_first() -> None:
    own = [_post("rival", 9.0)]
    memes = [_post("meme_one", 3.0, "meme")]
    section, limits = tr.content_section(own, True, None, None, 1, meme_first=memes)
    assert [p.account for p in section.posts] == ["meme_one", "rival"]
    assert any("밈·크리에이터 계정 게시물 1개를 먼저" in line for line in limits)
    plain, _ = tr.content_section(own, True, None, None, 1)
    assert [p.account for p in plain.posts] == ["rival"]  # 밈 말이 없으면 지금과 같다


def test_meme_source_posts_are_read_from_the_stored_radar() -> None:
    stored = ["known", {"meme_posts": [_post("meme_one", 3.0, "meme").model_dump(mode="json"),
                                       _post("brand", 9.0).model_dump(mode="json"),
                                       {"broken": True}]}, {}]  # fmt: skip
    assert [p.account for p in tr.meme_source_posts(stored)] == ["meme_one"]
    assert tr.meme_source_posts(None) == []


def test_materials_with_meme_evidence_come_first() -> None:
    def mat(kind: str, ids: list[str]) -> TrendMaterial:
        return TrendMaterial(kind=kind, text=f"{kind} 쓰기", evidence=ids,  # type: ignore[arg-type]
                             evidence_items=[TrendReportEvidence(id=i, text=i, short=i)
                                             for i in ids])  # fmt: skip

    items = [mat("hashtag", ["hashtag:1"]), mat("format", ["competitor:1"]),
             mat("meme", ["content:1"])]  # fmt: skip
    got = tr.meme_materials_first(items, {"content:1"})
    assert [m.kind for m in got] == ["meme", "hashtag", "format"]


# ---------------------------------------------------------------- 데이터 대화


def test_chat_material_splits_meme_and_benchmark_accounts() -> None:
    meme = {**_post("meme_one", 8.04, "meme").model_dump(mode="json"),
            "follower_engagement": 0.0423}  # fmt: skip
    brand = _post("brand", 3.0).model_dump(mode="json")
    judged = {"meme_accounts_total": 1, "meme_posts": [meme],
              "groups": [{"name": "#같이", "best_ratio": 3.0, "top_posts": [brand]}]}  # fmt: skip
    groups = dc.radar_posts(judged, NOW)
    assert groups[0]["게시물"][0]["종류"] == "벤치마크 계정"
    rows = dc.meme_rows(judged, NOW)
    assert rows[0]["종류"] == "밈·크리에이터 계정"
    assert rows[0]["팔로워 대비 반응"] == "팔로워의 약 4.2%"
    assert rows[0]["평소 대비"] == "평소의 약 8배"
    # 밈 계정이 없던 판정은 지금 모양 그대로 (종류 칸 없음)
    plain = dc.radar_posts({"groups": judged["groups"]}, NOW)
    assert "종류" not in plain[0]["게시물"][0]
