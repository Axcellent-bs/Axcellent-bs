"""긴 작업(비동기) 계약: 접수 응답, 진행·완료 이벤트, 평가에 실리는 응답 끊김 필드."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import (
    ACCEPTED_ASYNC,
    EVENT_DATA_MODELS,
    AsyncAcceptedOutput,
    EventType,
    StepTiming,
    TaskCompletedData,
    TaskProgressData,
)
from ax_contracts.schemas.hr import AgentSummary, EvaluatedStep


def test_the_two_new_events_have_data_models() -> None:
    assert EVENT_DATA_MODELS[EventType.TASK_PROGRESS] is TaskProgressData
    assert EVENT_DATA_MODELS[EventType.TASK_COMPLETED] is TaskCompletedData
    assert EventType.TASK_PROGRESS.value == "task.progress"
    assert EventType.TASK_COMPLETED.value == "task.completed"


def test_the_accepted_outcome_name_is_fixed() -> None:
    assert ACCEPTED_ASYNC == "accepted_async"


def test_an_accepted_answer_needs_a_task_id_and_a_positive_estimate() -> None:
    assert AsyncAcceptedOutput(task_id="t").expected_seconds is None
    with pytest.raises(ValidationError):
        AsyncAcceptedOutput(task_id="")
    with pytest.raises(ValidationError):
        AsyncAcceptedOutput(task_id="t", expected_seconds=0)


@pytest.mark.parametrize("percent", [-1, 101])
def test_progress_percent_stays_between_0_and_100(percent: int) -> None:
    with pytest.raises(ValidationError):
        TaskProgressData(task_id="t", skill="s", percent=percent)


def test_a_completion_is_done_or_failed_only() -> None:
    assert TaskCompletedData(task_id="t", skill="s", status="done").output == {}
    with pytest.raises(ValidationError):
        TaskCompletedData(task_id="t", skill="s", status="partial")


def test_step_timings_carry_the_longest_silence_as_an_optional_field() -> None:
    plain = StepTiming(step_id="a", status="completed")
    assert plain.max_silence_seconds is None  # 이 기능이 들어오기 전 값과 같다
    assert (
        StepTiming(step_id="a", status="completed", max_silence_seconds=12.5).max_silence_seconds
        == 12.5
    )
    with pytest.raises(ValidationError):
        StepTiming(step_id="a", status="completed", max_silence_seconds=-1)


def test_the_evaluation_models_know_the_silent_verdict() -> None:
    step = EvaluatedStep(
        step_id="impact",
        label="영향 분석",
        dept="data",
        group="조회·분석",
        verdict="silent",
        limit_seconds=60.0,
        max_silence_seconds=400.0,
    )
    assert step.verdict == "silent"
    summary = AgentSummary(
        dept="data",
        events=1,
        normal=0,
        check=0,
        no_record=0,
        blocked=0,
        approval_expired=0,
        in_progress=0,
        retried=0,
        slow=0,
    )
    assert summary.silent == 0  # 옛 응답에는 없던 필드라 기본값이 있다
