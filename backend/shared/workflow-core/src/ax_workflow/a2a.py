"""A2A 핸들러가 같이 쓰는 결과 모양 (분리 5단계: planning handlers.py에서 옮김)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.planning import HandoffKnownInput, HandoffKnownOutput
from ax_workflow.core import GateRefused


def _invalid(request: AgentRequest, exc: ValidationError) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome="invalid_input",
        output={"message": str(exc)},
    )


def _requester(request: AgentRequest) -> str:
    return request.meta.requester or "orchestrator"


def _missing_key(request: AgentRequest) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome="missing_idempotency_key",
        output={"message": "상태 변경 skill에는 idempotency_key가 필요하다"},
    )


def _gate_rejected(request: AgentRequest, exc: GateRefused) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome="gate_rejected",
        output={"message": f"실행 관문이 {exc.action}을 허락하지 않아 실행하지 않았다"},
        evidence=Evidence(
            data_summary=f"execute({exc.action}) → {exc.decision.status}",
            ref_ids=[exc.decision.execution_id],
        ),
    )


def handoff_known_handler(
    get_service: Callable[[], Any],
) -> Callable[[AgentRequest], Awaitable[AgentResult]]:
    """기획·개발이 같이 받는 `handoff_known` (read): 준 멱등 키 중 이 서비스가 반영한 것.

    다시 켰을 때 맞추기가 상대에게 한 번 묻고, 빠진 키(상대가 상태를 잃음)만 다시 보낸다."""

    async def handoff_known(request: AgentRequest) -> AgentResult:
        try:
            inp = HandoffKnownInput.model_validate(request.input)
        except ValidationError as exc:
            return _invalid(request, exc)
        known = get_service().handoff_known(inp.keys)
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            output=HandoffKnownOutput(known=known).model_dump(mode="json"),
            evidence=Evidence(
                queries=["중복 거르기 기록(idempotency_key)에 있는 키"],
                data_summary=f"넘기기 {len(inp.keys)}건 중 {len(known)}건 반영함",
            ),
        )

    return handoff_known
