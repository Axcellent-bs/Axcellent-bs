"""campaign_effect 매출 평가 관문: 채점기(`eval/campaign_revenue/score.py`)로 판매액 판정을 채점해
기준(`eval/campaign_revenue/baseline.json`)보다 나빠지면 실패한다. 주문·쿠폰 관문과 따로다.

기준은 판정 코드를 고쳐 숫자가 좋아졌을 때만 새로 저장한다. holdout은 결과 숫자만 본다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from data_agent.skills.campaign_effect import eval_sales_judge

REVENUE = Path(__file__).resolve().parents[1] / "eval" / "campaign_revenue"
HIGHER_IS_BETTER = (
    "campaign_revenue.direction_rate",
    "campaign_revenue.gap_unknown_rate",
    "campaign_revenue.direct_match",
    "holdout.campaign_revenue.direction_rate",
    "holdout.campaign_revenue.gap_unknown_rate",
    "holdout.campaign_revenue.direct_match",
)
LOWER_IS_BETTER = ("campaign_revenue.excess_error", "holdout.campaign_revenue.excess_error")

pytestmark = pytest.mark.eval


def _score():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("campaign_revenue_gate", REVENUE / "score.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sales_judge_is_not_worse_than_the_baseline() -> None:
    baseline = json.loads((REVENUE / "baseline.json").read_text(encoding="utf-8"))["metrics"]
    metrics = asyncio.run(_score().run(eval_sales_judge))["metrics"]
    worse = [
        f"{n}: {metrics[n]} < 기준 {baseline[n]}"
        for n in HIGHER_IS_BETTER
        if baseline[n] is not None and (metrics[n] is None or metrics[n] < baseline[n])
    ] + [
        f"{n}: {metrics[n]} > 기준 {baseline[n]}"
        for n in LOWER_IS_BETTER
        if baseline[n] is not None and (metrics[n] is None or metrics[n] > baseline[n])
    ]
    assert not worse, worse
