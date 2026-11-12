"""부서가 긴 작업(비동기)을 접수·진행·완료로 알리는 도구."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template import async_task
from ax_agent_template.events import MOCK_PUBLISHED
from ax_contracts.schemas import (
    ACCEPTED_ASYNC,
    AgentRequest,
    AsyncAcceptedOutput,
    EventType,
    TaskCompletedData,
    TaskProgressData,
    TaskStatus,
)


@pytest.fixture(autouse=True)
def _clean_published():
    MOCK_PUBLISHED.clear()
    yield
    MOCK_PUBLISHED.clear()


def _request() -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": "ctx_inc_async",
                "token": "mock:orchestrator",
                "deadline": datetime.now(UTC) + timedelta(seconds=30),
                "idempotency_key": "ctx_inc_async:impact_analysis:impact",
            },
            "skill": "impact_analysis",
            "input": {},
        }
    )


def test_accepting_answers_working_with_the_task_number() -> None:
    result = async_task.accepted(_request(), "tsk_1", expected_seconds=1800)
    assert result.status == TaskStatus.WORKING and result.outcome == ACCEPTED_ASYNC
    out = AsyncAcceptedOutput.model_validate(result.output)
    assert (out.task_id, out.expected_seconds) == ("tsk_1", 1800)
    assert "expected_seconds" not in async_task.accepted(_request(), "tsk_2").output


def test_task_numbers_are_unique() -> None:
    assert len({async_task.new_task_id() for _ in range(50)}) == 50


@pytest.mark.asyncio
async def test_progress_is_published_as_a_progress_event_for_the_same_incident() -> None:
    await async_task.report_progress(_request(), "data", "tsk_1", percent=40, note="절반")
    (event,) = MOCK_PUBLISHED
    assert event.type == EventType.TASK_PROGRESS
    assert (event.source, event.context_id) == ("data", "ctx_inc_async")
    data = TaskProgressData.model_validate(event.data)
    assert (data.task_id, data.skill, data.percent, data.note) == (
        "tsk_1",
        "impact_analysis",
        40,
        "절반",
    )


@pytest.mark.asyncio
async def test_every_progress_notice_is_delivered_not_deduplicated() -> None:
    for _ in range(3):
        await async_task.report_progress(_request(), "data", "tsk_1")
    assert len(MOCK_PUBLISHED) == 3  # 같은 일의 알림이어도 알림마다 다른 이벤트다


@pytest.mark.asyncio
async def test_completion_is_published_once_even_if_reported_twice() -> None:
    await async_task.report_completed(_request(), "data", "tsk_1", output={"n": 3})
    await async_task.report_completed(_request(), "data", "tsk_1", output={"n": 4})
    (event,) = MOCK_PUBLISHED
    assert event.type == EventType.TASK_COMPLETED
    data = TaskCompletedData.model_validate(event.data)
    assert (data.status, data.output) == ("done", {"n": 3})


@pytest.mark.asyncio
async def test_a_failed_completion_carries_the_reason() -> None:
    await async_task.report_completed(_request(), "data", "tsk_9", failed=True, note="원본 없음")
    data = TaskCompletedData.model_validate(MOCK_PUBLISHED[0].data)
    assert (data.status, data.note) == ("failed", "원본 없음")
