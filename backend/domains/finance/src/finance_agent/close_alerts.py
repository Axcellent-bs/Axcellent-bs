"""월 마감 체크리스트 기한 지남 → 사람 할 일함(받은 요청, dashboard-api)에 올린다 (11/16).

체크리스트는 기한이 지나면 `overdue`까지만 표시했고, 사람에게 알리는 흐름이 없었다. 재무 서버가
주기적으로(`FINANCE_CLOSE_ALERT_SECONDS`) 이번 달과 지난달 체크리스트를 보고, 기한이 지났는데
끝나지 않은 항목을 공용 "받은 요청"에 올린다. 기획의 원인 확인 요청(`ax_workflow.cause_inbox`)과
같은 길이다.

- 맡을 부서·담당자는 조직도(`org.yaml`)에서만 찾는다: skill `close_overdue` → 서비스 `finance`.
  조직도에 없으면 지어내지 않고 올리지 않는다(`no_assignee`).
- 같은 달·같은 항목·같은 기한은 한 번만 올린다. 올린 기록(`records.close_alerts`)을 남기고,
  할 일함도 같은 멱등 키면 처음 만든 일을 돌려준다. 기한이 바뀌면(예: 일 결산의 다음 남은 날)
  새 기한으로 다시 올린다.
- 돈을 움직이지 않고, 체크리스트를 바꾸지 않는다. 사람은 받은 요청에서 보고 재무 화면에서 처리한다.
- 목 모드에서는 올리지 않는다(할 일함이 떠 있지 않다).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError

from ax_agent_template import config as template_config
from ax_agent_template import routing
from ax_agent_template.client import INBOX, INBOX_PATH
from ax_agent_template.config import agent_token, agent_url
from ax_contracts.schemas import TENANT_ID_DEFAULT, HandoffCreate, HandoffTask

SKILL = "close_overdue"
SERVICE = "finance"
TIMEOUT_SECONDS = 5.0

Poster = Callable[[HandoffCreate], Awaitable[HandoffTask]]


def alert_key(month: str, item: dict[str, Any], tenant_id: str = TENANT_ID_DEFAULT) -> str:
    return f"close_overdue:{tenant_id}:{month}:{item['key']}:{item['due']}"


def overdue_items(checklist: dict[str, Any]) -> list[dict[str, Any]]:
    """기한이 지났는데 끝나지 않은 항목 (확인할 수 없음은 사람이 볼 일이라 넣는다)."""
    return [i for i in checklist["items"] if i.get("overdue") and i.get("status") != "done"]


def handoff_for(
    month: str, item: dict[str, Any], tenant_id: str = TENANT_ID_DEFAULT
) -> HandoffCreate | None:
    """받은 요청에 넣을 일. 조직도에 맡은 부서가 없으면 None (지어내지 않는다)."""
    assignment = routing.assignment_for(SERVICE, SKILL)
    if assignment is None:
        return None
    return HandoffCreate(
        tenant_id=tenant_id,
        context_id=f"ctx_close_{month}",
        from_service=SERVICE,
        to_service=SERVICE,
        skill=SKILL,
        input={
            "month": month,
            "item": item["key"],
            "label": item["label"],
            "due": item["due"],
            "detail": item.get("detail"),
            "link": f"/finance/daily#{item.get('link') or 'closing'}",
        },
        reason=(
            f'{month} 월 마감 "{item["label"]}" 기한({item["due"]})이 지났어요: '
            f"{item.get('detail')}"
        ),
        assignee_unit=assignment.unit,
        assignee=assignment.assignee,
        idempotency_key=alert_key(month, item, tenant_id),
    )


async def post(body: HandoffCreate) -> HandoffTask:
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{agent_url(INBOX)}{INBOX_PATH}",
            json=body.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {agent_token(SERVICE)}"},
            timeout=TIMEOUT_SECONDS,
        )
        res.raise_for_status()
        return HandoffTask.model_validate(res.json()["data"])


async def alert_overdue(
    checklists: list[dict[str, Any]],
    sent: dict[str, dict[str, Any]],
    tenant_id: str = TENANT_ID_DEFAULT,
    poster: Poster | None = None,
) -> list[dict[str, Any]]:
    """기한 지난 항목을 받은 요청에 올리고, 이번에 올린 것(또는 못 올린 까닭)을 돌려준다.

    `sent`(재무 기록 `close_alerts`)에 이미 있는 키는 다시 올리지 않는다. 올리면 거기에 남긴다.
    """
    if poster is None and template_config.is_mock(SERVICE):
        return []
    results: list[dict[str, Any]] = []
    for checklist in checklists:
        month = checklist["month"]
        for item in overdue_items(checklist):
            key = alert_key(month, item, tenant_id)
            if key in sent:
                continue
            body = handoff_for(month, item, tenant_id)
            if body is None:
                results.append({"key": key, "status": "no_assignee"})
                continue
            try:
                task = await (poster or post)(body)
            except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError) as exc:
                results.append({"key": key, "status": "failed", "why": type(exc).__name__})
                continue  # 다음 회차에 다시 올린다
            sent[key] = {
                "task_id": task.task_id,
                "month": month,
                "item": item["key"],
                "due": item["due"],
                "assignee_unit": task.assignee_unit,
                "at": datetime.now(UTC).isoformat(),
            }
            results.append({"key": key, "status": "posted", "task_id": task.task_id})
    return results


def mark_alerted(
    checklist: dict[str, Any], sent: dict[str, dict[str, Any]], tenant_id: str
) -> None:
    """화면용: 받은 요청에 올린 항목에 `alert`(task_id·부서·시각)를 붙인다."""
    for item in checklist["items"]:
        got = sent.get(alert_key(checklist["month"], item, tenant_id))
        if got:
            item["alert"] = {k: got[k] for k in ("task_id", "assignee_unit", "at")}
