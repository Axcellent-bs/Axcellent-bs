"""개발 AI 직접 대화 중계 (`/api/dashboard/dev-chat` → A2A `dev_chat_*`).

대화 자체(저장·답·작업 띄우기·열람 규칙)는 dev가 한다 (`domains/dev/.../dev_chat.py`).
여기서는 사람 토큰 이름을 `who`로 채워 넘기고, 거절(outcome)을 화면이 읽는 오류 모양으로 바꾼다.
기획 AI 직접 대화(`planning_chat.py`)와 같은 방식이다.
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
TARGET = "dev"
DEADLINE_SECONDS = 30  # 보내기는 저장만 하고 바로 돌아온다 (답은 뒤에서 붙는다)

# 개발 대화 skill이 거절할 때 돌려주는 outcome → (HTTP 상태, 오류 코드)
REFUSALS = {
    "invalid_input": (400, "INVALID_INPUT"),
    "forbidden": (403, "FORBIDDEN"),
    "not_found": (404, "NOT_FOUND"),
    "llm_not_configured": (409, "LLM_NOT_CONFIGURED"),
    "thread_closed": (409, "THREAD_CLOSED"),
    "thread_busy": (409, "THREAD_BUSY"),
    "wrong_role": (409, "WRONG_ROLE"),
}

SendFn = Callable[[str, AgentRequest], Awaitable[AgentResult]]
SEND: SendFn = call_agent  # 테스트에서 바꿀 수 있다


async def call(skill: str, payload: dict[str, Any]) -> dict[str, Any]:
    config.set_agent_name(AGENT_NAME)
    request = AgentRequest(
        meta=RequestMeta(
            context_id=f"ctx_devchat_{uuid.uuid4().hex[:12]}",
            token=config.agent_token(AGENT_NAME),
            deadline=datetime.now(UTC) + timedelta(seconds=DEADLINE_SECONDS),
            idempotency_key=f"ui:devchat:{uuid.uuid4().hex}",
        ),
        skill=skill,
        input=payload,
    )
    result = await SEND(TARGET, request)
    if result.status == TaskStatus.COMPLETED:
        return result.output or {}
    if result.status == TaskStatus.CANCELED:
        raise ApiError(504, "UPSTREAM_TIMEOUT", "개발 에이전트가 제때 응답하지 않았다")
    if result.status == TaskStatus.REJECTED:
        raise ApiError(403, "UPSTREAM_REJECTED", f"개발이 요청을 거부했다 ({result.outcome})")
    status, code = REFUSALS.get(result.outcome or "", (502, "UPSTREAM_FAILED"))
    message = (result.output or {}).get("message") or f"개발이 처리하지 못했다 ({result.outcome})"
    raise ApiError(status, code, str(message))
