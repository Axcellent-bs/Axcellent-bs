"""trend_popular: 지금 인기 주제 순위.

네이버·YouTube·LLM을 부르지 않는다 (저장된 스냅샷 + 가짜 LLM).

- 점수 계산: 영상 몫 + 조회 속도 몫 + 인기 검색어 순위 몫 (가중치는 설정)
- 출처 일부 없음 → 그 출처(몫)만 unknown + 이유, 점수는 아는 몫만
- 스냅샷 없음 → unknown + 이유, 빈 목록 + 이유
- 주제 분류는 묶어서 한 번, 추천 안 함 분류면 brand_safe false
- 오늘 판정(today_board)이 급상승이면 "지금 급등 중"(rising_now), 자동 묶음 쌍은 한 주제
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import TrendEvidence, TrendVerdict
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.google_trends import TrendingItem
from data_agent.connectors.youtube import VideoStat
from data_agent.skills.topic_category import CategoryCache, _Categories, _Item
from data_agent.skills.trend_popular import (
    PopularConfig,
    TrendPopularRequest,
    _Topic,
    popular,
    score_topics,
)
from data_agent.today_board import TodayBoard
from data_agent.trending_store import TrendingStore
from data_agent.youtube_store import Row, SnapshotStore

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)
T1 = NOW - timedelta(hours=1)
T0 = NOW - timedelta(hours=2)


def _stat(vid: str, rank: int, tags: tuple[str, ...], views: int) -> VideoStat:
    return VideoStat(vid, "ch", "20", NOW - timedelta(days=1), views, 10, 1, tags, rank=rank)


VIDEOS = [  # (id, 순위, 태그, 1시간 전 조회수, 지금 조회수)
    ("v1", 1, ("t1", "lck"), 1000, 3000),
    ("v2", 2, ("t1",), 1000, 2000),
    ("v3", 3, ("lck", "롤"), 500, 600),
    ("v4", 4, ("롤",), 100, 200),
]


def _env(tmp_path: Path, *, snapshots: int = 2, trending: bool = True, extra: str = "") -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "trends.toml"
    config.write_text(
        "[candidates]\nmin_videos = 2\n\n[popular]\ntop = 10\n" + extra, encoding="utf-8"
    )
    db = tmp_path / "youtube.db"
    store = SnapshotStore(db)
    if snapshots >= 2:
        store.save(T0, [Row(_stat(v, r, t, a), ("popular",)) for v, r, t, a, _ in VIDEOS])
    if snapshots >= 1:
        store.save(T1, [Row(_stat(v, r, t, b), ("popular",)) for v, r, t, _, b in VIDEOS])
    store.close()
    env = {
        "DATA_YOUTUBE_DB": str(db),
        "DATA_TREND_CONFIG": str(config),
        "DATA_TRENDING_DB": str(tmp_path / "trending.db"),
    }
    if trending:
        tstore = TrendingStore(tmp_path / "trending.db")
        tstore.save(
            T1,
            "KR",
            [
                TrendingItem("한국철강", 1, "100+", None, ("철강 뉴스",)),
                TrendingItem("LCK", 2, "500+", None),
            ],
        )
        tstore.close()
    return env


def _llm(answers: dict[str, str] | None = None) -> MockLLMClient:
    class LLM(MockLLMClient):
        async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
            self.calls.append(prompt)
            data = dict(untrusted or {})
            names = {k[2:]: v for k, v in data.items() if k.startswith("소재")}
            return _Categories(
                items=[
                    _Item(id=n, category=(answers or {}).get(v, "게임")) for n, v in names.items()
                ]
            )

    return LLM()


def _run(env: dict, llm: MockLLMClient | None = None, board: TodayBoard | None = None):  # noqa: ANN202
    return asyncio.run(
        popular(TrendPopularRequest(), llm or _llm(), env=env, now=NOW, board=board or TodayBoard())
    ).output


# ---------------------------------------------------------------- 점수 계산


def test_score_is_the_weighted_sum_of_known_shares() -> None:
    a = _Topic("a", video_ids=["v1", "v2"], trending_rank=2)
    b = _Topic("b", video_ids=["v3"])
    c = _Topic("c", trending_rank=1)  # 인기 영상에 없음
    speeds = {"v1": 2000.0, "v2": 1000.0, "v3": 100.0}
    config = PopularConfig(video_weight=1.0, velocity_weight=2.0, trending_weight=0.5)
    out = {
        t.keyword: (s, speed)
        for t, s, speed, _ in score_topics(
            [a, b, c], speeds=speeds, trending_n=2, youtube_known=True, config=config
        )
    }
    sa, speed_a = out["a"]
    assert (sa.video_share, sa.velocity_share, sa.trending_share) == (1.0, 1.0, 0.5)
    assert sa.score == 1.0 + 2.0 + 0.25 and speed_a == 3000.0
    sb, _ = out["b"]
    assert (sb.video_share, sb.velocity_share, sb.trending_share) == (0.5, 0.5, 0.0)  # 순위 몫
    sc, speed_c = out["c"]
    assert (sc.video_share, sc.velocity_share, sc.trending_share) == (0.0, 0.0, 1.0)
    assert speed_c is None  # 영상이 없으면 속도는 비운다 (0이 아님)


def test_unknown_source_is_left_out_of_the_score_not_zero() -> None:
    a = _Topic("a", video_ids=["v1"], trending_rank=1)
    [(_, s, speed, _)] = score_topics(
        [a], speeds=None, trending_n=None, youtube_known=True, config=PopularConfig()
    )
    assert (s.video_share, s.velocity_share, s.trending_share) == (1.0, None, None)
    assert s.score == 1.0 and speed is None


# ---------------------------------------------------------------- 실행


def test_popular_ranks_topics_from_saved_snapshots(tmp_path: Path) -> None:
    out = _run(_env(tmp_path))
    assert out.youtube_status == out.velocity_status == out.trending_status == "known"
    by = {i.keyword: i for i in out.items}
    assert by["t1"].youtube_videos == 2 and by["t1"].views_per_hour == 3000.0
    assert by["lck"].trending_rank == 2 and "trending_search" in by["lck"].origins
    assert by["한국철강"].youtube_videos == 0 and by["한국철강"].views_per_hour is None
    assert by["한국철강"].velocity_reason == "인기 영상에 나오지 않은 주제"
    assert [i.rank for i in out.items] == list(range(1, len(out.items) + 1))
    assert out.items[0].score.score >= out.items[-1].score.score
    assert out.weights == {"video": 1.0, "velocity": 1.0, "trending": 1.0}


def test_missing_sources_are_unknown_only_for_that_source(tmp_path: Path) -> None:
    out = _run(_env(tmp_path, snapshots=1, trending=False))
    assert out.youtube_status == "known"
    assert out.velocity_status == "unknown"
    assert out.velocity_reason == "YouTube 스냅샷이 2개 미만이라 조회 속도를 잴 수 없음"
    assert out.trending_status == "unknown" and "인기 검색어 스냅샷이 없음" in (
        out.trending_reason or ""
    )
    assert out.items and all(
        i.score.velocity_share is None and i.score.trending_share is None for i in out.items
    )
    assert all(i.score.score == i.score.video_share for i in out.items)


def test_no_snapshot_means_unknown_with_a_reason(tmp_path: Path) -> None:
    env = _env(tmp_path, snapshots=0, trending=False)
    out = _run(env)
    assert out.items == [] and out.empty_reason == "인기 주제를 볼 출처가 없음"
    assert out.youtube_status == "unknown" and "YouTube 스냅샷이 없음" in (out.youtube_reason or "")
    assert out.velocity_status == "unknown"


def test_categories_are_asked_once_and_excluded_ones_are_not_brand_safe(tmp_path: Path) -> None:
    llm = _llm({"한국철강": "뉴스·사건"})
    out = _run(_env(tmp_path), llm)
    assert len(llm.calls) == 1
    by = {i.keyword: i for i in out.items}
    assert by["한국철강"].topic_category == "뉴스·사건" and by["한국철강"].brand_safe is False
    assert by["t1"].topic_category == "게임" and by["t1"].brand_safe is True
    cache = CategoryCache()
    asyncio.run(popular(TrendPopularRequest(), llm, env=_env(tmp_path / "b"), now=NOW,
                        category_cache=cache, board=TodayBoard()))  # fmt: skip
    asyncio.run(popular(TrendPopularRequest(), llm, env=_env(tmp_path / "c"), now=NOW,
                        category_cache=cache, board=TodayBoard()))  # fmt: skip
    assert len(llm.calls) == 2  # 둘째는 하루 캐시


def test_rising_badge_comes_only_from_todays_verdict_and_pairs_merge(tmp_path: Path) -> None:
    board = TodayBoard()
    board.record_verdict(
        ["t1"],
        TrendVerdict(group_name="t1", status="rising", ratio=3.0, evidence=TrendEvidence(score=5)),
    )
    board.record_pair("롤", "lck")
    out = _run(_env(tmp_path), board=board)
    by = {i.keyword: i for i in out.items}
    assert by["t1"].rising_now is True and by["t1"].rising_ratio == 3.0
    assert all(i.rising_now is None for k, i in by.items() if k != "t1")
    merged = by.get("lck") or by.get("롤")
    assert merged is not None and {"lck", "롤"} <= {merged.keyword, *merged.aliases}
    assert "급상승" not in out.summary


def test_limits_come_from_the_config(tmp_path: Path) -> None:
    out = _run(_env(tmp_path, extra="video_weight = 0\nvelocity_weight = 0\n"))
    assert out.items[0].keyword == "한국철강"  # 검색어 순위 몫만 남음
    tmp2 = tmp_path / "top"
    tmp2.mkdir()
    env = _env(tmp2)
    env_path = Path(env["DATA_TREND_CONFIG"])
    text = env_path.read_text(encoding="utf-8").replace("top = 10", "per_category = 2")
    env_path.write_text(text, encoding="utf-8")
    assert len(_run(env).items) == 2


# ---------------------------------------------------------------- 순위 몫·카테고리별


def test_rank_shares_are_outlier_proof_and_ties_share_a_rank() -> None:
    from data_agent.skills.trend_popular import rank_shares

    assert rank_shares({0: 28.0, 1: 12.0, 2: 9.0, 3: 9.0}) == {0: 1.0, 1: 0.75, 2: 0.5, 3: 0.5}
    assert rank_shares({}) == {}


def test_popular_is_grouped_by_display_category_and_hides_the_rest(tmp_path: Path) -> None:
    answers = {"t1": "게임", "lck": "게임", "롤": "IT", "한국철강": "기부·재난"}
    out = _run(_env(tmp_path), _llm(answers))
    assert out.display_categories == ["게임", "뉴스·사건", "연예·스포츠", "IT"]
    by = {g.category: g for g in out.by_category}
    assert [i.keyword for i in by["게임"].items][:2] and all(
        i.topic_category == "게임" for i in by["게임"].items
    )
    assert [i.category_rank for i in by["게임"].items] == list(range(1, len(by["게임"].items) + 1))
    assert [i.keyword for i in by["IT"].items] == ["롤"]
    assert by["IT"].shortage_reason is not None and "1개뿐" in by["IT"].shortage_reason
    assert by["뉴스·사건"].items == [] and by["뉴스·사건"].shortage_reason
    # 화면 카테고리가 아닌 분류(기부·재난)는 응답에서 뺀다
    assert "한국철강" not in [i.keyword for i in out.items]
    assert out.score_method == "rank"


def test_todays_verdicts_survive_a_restart(tmp_path: Path) -> None:
    from datetime import date

    path = tmp_path / "board.db"
    day = lambda: date(2026, 11, 3)  # noqa: E731
    board = TodayBoard(today=day, path=path)
    board.record_verdict(
        ["t1"],
        TrendVerdict(group_name="t1", status="rising", ratio=3.0, evidence=TrendEvidence(score=5)),
    )
    board.record_pair("롤", "lck")
    again = TodayBoard(today=day, path=path)  # 서버를 다시 켠 것과 같다
    got = again.verdict(["T1"])
    assert got is not None and got.status == "rising" and again.pairs() == [("롤", "lck")]
    tomorrow = TodayBoard(today=lambda: date(2026, 11, 4), path=path)
    assert tomorrow.verdict(["t1"]) is None  # 다음 날은 그날 판정만


# ---------------------------------------------------------------- 칸별 영상: 공유 분류기로 칸 판정


class FillYouTube:
    """search_category 가짜: 한국 영상 2개·외국 영상 1개. 부른 카테고리를 남긴다."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.terms: list[str | None] = []
        self.fail = fail

    async def search_category(self, category_id, published_after, language=None, query=None):  # noqa: ANN001, ANN201
        self.calls.append((category_id, language))
        self.terms.append(query)
        if self.fail:
            raise ConnectorUnavailable("QUOTA_EXCEEDED", "할당량")
        return [
            _titled(f"{category_id}a", category_id, 500, "속보 뉴스", "뉴스채널"),
            _titled(f"{category_id}b", category_id, 900, "Breaking", "News"),
            _titled(f"{category_id}c", category_id, 700, "오늘 소식", "방송"),
        ]


