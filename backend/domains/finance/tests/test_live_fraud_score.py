"""판매자 심사가 data `fraud_score` 출력을 읽는 규칙 (필드 이름 `score`, 빠진 규칙이 있을 때)."""

from __future__ import annotations

from typing import Any

import pytest

from finance_agent.sources import fraud_score_from

FULL = {"seller_id": "sel_1", "score": 40, "level": "mid", "summary": "", "unknown_rules": []}


def test_reads_contract_field_score() -> None:
    """data 계약 필드는 `score`다. 예전 `risk_score`만 있으면 점수로 쓰지 않는다."""
    assert fraud_score_from(FULL) == 40
    assert fraud_score_from({"seller_id": "sel_1", "risk_score": 40}) is None


@pytest.mark.parametrize(
    "missing",
    [
        {"unknown_rules": ["duplicate_business", "similar_fraud_card"]},
        {"complete": False},
    ],
)
def test_incomplete_score_uses_worst_case(missing: dict[str, Any]) -> None:
    """빠진 규칙이 있으면 확인한 점수(최대 55점)로는 자동 승인된다. 최대 가능 점수를 쓴다."""
    out = FULL | {"score": 40} | missing | {"max_possible_score": 85}
    assert fraud_score_from(out) == 85


@pytest.mark.parametrize("worst", [None, 30])
def test_incomplete_without_usable_worst_case_is_undetermined(worst: int | None) -> None:
    """최대 가능 점수가 없거나 확인한 점수보다 낮으면 판단하지 않는다 (자동 승인 없음)."""
    out = FULL | {"complete": False, "max_possible_score": worst}
    assert fraud_score_from(out) is None


def test_complete_score_is_used_as_is() -> None:
    assert fraud_score_from(FULL | {"complete": True, "max_possible_score": 100}) == 40
