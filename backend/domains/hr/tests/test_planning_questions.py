"""기획 → 인사 되묻기 `answer_planning_question` (이슈 #267).

인사는 자기가 보낸 요청의 기록으로만 답한다. 기록에 없으면 unknown, 기록으로 못 답하면 needs_human.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.planning import (
    AnswerPlanningQuestionInput,
    AnswerPlanningQuestionOutput,
    ReceivePlanningAnswerInput,
)
from hr_agent import planning_questions
from hr_agent.handlers import HANDLERS
from hr_agent.planning_questions import human_answer_request, record_sent_request

CTX = "ctx_req_20261102_0001"
TENANT = "catbooks"
QUESTION = "이 요청의 근거(건수·사례 ID·발생 시각)를 알려 주세요"
FACT = "퇴사 예정자 3명의 인수인계가 미완료 (2026-11-02 기준)"


@pytest.fixture(autouse=True)
def _empty_store():
    planning_questions.STORE.clear()
    yield
    planning_questions.STORE.clear()


def _meta(ctx: str = CTX, tenant: str = TENANT) -> RequestMeta:
    return RequestMeta(
        context_id=ctx,
        tenant_id=tenant,
        token="mock:planning",
        path=["planning"],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


def _ask(
    svc,
    request_id: str = "REQ-7",
    about: str | None = "evidence",
    options: list[str] | None = None,
    ctx: str = CTX,
    tenant: str = TENANT,
):
    q = AnswerPlanningQuestionInput(
        question_id=f"Q-{request_id}-1",
        request_id=request_id,
        question=QUESTION,
        about=about,
        options=options or [],
    )
    request = AgentRequest(
        meta=_meta(ctx, tenant), skill="answer_planning_question", input=q.model_dump(mode="json")
    )
    result = asyncio.run(HANDLERS["answer_planning_question"](request))
    return result, AnswerPlanningQuestionOutput.model_validate(result.output)


def test_answered_from_own_record(svc) -> None:
    record_sent_request(
        TENANT, CTX, "REQ-7", facts=[FACT, "미완료 단계 5개"], evidence_ids=["ho:1"]
    )
    result, out = _ask(svc)
    assert result.status == TaskStatus.COMPLETED
    assert (out.status, out.answered_by) == ("answered", "agent")
    assert out.answer == f"{FACT}; 미완료 단계 5개"
    assert out.evidence_ids == ["ho:1"] == result.evidence.ref_ids


def test_unknown_when_hr_did_not_send_the_request(svc) -> None:
    """보낸 기록이 없으면 지어내지 않는다 (지금은 인사가 요청을 보내는 코드가 없어 늘 이 경우다)."""
    result, out = _ask(svc)
    assert result.status == TaskStatus.COMPLETED
    assert out.status == "unknown" and out.answer is None and out.evidence_ids == []


def test_unknown_when_context_tenant_or_request_does_not_match(svc) -> None:
    record_sent_request(TENANT, CTX, "REQ-7", facts=[FACT])
    assert _ask(svc, ctx="ctx_req_other")[1].status == "unknown"
    assert _ask(svc, tenant="other-shop")[1].status == "unknown"
    assert _ask(svc, request_id="REQ-8")[1].status == "unknown"


def test_needs_human_when_record_has_no_facts(svc) -> None:
    record_sent_request(TENANT, CTX, "REQ-7", facts=[])
    assert _ask(svc)[1].status == "needs_human"


@pytest.mark.parametrize(
    ("about", "options"), [("done", []), (None, []), ("evidence", ["예", "아니오"])]
)
def test_needs_human_for_questions_the_record_cannot_answer(svc, about, options) -> None:
    record_sent_request(TENANT, CTX, "REQ-7", facts=[FACT])
    assert _ask(svc, about=about, options=options)[1].status == "needs_human"


def test_invalid_input_is_refused(svc) -> None:
    request = AgentRequest(meta=_meta(), skill="answer_planning_question", input={"question": "?"})
    result = asyncio.run(HANDLERS["answer_planning_question"](request))
    assert result.status != TaskStatus.COMPLETED


def test_human_answer_goes_to_planning_with_idempotency_key() -> None:
    q = AnswerPlanningQuestionInput(
        question_id="Q-REQ-7-1", request_id="REQ-7", question=QUESTION, about="evidence"
    )
    old = _meta().model_copy(update={"deadline": datetime.now(UTC) - timedelta(hours=1)})
    req = human_answer_request(old, q, "인수인계 3건 미완료", ["ho:1"])
    assert req.skill == "receive_planning_answer"
    assert req.meta.idempotency_key == f"{CTX}:receive_planning_answer:Q-REQ-7-1"
    assert req.meta.deadline > datetime.now(UTC)
    body = ReceivePlanningAnswerInput.model_validate(req.input)
    assert (body.answered_by, body.evidence_ids) == ("human", ["ho:1"])
