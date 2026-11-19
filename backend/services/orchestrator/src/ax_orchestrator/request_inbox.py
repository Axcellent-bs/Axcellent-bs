"""처리하지 못한 의뢰를 사람의 받은 요청(할 일함)에 올린다 (의뢰 자동 넘김 3단계).

의뢰가 멈추지 않고 사람에게 가는 경우는 셋이다.

- `unknown`: 일 목록에 맞는 일이 없어 맡을 부서를 정하지 못했다(담당 불명)
- `unclear`: 되물었는데도(한 번만 묻는다) 무슨 일인지 알 수 없다
- `step_failed`: 부서가 일을 끝내지 못했다(재시도 뒤에도 실패)

받은 요청에는 의뢰 번호·이유·고객 원문(데이터 칸)만 올린다. 부서가 돌려준 문장은 올리지 않는다.
같은 의뢰의 같은 이유는 한 번만 올린다(`idempotency_key`). 못 올려도 의뢰 기록에 이유가 남고
의뢰는 `needs_human`으로 닫힌다.
"""

from __future__ import annotations

import asyncio
import logging

from ax_agent_template import routing
from ax_agent_template.services import route_target
from ax_contracts.schemas import TENANT_ID_DEFAULT, UNASSIGNED_UNIT, HandoffCreate
from ax_orchestrator.inbox import SOURCE, InboxPoster
from ax_orchestrator.work_requests import WorkRequests

log = logging.getLogger("orchestrator.request_inbox")

UNKNOWN = "unknown"
UNCLEAR = "unclear"
STEP_FAILED = "step_failed"
GENERIC_SERVICE = "orchestrator"
GENERIC_SKILL = "work_request"
TO_CHECK = {
    UNKNOWN: "어느 부서가 맡을지 정하지 못한 의뢰입니다. 담당을 정해 처리해 주세요",
    UNCLEAR: "되물었는데도 무슨 일인지 알 수 없는 의뢰입니다. 고객에게 확인해 주세요",
    STEP_FAILED: "부서가 재시도 뒤에도 일을 끝내지 못했습니다. 원인을 확인해 주세요",
}


def build(row: dict, why: str, step_id: str | None, tenant_id: str) -> HandoffCreate:
    """받은 요청 본문. 고객 원문은 `untrusted`(데이터)로만 둔다."""
    service, skill, label = GENERIC_SERVICE, GENERIC_SKILL, "의뢰"
    spec = next((s for s in row.get("plan") or [] if s["step_id"] == step_id), None)
    if spec and spec.get("dept"):
        service, skill, label = (
            route_target(spec["dept"], spec["skill"]),
            spec["skill"],
            spec["label"],
        )
    assignment = routing.assignment_for(service, skill)
    step = row["steps"].get(step_id or "") or {}
    return HandoffCreate(
        tenant_id=tenant_id,
        context_id=row.get("context_id") or f"ctx_{row['request_id']}",
        from_service=SOURCE,
        to_service=service,
        skill=skill,
        input={
            "request_id": row["request_id"],
            "why": why,
            "to_check": TO_CHECK[why],
            "step": label,
            "reason_code": step.get("reason_code"),
            "attempts": step.get("attempts"),
        },
        untrusted={"request_text": row["text"]},
        reason=f"의뢰 {row['request_id']} · {label}: {TO_CHECK[why]}",
        assignee_unit=assignment.unit if assignment else UNASSIGNED_UNIT,
        assignee=assignment.assignee if assignment else None,
        idempotency_key=f"work_request:{row['request_id']}:{why}",
    )


class RequestEscalation:
    def __init__(
        self,
        requests: WorkRequests,
        poster: InboxPoster | None = None,
        *,
        tenant_id: str = TENANT_ID_DEFAULT,
    ) -> None:
        self._requests = requests
        self.poster = poster  # None이면 올리지 않고 기록만 남긴다(목 모드·시험)
        self._tenant_id = tenant_id
        self._tasks: set[asyncio.Task[None]] = set()

    def queue(self, request_id: str, why: str, step_id: str | None = None) -> None:
        """받은 요청에 올린다 (뒤에서). 이미 올렸으면 다시 올리지 않는다."""
        task = asyncio.get_running_loop().create_task(self._post(request_id, why, step_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _post(self, request_id: str, why: str, step_id: str | None) -> None:
        row = self._requests.get(request_id)
        if row is None or row.get("handoff_task_id"):
            return
        if self.poster is None:
            self._requests.note(request_id, "받은 요청에 올리지 않았다 (연결 없음)")
            return
        try:
            task = await self.poster(build(row, why, step_id, self._tenant_id))
        except Exception:  # noqa: BLE001 - 올리지 못해도 의뢰 기록은 남는다
            log.warning("의뢰 사람 넘김 실패: %s", request_id)
            task = None
        if task is None:
            self._requests.note(request_id, "받은 요청에 올리지 못했다 (할 일함에 닿지 않음)")
            return
        self._requests.set_handoff(request_id, task.task_id)
        unit = "담당 미정" if task.assignee_unit == UNASSIGNED_UNIT else task.assignee_unit
        self._requests.note(request_id, f"받은 요청에 올렸다 ({unit}, {task.task_id})")
