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


def _setup(tmp: Path, target: float, peer: float,
           insights: list[dict] | None = None) -> tuple[dict, object]:  # fmt: skip
    """브리프 저장분(인사이트)·숏츠 통계. 같은 형식 숏츠 스냅샷도 늘 임시 폴더 (실제 파일 X)."""
    from ax_contracts.schemas.data import ShortsGroup, ShortsStatsOutput
    from data_agent.skills import video_performance as vp

    conn = sqlite3.connect(tmp / "brief.db")
    conn.execute("CREATE TABLE briefs (day TEXT, rules TEXT, created_at TEXT, output TEXT,"
                 " expires_at TEXT)")  # fmt: skip
    rows = [{"insight_id": i["insight_id"], "rank": n + 1, "topic": i["topic"], "line": "x",
             "shorts_format": i["format"], "target_views_reference": i["target"]}
            for n, i in enumerate(insights or [{"insight_id": "ins_eval", "topic": "평가",
                                                "format": "리뷰", "target": target}])]  # fmt: skip
    conn.execute("INSERT INTO briefs VALUES ('2026-11-06','r','x',?,NULL)",
                 (json.dumps({"insights": rows}),))  # fmt: skip
    conn.commit()
    conn.close()
    shorts = ShortsStatsOutput(status="known", snapshot_at=PUB, by_format=[
        ShortsGroup(name="리뷰", count=20, median_views=peer)])  # fmt: skip
    env = {vp.DB_ENV: str(tmp / "perf.db"), "DATA_INSIGHT_BRIEF_DB": str(tmp / "brief.db"),
           "DATA_YOUTUBE_DB": str(tmp / "youtube.db"),
           "DATA_SHORTS_DB": str(tmp / "shorts.db")}  # fmt: skip
    return env, shorts


def _peer_snapshots(tmp: Path, peers: list[dict]) -> None:
    """같은 형식 숏츠: 형식 분류(shorts.db)와 수집기 스냅샷(youtube.db, 1시간마다)."""
    from data_agent.skills.shorts_stats import ShortsStore
    from data_agent.youtube_store import SnapshotStore

    ShortsStore(tmp / "shorts.db").close()
    SnapshotStore(tmp / "youtube.db").close()
    shorts = sqlite3.connect(tmp / "shorts.db")
    yt = sqlite3.connect(tmp / "youtube.db")
    for p in peers:
        shorts.execute("INSERT INTO formats VALUES (?,?,NULL,?)", (p["id"], p["format"],
                                                                   PUB.isoformat()))  # fmt: skip
        pub = PUB + timedelta(hours=p["pub"])
        for age, views, likes, comments in make.measurements(p, p["pub"]):
            at = (pub + timedelta(hours=age)).replace(microsecond=0).isoformat()
            row = (at, p["id"], int(views), int(likes), int(comments), pub.isoformat())
            yt.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,NULL,?,'24','ch','[]','popular')",
                       row)  # fmt: skip
    shorts.commit()
    yt.commit()
    shorts.close()
    yt.close()


def _run_collector(videos: list[dict], tmp: Path, target: float, peer: float,
                   insights: list[dict] | None = None, list_all: bool = False) -> dict:  # fmt: skip
    """skill을 수집기처럼 돌린다: 영상마다 첫 측정에 등록, 측정 시각마다 measure_due, 끝에 결과."""
    from ax_contracts.schemas.data import VideoPerformanceRequest
    from data_agent.skills import video_performance as vp

    env, shorts = _setup(tmp, target, peer, insights)
    yt = CurveYouTube(videos)
    clocks = sorted({round(p[0] + v["pub"], 2) for v in videos for p in v["measurements"]})
    first = {v["id"]: round(v["measurements"][0][0] + v["pub"], 2) for v in videos}
    store = vp.store_of(env)

    def req(v: dict, url: bool) -> VideoPerformanceRequest:
        return VideoPerformanceRequest(
            video_url=f"https://youtu.be/{v['id']}" if url else None,
            campaign_id=f"cmp_{v['id']}", insight_id=v.get("insight", "ins_eval"))  # fmt: skip

    try:
        for clock in clocks:
            yt.now = PUB + timedelta(hours=clock)
            new = [v for v in videos if first[v["id"]] == clock]
            for v in new:
                asyncio.run(vp.video_performance(req(v, True), yt, env=env, shorts=shorts,
                                                 store=store, now=yt.now))  # fmt: skip
            if not new:
                asyncio.run(vp.measure_due(store, yt, yt.now))
        last = (VideoPerformanceRequest.model_validate({"list_all": True}) if list_all
                else req(videos[0], False))  # fmt: skip
        out = asyncio.run(vp.video_performance(last, yt, env=env, shorts=shorts, store=store,
                                               now=yt.now))  # fmt: skip
    finally:
        store.close()
    return out.output.model_dump(mode="json")


