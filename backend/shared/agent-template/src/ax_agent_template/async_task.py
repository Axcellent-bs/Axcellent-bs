"""부서가 긴 작업(비동기)을 하는 데 쓰는 도구. 규약은 `ax_contracts.schemas.async_task`에 있다.

    # 요청을 받은 handler: 일을 접수만 알리고, 실제 일은 백그라운드에서 한다
    async def long_analysis(request):
        task_id = new_task_id()
        asyncio.create_task(_work(request, task_id))
        return accepted(request, task_id, expected_seconds=1800)

    async def _work(request, task_id):
        for part in parts:
            ...  # 일을 한다
            await report_progress(request, "data", task_id, percent=40)  # 총괄 기준보다 자주
        await report_completed(request, "data", task_id, output={...})

- 진행 알림은 총괄이 정한 시간(기본 300초)보다 자주 보낸다. 끊기면 총괄이 단계를 실패로 끝낸다.
- 완료는 한 번만 보낸다(같은 `task_id`는 멱등 키가 같아 두 번 보내도 한 번만 전달된다).
- 상태를 바꾸는 일은 이 도구가 아니라 `execute()`(안전 관문)로 한다. 이 도구는 알림만 보낸다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from ax_agent_template.events import publish
from ax_contracts.schemas import (
    ACCEPTED_ASYNC,
    AgentRequest,
    AgentResult,
    AsyncAcceptedOutput,
    EventEnvelope,
    EventType,
    TaskCompletedData,
    TaskProgressData,
    TaskStatus,
)


def new_task_id() -> str:
    return f"tsk_{uuid.uuid4().hex[:12]}"


def accepted(
    request: AgentRequest, task_id: str, *, expected_seconds: float | None = None
) -> AgentResult:
    """일을 접수했다는 응답. 총괄은 이 응답을 받으면 단계를 "진행 중(비동기)"으로 두고 기다린다."""
    output = AsyncAcceptedOutput(task_id=task_id, expected_seconds=expected_seconds)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.WORKING,
        outcome=ACCEPTED_ASYNC,
        output=output.model_dump(mode="json", exclude_none=True),
    )


async def report_progress(
    request: AgentRequest,
    source: str,
    task_id: str,
    *,
    percent: int | None = None,
    note: str | None = None,
) -> None:
    """진행 알림(생존 신호). `source`는 이 부서의 id다."""
    data = TaskProgressData(task_id=task_id, skill=request.skill, percent=percent, note=note)
    key = f"task.progress:{task_id}:{uuid.uuid4().hex[:8]}"  # 알림마다 다른 키
    await publish(
        _envelope(request, source, EventType.TASK_PROGRESS, data.model_dump(mode="json"), key)
    )


async def report_completed(
    request: AgentRequest,
    source: str,
    task_id: str,
    *,
    output: dict[str, Any] | None = None,
    failed: bool = False,
    note: str | None = None,
) -> None:
    """완료 알림. 같은 `task_id`는 한 번만 전달된다."""
    data = TaskCompletedData(
        task_id=task_id,
        skill=request.skill,
        status="failed" if failed else "done",
        output=output or {},
        note=note,
    )
    key = f"task.completed:{task_id}"
    await publish(
        _envelope(request, source, EventType.TASK_COMPLETED, data.model_dump(mode="json"), key)
    )


def _envelope(
    request: AgentRequest, source: str, kind: EventType, data: dict[str, Any], key: str
) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"evt_{uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:12]}",
        type=kind,
        occurred_at=datetime.now(UTC),
        tenant_id=request.meta.tenant_id,
        context_id=request.meta.context_id,
        source=source,
        idempotency_key=key,
        data=data,
    )