def _titled(vid: str, cat: str, views: int, title: str, channel: str, rank: int | None = None):  # noqa: ANN202, PLR0913
    return VideoStat(vid, "c", cat, NOW - timedelta(days=1), views, 1, 1, ("태그",), title=title,
                     rank=rank, channel_title=channel, duration_seconds=100)  # fmt: skip


# 한국 인기 목록에 더 넣는 영상: (id, YouTube 카테고리, 조회수, 제목)
CHART = [
    ("c1", "20", 9000, "아이브 신곡 안무 커버"),  # YouTube는 게임, 내용은 음악
    ("c2", "20", 8000, "롤 하이라이트 모음"),
    ("c3", "20", 9500, "Funny cat"),  # 외국 영상: 빠진다
    ("c4", "25", 7000, "오늘의 뉴스 속보"),
]
VIDEO_ANSWERS = {
    "아이브 신곡 안무 커버": "K-pop·음악",
    "롤 하이라이트 모음": "게임",
    "오늘의 뉴스 속보": "뉴스·사건",
    "속보 뉴스": "뉴스·사건",
    "오늘 소식": "뉴스·사건",
}
FILL = (
    '\nfill_youtube_categories = { "게임" = "20", "뉴스·사건" = "25", "K-pop·음악" = "10" }\n'
    "fill_always = true\n"
    'categories = ["게임", "뉴스·사건", "연예·스포츠", "K-pop·음악", "IT"]\n'
)


