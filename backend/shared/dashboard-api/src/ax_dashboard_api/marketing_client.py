"""마케팅 캠페인 skill 호출 (A2A). 화면의 캠페인 시작·결과·취소가 쓴다.

재무 판단과 안전 관문은 마케팅 skill 안에서 일어난다. 보류·거절은 오류가 아니라 판단 결과라
화면이 이유를 보여 줄 수 있게 그대로 돌려준다. 닿지 못한 경우만 API 오류로 바꾼다.
"""

from __future__ import annotations

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
