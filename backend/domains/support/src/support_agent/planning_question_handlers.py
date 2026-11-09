"""기획 되묻기 skill 핸들러 (읽기 전용): 문의 서비스가 보낸 변경 요청의 근거를 기록으로 답한다."""

from __future__ import annotations

from datetime import UTC, datetime

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence
from ax_contracts.schemas.planning import AnswerPlanningQuestionInput
from support_agent import handlers as h
from support_agent import planning_questions


async def answer_planning_question(request: AgentRequest) -> AgentResult:
    """기획이 되묻는 근거 질문에 문의 서비스가 보낸 요청의 기록으로 답한다 (grade read)."""
    parsed = h._parse(request, AnswerPlanningQuestionInput)
    if isinstance(parsed, AgentResult):
        return parsed
    out = planning_questions.answer_question(
        parsed, request.meta.tenant_id, request.meta.context_id, h.CHANGES
    )
    return h._done(
        request,
        out.model_dump(mode="json"),
        outcome=out.status,
        evidence=Evidence(
            queries=[f"support.change_requests request_id={parsed.request_id}"],
            data_summary=f"{parsed.question_id} → {out.status}",
            collected_at=datetime.now(UTC),
            ref_ids=out.evidence_ids,
        ),
    )


PLANNING_QUESTION_HANDLERS = {"answer_planning_question": answer_planning_question}