def _vllm(videos: dict[str, str] | None = None, fail: bool = False) -> MockLLMClient:
    """소재 분류는 모두 게임, 영상 분류는 제목으로 답하는 가짜 LLM."""

    class LLM(MockLLMClient):
        async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
            self.calls.append(prompt)
            data = dict(untrusted or {})
            if any(k.startswith("영상") for k in data):
                if fail:
                    raise RuntimeError("down")
                titles = {k[2:].split(" ")[0]: v for k, v in data.items() if k.startswith("영상")}
                answers = videos if videos is not None else VIDEO_ANSWERS
                return _Categories(
                    items=[_Item(id=n, category=answers.get(t, "기타")) for n, t in titles.items()]
                )
            names = {k[2:]: v for k, v in data.items() if k.startswith("소재")}
            return _Categories(items=[_Item(id=n, category="게임") for n in names])

    return LLM()


def _fill_env(tmp_path: Path, extra: str = FILL) -> dict:
    env = _env(tmp_path, extra=extra)
    store = SnapshotStore(env["DATA_YOUTUBE_DB"])
    rows = [
        Row(_titled(v, c, n, t, "Cats" if v == "c3" else "채널", rank=50 + i), ("popular",))
        for i, (v, c, n, t) in enumerate(CHART)
    ]
    store.save(T1, rows)
    store.close()
    return {**env, "DATA_POPULAR_VIDEOS_DB": str(tmp_path / "pv.db")}


