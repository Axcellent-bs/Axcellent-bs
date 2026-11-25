"""과거 같은 장애의 처리 방식을 바로 다시 하는 유사도 하한(0.95) 경계 시험."""

from __future__ import annotations

import pytest

from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.plans.scenario_a import PAST_FIX_MIN_SIMILARITY, _past_fix_gate


def _with_past_case(similarity: float):
    inc = new_incident()
    inc.outputs["similar_cases"] = {
        "cases": [
            {
                "card_id": "card_old",
                "title": "t",
                "similarity": similarity,
                "same_type": True,
                "resolution": {
                    "outcome": "resolved",
                    "action": "결제 장애 상태 해제",
                    "action_code": "release_fault",
                },
            }
        ]
    }
    return inc


def test_threshold_is_095() -> None:
    assert PAST_FIX_MIN_SIMILARITY == 0.95


@pytest.mark.parametrize("similarity", [0.95, 0.97, 1.0])
def test_at_or_above_095_the_past_fix_is_replayed(similarity: float) -> None:
    assert _past_fix_gate(_with_past_case(similarity)) is None


@pytest.mark.parametrize("similarity", [0.85, 0.90, 0.949])
def test_below_095_is_not_replayed_even_if_the_old_085_passed(similarity: float) -> None:
    gate = _past_fix_gate(_with_past_case(similarity))
    assert gate is not None and gate[:2] == ("skipped", "not_similar_enough")


@pytest.mark.parametrize("similarity", [float("nan"), None, "0.99", True, 1.5, float("inf"), -0.1])
def test_non_numeric_or_out_of_range_similarity_is_not_replayed(similarity: object) -> None:
    """`< 기준` 비교로는 NaN이 통과한다: 수치·범위까지 보고 건너뛴다."""
    gate = _past_fix_gate(_with_past_case(similarity))  # type: ignore[arg-type]
    assert gate is not None and gate[:2] == ("skipped", "not_similar_enough")


def test_the_floor_is_the_shared_contract_value() -> None:
    from ax_contracts.schemas import infra

    assert PAST_FIX_MIN_SIMILARITY is infra.PAST_FIX_MIN_SIMILARITY
