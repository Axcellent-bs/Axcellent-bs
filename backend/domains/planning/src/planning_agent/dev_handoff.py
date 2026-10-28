"""기획 → 개발 넘기기: planning `start_dev` → dev 서비스, dev `receive_dev_result` → planning.

같은 기획개발 앱을 두 서비스(planning 8101, dev 8111)로 따로 띄우면 메모리가 나뉜다. 그래서 확정된
기획서와 작업을 dev로 보내고, dev가 만든 PR·작업 상태·요청 상태를 planning으로 돌려준다.

- 서비스 역할(`PLANNING_ROLE`)이 있을 때만 보낸다. 합친 모드(역할 없음)는 보내지 않는다.
- 결과 통지(`result_notify`)와 달리 목 모드에서도 보낸다. 두 서비스가 같이 떠 있어야 이어진다.
- 받는 서비스는 `call_agent`가 skill로 정한다(`route_target`: start_dev → dev, receive_dev_result
  → planning). 원래 요청 meta를 쓰지 않고 `path`를 비운 새 요청으로 보낸다(LOOP_DETECTED 방지).
- 실패는 상태 변경을 되돌리지 않는다. 서비스가 결과 한 줄을 활동에 남긴다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus

START_DEV = "start_dev"
RECEIVE_DEV_RESULT = "receive_dev_result"
ROLES = ("planning", "dev")
CALL_TIMEOUT_SECONDS = 10

# (skill, context_id, tenant_id, 입력, idempotency_key) → 보낸 결과 한 줄 (활동 기록용)
DevSender = Callable[[str, str, str, BaseModel, str], Awaitable[str]]


def start_dev_key(context_id: str, spec_id: str) -> str:
    return f"{context_id}:start_dev:{spec_id}"


def dev_result_key(context_id: str, spec_id: str, fingerprint: str) -> str:
    return f"{context_id}:dev_result:{spec_id}:{fingerprint}"


async def send_to_service(
    skill: str, context_id: str, tenant_id: str, payload: BaseModel, idempotency_key: str
) -> str:
    """planning 부서 이름으로 부르면 `call_agent`가 skill을 맡은 서비스로 보낸다."""
    meta = RequestMeta(
        context_id=context_id,
        tenant_id=tenant_id,
        token="replaced-by-call_agent",
        path=[],
        deadline=datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS),
        idempotency_key=idempotency_key,
    )
    out = await call_agent(
        "planning", AgentRequest(meta=meta, skill=skill, input=payload.model_dump(mode="json"))
    )
    if out.status == TaskStatus.COMPLETED:
        return "보냄"
    return f"보내지 못함 ({out.status.value} {out.outcome or ''})".strip()
