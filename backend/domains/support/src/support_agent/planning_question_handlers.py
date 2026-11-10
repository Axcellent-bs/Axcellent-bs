"""기획 되묻기 skill 핸들러 (읽기 전용): 문의 서비스가 보낸 변경 요청의 근거를 기록으로 답한다."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError

from ax_agent_template.execute import execute
from ax_agent_template.server import error_result
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.planning import (
    AnswerPlanningQuestionInput,
    ReceivePlanningAnswerInput,
    ReceivePlanningAnswerOutput,
)
from support_agent import guard, planning_questions
from support_agent import handlers as h
from support_agent.planning_question_schemas import (
    ListPlanningQuestionsInput,
    ListPlanningQuestionsOutput,
    SendPlanningAnswerInput,
    SendPlanningAnswerOutput,
)

PLANNING = "planning"


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


async def send_planning_answer(request: AgentRequest) -> AgentResult:
    """사람이 답한 기획 질문을 기획 `receive_planning_answer`(A)로 보낸다 (grade A, `execute()`).

    - 이 회사가 받아 둔 열린 질문에만 답할 수 있다. 모르는 질문·이미 닫힌 질문은 보내지 않는다.
    - 답 문구에 개인정보가 있으면 보내지 않는다(기획 기록에 남지 않게 코드가 막는다).
    - 안전 관문을 먼저 거치고, 실행되지 않으면(보류·검증 중·거부) 아무것도 보내지 않는다.
    - 기획이 답을 받으면(`applied`·`duplicate`) 질문을 닫는다. 못 받으면 열어 두어 다시 보낸다.
    - 기획은 답 문구를 근거 데이터로만 쓰고 지시로 읽지 않는다.
    """
    parsed = h._parse(request, SendPlanningAnswerInput)
    if isinstance(parsed, AgentResult):
        return parsed
    tenant = request.meta.tenant_id
    record = h.PLANNING_QUESTIONS.get(tenant, parsed.question_id)
    if record is None:
        return error_result(
            request.meta, TaskStatus.FAILED, "unknown_question", "받아 둔 기획 질문이 아니다"
        )
    key = request.meta.idempotency_key or (
        f"{record.context_id}:send_planning_answer:{parsed.question_id}"
    )
    if key in h.STORE.results:  # 같은 요청의 재시도
        return h._done(request, h.STORE.results[key])
    if record.status == "answered":
        return error_result(
            request.meta, TaskStatus.FAILED, "already_answered", "이미 답해서 닫힌 질문이다"
        )
    if guard.find_pii(parsed.answer):
        return error_result(
            request.meta, TaskStatus.FAILED, "invalid_input", "답에 개인정보가 있어 보낼 수 없다"
        )

    decision = await execute(
        "send_planning_answer",
        {"question_id": parsed.question_id, "evidence_count": len(parsed.evidence_ids)},
        request.meta.model_copy(update={"idempotency_key": key}),
        Evidence(
            ref_ids=[parsed.question_id, record.request_id],
            data_summary=f"send_planning_answer {parsed.question_id}",
        ),
    )
    if decision.status != "executed":
        return h._done(
            request,
            {"execution": decision.model_dump(mode="json")},
            outcome=f"not_executed_{decision.status}",
        )

    receive_key = f"{record.context_id}:receive_planning_answer:{record.question_id}"
    receive = AgentRequest(
        meta=request.meta.model_copy(
            update={"context_id": record.context_id, "idempotency_key": receive_key}
        ),
        skill="receive_planning_answer",
        input=ReceivePlanningAnswerInput(
            question_id=record.question_id,
            request_id=record.request_id,
            answer=parsed.answer,
            answered_by="human",
            evidence_ids=parsed.evidence_ids,
        ).model_dump(mode="json"),
    )
    reply = await h.SEND(PLANNING, receive)
    if reply.status != TaskStatus.COMPLETED:
        return error_result(  # 질문은 열어 둔다. 다시 보내면 이어서 보낸다
            request.meta,
            TaskStatus.FAILED,
            "planning_unavailable",
            f"기획이 답을 받지 못했다 ({reply.outcome or reply.status.value})",
        )
    try:
        received = ReceivePlanningAnswerOutput.model_validate(reply.output)
    except ValidationError as exc:
        return error_result(request.meta, TaskStatus.FAILED, "invalid_output", str(exc)[:200])
    closed = record
    if received.outcome in ("applied", "duplicate"):
        closed = (
            h.PLANNING_QUESTIONS.mark_answered(tenant, record.question_id, parsed.author) or record
        )
    output = SendPlanningAnswerOutput(
        question_id=record.question_id,
        request_id=record.request_id,
        planning_outcome=received.outcome,
        status=closed.status,
        spec_id=received.spec_id,
    ).model_dump(mode="json")
    h.STORE.results[key] = output
    return h._done(
        request,
        output,
        outcome=received.outcome,
        evidence=Evidence(
            ref_ids=[record.question_id, record.request_id],
            data_summary=f"{record.question_id} 답 → 기획 {received.outcome}",
        ),
    )


PLANNING_QUESTION_HANDLERS = {
    "answer_planning_question": answer_planning_question,
    "list_planning_questions": list_planning_questions,
    "send_planning_answer": send_planning_answer,
}
