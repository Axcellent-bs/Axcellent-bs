"""이슈 단계 시간의 판정은 인사 AI가 한다. 아카이브는 시간을 카드에 기록하고, 판정(정상/확인 필요)은
인사의 읽기 skill `judge_step_timings`를 A2A로 불러 받아 카드에 기준값 버전과 같이 둔다.

인사가 응답하지 않으면(꺼짐·시간 초과·기준값 파일을 못 읽음) 판정을 지어내지 않고 비워 둔다.
시각 기록은 막지 않는다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_agent_template.client import call_agent
from ax_contracts.schemas import (
    AgentRequest,
    JudgeStepTimingsOutput,
    RequestMeta,
    TaskStatus,
)

CALL_TIMEOUT_SECONDS = 10
TARGET = "hr"  # 부서 이름으로 부르면 skill을 맡은 서비스로 간다 (services.py)
SKILL = "judge_step_timings"

# (요청 메타, 단계 시간 목록) -> 판정 묶음. 판정할 수 없으면 None
JudgeFn = Callable[[RequestMeta, list[dict[str, Any]]], Awaitable[dict[str, Any] | None]]

# 판정을 카드에 저장한 뒤 반복 확인을 인사 AI에 맡긴다. (요청 메타, 이슈 번호) -> 했는지
DiagnoseFn = Callable[[RequestMeta, str], Awaitable[bool]]
DIAGNOSE_SKILL = "request_agent_diagnosis"


async def judge_via_hr(meta: RequestMeta, timings: list[dict[str, Any]]) -> dict[str, Any] | None:
    deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS))
    sent = AgentRequest(
        meta=RequestMeta(
            context_id=meta.context_id,
            tenant_id=meta.tenant_id,
            token="replaced-by-call_agent",
            path=list(meta.path),
            deadline=deadline,
        ),
        skill=SKILL,
        input={"step_timings": timings},
    )
    try:
        result = await call_agent(TARGET, sent)
    except Exception:  # noqa: BLE001 - 판정 연결이 안 돼도 시각 기록은 막지 않는다
        return None
    if result.status != TaskStatus.COMPLETED:
        return None
    try:
        return JudgeStepTimingsOutput.model_validate(result.output).model_dump(mode="json")
    except ValueError:
        return None


async def request_diagnosis_via_hr(meta: RequestMeta, context_id: str) -> bool:
    """판정이 카드에 저장된 이슈를 인사 AI에 알려, 같은 단계의 확인 필요가 반복됐으면 총괄에 진단을
    요청하게 한다. 인사가 응답하지 않아도 이슈 시각·판정 기록은 막지 않는다(실패하면 False)."""
    deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS))
    sent = AgentRequest(
        meta=RequestMeta(
            context_id=meta.context_id,
            tenant_id=meta.tenant_id,
            token="replaced-by-call_agent",
            path=list(meta.path),
            deadline=deadline,
        ),
        skill=DIAGNOSE_SKILL,
        input={"context_id": context_id},
    )
    try:
        result = await call_agent(TARGET, sent)
    except Exception:  # noqa: BLE001 - 반복 확인이 안 돼도 시각 기록은 막지 않는다
        return False
    return result.status == TaskStatus.COMPLETED
