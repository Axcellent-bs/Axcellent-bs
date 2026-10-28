"""다른 에이전트 호출 클라이언트.

`call_agent(target, req)`:

1. 호출 전에 `path`에 자기 이름을 붙이고, 토큰은 자기 에이전트 토큰으로 바꾼다
   (받는 쪽은 토큰에서 요청자를 꺼낸다).
2. `deadline`이 이미 지났으면 호출하지 않고 `canceled`(`TIMEOUT`)를 돌려준다.
3. 마감까지 응답이 없으면 포기하고 `canceled`(`TIMEOUT`), 연결·응답 오류는 `failed`로 돌려준다.
4. 받는 서비스가 켜져 있지 않으면(`routing.py`, `AX_ENABLED_SERVICES`) A2A 대신
   조직도에서 맡은 부서를 찾아 사람 할 일함(dashboard-api)에 `HandoffTask`로 넣고
   `input-required` + `handed_to_human`을 돌려준다. 조직도에도 없으면 `failed` + `no_assignee`.

`target`은 에이전트 이름(`planning`)이거나 주소(`http://localhost:8101`)다.
TODO: 이름 → 주소는 지금 포트 표와 환경변수(`AX_<이름>_URL`)로 찾는다.
나중에 Agent Card 레지스트리로 바꾼다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from ax_agent_template import routing
from ax_agent_template.config import agent_token, agent_url, get_agent_name
from ax_agent_template.logging import log_event
from ax_agent_template.server import error_result
from ax_agent_template.services import route_target
from ax_contracts.schemas import (
    HANDED_TO_HUMAN,
    INBOX_UNREACHABLE,
    NO_ASSIGNEE,
    AgentRequest,
    AgentResult,
    ErrorCode,
    HandedToHumanOutput,
    HandoffCreate,
    HandoffTask,
    InputReason,
    TaskStatus,
)

# 사람 할 일함 (dashboard-api). 에이전트는 dashboard-api를 import하지 않고 이 주소로만 넣는다
INBOX = "dashboard-api"
INBOX_PATH = "/api/dashboard/handoffs"


async def call_agent(
    target: str,
    req: AgentRequest,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> AgentResult:
    """대상 에이전트에 요청을 보내고 결과를 받는다. `http_client`는 테스트용으로 바꿀 수 있다."""
    name = get_agent_name()
    remaining = (req.meta.deadline - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        return _failed(
            name, target, req, error_result(req.meta, TaskStatus.CANCELED, ErrorCode.TIMEOUT)
        )

    # 부서 이름으로 불러도 그 skill을 맡은 서비스로 보낸다 (services.py)
    service = route_target(target, req.skill)
    # 켜지 않은 서비스면 사람 할 일함으로 넘긴다 (routing.py)
    if not routing.is_enabled(service):
        return await _hand_off(name, target, service, req, remaining, http_client)

    meta = req.meta.model_copy(update={"path": [*req.meta.path, name], "token": agent_token(name)})
    sent = req.model_copy(update={"meta": meta})
    url = f"{agent_url(service)}/a2a"

    client = http_client or httpx.AsyncClient()
    try:
        response = await client.post(url, json=sent.model_dump(mode="json"), timeout=remaining)
    except httpx.TimeoutException:
        return _failed(
            name, target, req, error_result(meta, TaskStatus.CANCELED, ErrorCode.TIMEOUT)
        )
    except httpx.HTTPError:
        return _failed(name, target, req, error_result(meta, TaskStatus.FAILED, "unreachable"))
    finally:
        if http_client is None:
            await client.aclose()

    if response.status_code != 200:
        failed = error_result(meta, TaskStatus.FAILED, f"http_{response.status_code}")
        return _failed(name, target, req, failed)
    try:
        return AgentResult.model_validate(response.json())
    except (ValidationError, ValueError):
        failed = error_result(meta, TaskStatus.FAILED, ErrorCode.INVALID_OUTPUT)
        return _failed(name, target, req, failed)


def _failed(name: str, target: str, req: AgentRequest, result: AgentResult) -> AgentResult:
    """호출이 상대에게 닿지 못했거나 응답이 깨졌다. 받는 쪽은 기록할 수 없으니 보낸 쪽이 남긴다."""
    log_event(
        req.meta.context_id,
        req.meta.tenant_id,
        "task_status",
        {"skill": req.skill, "target": target, "outcome": result.outcome},
        source=name,
        skill=req.skill,
        task_status=result.status.value,
    )
    return result


async def _hand_off(
    name: str,
    target: str,
    service: str,
    req: AgentRequest,
    remaining: float,
    http_client: httpx.AsyncClient | None,
) -> AgentResult:
    """켜지 않은 서비스로 가는 요청을 조직도의 부서 할 일함에 넣는다.

    모든 넘김을 공통 로그에 남긴다.
    """
    assignment = routing.assignment_for(service, req.skill)
    if assignment is None:
        failed = error_result(
            req.meta,
            TaskStatus.FAILED,
            NO_ASSIGNEE,
            f"{service} 서비스가 켜져 있지 않고 조직도에도 맡은 부서가 없다",
        )
        failed.output["to_service"] = service
        return _log_handoff(name, target, service, req, failed)

    body = HandoffCreate(
        tenant_id=req.meta.tenant_id,
        context_id=req.meta.context_id,
        from_service=name,
        to_service=service,
        skill=req.skill,
        input=req.input,
        untrusted=req.untrusted,  # 외부 입력 구획을 그대로 유지한다
        reason=f"{service} 서비스가 켜져 있지 않다 ({routing.ENABLED_ENV})",
        assignee_unit=assignment.unit,
        assignee=assignment.assignee,
        idempotency_key=req.meta.idempotency_key,
    )
    client = http_client or httpx.AsyncClient()
    try:
        response = await client.post(
            f"{agent_url(INBOX)}{INBOX_PATH}",
            json=body.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {agent_token(name)}"},
            timeout=remaining,
        )
        response.raise_for_status()
        task = HandoffTask.model_validate(response.json()["data"])
    except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError):
        failed = error_result(req.meta, TaskStatus.FAILED, INBOX_UNREACHABLE)
        failed.output["to_service"] = service
        return _log_handoff(name, target, service, req, failed)
    finally:
        if http_client is None:
            await client.aclose()

    output = HandedToHumanOutput(
        task_id=task.task_id,
        to_service=service,
        assignee_unit=task.assignee_unit,
        assignee=task.assignee,
    )
    handed = AgentResult(
        meta=req.meta,
        status=TaskStatus.INPUT_REQUIRED,
        outcome=HANDED_TO_HUMAN,
        reason_code=InputReason.DEPT_INPUT,
        output=output.model_dump(mode="json"),
        decision_reason=body.reason,
    )
    return _log_handoff(name, target, service, req, handed)


def _log_handoff(
    name: str, target: str, service: str, req: AgentRequest, result: AgentResult
) -> AgentResult:
    """넘김 결과를 공통 로그(`task_status`)에 한 줄 남긴다. payload에는 ID·코드만 담는다."""
    payload = {
        "skill": req.skill,
        "target": target,
        "to_service": service,
        "outcome": result.outcome,
    }
    for key in ("task_id", "assignee_unit"):
        if key in result.output:
            payload[key] = result.output[key]
    log_event(
        req.meta.context_id,
        req.meta.tenant_id,
        "task_status",
        payload,
        source=name,
        skill=req.skill,
        task_status=result.status.value,
    )
    return result
