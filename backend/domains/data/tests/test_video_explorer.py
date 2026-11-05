"""video_explorer: 영상 탐색. 실제 YouTube·LLM을 부르지 않는다 (저장된 스냅샷 + 가짜 커넥터·LLM).

- 풀(올린 지 7일 이내·한국 영상)에서 먼저 채우고 모자란 칸만 검색한다 (7일 먼저, 남으면 1일)
- 하루 검색 상한을 넘으면 그 칸만 "검색 상한"으로 모자란 채로, 같은 날 다시 검색하지 않는다
- 숏 = 60초 이하. 숏 검색 결과의 60초 초과·롱 검색 결과의 60초 이하는 뺀다
- 다른 저장분(숏츠 표본) 영상은 videos.list로 다시 읽어 올린 시각·좋아요·채널을 채운다
- 없는 값은 None + 이유, 정렬은 코드
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import VideoExplorerRequest
from data_agent.connectors.youtube import VideoStat
from data_agent.skills import video_explorer as ve
from data_agent.skills.shorts_stats import ShortsStore
from data_agent.skills.topic_category import CategoryCache, _Categories, _Item
from data_agent.youtube_store import Row, SnapshotStore

NOW = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)


def _v(vid: str, title: str, seconds: int, *, days: float = 1, views: int = 1000,
       likes: int | None = 10, channel: str = "c1") -> VideoStat:  # fmt: skip
    return VideoStat(vid, channel, "20", NOW - timedelta(days=days), views, likes, 1, (),
                     title=title, channel_title="한글 채널", duration_seconds=seconds)  # fmt: skip


class FakeLLM(MockLLMClient):
    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        assert schema is _Categories
        data = dict(untrusted or {})
        items = []
        for key, title in data.items():
            if not key.startswith("영상"):
                continue
            n = key[2:].split(" ")[0]
            cat = "게임" if "게임" in title else "유머" if "웃긴" in title else "생활·취미"
            items.append(_Item(id=n, category=cat, real_entity=False))
        return _Categories(items=items)


class FakeYouTube:
    def __init__(self) -> None:
        self.quota_used = 0
        self.searches: list[dict] = []
        self.refreshed: list[str] = []

    async def search_recent(self, published_after, *, query=None, category_id=None, short=False,  # noqa: ANN001, ANN201
                            region="KR", language=None, max_results=50):  # fmt: skip
        self.quota_used += 101
        self.searches.append({"after": published_after, "query": query, "category": category_id,
                              "short": short, "language": language})  # fmt: skip
        n = len(self.searches)
        word = "게임" if query else "웃긴"
        if short:
            return [_v(f"q{n}a", f"{word} 숏 {n}", 30, views=5000 + n),
                    _v(f"q{n}b", f"{word} 긴 숏 {n}", 200)]  # 숏 검색의 60초 초과는 뺀다
        return [_v(f"q{n}c", f"{word} 롱 {n}", 900, views=7000 + n),
                _v(f"q{n}d", f"{word} 짧은 {n}", 40)]  # 롱 검색의 60초 이하는 뺀다

    async def videos(self, ids):  # noqa: ANN001, ANN201
        self.quota_used += 1
        self.refreshed += list(ids)
        return [_v(i, "웃긴 숏츠 표본", 40, days=2, likes=None, channel="c2") for i in ids]

    async def channels(self, ids):  # noqa: ANN001, ANN201
        self.quota_used += 1
        return {"c1": 100, "c2": None}


def _env(tmp_path: Path, extra: str = "") -> dict[str, str]:
    db = tmp_path / "yt.db"
    store = SnapshotStore(db)
    store.save(NOW - timedelta(hours=1), [
        Row(_v("g1", "게임 하이라이트", 30, days=0.5, views=900), ("popular",)),
        Row(_v("g2", "게임 숏 공략", 45, days=2, views=3000, likes=None), ("popular",)),
        Row(_v("g3", "게임 웃음", 50, days=5, views=2000), ("popular",)),
        Row(_v("g4", "게임 풀영상", 1200, days=2, views=8000), ("popular",)),
        Row(_v("old", "게임 옛날", 30, days=9), ("popular",)),  # 7일 지남
        Row(_v("en", "english game", 30, days=1), ("popular",)),  # 한국 영상 아님
    ])  # fmt: skip
    store.close()
    shorts = ShortsStore(tmp_path / "shorts.db")
    shorts.save_search(NOW.date(), "23", "known", None,
                       [_v("s1", "웃긴 숏츠 표본", 40, days=2)], language="ko")  # fmt: skip
    shorts.close()
    config = tmp_path / "trends.toml"
    config.write_text(
        '[explorer]\ncategories = ["게임", "유머"]\nmin_per_cell = 3\ndaily_searches = 3\n'
        'max_videos = 5\n\n[popular]\nfill_youtube_categories = { "유머" = "23" }\n' + extra,
        encoding="utf-8",
    )
    return {"DATA_YOUTUBE_DB": str(db), "DATA_TREND_CONFIG": str(config),
            "DATA_SHORTS_DB": str(tmp_path / "shorts.db"),
            "DATA_POPULAR_VIDEOS_DB": str(tmp_path / "fill.db")}  # fmt: skip


async def _terms() -> dict[str, str]:
    return {"게임": "롤|발로란트"}


def _compute(tmp_path: Path, yt: FakeYouTube, store: ve.ExplorerStore, now: datetime = NOW):  # noqa: ANN202
    cache = CategoryCache(tmp_path / "topics.db", now=lambda: now)
    return asyncio.run(ve.compute(_env(tmp_path), youtube=yt, llm=FakeLLM(), store=store,
                                  cache=cache, terms=_terms, now=now))  # fmt: skip


def _view(tmp_path: Path, store: ve.ExplorerStore, **req):  # noqa: ANN003, ANN202
    payload = store.result()
    return ve.view(payload, VideoExplorerRequest(**req), env=_env(tmp_path), store=store,
                   now=NOW)[0]  # fmt: skip


def test_pool_first_then_search_short_cells_within_the_daily_cap(tmp_path: Path) -> None:
    yt = FakeYouTube()
    store = ve.ExplorerStore(tmp_path / "explorer.db")
    _compute(tmp_path, yt, store)
    # 게임 숏은 풀에 3개(g1·g2·g3)라 검색 안 함. 게임 롱(1)·유머 숏(1)·유머 롱(0)만 7일 검색 = 3번
    assert [(s["query"], s["category"], s["short"]) for s in yt.searches] == [
        ("롤|발로란트", None, False), (None, "23", True), (None, "23", False)]  # fmt: skip
    assert all(s["language"] == "ko" for s in yt.searches)
    assert all(NOW - s["after"] == timedelta(days=7) for s in yt.searches)
    searched = store.searched(ve._day(NOW))
    one_day = [s for s in searched.values() if s.window == "1d"]
    assert one_day and all(s.status == "skipped" and ve.CAP_REASON in (s.reason or "")
                           for s in one_day)  # 상한을 다 써서 1일 보충은 못 함  # fmt: skip
    assert store.searches_on(ve._day(NOW)) == 3
    # 같은 날 다시 계산해도 검색하지 않는다
    _compute(tmp_path, yt, store)
    assert len(yt.searches) == 3


def test_cells_split_by_period_and_length_and_unknown_values_have_reasons(tmp_path: Path) -> None:
    yt = FakeYouTube()
    store = ve.ExplorerStore(tmp_path / "explorer.db")
    _compute(tmp_path, yt, store)
    out = _view(tmp_path, store, category="게임", period="7d", length="short")
    [cell] = out.cells
    ids = [v.video_id for v in cell.videos]
    assert set(ids) == {"g1", "g2", "g3"} and "old" not in ids and "en" not in ids
    assert ids == ["g2", "g3", "g1"]  # 조회수 순
    g2 = next(v for v in cell.videos if v.video_id == "g2")
    assert g2.likes is None and g2.likes_reason == "좋아요 숨김"
    assert g2.video_url == "https://www.youtube.com/shorts/g2" and g2.duration_seconds == 45
    assert g2.channel_subscribers == 100 and g2.views_per_subscriber == 30.0
    assert g2.category == "게임" and g2.ad_usable is True
    day = _view(tmp_path, store, category="게임", period="1d", length="short").cells[0]
    assert [v.video_id for v in day.videos] == ["g1"]
    assert day.count == 1 and "이 기간 한국 영상 1개" in (day.shortage_reason or "")
    assert ve.CAP_REASON in (day.shortage_reason or "")
    long = _view(tmp_path, store, category="게임", period="7d", length="long").cells[0]
    assert [v.video_id for v in long.videos] == ["g4", "q1c"]  # 롱 검색의 40초 영상은 뺌
    likes = _view(tmp_path, store, category="게임", period="7d", length="short", sort="likes")
    assert likes.cells[0].videos[-1].video_id == "g2"  # 좋아요를 모르면 뒤


def test_other_stores_are_refreshed_once_and_hidden_subscribers_say_so(tmp_path: Path) -> None:
    yt = FakeYouTube()
    store = ve.ExplorerStore(tmp_path / "explorer.db")
    _compute(tmp_path, yt, store)
    assert yt.refreshed == ["s1"]  # 숏츠 표본 영상: 올린 시각·좋아요·채널을 다시 읽음
    out = _view(tmp_path, store, category="유머", period="7d", length="short")
    s1 = next(v for v in out.cells[0].videos if v.video_id == "s1")
    assert s1.source == "shorts" and s1.channel_id == "c2"
    assert s1.subscriber_reason == "채널이 구독자 수를 숨김" and s1.views_per_subscriber is None
    _compute(tmp_path, yt, store, NOW + timedelta(hours=1))
    assert yt.refreshed == ["s1"]  # 다시 읽은 지 12시간 안이면 다시 읽지 않음
    assert out.quota_used_today == store.units(ve._day(NOW)) > 0


def test_short_cells_are_filled_with_older_shorts_samples_marked(tmp_path: Path) -> None:
    """7일 안 영상이 최소 수보다 적은 숏 칸은 숏츠 표본(기간 무관)의 최근 것부터 채우고 표시한다."""
    from data_agent.skills.topic_category import CategoryResult

    env = _env(tmp_path)
    config = Path(env["DATA_TREND_CONFIG"])
    config.write_text(config.read_text(encoding="utf-8").replace(
        "max_videos = 5\n", 'max_videos = 5\nbackfill_categories = ["유머"]\n'),
        encoding="utf-8")  # fmt: skip
    store = ve.ExplorerStore(tmp_path / "explorer.db")
    cache = CategoryCache(tmp_path / "topics.db", now=lambda: NOW)
    old = [_v("o1", "웃긴 옛날 숏츠 하나", 30, days=20), _v("o2", "웃긴 옛날 숏츠 둘", 40, days=12),
           _v("o3", "옛날 긴 영상", 600, days=12)]  # fmt: skip
    store.save_videos(NOW, old, "shorts")
    for v in old:  # 숏츠 통계가 제목으로 분류해 둔 것 (새로 묻지 않음)
        cache.put(v.title, CategoryResult("유머", None, 0, real_entity=False, is_person=False))
    asyncio.run(ve.compute(env, youtube=None, llm=FakeLLM(), store=store, cache=cache,
                           terms=_terms, now=NOW))  # fmt: skip
    payload = store.result()
    day = ve.view(payload, VideoExplorerRequest(category="유머", period="1d", length="short"),
                  env=env, store=store, now=NOW)[0].cells[0]  # fmt: skip
    assert day.count == 0  # 1일 안에는 없음
    assert [v.video_id for v in day.videos] == ["o2", "o1"]  # 최근 올린 것부터
    assert all(v.before_period for v in day.videos)
    assert "7일 이전 숏츠 표본 2개로 채움" in (day.shortage_reason or "")
    long = ve.view(payload, VideoExplorerRequest(category="유머", period="7d", length="long"),
                   env=env, store=store, now=NOW)[0].cells[0]  # fmt: skip
    assert not any(v.before_period for v in long.videos)  # 롱 칸은 숏츠 표본으로 채우지 않음
    game = ve.view(payload, VideoExplorerRequest(category="게임", period="7d", length="short"),
                   env=env, store=store, now=NOW)[0].cells[0]  # fmt: skip
    assert not any(v.before_period for v in game.videos)  # 채울 카테고리가 아님


def test_screen_request_reads_stored_result_only(tmp_path: Path) -> None:
    store = ve.ExplorerStore(tmp_path / "explorer.db")
    empty = _view(tmp_path, store)
    assert empty.status == "unknown" and "미리 계산" in (empty.reason or "")
    _compute(tmp_path, FakeYouTube(), store)
    out = _view(tmp_path, store, period="3d", length="short")
    assert [c.category for c in out.cells] == ["게임", "유머"] and out.status == "known"
    assert out.computed_at == NOW and out.min_per_cell == 3 and out.search_cap == 3


class FailLLM(MockLLMClient):
    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        from data_agent import llm_failure

        error = RuntimeError("402 credit")
        llm_failure.mark(error)  # data LLM 래퍼가 하는 일 (handlers.TimedLLMClient)
        raise error


def test_a_compute_with_a_failed_llm_is_not_stored_and_is_retried(tmp_path: Path) -> None:
    from data_agent import llm_failure

    store = ve.ExplorerStore(tmp_path / "explorer.db")
    cache = CategoryCache(tmp_path / "topics.db", now=lambda: NOW)
    env = _env(tmp_path)
    with llm_failure.scope():
        failed = asyncio.run(ve.compute(env, youtube=None, llm=FailLLM(), store=store,
                                        cache=cache, terms=_terms, now=NOW))  # fmt: skip
    assert store.result() is None and any("저장하지 않음" in x for x in failed["limits"])
    assert failed["videos"] == []  # 분류를 못 해 칸에 넣지 않음 (unknown은 분류 저장분에도 없음)
    with llm_failure.scope():
        ok = asyncio.run(ve.compute(env, youtube=None, llm=FakeLLM(), store=store, cache=cache,
                                    terms=_terms, now=NOW))  # fmt: skip
    assert store.result() is not None and ok["videos"]


def test_pending_classification_is_split_into_daily_shares(tmp_path: Path) -> None:
    """새로 묻는 영상은 하루 몫까지. 나머지는 분류 대기로 남기고 다음 날 이어서."""
    store = ve.ExplorerStore(tmp_path / "explorer.db")
    env = _env(tmp_path, "")
    config = Path(env["DATA_TREND_CONFIG"])
    config.write_text(config.read_text(encoding="utf-8").replace(
        "max_videos = 5\n", "max_videos = 5\nclassify_daily = 2\n"), encoding="utf-8")  # fmt: skip
    cache = CategoryCache(tmp_path / "topics.db", now=lambda: NOW)

    def run(now: datetime) -> dict:
        return asyncio.run(ve.compute(env, youtube=None, llm=FakeLLM(), store=store, cache=cache,
                                      terms=_terms, now=now))  # fmt: skip

    first = run(NOW)
    assert store.calls(ve._day(NOW), "classify") == 2
    assert any("분류 대기 3개" in x for x in first["limits"])  # 풀 5개 중 2개만
    again = run(NOW + timedelta(hours=1))
    assert store.calls(ve._day(NOW), "classify") == 2  # 같은 날은 몫을 다 써서 더 묻지 않음
    assert any("분류 대기 3개" in x for x in again["limits"])
    nxt = run(NOW + timedelta(days=1))
    assert store.calls(ve._day(NOW + timedelta(days=1)), "classify") == 2
    assert any("분류 대기 1개" in x for x in nxt["limits"])


def test_two_computes_at_once_search_each_cell_once_within_the_cap(tmp_path: Path) -> None:
    """서버 첫 요청과 수집기 미리 계산이 겹쳐도 칸마다 한 번, 상한은 둘이 함께 센다."""
    day = ve._day(NOW)
    a = ve.ExplorerStore(tmp_path / "explorer.db")
    b = ve.ExplorerStore(tmp_path / "explorer.db")  # 다른 프로세스처럼 따로 연 연결
    assert a.reserve(day, "게임", "short", "7d", 2) == "ok"
    assert b.reserve(day, "게임", "short", "7d", 2) == "done"  # 검색 중인 칸은 건너뜀
    assert b.searched(day)[("게임", "short", "7d")].status == "unknown"
    assert b.reserve(day, "게임", "long", "7d", 2) == "ok"
    assert a.reserve(day, "유머", "short", "7d", 2) == "cap"
    yt = FakeYouTube()
    store = ve.ExplorerStore(tmp_path / "other.db")
    first = _compute(tmp_path, yt, store)
    del first
    again = ve.ExplorerStore(tmp_path / "other.db")
    _compute(tmp_path, yt, again)
    assert len(yt.searches) == 3 and again.searches_on(day) == 3
