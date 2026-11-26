"""VideoPlan.goal (11/26 K0): 영상 목적 4가지. 추가만, 기본값 None이라 예전 제작안 그대로 읽힌다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.planning import VideoPlan

SCENE = {"n": 1, "start_s": 0, "end_s": 15, "visual": "v", "prompt": "p"}
OLD = {"concept": "c", "duration_s": 15, "script": "s", "scenes": [SCENE], "style": "st",
       "negative": "ng", "purpose": "channel_content"}  # fmt: skip


def test_old_plan_without_goal_reads_as_none_and_keeps_purpose() -> None:
    plan = VideoPlan.model_validate(OLD)
    assert plan.goal is None and plan.purpose == "channel_content"


@pytest.mark.parametrize("goal", ["fun", "info", "promo", "ad"])
def test_goal_values_round_trip(goal: str) -> None:
    plan = VideoPlan.model_validate(OLD | {"goal": goal})
    assert plan.goal == goal
    assert VideoPlan.model_validate(plan.model_dump(mode="json")).goal == goal


def test_unknown_goal_rejected() -> None:
    with pytest.raises(ValidationError):
        VideoPlan.model_validate(OLD | {"goal": "viral"})
