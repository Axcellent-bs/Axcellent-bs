"""trend_candidates skill: YouTube 인기 태그 후보 + 네이버 급상승.

실제 YouTube·네이버를 부르지 않는다.

- 태그 정리(대소문자·#·공백), 최소 영상 수, 회사 제외 목록, 순서·상한
- 스냅샷 없음·오래됨 → unknown + 이유
- 네이버 일부 실패 → 그 후보만 unknown
- 판정은 trend_scan과 같은 함수 (같은 시계열이면 같은 결과)
- 핸들러 응답 모양
"""

from __future__ import annotations

import asyncio
import math
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import TrendCandidatesRequest, TrendScanInput
from data_agent import handlers
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.connectors.youtube import VideoStat
from data_agent.skills.trend_candidates import (
    CandidateConfig,
    clean_tag,
    find_candidates,
    load_config,
    pick_candidates,
    pick_verdict,
)
from data_agent.skills.trend_scan import REQUEST_DAYS, judge
from data_agent.youtube_store import (
    PopularSnapshot,
    PopularVideo,
    Row,
    SnapshotStore,
    latest_popular,
)

NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
# 판정 마지막 날(END = 11-01) 한국 시간 23시. 지난 기간(end_date) 판정은 그 기간 끝(11-02 0시 KST)
# 이전 스냅샷만 쓴다
SNAP_AT = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
END = date(2026, 11, 1)
START = END - timedelta(days=REQUEST_DAYS - 1)
CATBOOKS_TRENDS = (
    Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "trends.toml"
)


def _stat(video_id: str, rank: int | None, tags: tuple[str, ...]) -> VideoStat:
    return VideoStat(
        video_id=video_id,
        channel_id="ch",
        category_id="20",
        published_at=NOW - timedelta(days=1),
        view_count=1000,
        like_count=10,
        comment_count=1,
        tags=tags,
        rank=rank,
    )


def _db(tmp_path: Path, videos: list[tuple[str, int | None, tuple[str, ...]]], at=SNAP_AT) -> Path:  # noqa: ANN001
    path = tmp_path / "youtube.db"
    store = SnapshotStore(path)
    store.save(at, [Row(_stat(v, r, t), ("popular",) if r else ("tracked",)) for v, r, t in videos])
    store.close()
    return path


def _snapshot(*videos: tuple[str, int, tuple[str, ...]]) -> PopularSnapshot:
    return PopularSnapshot(SNAP_AT, tuple(PopularVideo(v, r, t) for v, r, t in videos))


# ---------------------------------------------------------------- 후보 뽑기 (코드 규칙)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [("T1", "t1"), ("  #T1 ", "t1"), ("##LoL##", "lol"), ("리그오브레전드", "리그오브레전드"),
     (" # ", ""), ("League of Legends", "league of legends")],
)  # fmt: skip
def test_clean_tag(raw: str, clean: str) -> None:
    assert clean_tag(raw) == clean


def test_tags_that_differ_only_by_case_hash_or_spaces_are_one_candidate() -> None:
    snap = _snapshot(("a", 1, ("T1",)), ("b", 2, ("#t1 ",)), ("c", 3, (" t1",)))
    [only] = pick_candidates(snap, CandidateConfig(), 5)
    assert only.keyword == "t1"
    assert only.video_count == 3
    assert only.video_ids == ("a", "b", "c")


def test_the_same_tag_twice_on_one_video_counts_once() -> None:
    snap = _snapshot(("a", 1, ("T1", "#t1", "t1")), ("b", 2, ("x",)))
    assert pick_candidates(snap, CandidateConfig(), 5) == []  # t1은 영상 1개뿐


def test_only_tags_on_at_least_min_videos() -> None:
    snap = _snapshot(("a", 1, ("둘", "하나", "셋")), ("b", 2, ("둘", "셋")), ("c", 3, ("셋",)))
    assert [c.keyword for c in pick_candidates(snap, CandidateConfig(), 5)] == ["셋", "둘"]
    strict = CandidateConfig(min_videos=3)
    assert [c.keyword for c in pick_candidates(snap, strict, 5)] == ["셋"]


