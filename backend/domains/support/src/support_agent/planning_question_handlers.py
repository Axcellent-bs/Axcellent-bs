"""기획 되묻기 skill 핸들러 (읽기 전용): 문의 서비스가 보낸 변경 요청의 근거를 기록으로 답한다."""

from __future__ import annotations

from datetime import UTC, datetime

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence
from ax_contracts.schemas.planning import AnswerPlanningQuestionInput
from support_agent import handlers as h
from support_agent import planning_questions
from support_agent.planning_question_schemas import (
    ListPlanningQuestionsInput,
    ListPlanningQuestionsOutput,
)


def _remember_unanswered(
    q: AnswerPlanningQuestionInput, status: str, request: AgentRequest
) -> None:
    """기록으로 답하지 못한 질문(`needs_human`)만 사람이 나중에 답할 수 있게 적어 둔다.

    **이것이 이 skill이 하는 유일한 쓰기다.** 받은 질문을 적는 기록이고(같은 질문은 한 번만,
    개인정보는 가림) 사업 상태를 바꾸지 않아 `execute()`를 거치지 않는다. 기획은 기다리지 않는다.
    """
    if status == "needs_human":
        h.PLANNING_QUESTIONS.record(q, request.meta.tenant_id, request.meta.context_id)


async def answer_planning_question(request: AgentRequest) -> AgentResult:
    """기획이 되묻는 근거 질문에 기록으로 답한다 (grade read).

    사업 상태는 바꾸지 않는다. 다만 기록으로 답하지 못하면(`needs_human`) 받은 질문을 적어 둔다
    (`_remember_unanswered`, 이 skill의 유일한 쓰기).
    """
    parsed = h._parse(request, AnswerPlanningQuestionInput)
    if isinstance(parsed, AgentResult):
        return parsed
    out = planning_questions.answer_question(
        parsed, request.meta.tenant_id, request.meta.context_id, h.CHANGES
    )
    _remember_unanswered(parsed, out.status, request)
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


async def list_planning_questions(request: AgentRequest) -> AgentResult:
    """사람이 아직 답하지 못한 기획 질문 목록 (grade read, 요청한 회사의 질문만)."""
    parsed = h._parse(request, ListPlanningQuestionsInput)
    if isinstance(parsed, AgentResult):
        return parsed
    tenant = request.meta.tenant_id
    items = h.PLANNING_QUESTIONS.all(tenant)
    open_count = sum(1 for r in items if r.status == "open")
    if parsed.status is not None:
        items = [r for r in items if r.status == parsed.status]
    if parsed.request_id is not None:
        items = [r for r in items if r.request_id == parsed.request_id]
    return h._done(
        request,
        ListPlanningQuestionsOutput(items=items, open_count=open_count).model_dump(mode="json"),
    )


PLANNING_QUESTION_HANDLERS = {
    "answer_planning_question": answer_planning_question,
    "list_planning_questions": list_planning_questions,
}
