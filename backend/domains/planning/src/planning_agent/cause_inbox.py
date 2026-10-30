"""사건의 원인 확인 요청을 사람 할 일함(받은 요청, dashboard-api)에 올린다.

총괄이 사건에서 보낸 원인 확인 요청은 자동 기획·PR로 넘기지 않고(service._register_ticket),
사람이 원인을 보도록 할 일함에 넣는다. 맡을 부서는 조직도(`investigate_incident` → planning)에서
찾고, 없으면 개발팀으로 둔다. 같은 요청은 같은 멱등 키라 두 번 넣어도 하나만 생긴다.

- 요청 부서가 보낸 근거 문장(evidence)은 외부 입력이라 `untrusted`에만 둔다.
- 목 모드에서는 넣지 않는다(할 일함이 떠 있지 않다).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
from pydantic import ValidationError

from ax_agent_template import config as template_config
from ax_agent_template import routing
from ax_agent_template.client import INBOX, INBOX_PATH
from ax_agent_template.config import agent_token, agent_url
from ax_contracts.schemas import HandoffCreate, HandoffTask
from planning_agent.domain.models import Request

SKILL = "investigate_incident"
DEFAULT_UNIT = "개발팀"
TIMEOUT_SECONDS = 5.0

Poster = Callable[[HandoffCreate], Awaitable[HandoffTask]]


def handoff_of(req: Request, tenant_id: str) -> HandoffCreate:
    assignment = routing.assignment_for("planning", SKILL)
    return HandoffCreate(
        tenant_id=tenant_id,
        context_id=req.context_id,
        from_service="planning",
        to_service="planning",
        skill=SKILL,
        input={
            "request_id": req.id,
            "title": req.title,
            "incident": req.source_ref,
            "evidence_ids": list(req.evidence_ids),
        },
        untrusted={"evidence": req.evidence},
        reason="사건 원인 확인: 자동 기획·PR 없이 사람이 원인을 본다",
        assignee_unit=assignment.unit if assignment else DEFAULT_UNIT,
        assignee=assignment.assignee if assignment else None,
        idempotency_key=f"cause_check:{req.id}",
    )


async def post(body: HandoffCreate) -> HandoffTask:
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{agent_url(INBOX)}{INBOX_PATH}",
            json=body.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {agent_token('planning')}"},
            timeout=TIMEOUT_SECONDS,
        )
        res.raise_for_status()
        return HandoffTask.model_validate(res.json()["data"])


async def send(req: Request, tenant_id: str, poster: Poster | None = None) -> str:
    """할 일함에 넣고 활동에 남길 한 줄을 돌려준다. 실패해도 예외를 던지지 않는다."""
    if poster is None and template_config.is_mock("planning"):
        return f"{req.id} 받은 요청 올림 생략 (목 모드)"
    try:
        task = await (poster or post)(handoff_of(req, tenant_id))
    except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError) as exc:
        why = type(exc).__name__
        return f"{req.id} 받은 요청에 올리지 못함 ({why}) → 기획 화면 요청 목록에서 확인"
    return f"{req.id} 받은 요청 {task.task_id}에 올림 → {task.assignee_unit} 원인 확인"
