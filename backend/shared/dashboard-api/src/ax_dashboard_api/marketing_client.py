"""마케팅 캠페인 skill 호출 (A2A). 화면의 캠페인 시작·결과·취소가 쓴다.

재무 판단과 안전 관문은 마케팅 skill 안에서 일어난다. 보류·거절은 오류가 아니라 판단 결과라
화면이 이유를 보여 줄 수 있게 그대로 돌려준다. 닿지 못한 경우만 API 오류로 바꾼다.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_agent_template import config
from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_dashboard_api.envelope import ApiError

AGENT_NAME = "dashboard-api"
TARGET = "marketing"
DEADLINE_SECONDS = 60

SendFn = Callable[[str, AgentRequest], Awaitable[AgentResult]]
SEND: SendFn = call_agent  # 테스트에서 바꿀 수 있다


async def call(
    skill: str, payload: dict[str, Any], *, campaign_id: str, key: str
) -> dict[str, Any]:
    config.set_agent_name(AGENT_NAME)
    request = AgentRequest(
        meta=RequestMeta(
            context_id=f"ctx_cmp_{campaign_id}",
            token=config.agent_token(AGENT_NAME),
            deadline=datetime.now(UTC) + timedelta(seconds=DEADLINE_SECONDS),
            idempotency_key=key,
        ),
        skill=skill,
        input=payload,
    )
    result = await SEND(TARGET, request)
    if result.status == TaskStatus.REJECTED:
        raise ApiError(403, "UPSTREAM_REJECTED", f"마케팅이 요청을 거부했다 ({result.outcome})")
    if result.status == TaskStatus.CANCELED:
        raise ApiError(504, "UPSTREAM_TIMEOUT", "마케팅 에이전트가 제때 응답하지 않았다")
    return {"status": result.status.value, "outcome": result.outcome, "output": result.output}


# 기획안 넘기기 skill이 실패로 돌려주는 outcome → (HTTP 상태, 오류 코드).
# `handed`·`already_handed`·`failed`(이유 코드는 output에)는 판단 결과라 오류가 아니다
HANDOFF_FAILURES = {
    "brief_not_found": (404, "NOT_FOUND"),
    "invalid_input": (400, "INVALID_INPUT"),
    "storage_error": (503, "STORAGE_ERROR"),
}


async def handoff_call(proposal_id: str, media_kind: str, key: str) -> dict[str, Any]:
    """받은 기획안을 SNS·미디어로 넘긴다. 미디어 생성·게시는 하지 않는다(마케팅 skill 쪽 규칙)."""
    out = await call(
        "handoff_brief_to_sns",
        {"proposal_id": proposal_id, "media_kind": media_kind},
        campaign_id=f"cmp_{proposal_id}",
        key=key,
    )
    if out["status"] == TaskStatus.FAILED.value:
        status, code = HANDOFF_FAILURES.get(out["outcome"] or "", (502, "UPSTREAM_FAILED"))
        raise ApiError(status, code, f"기획안을 넘기지 못했다 ({out['outcome']})")
    return out


CHAT_DEADLINE_SECONDS = 30
# 마케팅 대화 skill이 거절할 때 돌려주는 outcome → (HTTP 상태, 오류 코드)
CHAT_REFUSALS = {
    "llm_not_configured": (409, "LLM_NOT_CONFIGURED"),
    "forbidden": (403, "FORBIDDEN"),
    "not_found": (404, "NOT_FOUND"),
    "thread_closed": (409, "THREAD_CLOSED"),
    "thread_busy": (409, "THREAD_BUSY"),
    "invalid_input": (400, "INVALID_INPUT"),
    "storage_error": (503, "STORAGE_ERROR"),
}


async def chat_call(skill: str, payload: dict[str, Any]) -> dict[str, Any]:
    """마케팅 AI 대화 skill 호출. 거절(outcome)은 오류 모양(`{error:{code,message}}`)으로 바꾼다."""
    config.set_agent_name(AGENT_NAME)
    request = AgentRequest(
        meta=RequestMeta(
            context_id=f"ctx_chat_{uuid.uuid4().hex[:12]}",
            token=config.agent_token(AGENT_NAME),
            deadline=datetime.now(UTC) + timedelta(seconds=CHAT_DEADLINE_SECONDS),
            idempotency_key=f"ui:chat:{uuid.uuid4().hex}",
        ),
        skill=skill,
        input=payload,
    )
    result = await SEND(TARGET, request)
    if result.status == TaskStatus.COMPLETED:
        return result.output or {}
    if result.status == TaskStatus.CANCELED:
        raise ApiError(504, "UPSTREAM_TIMEOUT", "마케팅 에이전트가 제때 응답하지 않았다")
    status, code = CHAT_REFUSALS.get(result.outcome or "", (502, "UPSTREAM_FAILED"))
    message = (result.output or {}).get(
        "message"
    ) or f"마케팅이 요청을 처리하지 못했다 ({result.outcome})"
    raise ApiError(status, code, str(message))
