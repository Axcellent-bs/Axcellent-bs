"""기획 → data 되묻기 `answer_planning_question` (이슈 #266).

data는 자기가 보낸 요청의 기록으로만 답한다. 기록에 없으면 unknown, 기록으로 못 답하면 needs_human.
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
from data_agent import handlers, planning_questions
from data_agent.planning_questions import human_answer_request, record_sent_request

CTX = "ctx_req_20261102_0001"
TENANT = "catbooks"
QUESTION = "이 요청의 근거(건수·사례 ID·발생 시각)를 알려 주세요"


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
    request_id: str = "REQ-7",
    about: str | None = "evidence",
    options: list[str] | None = None,
    ctx: str = CTX,
    tenant: str = TENANT,
) -> tuple[TaskStatus, AnswerPlanningQuestionOutput, list[str]]:
    q = AnswerPlanningQuestionInput(
        question_id=f"Q-{request_id}-1",
        request_id=request_id,
        spec_id="SPEC-3",
        question=QUESTION,
        about=about,
        options=options or [],
    )
    request = AgentRequest(
        meta=_meta(ctx, tenant), skill="answer_planning_question", input=q.model_dump(mode="json")
    )
    result = asyncio.run(handlers.HANDLERS["answer_planning_question"](request))
    out = AnswerPlanningQuestionOutput.model_validate(result.output)
    refs = result.evidence.ref_ids if result.evidence else []
    return result.status, out, refs


def _record(facts: list[str] | None = None, ids: list[str] | None = None) -> None:
    record_sent_request(TENANT, CTX, "REQ-7", facts=facts, evidence_ids=ids)


FACT = "결제 실패율 기준선 대비 3.2배 (2026-11-02 14:05~14:20 KST)"


def test_answered_from_own_record() -> None:
    _record(
        facts=[FACT, "실패 주문 41건"],
        ids=["anomaly:pay_fail_rate:20261102T0505Z", "order:O-1001"],
    )
    status, out, refs = _ask()
    assert status == TaskStatus.COMPLETED
    assert out.status == "answered"
    assert out.answered_by == "agent"
    assert out.answer == f"{FACT}; 실패 주문 41건"
    assert out.evidence_ids == ["anomaly:pay_fail_rate:20261102T0505Z", "order:O-1001"]
    assert refs == out.evidence_ids


def test_unknown_when_data_did_not_send_the_request() -> None:
    """data가 보낸 기록이 없으면 지어내지 않는다 (지금은 보내는 코드가 없어 늘 이 경우다)."""
    status, out, _ = _ask()
    assert status == TaskStatus.COMPLETED
    assert out.status == "unknown"
    assert out.answer is None and out.evidence_ids == []


def test_unknown_when_context_or_tenant_does_not_match() -> None:
    _record(facts=["실패 주문 41건"])
    assert _ask(ctx="ctx_req_other")[1].status == "unknown"
    assert _ask(tenant="other-shop")[1].status == "unknown"
    assert _ask(request_id="REQ-8")[1].status == "unknown"


def test_needs_human_when_record_has_no_facts() -> None:
    _record(facts=[])
    status, out, _ = _ask()
    assert status == TaskStatus.COMPLETED
    assert out.status == "needs_human"
    assert out.answer is None


@pytest.mark.parametrize(
    ("about", "options"), [("done", []), (None, []), ("evidence", ["예", "아니오"])]
)
def test_needs_human_for_questions_the_record_cannot_answer(
    about: str | None, options: list[str]
) -> None:
    _record(facts=["실패 주문 41건"])
    assert _ask(about=about, options=options)[1].status == "needs_human"


def test_invalid_input_is_refused() -> None:
    request = AgentRequest(meta=_meta(), skill="answer_planning_question", input={"question": "?"})
    result = asyncio.run(handlers.HANDLERS["answer_planning_question"](request))
    assert result.status == TaskStatus.FAILED
    assert result.outcome == "invalid_input"


def test_human_answer_goes_to_planning_with_idempotency_key() -> None:
    q = AnswerPlanningQuestionInput(
        question_id="Q-REQ-7-1", request_id="REQ-7", question=QUESTION, about="evidence"
    )
    old = _meta().model_copy(update={"deadline": datetime.now(UTC) - timedelta(hours=1)})
    req = human_answer_request(old, q, "지난주 주문 실패 문의 12건", ["inquiry:I-55"])
    assert req.skill == "receive_planning_answer"
    assert req.meta.idempotency_key == f"{CTX}:receive_planning_answer:Q-REQ-7-1"
    assert req.meta.deadline > datetime.now(UTC)  # 사람이 늦게 답해도 새 기한
    sent = ReceivePlanningAnswerInput.model_validate(req.input)
    assert sent.answered_by == "human"
    assert sent.answer == "지난주 주문 실패 문의 12건"
    assert sent.evidence_ids == ["inquiry:I-55"]
    again = human_answer_request(_meta(), q, "지난주 주문 실패 문의 12건")
    assert again.meta.idempotency_key == req.meta.idempotency_key
