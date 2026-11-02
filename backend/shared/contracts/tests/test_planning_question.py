"""기획 → 요청 서비스 되묻기 계약 (`answer_planning_question`·`receive_planning_answer`)."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import (
    AnswerPlanningQuestionInput,
    AnswerPlanningQuestionOutput,
    ReceivePlanningAnswerInput,
    ReceivePlanningAnswerOutput,
)


def test_question_round_trip() -> None:
    q = AnswerPlanningQuestionInput.model_validate(
        {"question_id": "Q-REQ-21-1", "request_id": "REQ-21", "question": "근거 건수는?"}
    )
    assert q.spec_id is None and q.options == []


def test_answered_needs_answer_text() -> None:
    ok = AnswerPlanningQuestionOutput.model_validate(
        {"status": "answered", "answer": "42건", "answered_by": "agent", "evidence_ids": ["inq_1"]}
    )
    assert ok.answer == "42건"
    assert AnswerPlanningQuestionOutput.model_validate({"status": "needs_human"}).answer is None
    with pytest.raises(ValidationError):
        AnswerPlanningQuestionOutput.model_validate({"status": "answered"})
    with pytest.raises(ValidationError):
        AnswerPlanningQuestionOutput.model_validate({"status": "unknown", "answer": "지어냄"})


def test_receive_answer() -> None:
    a = ReceivePlanningAnswerInput.model_validate(
        {"question_id": "Q-REQ-21-1", "request_id": "REQ-21", "answer": "42건"}
    )
    assert a.answered_by == "human"
    with pytest.raises(ValidationError):
        ReceivePlanningAnswerInput.model_validate(
            {"question_id": "Q", "request_id": "REQ-21", "answer": ""}
        )
    assert ReceivePlanningAnswerOutput.model_validate({"outcome": "duplicate"}).spec_id is None
