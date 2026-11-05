"""campaign_effect 유입경로 평가 관문 (#648).

채점기(`eval/campaign_attribution/score.py`)로 유입 판정을 채점해
기준(`eval/campaign_attribution/baseline.json`)보다 나빠지면 실패한다.

평가 정의 PR에서는 관찰만 했고, 이 skill PR에서 관문을 켠다. 기준은 판정 코드를 고쳐 숫자가 좋아졌을
때만 새로 저장한다(낮추지 않는다). holdout은 결과 숫자만 본다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from data_agent.skills.campaign_effect import eval_attribution_judge

ATTR = Path(__file__).resolve().parents[1] / "eval" / "campaign_attribution"
LOWER_IS_BETTER = (
    "campaign_attribution.share_error",
    "holdout.campaign_attribution.share_error",
)

pytestmark = pytest.mark.eval


def _score():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("attribution_gate", ATTR / "score.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_attribution_judge_is_not_worse_than_the_baseline() -> None:
    baseline = json.loads((ATTR / "baseline.json").read_text(encoding="utf-8"))["metrics"]
    metrics = asyncio.run(_score().run(eval_attribution_judge))["metrics"]
    worse = []
    for name, want in baseline.items():
        if want is None:
            continue
        got = metrics[name]
        if name in LOWER_IS_BETTER and (got is None or got > want):
            worse.append(f"{name}: {got} > 기준 {want}")
        elif name not in LOWER_IS_BETTER and (got is None or got < want):
            worse.append(f"{name}: {got} < 기준 {want}")
    assert not worse, worse
