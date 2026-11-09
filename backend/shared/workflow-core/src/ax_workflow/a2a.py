"""A2A 핸들러가 같이 쓰는 결과 모양 (분리 5단계: planning handlers.py에서 옮김)."""

from __future__ import annotations

from pydantic import ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
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
