"""video_performance 평가 정의(`eval/video_performance/`)의 약속.

- 정답은 합성 조회수 곡선과 수집기 측정 시각에서 나온다 (잘 된 영상·평범한 영상·데이터 끊김)
- 목표 대비 배수는 목표 참고값이 정의된 시점(7일)에만 있다
- 섞인 시점 케이스: 같은 인사이트 영상 2개 중 하나가 늦게 게시돼 7일이 아직. 성과 모으기는
  시점마다 그 시점을 잰 영상만 센다 (7일 n=1, 3일 n=2)
- 채점기: 정답 그대로면 1.0·1.0·오차 0·1.0, 값이 빠지거나 없어야 할 값이 있으면 틀림
- skill을 같은 측정으로 돌려 지표를 관찰한다 (이 PR에서는 관문 없음, 값만 남긴다)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "video_performance"
PUB = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"video_perf_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_curves() -> None:
    by = {c["name"]: c["expected"] for c in make.cases()}
    assert by["good"]["1h"]["hours_after"] == 1.25 and by["good"]["1d"]["peer_ratio"] > 16
    assert by["average"]["7d"]["target_ratio"] == 0.8  # 총량 4000 ÷ 목표 5000 근처
    for e in by.values():  # 목표 대비는 7일에만
        assert all(e[c].get("target_ratio") is None for c in ("1h", "1d", "3d"))
    assert by["gap"]["1d"] == {"status": "unknown", "reason_word": "놓침"}  # 20~31시간 멈춤
    assert by["gap"]["3d"] == by["average"]["3d"]  # 끊김 뒤는 평범한 영상과 같다
    for case in make.cases():  # 같은 명세면 늘 같은 결과
        assert make.build(case["name"]) == case


def test_the_mixed_case_counts_only_videos_measured_at_that_horizon() -> None:
    (mixed,) = make.aggregate_cases()
    late = mixed["videos"][1]["expected"]
    assert late["3d"]["status"] == "known"
    assert late["7d"] == {"status": "unknown", "reason_word": "측정 중"}
    agg = mixed["aggregate"]
    seven = agg["7d"]
    assert (seven["n"], seven["excluded_measuring"], seven["low_sample"]) == (1, 1, True)
    assert agg["7d"]["median_views"] == mixed["videos"][0]["expected"]["7d"]["views"]
    assert (agg["3d"]["n"], agg["3d"]["excluded"]) == (2, 0)
    assert agg["3d"]["median_target_ratio"] is None  # 목표 대비는 7일 집계에만
    assert make.build_mixed() == mixed


def _perfect(case: dict) -> dict:
    cps = []
    for name, want in case["expected"].items():
        if want["status"] == "unknown":
            cps.append({"checkpoint": name, "status": "unknown",
                        "reason": f"{want['reason_word']}: 데이터 끊김"})  # fmt: skip
        else:
            cps.append({"checkpoint": name, **want})
    return {"checkpoints": cps}


def _perfect_aggregate(case: dict) -> dict:
    return {"performance_by_horizon": [
        {"key": "insight", "value": "ins_eval", "checkpoint": h, **row}
        for h, row in case["aggregate"].items()]}  # fmt: skip


def test_the_scorer() -> None:
    cases = make.cases()
    perfect = score.score(cases, {c["name"]: _perfect(c) for c in cases})
    assert perfect == {"video_performance.status_accuracy": 1.0,
                       "video_performance.value_exact_rate": 1.0,
                       "video_performance.ratio_max_error": 0.0}  # fmt: skip
    none = score.score(cases, {})
    assert none["video_performance.status_accuracy"] == 0.0
    assert none["video_performance.value_exact_rate"] == 0.0
    assert math.isinf(none["video_performance.ratio_max_error"])
    # 목표 시점이 아닌데 목표 대비가 있으면 틀림
    extra = {c["name"]: _perfect(c) for c in cases}
    extra["good"]["checkpoints"][1]["target_ratio"] = 6.653
    assert math.isinf(score.score(cases, extra)["video_performance.ratio_max_error"])
    agg = make.aggregate_cases()
    assert score.score_aggregate(agg, {c["name"]: _perfect_aggregate(c) for c in agg}) == {
        score.AGGREGATE_METRIC: 1.0}  # fmt: skip
    assert score.score_aggregate(agg, {}) == {score.AGGREGATE_METRIC: 0.0}
    mixed_in = _perfect_aggregate(agg[0])  # 7일에 늦은 영상까지 넣으면 틀림
    mixed_in["performance_by_horizon"][2]["n"] = 2
    assert score.score_aggregate(agg, {"mixed": mixed_in})[score.AGGREGATE_METRIC] < 1.0


class CurveYouTube:
    """수집기 시계(첫 영상 게시 뒤 시간)에 맞춰 영상마다 케이스의 측정값을 준다."""

    def __init__(self, videos: list[dict]) -> None:
        self.videos_by_id = {v["id"]: v for v in videos}
        self.points = {v["id"]: {p[0]: p for p in v["measurements"]} for v in videos}
        self.now = PUB

    async def videos(self, ids):  # noqa: ANN001, ANN201
        from data_agent.connectors.youtube import VideoStat

        clock = (self.now - PUB).total_seconds() / 3600
        out = []
        for i in ids:
            v = self.videos_by_id[i]
            p = self.points[i].get(round(clock - v["pub"], 2))
            if p is not None:
                out.append(VideoStat(i, "ch", "24", PUB + timedelta(hours=v["pub"]), int(p[1]),
                                     int(p[2]), int(p[3]), ()))  # fmt: skip
        return out


def _setup(tmp: Path, target: float, peer: float) -> tuple[dict, object]:
    from ax_contracts.schemas.data import ShortsGroup, ShortsStatsOutput
    from data_agent.skills import video_performance as vp

    conn = sqlite3.connect(tmp / "brief.db")
    conn.execute("CREATE TABLE briefs (day TEXT, rules TEXT, created_at TEXT, output TEXT,"
                 " expires_at TEXT)")  # fmt: skip
    insight = {"insight_id": "ins_eval", "rank": 1, "topic": "평가", "line": "x",
               "shorts_format": "리뷰", "target_views_reference": target}  # fmt: skip
    conn.execute("INSERT INTO briefs VALUES ('2026-11-06','r','x',?,NULL)",
                 (json.dumps({"insights": [insight]}),))  # fmt: skip
    conn.commit()
    conn.close()
    shorts = ShortsStatsOutput(status="known", snapshot_at=PUB, by_format=[
        ShortsGroup(name="리뷰", count=20, median_views=peer)])  # fmt: skip
    env = {vp.DB_ENV: str(tmp / "perf.db"), "DATA_INSIGHT_BRIEF_DB": str(tmp / "brief.db")}
    return env, shorts


def _run_collector(videos: list[dict], tmp: Path, target: float, peer: float) -> dict:
    """skill을 수집기처럼 돌린다: 영상마다 첫 측정에 등록, 측정 시각마다 measure_due, 끝에 결과."""
    from ax_contracts.schemas.data import VideoPerformanceRequest
    from data_agent.skills import video_performance as vp

    env, shorts = _setup(tmp, target, peer)
    yt = CurveYouTube(videos)
    clocks = sorted({round(p[0] + v["pub"], 2) for v in videos for p in v["measurements"]})
    first = {v["id"]: round(v["measurements"][0][0] + v["pub"], 2) for v in videos}
    store = vp.store_of(env)

    def req(v: dict, url: bool) -> VideoPerformanceRequest:
        return VideoPerformanceRequest(
            video_url=f"https://youtu.be/{v['id']}" if url else None,
            campaign_id=f"cmp_{v['id']}", insight_id="ins_eval")  # fmt: skip

    try:
        for clock in clocks:
            yt.now = PUB + timedelta(hours=clock)
            new = [v for v in videos if first[v["id"]] == clock]
            for v in new:
                asyncio.run(vp.video_performance(req(v, True), yt, env=env, shorts=shorts,
                                                 store=store, now=yt.now))  # fmt: skip
            if not new:
                asyncio.run(vp.measure_due(store, yt, yt.now))
        out = asyncio.run(vp.video_performance(req(videos[0], False), yt, env=env, shorts=shorts,
                                               store=store, now=yt.now))  # fmt: skip
    finally:
        store.close()
    return out.output.model_dump(mode="json")


def _skill_answer(case: dict, tmp: Path) -> dict:
    video = {"id": "abcdefghijk", "pub": 0.0, "measurements": case["measurements"]}
    return _run_collector([video], tmp, case["target"], case["peer"])


def _observe(tmp_path: Path) -> dict[str, float]:
    cases = make.cases()
    answers = {}
    for c in cases:
        folder = tmp_path / c["name"]
        folder.mkdir()
        answers[c["name"]] = _skill_answer(c, folder)
    metrics = score.score(cases, answers)
    agg_answers = {}
    for c in make.aggregate_cases():
        folder = tmp_path / c["name"]
        folder.mkdir()
        agg_answers[c["name"]] = _run_collector(c["videos"], folder, c["target"], c["peer"])
    metrics |= score.score_aggregate(make.aggregate_cases(), agg_answers)
    return metrics


def test_observe_the_skill_on_the_curves(tmp_path: Path) -> None:
    """관찰만: 지표를 남긴다 (관문은 skill을 고치는 PR에서 켠다)."""
    metrics = _observe(tmp_path)
    print("video_performance eval:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == {*score.METRICS, score.AGGREGATE_METRIC}
