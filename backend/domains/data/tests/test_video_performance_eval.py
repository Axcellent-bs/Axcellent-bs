"""video_performance 평가 정의(`eval/video_performance/`)의 약속.

- 정답은 합성 조회수 곡선과 수집기 측정 시각에서 나온다 (잘 된 영상·평범한 영상·데이터 끊김)
- 채점기: 정답 그대로면 1.0·1.0·오차 0, 값이 빠지면 틀림
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
    assert by["good"]["1d"]["target_ratio"] > 6 and by["good"]["1h"]["hours_after"] == 1.25
    assert by["average"]["7d"]["target_ratio"] == 0.8  # 총량 4000 ÷ 목표 5000 근처
    assert by["gap"]["1d"] == {"status": "unknown", "reason_word": "놓침"}  # 20~31시간 멈춤
    assert by["gap"]["3d"] == by["average"]["3d"]  # 끊김 뒤는 평범한 영상과 같다
    for case in make.cases():  # 같은 명세면 늘 같은 결과
        assert make.build(case["name"]) == case


def _perfect(case: dict) -> dict:
    cps = []
    for name, want in case["expected"].items():
        if want["status"] == "unknown":
            cps.append({"checkpoint": name, "status": "unknown", "reason": "놓침: 데이터 끊김"})
        else:
            cps.append({"checkpoint": name, **want})
    return {"checkpoints": cps}


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


class CurveYouTube:
    def __init__(self, case: dict) -> None:
        self.points = {p[0]: p for p in case["measurements"]}
        self.now = PUB

    async def videos(self, ids):  # noqa: ANN001, ANN201
        from data_agent.connectors.youtube import VideoStat

        hours = round((self.now - PUB).total_seconds() / 3600, 2)
        p = self.points[hours]
        return [VideoStat(i, "ch", "24", PUB, int(p[1]), int(p[2]), int(p[3]), ()) for i in ids]


def _skill_answer(case: dict, tmp: Path) -> dict:
    """skill을 수집기처럼 돌린다: 첫 측정에 등록, 측정 시각마다 measure_due, 끝에 결과."""
    from ax_contracts.schemas.data import ShortsGroup, ShortsStatsOutput, VideoPerformanceRequest
    from data_agent.skills import video_performance as vp

    conn = sqlite3.connect(tmp / "brief.db")
    conn.execute("CREATE TABLE briefs (day TEXT, rules TEXT, created_at TEXT, output TEXT,"
                 " expires_at TEXT)")  # fmt: skip
    insight = {"insight_id": "ins_eval", "rank": 1, "topic": "평가", "line": "x",
               "shorts_format": "리뷰", "target_views_reference": case["target"]}  # fmt: skip
    conn.execute("INSERT INTO briefs VALUES ('2026-11-06','r','x',?,NULL)",
                 (json.dumps({"insights": [insight]}),))  # fmt: skip
    conn.commit()
    conn.close()
    shorts = ShortsStatsOutput(status="known", snapshot_at=PUB, by_format=[
        ShortsGroup(name="리뷰", count=20, median_views=case["peer"])])  # fmt: skip
    env = {vp.DB_ENV: str(tmp / "perf.db"), "DATA_INSIGHT_BRIEF_DB": str(tmp / "brief.db")}
    yt = CurveYouTube(case)
    hours = [p[0] for p in case["measurements"]]
    req = VideoPerformanceRequest(video_url="https://youtu.be/abcdefghijk", insight_id="ins_eval")
    store = vp.store_of(env)
    try:
        for i, h in enumerate(hours):
            yt.now = PUB + timedelta(hours=h)
            if i == 0:
                asyncio.run(vp.video_performance(req, yt, env=env, shorts=shorts, store=store,
                                                 now=yt.now))  # fmt: skip
            else:
                asyncio.run(vp.measure_due(store, yt, yt.now))
        out = asyncio.run(vp.video_performance(req, yt, env=env, shorts=shorts, store=store,
                                               now=yt.now))  # fmt: skip
    finally:
        store.close()
    return out.output.model_dump(mode="json")


def test_observe_the_skill_on_the_curves(tmp_path: Path) -> None:
    """관찰만: 지표를 남긴다 (관문은 skill을 고치는 PR에서 켠다)."""
    cases = make.cases()
    answers = {}
    for c in cases:
        folder = tmp_path / c["name"]
        folder.mkdir()
        answers[c["name"]] = _skill_answer(c, folder)
    metrics = score.score(cases, answers)
    print("video_performance eval:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == set(score.METRICS)
