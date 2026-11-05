"""이름 자동 묶기: 같은 영상에 함께 나오고 네이버 일별 값(로그) 상관이 매우 높으면 같은 대상.

실제 네이버를 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ax_contracts.schemas.data import (
    TrendCandidatesRequest,
    TrendPoint,
    TrendScanInput,
    TrendVerdict,
)
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.connectors.youtube import VideoStat
from data_agent.skills.trend_candidates import (
    CandidateConfig,
    Judged,
    components,
    find_candidates,
    load_config,
    log_correlation,
    name_pairs,
    shared_videos,
)
from data_agent.skills.trend_scan import REQUEST_DAYS
from data_agent.youtube_store import HistoryVideo, Row, SnapshotStore

NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
SNAP_AT = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
END = date(2026, 11, 1)
START = END - timedelta(days=REQUEST_DAYS - 1)
CATBOOKS_TRENDS = (
    Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "trends.toml"
)


def _wave(i: int, shift: float = 0.0, scale: float = 1.0, noise: float = 0.0) -> float:
    return scale * (40 + 10 * math.sin(i / 3 + shift) + noise * math.sin(i * 7.7))


def _verdict(name: str, values: list[float]) -> TrendVerdict:
    series = [TrendPoint(day=START + timedelta(days=i), value=v) for i, v in enumerate(values)]
    return TrendVerdict(group_name=name, status="normal", ratio=1.0, series=series)


def _judged(name: str, values: list[float]) -> Judged:
    v = _verdict(name, values)
    return Judged([name], v, "group", [], [], v)


N = REQUEST_DAYS
SAME = [_wave(i) for i in range(N)]


def test_log_correlation_ignores_request_scale_and_zero_days() -> None:
    a = _verdict("a", SAME)
    b = _verdict("b", [v * 0.01 for v in SAME])  # 같은 모양, 다른 요청 배수
    r, days = log_correlation(a, b)
    assert r == 1.0 and days == N
    holes = _verdict("c", [0.0 if i % 2 else v for i, v in enumerate(SAME)])
    assert log_correlation(a, holes)[1] == N // 2 + N % 2  # 0인 날은 뺀다
    flat = _verdict("d", [5.0] * N)
    assert log_correlation(a, flat) == (None, N)


def test_shared_videos_count_names_on_the_same_video_from_tags_and_titles() -> None:
    history = [
        HistoryVideo("1", "20", ("LoL", "T1"), "", frozenset()),
        HistoryVideo("2", "20", ("t1",), "페이커 하이라이트 모음", frozenset()),
        HistoryVideo("3", "20", ("젠지",), "", frozenset()),
    ]
    config = CandidateConfig(titles=True, aliases={"리그오브레전드": ("lol",)})
    got = shared_videos(history, ["리그오브레전드", "t1", "페이커 하이라이트", "젠지"], config)
    assert got == {
        frozenset({"리그오브레전드", "t1"}): 1,
        frozenset({"t1", "페이커 하이라이트"}): 1,
    }


def test_names_that_differ_only_by_spaces_are_counted_separately_for_tags() -> None:
    history = [
        HistoryVideo("1", "20", ("리그오브레전드", "lol"), "", frozenset()),
        HistoryVideo("2", "20", ("리그 오브 레전드", "lol"), "", frozenset()),
    ]
    got = shared_videos(history, ["리그오브레전드", "리그 오브 레전드", "lol"], CandidateConfig())
    assert got == {
        frozenset({"리그오브레전드", "lol"}): 1,
        frozenset({"리그 오브 레전드", "lol"}): 1,
    }


GROUP = CandidateConfig(auto_group=True, group_merge_corr=0.9, group_suggest_corr=0.7)


def test_pairs_need_shared_videos_enough_days_and_high_correlation() -> None:
    judged = [
        ("a", _judged("a", SAME)),
        ("b", _judged("b", [v * 3 for v in SAME])),  # 상관 1 → 묶음
        ("c", _judged("c", [_wave(i, shift=0.5) for i in range(N)])),  # 조금 어긋남 → 제안
        ("d", _judged("d", [_wave(i, shift=3.1) for i in range(N)])),  # 반대 → 없음
    ]
    shared = {frozenset("ab"): 2, frozenset("ac"): 1, frozenset("ad"): 1}
    pairs = name_pairs(judged, shared, GROUP)
    assert [(p.names, p.applied) for p in pairs] == [(["a", "b"], True), (["a", "c"], False)]
    assert 0.7 <= pairs[1].correlation < 0.9 and pairs[0].shared_videos == 2
    # 함께 나온 적이 없으면 상관이 1이어도 묶지 않는다 (b·c 쌍은 shared에 없음)
    assert all(p.names != ["b", "c"] for p in pairs)
    short = CandidateConfig(auto_group=True, group_min_days=N + 1)
    assert name_pairs(judged, shared, short) == []


def test_components_follow_applied_pairs_only_and_keep_order() -> None:
    from ax_contracts.schemas.data import NamePairEvidence

    pairs = [
        NamePairEvidence(
            names=["b", "a"], correlation=0.95, days=60, shared_videos=1, applied=True
        ),
        NamePairEvidence(
            names=["c", "b"], correlation=0.93, days=60, shared_videos=1, applied=True
        ),
        NamePairEvidence(
            names=["d", "e"], correlation=0.8, days=60, shared_videos=1, applied=False
        ),
    ]
    assert components(["a", "b", "c", "d", "e"], pairs) == [["a", "b", "c"]]


def test_company_config_groups_conservatively() -> None:
    config = load_config({"DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)})
    assert config.auto_group and config.group_merge_corr >= 0.9
    assert config.group_suggest_corr < config.group_merge_corr
    assert config.group_min_days >= 28
    assert config.aliases  # 수동 별칭 표는 덮어쓰기용으로 남는다


# ---------------------------------------------------------------- skill


def _series(name: str, spike: float = 1.0) -> tuple[tuple[date, float], ...]:
    out = []
    for i in range(N):
        v = _wave(i, scale=1.0)
        if name in {"페이커", "faker 영상"} and i >= N - 3:
            v *= spike
        out.append((START + timedelta(days=i), round(v, 4)))
    return tuple(out)


class Naver:
    """'다른'은 다른 모양, 나머지는 같은 모양(상관 1). '페이커'만 마지막 3일 급상승."""

    def __init__(self) -> None:
        self.queries: list[TrendScanInput] = []

    async def search_trend(self, query: TrendScanInput) -> TrendResponse:
        self.queries.append(query)
        name = query.keyword_groups[0].group_name
        if name == "다른":
            points = tuple(
                (START + timedelta(days=i), round(_wave(i, shift=3.1), 4)) for i in range(N)
            )
        else:
            spike = 3.0 if name == "페이커" else 1.0
            points = _series(name, spike)
        return TrendResponse(
            query.start_date, query.end_date, query.time_unit, (TrendSeries(name, (name,), points),)
        )


def _stat(vid: str, rank: int, tags: tuple[str, ...]) -> VideoStat:
    return VideoStat(
        video_id=vid, channel_id="c", category_id="20", published_at=NOW - timedelta(days=1),
        view_count=1, like_count=1, comment_count=1, tags=tags, rank=rank,
    )  # fmt: skip


def test_names_that_move_together_on_shared_videos_become_one_candidate(tmp_path: Path) -> None:
    db = tmp_path / "youtube.db"
    store = SnapshotStore(db)
    store.save(SNAP_AT, [
        Row(_stat("a", 1, ("이상호", "축구")), ("popular",)),
        Row(_stat("b", 2, ("이상호", "축구")), ("popular",)),
        Row(_stat("c", 3, ("페이커", "다른")), ("popular",)),
        Row(_stat("d", 4, ("페이커", "다른")), ("popular",)),
    ])  # fmt: skip
    store.close()
    config = tmp_path / "trends.toml"
    config.write_text(
        "[candidates]\nmax_candidates = 5\n[candidates.auto_group]\n"
        "merge_corr = 0.9\nsuggest_corr = 0.7\nmin_days = 28\n",
        encoding="utf-8",
    )
    naver = Naver()
    env = {"DATA_YOUTUBE_DB": str(db), "DATA_TREND_CONFIG": str(config)}
    out = asyncio.run(
        find_candidates(TrendCandidatesRequest(end_date=END), naver, env=env, now=NOW)
    ).output
    # 이상호·축구: 같은 영상 + 상관 1 → 묶음. 페이커·다른: 같은 영상이지만 모양이 반대 → 따로
    by = {c.keyword: c for c in out.candidates}
    assert set(by) == {"이상호", "페이커", "다른"}
    lee = by["이상호"]
    assert lee.auto_grouped == ["축구"] and lee.aliases == ["축구"]
    assert lee.youtube.video_count == 2 and lee.youtube.matched_tags == ["이상호", "축구"]
    # 묶음 다시 판정 (#375 규칙): 묶음 그룹 1번 + 이름별 판정은 이미 받은 각 이름 판정
    assert [g.group_name for g in lee.name_verdicts] == ["이상호", "축구"]
    assert [(p.names, p.applied) for p in out.auto_groups] == [(["이상호", "축구"], True)]
    assert out.group_suggestions == []
    # 네이버: 후보 4개 각 1번 + 묶음 1번
    assert out.naver_calls == 5 == len(naver.queries)
    assert naver.queries[-1].keyword_groups[0].keywords == ["이상호", "축구"]
    assert out.summary.endswith("자동 묶음 1쌍.")


def test_grouping_is_off_without_config(tmp_path: Path) -> None:
    db = tmp_path / "youtube.db"
    store = SnapshotStore(db)
    store.save(SNAP_AT, [Row(_stat("a", 1, ("이상호", "축구")), ("popular",)),
                         Row(_stat("b", 2, ("이상호", "축구")), ("popular",))])  # fmt: skip
    store.close()
    env = {"DATA_YOUTUBE_DB": str(db)}
    out = asyncio.run(
        find_candidates(TrendCandidatesRequest(end_date=END), Naver(), env=env, now=NOW)
    ).output
    assert sorted(c.keyword for c in out.candidates) == ["이상호", "축구"]
    assert out.auto_groups == [] and out.naver_calls == 2


# ---------------------------------------------------------------- 같은 대상 (형식 빌리기 판정)


def test_same_target_comes_from_applied_auto_groups_only() -> None:
    """자동 묶음(applied)만 같은 대상. 제안(applied=false)·이름 하나는 쓰지 않는다."""
    from data_agent.skills.trend_candidates import auto_same_targets

    output = {"auto_groups": [
        {"names": ["메이플", "메이플스토리"], "correlation": 0.9752, "applied": True},
        {"names": ["리그오브레전드", "t1"], "correlation": 0.82, "applied": False},  # 제안
        {"names": ["메이플", "#MapleStory "], "applied": True},
        {"names": ["혼자"], "applied": True},
    ]}  # fmt: skip
    assert auto_same_targets(output) == {"메이플": ("메이플스토리", "maplestory")}
    assert auto_same_targets(None) == {} and auto_same_targets({}) == {}


def test_brief_same_targets_are_auto_groups_plus_override(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    from data_agent import handlers
    from data_agent.response_cache import ResponseCache

    cache = ResponseCache(tmp_path / "responses.db")
    groups = [{"names": ["메이플", "메이플스토리"], "applied": True}]
    cache.put("k1", ["known", {"auto_groups": []}, {}], "trend_candidates")
    cache.put("k2", ["known", {"auto_groups": groups}, {}], "trend_candidates")  # 최근 것
    monkeypatch.setattr(handlers, "RESPONSES", cache)
    cfg = tmp_path / "trends.toml"
    cfg.write_text("[candidates]\n", encoding="utf-8")
    assert handlers.cand_same_targets({"DATA_TREND_CONFIG": str(cfg)}) == {
        "메이플": ("메이플스토리",)}  # fmt: skip
    # 사람 덮어쓰기: 자동으로는 안 묶이는 쌍을 더한다
    cfg.write_text('[candidates.same_target]\n"리그오브레전드" = ["t1"]\n', encoding="utf-8")
    assert handlers.cand_same_targets({"DATA_TREND_CONFIG": str(cfg)}) == {
        "메이플": ("메이플스토리",), "리그오브레전드": ("t1",)}  # fmt: skip
    assert ResponseCache().latest("trend_candidates") is None  # 메모리 저장은 없음
