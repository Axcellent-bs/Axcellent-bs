"""`transfer_queued` 단계를 사람의 받은 요청(할 일함)에 올린다.

총괄이 스스로 끝내지 못해 "이관 큐로 넘김"으로 표시한 단계(원인을 몰라 되돌리기 보류, 복구 확인이
정상이 아님, 소관 부서를 못 찾음)는 지금까지 총괄 안에만 남아 사람의 받은 요청에는 보이지 않았다.
여기서 그 단계를 `HandoffTask`로 만들어 dashboard-api 받은 요청에 넣는다.

- 받은 요청에는 한 줄 설명을 남긴다: 어느 이벤트(장애 방 링크는 `context_id`),
  무엇을 확인해야 하는지, 근거(진단 결과 요약). 받은 시각은 할 일함이 정한다.
- 맡을 부서는 조직도(`routing.assignment_for`)에서 찾는다.
  없으면 `UNASSIGNED_UNIT`("담당 미정")이다.
- `idempotency_key`가 이벤트·단계마다 같아서 같은 단계가 다시 돌아도 중복으로 생기지 않는다.
- 사람이 끝내면 `handoff.completed`가 오고, 총괄은 그 결과를 이 단계의 메모로 남긴다
  (단계 상태는 `transfer_queued` 그대로다. 사람이 처리해도 총괄 계획이 이어 가지는 않는다).
- 넣지 못해도 단계는 그대로 "이관 큐로 넘김"이고 이유만 이벤트 기록에 남긴다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import ValidationError

from ax_agent_template import routing
from ax_agent_template.client import INBOX, INBOX_PATH
from ax_agent_template.config import agent_token, agent_url
from ax_agent_template.services import route_target
from ax_contracts.schemas import UNASSIGNED_UNIT, HandoffCreate, HandoffTask
from ax_orchestrator.models import Incident, StepRun
from ax_orchestrator.naming import DEPT_KO

# 받은 요청에 올리는 함수: 본문을 받아 만들어진 일(실패하면 None)을 돌려준다. 시험에서 바꿔 끼운다
InboxPoster = Callable[[HandoffCreate], Awaitable[HandoffTask | None]]

SOURCE = "orchestrator"
POST_TIMEOUT_SECONDS = 5.0

# 사유 코드별로 사람이 확인할 일 한 줄 (모르는 코드는 단계 메모를 그대로 쓴다)
TO_CHECK = {
    "no_rollback_target": "원인을 모르므로 되돌리기를 보류했습니다. 원인을 확인해 주세요",
    "not_recovered": "복구 확인이 정상으로 나오지 않았습니다. 서버와 업무 지표를 확인해 주세요",
}
FALLBACK_TO_CHECK = "총괄이 맡길 부서를 찾지 못했습니다. 담당을 정해 처리해 주세요"
# 근거로 붙이는 진단 결과 키. 길이를 줄여 데이터로만 담는다
EVIDENCE_KEYS = ("status", "summary", "cause", "suspects")
EVIDENCE_MAX_CHARS = 300


def idempotency_key(inc: Incident, run: StepRun) -> str:
    """같은 이벤트의 같은 단계는 한 번만 올린다."""
    return f"transfer_queued:{inc.context_id}:{run.step_id}"


def to_check(run: StepRun) -> str:
    """사람이 무엇을 확인해야 하는지 한 줄."""
    if run.reason_code in TO_CHECK:
        return TO_CHECK[run.reason_code]
    return FALLBACK_TO_CHECK


def evidence(inc: Incident) -> dict[str, Any]:
    """근거: 진단 결과 요약. 없는 값은 만들어 내지 않는다."""
    diagnosis = inc.outputs.get("diagnose") or {}
    kept: dict[str, Any] = {}
    for key in EVIDENCE_KEYS:
        value = diagnosis.get(key)
        if value in (None, "", [], {}):
            continue
        text = value if isinstance(value, str) else str(value)
        kept[key] = text[:EVIDENCE_MAX_CHARS]
    return kept


def build(inc: Incident, run: StepRun, tenant_id: str) -> HandoffCreate:
    """받은 요청 본문. 이벤트 링크는 `context_id`, 한 줄 설명은 `reason`, 근거는 `input`에 둔다."""
    service = route_target(run.dept or "orchestrator", run.skill)
    assignment = routing.assignment_for(service, run.skill)
    check = to_check(run)
    where = DEPT_KO.get(run.dept or "", run.dept or "총괄")
    return HandoffCreate(
        tenant_id=tenant_id,
        context_id=inc.context_id,
        from_service=SOURCE,
        to_service=service,
        skill=run.skill,
        input={
            "event_title": inc.title,
            "step": run.label,
            "to_check": check,
            "reason_code": run.reason_code,
            "note": run.note,
            "evidence": evidence(inc),
        },
        reason=f"{inc.title} · {run.label}: {check} ({where})",
        assignee_unit=assignment.unit if assignment else UNASSIGNED_UNIT,
        assignee=assignment.assignee if assignment else None,
        idempotency_key=idempotency_key(inc, run),
    )


async def post_to_inbox(body: HandoffCreate) -> HandoffTask | None:
    """dashboard-api 받은 요청에 넣는다. 닿지 않거나 응답이 이상하면 None."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{agent_url(INBOX)}{INBOX_PATH}",
                json=body.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {agent_token(SOURCE)}"},
                timeout=POST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return HandoffTask.model_validate(response.json()["data"])
    except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError):
        return None


async def queue_transfer(
    inc: Incident, run: StepRun, post: InboxPoster, tenant_id: str
) -> str | None:
    """`transfer_queued` 단계를 받은 요청에 올리고 일 번호를 단계에 적는다. 올렸으면 건너뛴다."""
    if run.status != "transfer_queued" or run.handoff_task_id:
        return None
    task = await post(build(inc, run, tenant_id))
    if task is None:
        inc.log(f"{run.label}: 받은 요청에 올리지 못함 (할 일함에 닿지 않음)")
        return None
    run.handoff_task_id = task.task_id
    run.output = {**run.output, "assignee_unit": task.assignee_unit, "inbox_task_id": task.task_id}
    unit = "담당 미정" if task.assignee_unit == UNASSIGNED_UNIT else task.assignee_unit
    inc.log(f"{run.label}: 받은 요청에 올림 ({unit}, {task.task_id})")
    return task.task_id