def _run_fill(env: dict, youtube: object, llm: MockLLMClient, cache: CategoryCache | None = None,
              fill: bool = True):  # fmt: skip  # noqa: ANN202
    return asyncio.run(
        popular(
            TrendPopularRequest(),
            llm,
            env=env,
            now=NOW,
            board=TodayBoard(),
            youtube=youtube,
            category_cache=cache,
            fill=fill,
        )  # fmt: skip
    ).output


def _video_calls(llm: MockLLMClient) -> int:
    return sum(1 for p in llm.calls if "YouTube 영상 여러 개" in p)


def test_chart_videos_are_put_in_cells_by_the_classifier_not_the_youtube_category(
    tmp_path: Path,
) -> None:
    env = _fill_env(tmp_path)
    youtube, llm = FillYouTube(), _vllm()
    out = _run_fill(env, youtube, llm, CategoryCache(tmp_path / "t.db"))
    by = {g.category: g for g in out.by_category}
    # YouTube 카테고리 20(게임)인 안무 커버는 분류기를 따라 K-pop·음악 칸으로, 다름 표시
    music = by["K-pop·음악"].top_videos
    assert [v.video_id for v in music] == ["c1"]
    assert music[0].youtube_category_id == "20" and music[0].category_mismatch is True
    assert music[0].topic_category == "K-pop·음악" and music[0].source == "chart"
    game = by["게임"].top_videos
    assert [v.video_id for v in game] == ["c2"] and game[0].category_mismatch is False
    # 외국 영상(c3)은 칸에 없다. 뉴스 칸은 인기 목록 1개 + 그 칸 검색 보충(한국 영상만, 조회수 순)
    assert [v.video_id for v in by["뉴스·사건"].top_videos] == ["c4", "25c", "25a"]
    assert [v.source for v in by["뉴스·사건"].top_videos] == ["chart", "search", "search"]
    # 검색 결과도 분류기를 통과해야 한다: 게임 검색에 나온 뉴스 영상은 게임 칸에 넣지 않는다
    assert "20a" not in [v.video_id for v in game]
    assert youtube.calls == [("20", "ko"), ("25", "ko"), ("10", "ko")]  # 5개 미만인 칸만 보충
    assert "분류해 이 칸" in (by["게임"].top_videos_reason or "")
    first = _video_calls(llm)
    assert first >= 1
    # 같은 날 다시: 검색도 영상 분류도 저장한 것을 쓴다
    again = _vllm()
    _run_fill(env, youtube, again, CategoryCache(tmp_path / "t.db"))
    assert len(youtube.calls) == 3 and _video_calls(again) == 0