def _scenario_answer(case: dict, tmp: Path) -> dict:
    """수집기처럼 돌린 뒤 list_all 답. 계약에 list_all이 없으면(평가 정의만 있는 dev) 0점."""
    from pydantic import ValidationError

    videos = [{**v, "measurements": make.measurements(v, v["pub"])} for v in case["videos"]]
    _peer_snapshots(tmp, case["peers"])
    try:
        return _run_collector(videos, tmp, 0.0, case["peer"], case["insights"], list_all=True)
    except ValidationError as error:
        return {"error": str(error)}


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


GATES = {  # 관문: 시점·값은 모두 맞고, 배수 오차 0, 시점별 집계 모두 맞음 (기준선 낮추지 않음)
    "video_performance.status_accuracy": 1.0,
    "video_performance.value_exact_rate": 1.0,
    "video_performance.ratio_max_error": 0.0,
    "video_performance.aggregate_accuracy": 1.0,
}


def test_the_scenario_answers_come_from_the_curves() -> None:
    """성과 요인 시나리오 정답 (①~⑤)."""
    (case,) = make.scenario_cases()
    by = {a["name"]: a for a in case["answers"]}
    avg = by["평범한 영상"]
    assert avg["verdict"] == "missed" and avg["checkpoints"]["7d"]["peer_age_ratio"] <= 1  # ①
    assert avg["checkpoints"]["7d"]["views"] / case["peer"] >= 1.9  # 시간 안 맞추면 2배로 반대
    assert "주제는 같으니" in avg["phrases"] and "10.1배" in avg["numbers"]  # ②
    gap = by["수집 멈춘 영상"]
    assert gap["levels"]["grow"] == "unknown" and "데이터 문제" in gap["phrases"]  # ③
    late = by["늦게 올린 영상"]
    assert late["verdict"] == "measuring" and late["projection"]["is_estimate"]  # ④
    assert set(by["강의 영상"]["levels"].values()) == {"unknown"}  # ⑤ 강의 표본 3개
    assert make.build_scenario() == case


SCENARIO_GATES = dict.fromkeys(score.SCENARIO_METRICS, 1.0)  # 모두 맞아야 함 (기준선 그대로)


def test_the_skill_passes_the_factor_scenario_gates(tmp_path: Path) -> None:
    """관문: 시간 맞춤 기준·판정·신호 등급·문장 숫자·문구·예상치 표기가 모두 정답과 같다."""
    answers = {}
    for c in make.scenario_cases():
        folder = tmp_path / c["name"]
        folder.mkdir()
        answers[c["name"]] = _scenario_answer(c, folder)
    metrics = score.score_scenario(make.scenario_cases(), answers)
    print("video_performance scenario:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == set(score.SCENARIO_METRICS)
    for name, gate in SCENARIO_GATES.items():
        assert metrics[name] >= gate, f"{name} {metrics[name]} (관문 {gate})"


def test_the_scenario_scorer() -> None:
    (case,) = make.scenario_cases()
    perfect = {"videos": [
        {"video_id": a["id"], "verdict": a["verdict"],
         "checkpoints": [{"checkpoint": k, **v} for k, v in a["checkpoints"].items()],
         "signals": {s: {"level": lv} for s, lv in a["levels"].items()},
         "factors": [" ".join(a["numbers"] + a["phrases"])], "projection": a["projection"]}
        for a in case["answers"]]}  # fmt: skip
    got = score.score_scenario([case], {"scenario": perfect})
    assert got == dict.fromkeys(score.SCENARIO_METRICS, 1.0)
    assert score.score_scenario([case], {}) == dict.fromkeys(score.SCENARIO_METRICS, 0.0)


def test_the_skill_passes_the_gates_on_the_curves(tmp_path: Path) -> None:
    metrics = _observe(tmp_path)
    print("video_performance eval:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == {*score.METRICS, score.AGGREGATE_METRIC}
    for name, gate in GATES.items():  # 오차는 작을수록, 나머지는 클수록 좋다
        ok = metrics[name] <= gate if name.endswith("_error") else metrics[name] >= gate
        assert ok, f"{name} {metrics[name]} (관문 {gate})"