def test_excluded_tags_are_left_out_after_cleaning() -> None:
    snap = _snapshot(
        ("a", 1, ("#Shorts", "브이로그", "t1")), ("b", 2, ("shorts", "브이로그", "t1"))
    )
    config = CandidateConfig(exclude_tags=frozenset({"shorts", "브이로그"}))
    assert [c.keyword for c in pick_candidates(snap, config, 5)] == ["t1"]


def test_order_is_video_count_then_best_rank_and_capped() -> None:
    snap = _snapshot(
        ("a", 1, ("b태그", "c태그")),
        ("b", 2, ("a태그", "b태그", "c태그")),
        ("c", 3, ("a태그", "c태그")),
        ("d", 4, ("a태그",)),
    )
    picked = pick_candidates(snap, CandidateConfig(), 2)
    # a태그·c태그 모두 3개 → 순위가 더 높은 영상(1위)에 붙은 c태그가 먼저. 상한 2개
    assert [(c.keyword, c.video_count) for c in picked] == [("c태그", 3), ("a태그", 3)]


def test_company_config_has_small_defaults_and_an_exclude_list() -> None:
    config = load_config({"DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)})
    assert 1 <= config.max_candidates <= 10  # 후보마다 네이버 1번
    assert config.min_videos == 2
    assert {"shorts", "브이로그"} <= config.exclude_tags
    assert config.canonical("lol") == "리그오브레전드" and config.canonical("티원") == "t1"
    assert load_config({}) == CandidateConfig()  # 설정이 없으면 기본값, 제외 목록·별칭 없음
    # 같은 대상 사람 덮어쓰기 표는 기본이 비어 있다 (기본 판정은 자동 묶기). t1은 그대로 대표 이름
    assert config.same_targets == {} and config.canonical("t1") == "t1"


# ---------------------------------------------------------------- 별칭 묶기

LOL = CandidateConfig(
    aliases={"리그오브레전드": ("lol", "리그 오브 레전드"), "t1": ("티원",)},
)


def test_aliases_merge_into_the_main_name() -> None:
    """lol·리그 오브 레전드가 리그오브레전드 하나로: 영상 수는 영상마다 한 번씩 합친다."""
    snap = _snapshot(
        ("a", 1, ("LoL",)),
        ("b", 2, ("리그 오브 레전드",)),
        ("c", 3, ("리그오브레전드",)),
        ("d", 4, ("#티원",)),
        ("e", 5, ("T1",)),
    )
    picked = pick_candidates(snap, LOL, 5)
    assert [(c.keyword, c.video_ids) for c in picked] == [
        ("리그오브레전드", ("a", "b", "c")),
        ("t1", ("d", "e")),
    ]
    assert picked[0].matched == ("lol", "리그 오브 레전드", "리그오브레전드")
    assert picked[1].matched == ("티원", "t1")
    # 별칭 없이는 따로 세어 2개 이상인 것이 없다
    assert pick_candidates(snap, CandidateConfig(), 5) == []


def test_one_video_with_the_name_and_its_alias_counts_once() -> None:
    snap = _snapshot(("a", 1, ("lol", "리그오브레전드", "#LOL")), ("b", 2, ("리그 오브 레전드",)))
    [only] = pick_candidates(snap, LOL, 5)
    assert only.keyword == "리그오브레전드"
    assert only.video_ids == ("a", "b") and only.video_count == 2


def test_an_excluded_main_name_drops_its_aliases_too() -> None:
    config = CandidateConfig(aliases={"게임": ("game",)}, exclude_tags=frozenset({"게임"}))
    snap = _snapshot(("a", 1, ("game", "x")), ("b", 2, ("GAME", "x")))
    assert [c.keyword for c in pick_candidates(snap, config, 5)] == ["x"]


def test_naver_keywords_are_the_main_name_then_aliases() -> None:
    assert LOL.keywords("리그오브레전드") == ["리그오브레전드", "lol", "리그 오브 레전드"]
    assert LOL.keywords("젠지") == ["젠지"]  # 별칭이 없으면 그 이름 하나


def test_alias_table_is_cleaned_and_an_alias_belongs_to_one_name(tmp_path: Path) -> None:
    path = tmp_path / "trends.toml"
    path.write_text(
        "[candidates.aliases]\n"
        '"리그오브레전드" = ["LoL", " #lol ", "리그오브레전드", "티원"]\n'
        '"T1" = ["티원", "t1"]\n'
        '"롤드컵" = ["lol"]\n',
        encoding="utf-8",
    )
    config = load_config({"DATA_TREND_CONFIG": str(path)})
    # 같은 별칭 한 번만, 대표 이름 자신은 별칭이 아님, 다른 대표 이름(t1)도 별칭이 될 수 없음,
    # 별칭은 앞에 적은 대표 이름 하나에만
    assert config.aliases == {"리그오브레전드": ("lol", "티원"), "t1": (), "롤드컵": ()}


def test_without_an_alias_table_candidates_are_the_same_as_before(tmp_path: Path) -> None:
    """별칭 표가 없으면 예전과 같다: 태그마다 따로 세고, 네이버 그룹은 키워드 하나."""
    path = tmp_path / "trends.toml"
    path.write_text('[candidates]\nexclude_tags = ["shorts"]\n', encoding="utf-8")
    naver = Naver({"t1": 3.0})
    request = TrendCandidatesRequest(end_date=END)
    env = {"DATA_YOUTUBE_DB": str(_db(tmp_path, VIDEOS)), "DATA_TREND_CONFIG": str(path)}
    out = asyncio.run(find_candidates(request, naver, env=env, now=NOW)).output
    assert [(c.keyword, c.youtube.video_count) for c in out.candidates] == [
        ("t1", 3),
        ("롤", 2),
        ("젠지", 2),
    ]
    assert [q.keyword_groups[0].keywords for q in naver.queries] == [["t1"], ["롤"], ["젠지"]]
    assert all(c.aliases == [] for c in out.candidates)
    assert [c.youtube.matched_tags for c in out.candidates] == [["t1"], ["롤"], ["젠지"]]


# ---------------------------------------------------------------- 스냅샷 읽기


def test_latest_popular_reads_only_the_newest_popular_snapshot(tmp_path: Path) -> None:
    path = _db(tmp_path, [("old", 1, ("옛날",))], at=SNAP_AT - timedelta(hours=1))
    store = SnapshotStore(path)
    store.save(SNAP_AT, [Row(_stat("p2", 2, ("t1",)), ("popular",)),
                         Row(_stat("p1", 1, ("t1",)), ("popular",)),
                         Row(_stat("tr", None, ("추적",)), ("tracked",))])  # fmt: skip
    store.close()
    snap = latest_popular(path)
    assert snap is not None and snap.collected_at == SNAP_AT
    assert [v.video_id for v in snap.videos] == ["p1", "p2"]  # 인기 순위 순, 추적 영상 제외


def test_latest_popular_does_not_create_a_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "none.db"
    assert latest_popular(missing) is None
    assert not missing.exists()


# ---------------------------------------------------------------- skill


def _calm(day: date, phase: float = 0.0) -> float:
    return 50.0 * (1 + 0.05 * math.sin(day.toordinal() * 2.1 + phase))


def _phase(name: str) -> float:
    """키워드마다 날짜별 흔들림 위상이 다르다 (실제 검색어처럼 서로 다른 모양).

    모든 키워드가 같은 모양이면 상관이 1이라 이름 자동 묶기가 서로 다른 후보를 묶는다.
    """
    return random.Random(name).uniform(0.6, 2.5)


def _points(spike: float = 1.0, phase: float = 0.0) -> tuple[tuple[date, float], ...]:
    out, day = [], START
    while day <= END:
        v = _calm(day, phase) * (spike if day > END - timedelta(days=3) else 1.0)
        out.append((day, v))
        day += timedelta(days=1)
    top = max(v for _, v in out)
    return tuple((d, round(v * 100 / top, 5)) for d, v in out)


class Naver:
    """키워드마다 정해진 시계열을 주는 메모리 커넥터. `fail`에 든 키워드는 실패한다."""

    def __init__(self, spikes: dict[str, float], fail: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.spikes = spikes
        self.fail = fail
        self.queries: list[TrendScanInput] = []

    async def search_trend(self, query: TrendScanInput) -> TrendResponse:
        self.queries.append(query)
        name = query.keyword_groups[0].group_name
        if name in self.fail:
            raise ConnectorUnavailable("UNAVAILABLE", "네이버 검색어 트렌드에 연결할 수 없다")
        series = TrendSeries(name, (name,), _points(self.spikes.get(name, 1.0), _phase(name)))
        return TrendResponse(query.start_date, query.end_date, query.time_unit, (series,))


VIDEOS = [
    ("v1", 1, ("T1", "#shorts", "롤")),
    ("v2", 2, ("t1 ", "Shorts", "롤", "젠지")),
    ("v3", 3, ("#T1", "젠지")),
    ("v4", 4, ("혼자",)),
]


def _env(path: Path) -> dict[str, str]:
    return {"DATA_YOUTUBE_DB": str(path), "DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)}


def _run(path: Path, naver: Naver, **kwargs):  # noqa: ANN003, ANN202
    request = TrendCandidatesRequest(end_date=END, **kwargs)
    return asyncio.run(find_candidates(request, naver, env=_env(path), now=NOW))


def test_candidates_carry_youtube_evidence_and_the_naver_verdict(tmp_path: Path) -> None:
    naver = Naver({"t1": 3.0})
    out = _run(_db(tmp_path, VIDEOS), naver).output
    assert out.status == "known" and out.snapshot_at == SNAP_AT and out.snapshot_videos == 4
    assert [c.keyword for c in out.candidates] == ["t1", "롤", "젠지"]  # shorts는 회사 제외 목록
    t1 = out.candidates[0]
    assert t1.youtube.video_count == 3 and t1.youtube.snapshot_at == SNAP_AT
    assert t1.youtube.video_ids == ["v1", "v2", "v3"]
    assert t1.naver.status == "rising" and t1.naver.group_name == "t1"
    assert out.candidates[1].naver.status == "normal"
    # 회사 별칭 표에 t1 = ["티원"]이 있어 t1 그룹에는 별칭이 키워드로 함께 들어가고,
    # 이름마다 따로도 판정한다(t1·티원 2번 더) → 묶음 3 + 이름 2 = 5번
    assert out.naver_calls == 5 == len(naver.queries)
    assert [q.keyword_groups[0].keywords for q in naver.queries] == [
        ["t1", "티원"],
        ["t1"],
        ["티원"],
        ["롤"],
        ["젠지"],
    ]
    assert t1.aliases == ["티원"] and out.candidates[1].aliases == []
    assert t1.youtube.matched_tags == ["t1"]  # 스냅샷에 실제로 나온 것은 t1뿐
    assert all((q.start_date, q.end_date) == (START, END) for q in naver.queries)
    assert out.summary == "YouTube 인기 영상 4개의 태그에서 후보 3개, 네이버 급상승 1개 (t1)."


def test_naver_verdict_is_exactly_the_trend_scan_judgement(tmp_path: Path) -> None:
    """판정 함수를 다시 만들지 않는다: 같은 응답을 trend_scan.judge에 넣은 결과와 같다."""
    naver = Naver({"t1": 3.0})
    out = _run(_db(tmp_path, VIDEOS), naver, max_candidates=1).output
    query = naver.queries[0]
    response = asyncio.run(Naver({"t1": 3.0}).search_trend(query))
    assert out.candidates[0].naver == judge(query, response).results[0]


def test_max_candidates_in_the_request_caps_naver_calls(tmp_path: Path) -> None:
    naver = Naver({})
    out = _run(_db(tmp_path, VIDEOS), naver, max_candidates=1).output
    # 후보 1개(t1) = 묶음 1번 + 이름별(t1·티원) 2번
    assert [c.keyword for c in out.candidates] == ["t1"] and out.naver_calls == 3


def test_a_naver_failure_makes_only_that_candidate_unknown(tmp_path: Path) -> None:
    result = _run(_db(tmp_path, VIDEOS), Naver({"t1": 3.0}, fail={"롤"}))
    by = {c.keyword: c.naver for c in result.output.candidates}
    assert by["t1"].status == "rising"
    assert by["롤"].status == "unknown"
    assert by["롤"].unknown_reason == "네이버 검색어 트렌드를 읽지 못함 [UNAVAILABLE]"
    assert by["젠지"].status == "normal"
    assert result.output.naver_calls == 5  # 실패도 부른 수에 든다 (t1은 이름별 2번 더)
    assert "판정 불가 1개" in result.output.summary
    assert "네이버를 읽지 못한 후보: 롤" in result.evidence.data_summary


def test_no_snapshot_is_unknown_with_a_reason(tmp_path: Path) -> None:
    naver = Naver({})
    out = _run(tmp_path / "missing.db", naver).output
    assert out.status == "unknown" and out.candidates == [] and out.snapshot_at is None
    assert "스냅샷이 없음" in (out.unknown_reason or "")
    assert naver.queries == []  # 네이버를 부르지 않는다


def test_a_db_with_only_tracked_videos_has_no_popular_snapshot(tmp_path: Path) -> None:
    out = _run(_db(tmp_path, [("tr", None, ("t1",))]), Naver({})).output
    assert out.status == "unknown"
    assert "스냅샷이 없음" in (out.unknown_reason or "")


def test_an_old_snapshot_is_unknown_with_its_age(tmp_path: Path) -> None:
    naver = Naver({})
    old = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)  # 판정 기간 끝(11-01 15시 UTC)보다 30시간 전
    out = _run(_db(tmp_path, VIDEOS, at=old), naver).output
    assert out.status == "unknown" and out.candidates == []
    assert out.snapshot_at == old
    assert out.unknown_reason == (
        "YouTube 스냅샷이 오래됨: 마지막 수집 2026-10-31T09:00:00+00:00"
        " (판정 기간 끝보다 30.0시간 전, 기준 24시간)"
    )
    assert naver.queries == []


def test_no_tag_on_two_videos_is_known_with_no_candidates(tmp_path: Path) -> None:
    out = _run(_db(tmp_path, [("a", 1, ("x",)), ("b", 2, ("y",))]), Naver({})).output
    assert out.status == "known" and out.candidates == [] and out.naver_calls == 0


# ---------------------------------------------------------------- 핸들러


def _request(payload: dict) -> AgentRequest:
    meta = RequestMeta(
        context_id="ctx_trend", token="x", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    return AgentRequest(meta=meta, skill="trend_candidates", input=payload)


def test_handler_completes_with_the_contract_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _db(tmp_path, VIDEOS, at=datetime.now(UTC) - timedelta(minutes=5))
    monkeypatch.setenv("DATA_YOUTUBE_DB", str(path))
    monkeypatch.setenv("DATA_TREND_CONFIG", str(CATBOOKS_TRENDS))
    monkeypatch.setattr(handlers, "get_trend_connector", lambda: Naver({"t1": 3.0}))
    result = asyncio.run(
        handlers.HANDLERS["trend_candidates"](_request({}))  # 지금 기준 (가장 최근 스냅샷)
    )
    assert result.status == TaskStatus.COMPLETED and result.outcome == "known"
    assert [c["keyword"] for c in result.output["candidates"]] == ["t1", "롤", "젠지"]
    assert result.evidence is not None and result.evidence.queries[1].startswith(
        "naver search-trend group=t1"
    )


def test_handler_reports_unknown_snapshot_as_completed_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATA_YOUTUBE_DB", str(tmp_path / "missing.db"))
    monkeypatch.setattr(handlers, "get_trend_connector", lambda: Naver({}))
    result = asyncio.run(handlers.HANDLERS["trend_candidates"](_request({})))
    assert result.status == TaskStatus.COMPLETED and result.outcome == "unknown"
    assert result.output["unknown_reason"].startswith("YouTube 스냅샷이 없음")


def test_handler_rejects_a_bad_input(monkeypatch: pytest.MonkeyPatch) -> None:
    result = asyncio.run(handlers.HANDLERS["trend_candidates"](_request({"max_candidates": 0})))
    assert result.status == TaskStatus.FAILED and result.outcome == "invalid_input"


# -------------------------------------------------- 이름별 판정 (작은 이름이 묻히지 않게)

LOL_VIDEOS = [
    ("a", 1, ("리그오브레전드", "lol")),
    ("b", 2, ("리그오브레전드",)),
    ("c", 3, ("lol", "젠지")),
    ("d", 4, ("젠지",)),
]


def _lol_env(tmp_path: Path) -> dict[str, str]:
    config = tmp_path / "trends.toml"
    config.write_text(
        '[candidates]\nmin_videos = 2\n\n[candidates.aliases]\n"리그오브레전드" = ["lol"]\n',
        encoding="utf-8",
    )
    return {"DATA_YOUTUBE_DB": str(_db(tmp_path, LOL_VIDEOS)), "DATA_TREND_CONFIG": str(config)}


def _lol_run(tmp_path: Path, naver: Naver):  # noqa: ANN202
    request = TrendCandidatesRequest(end_date=END)
    return asyncio.run(find_candidates(request, naver, env=_lol_env(tmp_path), now=NOW))


def test_a_rising_alias_buried_in_the_group_is_raised(tmp_path: Path) -> None:
    """묶음(리그오브레전드·lol)은 평범인데 lol만 급상승 → 급상승으로 올리고 올라간 이름을 남긴다."""
    naver = Naver({"lol": 3.0})  # 묶음 그룹(이름 = 리그오브레전드)은 평범
    out = _lol_run(tmp_path, naver).output
    lol = next(c for c in out.candidates if c.keyword == "리그오브레전드")
    assert lol.naver.status == "rising" and lol.naver.group_name == "lol"
    assert lol.naver_from == "name" and lol.rising_names == ["lol"]
    assert [v.group_name for v in lol.name_verdicts] == ["리그오브레전드", "lol"]
    assert [v.status for v in lol.name_verdicts] == ["normal", "rising"]
    # 묶음 1번 + 이름별 2번(리그오브레전드·lol) + 젠지 1번 = 4번
    assert out.naver_calls == 4 == len(naver.queries)


def test_a_rising_group_keeps_the_group_verdict(tmp_path: Path) -> None:
    naver = Naver({"리그오브레전드": 3.0, "lol": 3.0})
    lol = next(
        c for c in _lol_run(tmp_path, naver).output.candidates if c.keyword == "리그오브레전드"
    )
    assert lol.naver.group_name == "리그오브레전드" and lol.naver_from == "group"
    assert lol.rising_names == ["리그오브레전드", "lol"]


def test_no_alias_means_no_extra_naver_calls(tmp_path: Path) -> None:
    naver = Naver({})
    out = _lol_run(tmp_path, naver).output
    zenji = next(c for c in out.candidates if c.keyword == "젠지")
    assert zenji.name_verdicts == [] and zenji.rising_names == [] and zenji.naver_from == "group"
    assert sum(q.keyword_groups[0].group_name == "젠지" for q in naver.queries) == 1


def test_a_failed_name_does_not_raise_and_the_group_stays(tmp_path: Path) -> None:
    naver = Naver({}, fail={"lol"})
    lol = next(
        c for c in _lol_run(tmp_path, naver).output.candidates if c.keyword == "리그오브레전드"
    )
    assert lol.naver.status == "normal" and lol.naver_from == "group"
    assert [v.status for v in lol.name_verdicts] == ["normal", "unknown"]
    assert lol.rising_names == []


def test_pick_verdict_takes_the_biggest_z_among_rising_names() -> None:
    from ax_contracts.schemas.data import TrendEvidence, TrendVerdict

    def v(name: str, status: str, z: float = 0.0) -> TrendVerdict:
        return TrendVerdict(
            group_name=name, status=status, ratio=1.0, evidence=TrendEvidence(score=z)
        )  # type: ignore[arg-type]

    group = v("묶음", "normal")
    chosen, source, rising = pick_verdict(group, [v("가", "rising", 4.0), v("나", "rising", 6.0)])
    assert (chosen.group_name, source, rising) == ("나", "name", ["가", "나"])
    assert pick_verdict(group, [])[1] == "group"


# -------------------------------------------------- 지난 기간 판정 = 그 시점 스냅샷


def test_latest_popular_can_look_back_to_a_time(tmp_path: Path) -> None:
    path = _db(tmp_path, [("old", 1, ("옛날",))], at=SNAP_AT - timedelta(days=2))
    store = SnapshotStore(path)
    store.save(SNAP_AT, [Row(_stat("new", 1, ("요즘",)), ("popular",))])
    store.close()
    assert latest_popular(path).collected_at == SNAP_AT  # type: ignore[union-attr]
    back = latest_popular(path, SNAP_AT - timedelta(days=1))
    assert back is not None and [v.video_id for v in back.videos] == ["old"]
    assert latest_popular(path, SNAP_AT - timedelta(days=3)) is None


def _two_snapshots(tmp_path: Path) -> Path:
    """10-24 23시 KST(그 주 끝)와 11-01 23시 KST 두 번 받은 DB. 태그가 다르다."""
    first = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)
    path = _db(tmp_path, [("a", 1, ("가을",)), ("b", 2, ("가을",))], at=first)
    store = SnapshotStore(path)
    store.save(SNAP_AT, [Row(_stat("c", 1, ("t1",)), ("popular",)),
                         Row(_stat("d", 2, ("t1",)), ("popular",))])  # fmt: skip
    store.close()
    return path


def test_a_past_period_uses_the_snapshot_from_that_time(tmp_path: Path) -> None:
    path = _two_snapshots(tmp_path)
    env = {"DATA_YOUTUBE_DB": str(path), "DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)}
    past = TrendCandidatesRequest(end_date=date(2026, 10, 24))
    out = asyncio.run(find_candidates(past, Naver({}), env=env, now=NOW)).output
    assert out.snapshot_at == datetime(2026, 10, 24, 14, 0, tzinfo=UTC)
    assert [c.keyword for c in out.candidates] == ["가을"]
    now = asyncio.run(find_candidates(TrendCandidatesRequest(), Naver({}), env=env, now=NOW)).output
    assert now.snapshot_at == SNAP_AT and [c.keyword for c in now.candidates] == ["t1"]


def test_a_past_period_without_a_snapshot_then_is_unknown(tmp_path: Path) -> None:
    path = _two_snapshots(tmp_path)
    env = {"DATA_YOUTUBE_DB": str(path), "DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)}
    naver = Naver({})
    early = TrendCandidatesRequest(end_date=date(2026, 10, 11))
    out = asyncio.run(find_candidates(early, naver, env=env, now=NOW)).output
    assert out.status == "unknown" and out.candidates == [] and naver.queries == []
    assert out.unknown_reason == (
        "그 시점(2026-10-12 0시 KST) 이전 YouTube 스냅샷이 없음"
        " (지난 기간은 그때 스냅샷의 태그로만 본다)"
    )


def test_a_past_period_snapshot_too_far_before_is_old(tmp_path: Path) -> None:
    path = _two_snapshots(tmp_path)
    env = {"DATA_YOUTUBE_DB": str(path), "DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)}
    later = TrendCandidatesRequest(end_date=date(2026, 10, 28))  # 10-24 스냅샷은 4일 전
    out = asyncio.run(find_candidates(later, Naver({}), env=env, now=NOW)).output
    assert out.status == "unknown" and "판정 기간 끝보다" in (out.unknown_reason or "")
