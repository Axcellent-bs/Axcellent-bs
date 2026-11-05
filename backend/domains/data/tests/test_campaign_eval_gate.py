"""campaign_effect 평가 관문: 채점기(`eval/campaign/score.py`)로 skill 판정을 채점해 기준
(`eval/campaign/baseline.json`)보다 나빠지면 실패한다. 결제·trend 평가 관문과 따로다.

기준은 판정 코드를 고쳐 숫자가 좋아졌을 때만 새로 저장한다:
`uv run python domains/data/eval/campaign/score.py --out <파일>
--judge data_agent.skills.campaign_effect:eval_judge`의 `metrics`를 `baseline.json`에 옮긴다.
holdout은 결과 숫자만 본다(케이스 내용을 열지 않는다).
매출(`campaign.revenue_*`)은 관문에 넣지 않는다. 여러 날 케이스는 `multiday_metrics`,
4주 기준선 케이스(`multiday4/`)는 `multiday4_metrics`와 비교한다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from data_agent.skills.campaign_effect import eval_judge

CAMPAIGN = Path(__file__).resolve().parents[1] / "eval" / "campaign"
HIGHER_IS_BETTER = (
    "campaign.order_direction_rate",
    "campaign.unknown_handled_rate",
    "campaign.coupon_usage_match",
    "holdout.campaign.order_direction_rate",
    "holdout.campaign.coupon_usage_match",
)
LOWER_IS_BETTER = ("campaign.order_excess_error", "holdout.campaign.order_excess_error")

pytestmark = pytest.mark.eval


def _score():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("campaign_score_gate", CAMPAIGN / "score.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _worse(metrics: dict, baseline: dict) -> list[str]:
    return [
        f"{name}: {metrics[name]} < 기준 {baseline[name]}"
        for name in HIGHER_IS_BETTER
        if baseline[name] is not None and (metrics[name] is None or metrics[name] < baseline[name])
    ] + [
        f"{name}: {metrics[name]} > 기준 {baseline[name]}"
        for name in LOWER_IS_BETTER
        if baseline[name] is not None and (metrics[name] is None or metrics[name] > baseline[name])
    ]


def test_multiday_campaigns_are_not_worse_than_the_baseline() -> None:
    """여러 날 캠페인 케이스(`multiday/`)도 같은 채점기로 폴더만 넘겨 본다."""
    baseline = json.loads((CAMPAIGN / "baseline.json").read_text(encoding="utf-8"))
    report = asyncio.run(
        _score().run(
            eval_judge,
            cases_dir=CAMPAIGN / "multiday" / "cases",
            holdout_dir=CAMPAIGN / "multiday" / "holdout",
        )
    )
    worse = _worse(report["metrics"], baseline["multiday_metrics"])
    assert worse == [], "여러 날 campaign 평가가 기준보다 나빠졌다: " + "; ".join(worse)


def test_four_week_multiday_campaigns_are_not_worse_than_the_baseline() -> None:
    """4주 기준선 정의 케이스(`multiday4/`, 기록 21일 = 3구간 케이스 포함)도 같은 채점기로 본다."""
    baseline = json.loads((CAMPAIGN / "baseline.json").read_text(encoding="utf-8"))
    report = asyncio.run(
        _score().run(
            eval_judge,
            cases_dir=CAMPAIGN / "multiday4" / "cases",
            holdout_dir=CAMPAIGN / "multiday4" / "holdout",
        )
    )
    worse = _worse(report["metrics"], baseline["multiday4_metrics"])
    assert worse == [], "4주 여러 날 campaign 평가가 기준보다 나빠졌다: " + "; ".join(worse)


def test_campaign_effect_is_not_worse_than_the_baseline() -> None:
    baseline = json.loads((CAMPAIGN / "baseline.json").read_text(encoding="utf-8"))["metrics"]
    metrics = asyncio.run(_score().run(eval_judge))["metrics"]
    worse = [
        f"{name}: {metrics[name]} < 기준 {baseline[name]}"
        for name in HIGHER_IS_BETTER
        if baseline[name] is not None and (metrics[name] is None or metrics[name] < baseline[name])
    ] + [
        f"{name}: {metrics[name]} > 기준 {baseline[name]}"
        for name in LOWER_IS_BETTER
        if baseline[name] is not None and (metrics[name] is None or metrics[name] > baseline[name])
    ]
    assert worse == [], "campaign 평가가 기준보다 나빠졌다: " + "; ".join(worse)
