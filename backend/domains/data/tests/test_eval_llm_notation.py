"""평가용 가짜 LLM의 `realistic_notation` 모드 (#133): 표기만 바꾸고 검증은 그 표기를 통과시킨다."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from data_agent.explain import explain

EVAL = Path(__file__).resolve().parents[1] / "eval"


def _eval_llm():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("data_eval_eval_llm", EVAL / "eval_llm.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("value", "shown"),
    [(0.42, "42%"), (2.747, "2.7"), (1234000, "1,234,000"), (7, "7"), (3.0, "3"), ("x", "x")],
)
def test_realistic_notation_changes_only_the_notation(value, shown) -> None:  # noqa: ANN001
    assert _eval_llm().realistic(value) == shown


def test_validation_passes_percent_rounding_and_commas() -> None:
    llm = _eval_llm().ScriptedLLM("realistic_notation")
    rounded = asyncio.run(explain("anomaly", {"baseline": 0.02, "observed": 2.747}, llm))
    assert rounded.fallback is False
    assert rounded.reason is None
    assert rounded.bad_numbers == ()
    assert "2%" in rounded.summary
    assert "2.7" in rounded.summary
    commas = asyncio.run(explain("impact", {"damage_amount": 1234000}, llm))
    assert commas.fallback is False
    assert "1,234,000" in commas.summary
