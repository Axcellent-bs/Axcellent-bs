"""YouTube 제목 구 후보와 자동 제외(늘 붙는 말), 제목 저장·채우기.

실제 YouTube·네이버를 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ax_contracts.schemas.data import TrendCandidatesRequest, TrendScanInput
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.connectors.youtube import VideoStat
from data_agent.skills.trend_candidates import (
    CandidateConfig,
    common_terms,
    find_candidates,
    load_config,
    pick_names,
    title_phrases,
)
from data_agent.skills.trend_scan import REQUEST_DAYS
from data_agent.youtube_collect import backfill_titles
from data_agent.youtube_store import (
    HistoryVideo,
    PopularSnapshot,
    PopularVideo,
    Row,
    SnapshotStore,
    latest_popular,
    popular_history,
)

NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
SNAP_AT = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
END = date(2026, 11, 1)
CATBOOKS_TRENDS = (
    Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "trends.toml"
)
TITLES = CandidateConfig(titles=True)


def _snap(*videos: tuple[str, int, tuple[str, ...], str]) -> PopularSnapshot:
    return PopularSnapshot(
        SNAP_AT, tuple(PopularVideo(v, r, t, title) for v, r, t, title in videos)
    )


# ---------------------------------------------------------------- 제목 구


def test_title_phrases_are_two_to_three_word_runs_without_punctuation() -> None:
    got = title_phrases("[LCK] T1 vs 젠지, 결승 하이라이트!")
    assert {"lck t1", "t1 vs", "vs 젠지", "젠지 결승", "결승 하이라이트"} <= got
    assert {"lck t1 vs", "t1 vs 젠지", "젠지 결승 하이라이트"} <= got
    assert "t1" not in got  # 한 어절은 구가 아니다
    assert "2026 11" not in title_phrases("2026 11 결산")  # 숫자로만 된 구는 뺀다
    assert "youre the" in title_phrases("You're the one")  # 아포스트로피는 붙여 읽는다


def test_a_phrase_needs_min_videos_and_joins_a_tag_that_differs_only_by_spaces() -> None:
    snap = _snap(
        ("a", 1, ("리그오브레전드",), "리그 오브 레전드 매드무비"),
        ("b", 2, (), "오늘의 리그 오브 레전드"),
        ("c", 3, (), "고양이 영상 모음"),
    )
    [lol] = pick_names(snap, TITLES, 5).candidates
    assert lol.keyword == "리그오브레전드" and lol.video_ids == ("a", "b")
    assert lol.origins == ("youtube_tag", "youtube_title")
    assert lol.phrases == (
        "리그 오브 레전드",
    )  # 짧은 조각(리그 오브·오브 레전드)은 같은 영상이라 뺀다
    assert lol.matched == ("리그오브레전드",)


def test_title_only_phrase_is_its_own_candidate() -> None:
    snap = _snap(("a", 1, (), "고양이 집사 브이로그"), ("b", 2, (), "초보 고양이 집사"))
    [only] = pick_names(snap, TITLES, 5).candidates
    assert (only.keyword, only.origins, only.video_ids) == (
        "고양이 집사",
        ("youtube_title",),
        ("a", "b"),
    )


def test_titles_are_off_unless_configured() -> None:
    snap = _snap(("a", 1, (), "고양이 집사"), ("b", 2, (), "고양이 집사"))
    assert pick_names(snap, CandidateConfig(), 5).candidates == []


# ---------------------------------------------------------------- 자동 제외


def _hist(vid: str, cat: str, tags: tuple[str, ...], days: tuple[int, ...], title: str = ""):  # noqa: ANN202
    return HistoryVideo(vid, cat, tags, title, frozenset(date(2026, 11, d) for d in days))


AUTO = CandidateConfig(
    auto_exclude=True, auto_min_doc_ratio=0.3, auto_min_categories=3, auto_min_day_ratio=0.5
)


def test_common_terms_need_spread_over_categories_days_and_videos() -> None:
    history = [
        _hist("1", "20", ("스트리머", "t1"), (1, 2)),
        _hist("2", "24", ("스트리머", "t1"), (2,)),
        _hist("3", "22", ("스트리머", "t1"), (1,)),
        _hist("4", "20", ("하루만",), (1,)),
        _hist("5", "24", ("하루만",), (1,)),
        _hist("6", "22", ("하루만",), (1,)),
        _hist("7", "10", ("x",), (3,)),
        _hist("8", "10", ("x",), (4,)),
    ]
    common = common_terms(history, AUTO)
    assert list(common) == ["스트리머", "t1"] or set(common) == {"스트리머", "t1"}
    term = common["스트리머"]
    assert (term.videos, term.total_videos, term.categories, term.days, term.total_days) == (
        3, 8, 3, 2, 4,
    )  # fmt: skip
    assert (
        term.reason() == "늘 붙는 말(태그): 쌓인 인기 영상 8개 중 3개(37.5%), 카테고리 3개, 2/4일"
    )
    # 하루만: 카테고리 3개지만 4일 중 1일 → 날짜 기준 미달. x: 카테고리 1개
    assert "하루만" not in common and "x" not in common


def test_keep_tags_override_and_off_means_no_auto_exclusion() -> None:
    history = [_hist(str(i), str(i), ("스트리머",), (1,)) for i in range(3)]
    assert set(common_terms(history, AUTO)) == {"스트리머"}
    keep = dataclasses.replace(AUTO, keep_tags=frozenset({"스트리머"}))
    assert common_terms(history, keep) == {}
    assert common_terms(history, CandidateConfig()) == {}  # 꺼져 있으면 빈 표


def test_auto_excluded_names_are_dropped_and_reported_even_if_also_manual() -> None:
    history = [_hist(str(i), str(i), ("스트리머", "shorts"), (1,)) for i in range(3)]
    config = dataclasses.replace(AUTO, exclude_tags=frozenset({"shorts"}))
    common = common_terms(history, config)
    snap = _snap(
        ("a", 1, ("스트리머", "shorts", "t1"), ""), ("b", 2, ("스트리머", "shorts", "t1"), "")
    )
    picked = pick_names(snap, config, 5, common)
    assert [c.keyword for c in picked.candidates] == ["t1"]
    assert [(t.name, n) for t, n in picked.auto_dropped] == [("shorts", 2), ("스트리머", 2)]


def test_company_config_turns_on_titles_and_conservative_auto_exclusion() -> None:
    config = load_config({"DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)})
    assert config.titles and config.auto_exclude
    assert config.auto_min_categories >= 3 and config.auto_min_doc_ratio > 0
    assert {"shorts", "브이로그"} <= config.exclude_tags  # 수동 목록은 덮어쓰기용으로 남는다


# ---------------------------------------------------------------- 저장 (제목)


def _stat(
    vid: str, rank: int | None, tags: tuple[str, ...], title: str = "", cat: str = "20"
) -> VideoStat:
    return VideoStat(
        video_id=vid, channel_id="ch", category_id=cat, published_at=NOW - timedelta(days=1),
        view_count=1, like_count=None, comment_count=None, tags=tags, title=title, rank=rank,
    )  # fmt: skip


def test_titles_are_stored_beside_snapshots_and_read_with_the_popular_list(tmp_path: Path) -> None:
    path = tmp_path / "youtube.db"
    store = SnapshotStore(path)
    store.save(SNAP_AT, [Row(_stat("a", 1, ("t1",), "첫 제목"), ("popular",))])
    store.save(
        SNAP_AT + timedelta(hours=1), [Row(_stat("a", 1, ("t1",), "바뀐 제목"), ("popular",))]
    )
    store.close()
    snap = latest_popular(path)
    assert snap is not None and snap.videos[0].title == "바뀐 제목"
    assert snap.videos[0].category_id == "20"
    # 스냅샷 표 모양은 그대로 (돌고 있는 예전 수집기의 INSERT가 깨지지 않게)
    conn = sqlite3.connect(path)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(snapshots)")]
    conn.close()
    assert len(columns) == 11 and "title" not in columns


def test_an_old_db_without_the_title_table_still_reads(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE snapshots (collected_at TEXT, video_id TEXT, view_count INTEGER,"
        " like_count INTEGER, comment_count INTEGER, popular_rank INTEGER, published_at TEXT,"
        " category_id TEXT, channel_id TEXT, tags TEXT, sources TEXT)"
    )
    conn.execute(
        "INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (SNAP_AT.isoformat(), "a", 1, None, None, 1, SNAP_AT.isoformat(), "20", "c",
         json.dumps(["t1"]), "popular"),
    )  # fmt: skip
    conn.commit()
    conn.close()
    snap = latest_popular(path)
    assert snap is not None and snap.videos[0].title == "" and snap.videos[0].tags == ("t1",)
    [hist] = popular_history(path)
    assert hist.title == "" and hist.days == frozenset({date(2026, 11, 1)})


def test_popular_history_unions_tags_and_days_until_the_cutoff(tmp_path: Path) -> None:
    path = tmp_path / "youtube.db"
    store = SnapshotStore(path)
    store.save(SNAP_AT, [Row(_stat("a", 1, ("t1",), "제목"), ("popular",)),
                         Row(_stat("tr", None, ("추적",)), ("tracked",))])  # fmt: skip
    store.save(SNAP_AT + timedelta(days=1), [Row(_stat("a", 2, ("lol",)), ("popular",))])
    store.close()
    [a] = popular_history(path)  # 추적 영상(인기 순위 없음)은 뺀다
    assert a.tags == ("lol", "t1") and len(a.days) == 2 and a.title == "제목"
    [early] = popular_history(path, SNAP_AT)
    assert early.tags == ("t1",)


class FakeYouTube:
    def __init__(self, fail: bool = False) -> None:
        self.fail, self.asked, self.quota_used = fail, [], 0

    async def videos(self, ids: list[str]) -> list[VideoStat]:
        self.asked.append(list(ids))
        if self.fail:
            raise ConnectorUnavailable("QUOTA_EXCEEDED", "할당량")
        return [_stat(i, None, (), f"제목 {i}") for i in ids if i != "gone"]


def test_backfill_fills_only_missing_titles_and_never_invents(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "y.db")
    # 예전 수집기처럼 제목 없이 저장된 인기 영상 (gone은 지워진 영상)
    rows = [Row(_stat("a", 1, ()), ("popular",)), Row(_stat("gone", 2, ()), ("popular",)),
            Row(_stat("has", 3, (), "있는 제목"), ("popular",))]  # fmt: skip
    store.save(SNAP_AT, rows)
    yt = FakeYouTube()
    found, saved, errors = asyncio.run(backfill_titles(yt, store, now=NOW))  # type: ignore[arg-type]
    assert (found, saved, errors) == (2, 1, []) and sorted(yt.asked[0]) == ["a", "gone"]
    assert store.popular_ids_without_title() == ["gone"]
    failed = asyncio.run(backfill_titles(FakeYouTube(fail=True), store, now=NOW))  # type: ignore[arg-type]
    assert failed == (1, 0, ["제목 [QUOTA_EXCEEDED]"])
    store.close()


# ---------------------------------------------------------------- skill 출력


class Naver:
    def __init__(self) -> None:
        self.queries: list[TrendScanInput] = []

    async def search_trend(self, query: TrendScanInput) -> TrendResponse:
        self.queries.append(query)
        name = query.keyword_groups[0].group_name
        start = END - timedelta(days=REQUEST_DAYS - 1)
        points = tuple((start + timedelta(days=i), 50.0 + (i % 3)) for i in range(REQUEST_DAYS))
        return TrendResponse(
            query.start_date, query.end_date, query.time_unit, (TrendSeries(name, (name,), points),)
        )


def test_output_has_origins_phrases_and_auto_excluded_with_reasons(tmp_path: Path) -> None:
    path = tmp_path / "youtube.db"
    store = SnapshotStore(path)
    store.save(SNAP_AT, [
        Row(_stat("a", 1, ("스트리머", "t1"), "고양이 집사 일기", "20"), ("popular",)),
        Row(_stat("b", 2, ("스트리머", "t1"), "고양이 집사 브이로그", "24"), ("popular",)),
        Row(_stat("c", 3, ("스트리머",), "다른 영상", "22"), ("popular",)),
    ])  # fmt: skip
    store.close()
    config = tmp_path / "trends.toml"
    config.write_text(
        "[candidates]\ntitles = true\n[candidates.auto_exclude]\n"
        "min_doc_ratio = 0.5\nmin_categories = 3\nmin_day_ratio = 0.5\n",
        encoding="utf-8",
    )
    env = {"DATA_YOUTUBE_DB": str(path), "DATA_TREND_CONFIG": str(config)}
    out = asyncio.run(
        find_candidates(TrendCandidatesRequest(end_date=END), Naver(), env=env, now=NOW)
    ).output
    assert [(c.keyword, c.origins) for c in out.candidates] == [
        ("t1", ["youtube_tag"]),
        ("고양이 집사", ["youtube_title"]),
    ]
    assert out.candidates[1].youtube.matched_phrases == ["고양이 집사"]
    [auto] = out.auto_excluded
    assert (auto.name, auto.source, auto.snapshot_videos, auto.categories) == (
        "스트리머",
        "youtube_tag",
        3,
        3,
    )
    assert auto.reason.startswith("늘 붙는 말(태그): 쌓인 인기 영상 3개 중 3개")
    assert out.exclusion_basis == "쌓인 인기 영상 3개, 기록이 있는 날 1일"
    assert out.summary.endswith("자동 제외 1개 (스트리머).")
