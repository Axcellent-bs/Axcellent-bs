"""trend_scan 평가 관문: 채점기(`eval/trend/score.py`)로 skill 판정을 채점해 기준
(`eval/trend/baseline.json`)보다 나빠지면 실패한다. 결제 평가 관문(`test_eval_gate.py`)과 따로다.

기준은 판정 코드를 고쳐 숫자가 좋아졌을 때만 새로 저장한다:
`uv run python domains/data/eval/trend/score.py --judge data_agent.skills.trend_scan:judge
--out <파일>`의 `metrics`를 `baseline.json`에 옮긴다.
holdout은 결과 숫자만 본다(케이스 내용을 열지 않는다).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from data_agent.skills.trend_scan import judge

TREND = Path(__file__).resolve().parents[1] / "eval" / "trend"
HIGHER_IS_BETTER = (
    "trend.detection_rate",
    "trend.unknown_handled_rate",
    "trend.group_isolation",
    "holdout.trend.detection_rate",
)
LOWER_IS_BETTER = ("trend.false_positive_rate", "holdout.trend.false_positive_rate")

pytestmark = pytest.mark.eval


def _score():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("trend_score_gate", TREND / "score.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_trend_scan_is_not_worse_than_the_baseline() -> None:
    baseline = json.loads((TREND / "baseline.json").read_text(encoding="utf-8"))["metrics"]
    metrics = asyncio.run(_score().run(judge))["metrics"]
    worse = [
        f"{name}: {metrics[name]} < 기준 {baseline[name]}"
        for name in HIGHER_IS_BETTER
        if metrics[name] < baseline[name]
    ] + [
        f"{name}: {metrics[name]} > 기준 {baseline[name]}"
        for name in LOWER_IS_BETTER
        if metrics[name] > baseline[name]
    ]
    assert worse == [], "trend 평가가 기준보다 나빠졌다: " + "; ".join(worse)