def test_cells_with_enough_chart_videos_do_not_search(tmp_path: Path) -> None:
    env = _fill_env(tmp_path, FILL + "per_category = 1\n")
    youtube = FillYouTube()
    out = _run_fill(env, youtube, _vllm())
    by = {g.category: g for g in out.by_category}
    assert [v.video_id for v in by["게임"].top_videos] == ["c2"]
    assert youtube.calls == []  # 세 칸 모두 인기 목록으로 1개를 채웠다


def test_search_failure_and_unclassified_videos_leave_reasons(tmp_path: Path) -> None:
    env = _fill_env(tmp_path)
    out = _run_fill(env, FillYouTube(fail=True), _vllm())
    news = next(g for g in out.by_category if g.category == "뉴스·사건")
    assert [v.video_id for v in news.top_videos] == ["c4"]
    assert "검색 실패: 최근 조회수 높은 영상 검색 실패 [QUOTA_EXCEEDED]" in (
        news.top_videos_reason or ""
    )
    # 영상 분류를 못 하면 칸에 넣지 않고(지어내지 않음) 수를 남긴다
    out = _run_fill(_fill_env(tmp_path / "b"), FillYouTube(), _vllm(fail=True))
    assert all(g.top_videos == [] for g in out.by_category)
    game = next(g for g in out.by_category if g.category == "게임")
    assert "분류 못 한 영상 3개 뺌" in (game.top_videos_reason or "")


def test_fill_off_or_unmapped_category_makes_no_video_calls(tmp_path: Path) -> None:
    llm = _vllm()
    out = _run_fill(_fill_env(tmp_path), FillYouTube(), llm, fill=False)
    assert all(g.top_videos == [] and g.top_videos_reason is None for g in out.by_category)
    assert _video_calls(llm) == 0  # 기획안 재료(promo)는 영상 칸을 쓰지 않는다
    out = _run_fill(_fill_env(tmp_path / "b", ""), FillYouTube(), _vllm())
    assert all(g.top_videos == [] for g in out.by_category)  # 설정 표가 없으면 채우지 않는다


# ---------------------------------------------------------------- 게임 보충 검색에 검색어


def test_game_search_uses_top_game_topic_names_and_results_still_pass_the_classifier(
    tmp_path: Path,
) -> None:
    extra = 'fill_query_cells = ["게임"]\nfill_query_topics = 2\n'
    env = _fill_env(tmp_path, FILL + extra)
    youtube = FillYouTube()
    out = _run_fill(env, youtube, _vllm())
    # 소재 분류가 모두 게임인 가짜 LLM: 지금 인기 게임 칸 앞 2개 이름을 |로 묶는다
    # (손으로 적지 않음)
    game_topics = [i.keyword for i in sorted(out.items, key=lambda i: i.rank)
                   if i.topic_category == "게임"][:2]  # fmt: skip
    assert youtube.terms[0] == "|".join(game_topics) and youtube.terms[1:] == [None, None]
    by = {g.category: g for g in out.by_category}
    # 검색어로 받은 결과도 분류기를 통과해야 한다: 뉴스로 분류된 영상은 게임 칸에 안 들어간다
    assert [v.video_id for v in by["게임"].top_videos] == ["c2"]
    assert "검색어 '" in (by["게임"].top_videos_reason or "")


def test_search_cap_leaves_only_that_cell_unknown(tmp_path: Path) -> None:
    env = _fill_env(tmp_path, FILL + "fill_search_calls = 1\n")
    youtube = FillYouTube()
    out = _run_fill(env, youtube, _vllm())
    by = {g.category: g for g in out.by_category}
    assert youtube.calls == [("20", "ko")]  # 상한 1번
    news = by["뉴스·사건"]
    assert [v.video_id for v in news.top_videos] == ["c4"]  # 인기 목록 영상은 그대로
    assert "확인할 수 없음(보충): 하루 보충 검색 상한(1번)에 닿아 검색하지 않음" in (
        news.top_videos_reason or ""
    )
    assert "확인할 수 없음" not in (by["게임"].top_videos_reason or "")
