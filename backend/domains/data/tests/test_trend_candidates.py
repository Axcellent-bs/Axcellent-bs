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
SNAP_AT = NOW - timedelta(hours=1)
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
    assert load_config({}) == CandidateConfig()  # 설정이 없으면 기본값, 제외 목록 없음


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


def _calm(day: date) -> float:
    return 50.0 * (1 + 0.05 * math.sin(day.toordinal() * 2.1))


def _points(spike: float = 1.0) -> tuple[tuple[date, float], ...]:
    out, day = [], START
    while day <= END:
        v = _calm(day) * (spike if day > END - timedelta(days=3) else 1.0)
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
        series = TrendSeries(name, (name,), _points(self.spikes.get(name, 1.0)))
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
    assert out.naver_calls == 3 == len(naver.queries)
    assert [q.keyword_groups[0].keywords for q in naver.queries] == [["t1"], ["롤"], ["젠지"]]
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
    assert [c.keyword for c in out.candidates] == ["t1"] and out.naver_calls == 1


def test_a_naver_failure_makes_only_that_candidate_unknown(tmp_path: Path) -> None:
    result = _run(_db(tmp_path, VIDEOS), Naver({"t1": 3.0}, fail={"롤"}))
    by = {c.keyword: c.naver for c in result.output.candidates}
    assert by["t1"].status == "rising"
    assert by["롤"].status == "unknown"
    assert by["롤"].unknown_reason == "네이버 검색어 트렌드를 읽지 못함 [UNAVAILABLE]"
    assert by["젠지"].status == "normal"
    assert result.output.naver_calls == 3  # 실패도 부른 수에 든다
    assert "판정 불가 1개" in result.output.summary
    assert "네이버를 읽지 못한 후보: 롤" in result.evidence.data_summary


def test_no_snapshot_is_unknown_with_a_reason(tmp_path: Path) -> None:
    naver = Naver({})
    out = _run(tmp_path / "missing.db", naver).output
    assert out.status == "unknown" and out.candidates == [] and out.snapshot_at is None
    assert (out.unknown_reason or "").startswith("YouTube 스냅샷이 없음")
    assert naver.queries == []  # 네이버를 부르지 않는다


def test_a_db_with_only_tracked_videos_has_no_popular_snapshot(tmp_path: Path) -> None:
    out = _run(_db(tmp_path, [("tr", None, ("t1",))]), Naver({})).output
    assert out.status == "unknown"
    assert (out.unknown_reason or "").startswith("YouTube 스냅샷이 없음")


def test_an_old_snapshot_is_unknown_with_its_age(tmp_path: Path) -> None:
    naver = Naver({})
    old = NOW - timedelta(hours=30)
    out = _run(_db(tmp_path, VIDEOS, at=old), naver).output
    assert out.status == "unknown" and out.candidates == []
    assert out.snapshot_at == old
    assert out.unknown_reason == (
        "YouTube 스냅샷이 오래됨: 마지막 수집 2026-11-01T00:00:00+00:00 (30.0시간 전, 기준 24시간)"
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
        handlers.HANDLERS["trend_candidates"](_request({"end_date": "2026-11-01"}))
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
