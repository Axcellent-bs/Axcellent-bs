"""진단(`diagnosed`)을 해당 에이전트에 개선 노트로 보낸다(공통 skill `receive_improvement_note`).

노트는 기록·알림이다. 받은 에이전트의 동작을 총괄이 바꾸지 않는다. 에이전트가 응답으로 `accepted`
(받음)·`duplicate`(이미 받음)·`rejected`(자기 일이 아님 등, 이유 포함)·`revoked`(거둠)를 돌려준다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.diagnosis import (
    ReceiveImprovementNoteInput,
    ReceiveImprovementNoteOutput,
)

CALL_TIMEOUT_SECONDS = 15
SKILL = "receive_improvement_note"


def note_id_of(request_id: str) -> str:
    return f"note-{request_id}"


def skill_of(step_id: str) -> str | None:
    """장애 대응 계획에서 이 단계가 부르는 skill 이름. 모르면 None."""
    from ax_orchestrator.plans.scenario_a import scenario_a

    plan = scenario_a()
    for step in [*plan.steps, *plan.late_steps]:
        if step.id == step_id:
            return step.skill
    return None


def build_note(row: dict[str, Any], *, revoke: bool = False) -> ReceiveImprovementNoteInput:
    """진단 한 건에서 노트를 만든다. 같은 진단은 항상 같은 `note_id`다."""
    diagnosis = row["diagnosis"]
    contexts: list[str] = []
    for item in diagnosis["items"]:
        if item["context_id"] not in contexts:
            contexts.append(item["context_id"])
    return ReceiveImprovementNoteInput(
        note_id=note_id_of(row["request_id"]),
        diagnosis_id=row["request_id"],
        agent=row["agent"],
        step_id=row["step_id"],
        step_label=row["step_label"],
        summary=diagnosis["summary"],
        findings=[f["text"] for f in diagnosis["findings"]],
        evidence_contexts=contexts,
        method=diagnosis.get("method", "rules"),
        issued_at=datetime.now(UTC),
        revoke=revoke,
        cause_known=row["status"] == "diagnosed",
        skill=skill_of(row["step_id"]),
    )


async def send_note_via_agent(
    note: ReceiveImprovementNoteInput, request_context: str
) -> ReceiveImprovementNoteOutput | None:
    """에이전트(부서 이름)에게 보낸다. 못 보냈거나 이상한 응답이면 None."""
    sent = AgentRequest(
        meta=RequestMeta(
            context_id=request_context,
            token="replaced-by-call_agent",
            path=[],  # 진단이 끝난 뒤 따로 시작하는 알림이다
            deadline=datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS),
            idempotency_key=f"improve:{note.agent}:{note.step_id}:{note.diagnosis_id}"
            + (":revoke" if note.revoke else ""),
        ),
        skill=SKILL,
        input=note.model_dump(mode="json"),
    )
    try:
        result = await call_agent(note.agent, sent)
    except Exception:  # noqa: BLE001
        return None
    if result.status != TaskStatus.COMPLETED:
        return None
    try:
        return ReceiveImprovementNoteOutput.model_validate(result.output)
    except ValueError:
        return None
