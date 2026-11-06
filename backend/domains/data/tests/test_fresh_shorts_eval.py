"""새 숏츠 추적 평가 정의(`eval/fresh_shorts/`)의 약속.

- 가짜 YouTube 세상을 1시간마다 돌려 찾기·추적·기준 표본을 정답과 비교한다 (실제 API 호출 없음)
- 기준은 fresh_track 스냅샷 중 시점 창 안 것만 (인기 목록 스냅샷은 세지 않음), 60초 초과 제외,
  게시 8일 뒤 추적 종료, 하루 상한에서 찾기 먼저 멈춤, 표본 5개 미만이면 "기준 모으는 중 (n/5)"
- 이 PR에서는 관찰만 (관문은 코드 PR에서)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "fresh_shorts"
T0 = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)  # 한국 11월 6일 0시


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"fresh_shorts_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_world() -> None:
    window, cap, label = make.cases()
    counts = window["expected"]["snap_counts"]
    long_ones = [v["id"] for v in window["videos"] if v["seconds"] > make.MAX_SECONDS]
    assert long_ones and not set(long_ones) & set(counts)  # 60초 초과는 추적 안 함
    assert max(counts.values()) <= make.TRACK_HOURS + 1  # 게시 8일 뒤 종료
    assert window["expected"]["used"] <= 2500
    assert cap["expected"]["skipped_finds"] > 0 and cap["expected"]["used"] <= 450
    assert label["labels"][3]["want"] == "기준 모으는 중 (3/5)"
    assert make.cases() == [window, cap, label]


def test_the_scorer() -> None:
    cases = make.cases()
    perfect = {
        "window": dict(cases[0]["expected"]),
        "cap": dict(cases[1]["expected"]),
        "label": {"labels": {str(i["n"]): i["want"] for i in cases[2]["labels"]}},
    }
    assert score.score(cases, perfect) == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)


class FakeYouTube:
    """가짜 YouTube: 형식 검색(최신순, 게시 6시간 안)과 영상 조회. 장부에 단가대로 적는다."""

    def __init__(self, videos: list[dict], fresh: ModuleType, ledger: object) -> None:
        self.videos_by_id = {v["id"]: v for v in videos}
        self.by_query = {fresh.query_for(f): f for f in make.FORMATS}
        self.ledger = ledger
        self.now = T0

    def _hours(self) -> float:
        return (self.now - T0).total_seconds() / 3600

    def _charge(self, units: int, search: bool = False) -> None:
        from data_agent import youtube_quota

        self.ledger.add("fresh_shorts", units, search=search,
                        day=self.now.astimezone(youtube_quota.KST).date())  # fmt: skip

    def _stat(self, v: dict):  # noqa: ANN202
        from data_agent.connectors.youtube import VideoStat

        views = make.views_at(v, self._hours() - v["pub"])
        return VideoStat(v["id"], "ch", "24", T0 + timedelta(hours=v["pub"]), views,
                         int(views * v["like"]), int(views * v["comment"]), (), title=v["id"],
                         duration_seconds=v["seconds"])  # fmt: skip

    async def search_fresh_shorts(self, query, published_after, **_):  # noqa: ANN001, ANN003, ANN201
        self._charge(100, search=True)
        fmt = self.by_query[query]
        after = (published_after - T0).total_seconds() / 3600
        hits = sorted((v for v in self.videos_by_id.values() if v["format"] == fmt
                       and after < v["pub"] <= self._hours()), key=lambda v: -v["pub"])  # fmt: skip
        return await self.videos([v["id"] for v in hits]) if hits else []

    async def videos(self, ids):  # noqa: ANN001, ANN201
        unique = list(dict.fromkeys(ids))
        for _ in range(0, len(unique), 50):
            self._charge(1)
        return [self._stat(self.videos_by_id[i]) for i in unique if i in self.videos_by_id]


def _env(tmp: Path, cap: int) -> dict[str, str]:
    return {"DATA_YOUTUBE_DB": str(tmp / "youtube.db"), "DATA_SHORTS_DB": str(tmp / "shorts.db"),
            "DATA_FRESH_SHORTS_DB": str(tmp / "fresh.db"),
            "DATA_FRESH_SHORTS_DAILY_UNITS": str(cap)}  # fmt: skip


def _popular(tmp: Path, rows: list[dict]) -> None:
    """인기 목록에서 따로 본 스냅샷 (source popular)과 그 형식 분류: 기준에 세면 틀림."""
    from data_agent.skills.shorts_stats import ShortsStore
    from data_agent.youtube_store import SnapshotStore

    ShortsStore(tmp / "shorts.db").close()
    SnapshotStore(tmp / "youtube.db").close()
    sh, yt = sqlite3.connect(tmp / "shorts.db"), sqlite3.connect(tmp / "youtube.db")
    for r in rows:
        sh.execute("INSERT INTO formats VALUES (?,?,NULL,?)", (r["id"], r["format"],
                                                               T0.isoformat()))  # fmt: skip
        pub = T0 + timedelta(hours=r["pub"])
        for age in r["ages"]:
            at = (pub + timedelta(hours=age)).isoformat()
            yt.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,NULL,?,'24','ch','[]','popular')",
                       (at, r["id"], r["views"], 0, 0, pub.isoformat()))  # fmt: skip
    sh.commit()
    yt.commit()
    sh.close()
    yt.close()


def _run(case: dict, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict | None:
    """실제 모듈로 1시간마다 돌린다. 모듈이 없으면(평가 정의만 있는 dev) None = 0점."""
    try:
        from data_agent import fresh_shorts
    except ImportError:
        return None
    from data_agent import youtube_quota

    ledger = youtube_quota.Ledger(None)
    monkeypatch.setattr(youtube_quota, "LEDGER", ledger)
    env = _env(tmp, case["cap"])
    _popular(tmp, case["popular"])
    fmt_of = {v["id"]: v["format"] for v in case["videos"]}
    yt = FakeYouTube(case["videos"], fresh_shorts, ledger)
    finds = skipped = track_ticks = 0
    peak = 0
    for t in range(case["ticks"] + 1):
        yt.now = T0 + timedelta(hours=t)
        result = asyncio.run(fresh_shorts.run_tick(
            yt, env, yt.now, formats=list(make.FORMATS),
            classify=lambda stats: {s.video_id: fmt_of[s.video_id] for s in stats}))  # fmt: skip
        finds += result.searches
        skipped += result.skipped_searches
        track_ticks += 1 if result.tracked_calls else 0
        day = yt.now.astimezone(youtube_quota.KST).date()
        peak = max(peak, sum(u for c, u, _ in ledger.rows(day) if c == "fresh_shorts"))
    conn = sqlite3.connect(tmp / "youtube.db")
    snaps = dict(conn.execute("SELECT video_id, COUNT(*) FROM snapshots WHERE sources ="
                              " 'fresh_track' GROUP BY video_id").fetchall())  # fmt: skip
    conn.close()
    from data_agent.skills import video_baseline

    windows = {k: v for k, v in make.CHECKPOINTS.items()}
    loaded = video_baseline.load(env, windows)
    baseline = {f: {cp: dict(zip(("median", "n"), loaded[f].views(cp), strict=True))
                    for cp in make.CHECKPOINTS} for f in make.FORMATS if f in loaded}  # fmt: skip
    return {"snap_counts": snaps, "baseline": baseline, "used": peak, "finds": finds,
            "skipped_finds": skipped, "track_ticks": track_ticks}  # fmt: skip


def _labels() -> dict:
    from data_agent.skills.video_factors import level_of

    return {"labels": {str(n): level_of(100.0, None, n, "1시간 조회")[1] for n in range(5)}}


def observe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    cases = make.cases()
    answers: dict[str, dict | None] = {"label": _labels()}
    for case in cases[:2]:
        folder = tmp_path / case["name"]
        folder.mkdir()
        answers[case["name"]] = _run(case, folder, monkeypatch)
    return score.score(cases, answers)


GATES = dict.fromkeys(score.METRICS, 1.0)  # 모두 맞아야 함 (기준선 그대로)


def test_fresh_shorts_passes_the_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """관문: 창 일치(fresh_track·60초·8일), 하루 상한(찾기 먼저 멈춤), 기준 모으는 중 문구."""
    metrics = observe(tmp_path, monkeypatch)
    print("fresh_shorts eval:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == set(score.METRICS)
    for name, gate in GATES.items():
        assert metrics[name] >= gate, f"{name} {metrics[name]} (관문 {gate})"
