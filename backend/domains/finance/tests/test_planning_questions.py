"""기획 → 재무 되묻기 `answer_planning_question` (이슈 #269)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from finance_agent import planning_questions
from finance_agent.handlers import HANDLERS
from finance_agent.main import load_card
from finance_agent.planning_questions import record_sent_request

CTX = "ctx_req_20261103_0001"
TENANT = "catbooks"
FACT = "10월 결산의 PG 수수료가 지난달보다 40% 늘었음"


@pytest.fixture(autouse=True)
def _empty_store():
    planning_questions.STORE.items.clear()
    yield
    planning_questions.STORE.items.clear()


def _ask(about: str | None = "evidence", ctx: str = CTX, options: list[str] | None = None):
    request = AgentRequest(
        meta=RequestMeta(
            context_id=ctx,
            tenant_id=TENANT,
            token="mock:planning",
            path=["planning"],
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        skill="answer_planning_question",
        input={
            "question_id": "Q-REQ-7-1",
            "request_id": "REQ-7",
            "question": "이 요청의 근거를 알려 주세요",
            "about": about,
            "options": options or [],
        },
    )
    return asyncio.run(HANDLERS["answer_planning_question"](request))


def test_card_opens_the_skill_to_planning_only() -> None:
    skill = next(s for s in load_card().skills if s.id == "answer_planning_question")
    assert (skill.grade, skill.allowed_requesters) == ("read", ["planning"])


def test_unknown_when_finance_did_not_send_the_request() -> None:
    result = _ask()
    assert (result.status, result.output["status"]) == (TaskStatus.COMPLETED, "unknown")
    assert result.output["answer"] is None


def test_unknown_when_the_context_does_not_match() -> None:
    record_sent_request(TENANT, CTX, "REQ-7", [FACT])
    assert _ask(ctx="ctx_req_other").output["status"] == "unknown"


def test_answered_from_the_record_with_evidence() -> None:
    record_sent_request(TENANT, CTX, "REQ-7", [FACT], ["closing:2026-10"])
    result = _ask()
    assert result.output["status"] == "answered"
    assert result.output["answer"] == FACT
    assert result.output["answered_by"] == "agent"
    assert result.output["evidence_ids"] == ["closing:2026-10"]
    assert result.evidence.ref_ids == ["closing:2026-10"]


@pytest.mark.parametrize(
    ("about", "options", "facts"),
    [("done", None, [FACT]), ("evidence", ["a", "b"], [FACT]), ("evidence", None, [])],
)
def test_needs_human_when_the_record_cannot_answer(about, options, facts) -> None:
    record_sent_request(TENANT, CTX, "REQ-7", facts)
    result = _ask(about, options=options)
    assert result.output["status"] == "needs_human"
    assert result.output["answer"] is None
