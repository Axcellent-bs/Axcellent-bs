"""숫자 확인 평가 묶음(`eval/number_fidelity_cases.json`)을 `chat/verify.py`에 그대로 돌린다.

재무 대화 답과 결산 설명은 장부(facts·넘긴 숫자)와 사람이 한 말에 없는 숫자를 말하면 안 된다.
각 경우의 `expect_bad`(걸려야 하는 글자)가 verify 결과와 정확히 같아야 한다.
note가 "알려진 한계"로 시작하는 경우는 지금 verify가 놓치거나 지나치게 잡는 것을 그대로 적어 둔
것이다(고치면 기대값도 바꾼다).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from finance_agent import closing
from finance_agent.chat import verify

CASES_FILE = Path(__file__).resolve().parents[1] / "eval" / "number_fidelity_cases.json"
CASES: list[dict[str, Any]] = json.loads(CASES_FILE.read_text("utf-8"))["cases"]


def test_the_eval_set_covers_chat_and_commentary() -> None:
    kinds = [c["kind"] for c in CASES]
    assert len(CASES) >= 25
    assert kinds.count("chat") >= 10 and kinds.count("commentary") >= 5
    assert len({c["id"] for c in CASES}) == len(CASES)  # id는 겹치지 않는다
    assert any(c["expect_bad"] for c in CASES) and any(not c["expect_bad"] for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_verify_flags_exactly_the_numbers_not_in_the_books(case: dict[str, Any]) -> None:
    allowed = verify.allowed_numbers(case["facts"], case.get("said", []))
    assert verify.bad_numbers(case["reply"], allowed) == case["expect_bad"]


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["kind"] == "commentary"], ids=lambda c: c["id"]
)
def test_commentary_cases_give_the_same_answer_through_closing(case: dict[str, Any]) -> None:
    """결산 설명 검사(`closing.unsupported_numbers`)는 대화 답과 같은 검사다."""
    assert closing.unsupported_numbers(case["reply"], case["facts"]) == case["expect_bad"]
