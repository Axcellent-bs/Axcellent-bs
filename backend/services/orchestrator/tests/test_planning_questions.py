"""기획 → 총괄 되묻기 `answer_planning_question` (이슈 #268).

총괄은 자기가 보낸 원인 확인 요청(`cause_check`)의 기록으로만 답한다.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.config import set_agent_name
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.planning import (
    AnswerPlanningQuestionInput,
    AnswerPlanningQuestionOutput,
    ReceivePlanningAnswerInput,
)
from ax_orchestrator.main import app, state
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.models import Incident
from ax_orchestrator.planning_questions import human_answer_request
from ax_orchestrator.plans.scenario_a import scenario_a

CTX = "ctx_req_20261102_0001"
QUESTION = "이 요청의 근거(건수·사례 ID·발생 시각)를 알려 주세요"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    manager = IncidentManager(None, scenario_a)  # type: ignore[arg-type]
    monkeypatch.setattr(state, "manager", manager)
    yield TestClient(app), manager
    set_agent_name("orchestrator")


def _incident(manager: IncidentManager, request_id: str = "REQ-7") -> Incident:
    inc = Incident(
        context_id="ctx_inc_test", kind="scenario_a", title="결제 장애", component="payment"
    )
    inc.outputs["cause_check"] = {"request_id": request_id, "context_id": CTX}
    inc.outputs["root_cause"] = {
        "summary": "결제 실패율이 기준선보다 높다",
        "needs_confirmation": True,
        "facts": [{"fact_id": "f1", "kind": "release", "ref": "pr:42"}],
        "hypotheses": [
            {"confidence": "mid", "explanation": "결제 모듈 배포 직후 오류", "fact_ids": ["f1"]}
        ],
    }
    manager.incidents[inc.context_id] = inc
    return inc


def _ask(
    http: TestClient,
    request_id: str = "REQ-7",
    about: str | None = "evidence",
    options: list[str] | None = None,
    ctx: str = CTX,
    requester: str = "planning",
) -> AgentResult:
    q = AnswerPlanningQuestionInput(
        question_id=f"Q-{request_id}-1",
        request_id=request_id,
        question=QUESTION,
        about=about,
        options=options or [],
    )
    meta = RequestMeta(
        context_id=ctx,
        token=f"mock:{requester}",
        path=[requester],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    body = AgentRequest(
        meta=meta, skill="answer_planning_question", input=q.model_dump(mode="json")
    )
    return AgentResult.model_validate(http.post("/a2a", json=body.model_dump(mode="json")).json())


def _out(res: AgentResult) -> AnswerPlanningQuestionOutput:
    return AnswerPlanningQuestionOutput.model_validate(res.output)


def test_answered_from_the_event_that_sent_the_request(client) -> None:
    http, manager = client
    _incident(manager)
    res = _ask(http)
    out = _out(res)
    assert res.status == TaskStatus.COMPLETED and res.outcome == "answered"
    assert out.answered_by == "agent"
    assert "결제 실패율이 기준선보다 높다" in out.answer
    assert "결제 모듈 배포 직후 오류" in out.answer
    assert out.evidence_ids == ["pr:42", "ctx_inc_test"]


def test_unknown_when_orchestrator_did_not_send_the_request(client) -> None:
    http, manager = client
    _incident(manager)
    assert _out(_ask(http, request_id="REQ-8")).status == "unknown"
    assert _out(_ask(http, ctx="ctx_req_other")).status == "unknown"


def test_unknown_without_any_event(client) -> None:
    http, _ = client
    out = _out(_ask(http))
    assert out.status == "unknown" and out.answer is None


@pytest.mark.parametrize(
    ("about", "options"), [("done", []), (None, []), ("evidence", ["예", "아니오"])]
)
def test_needs_human_for_questions_the_record_cannot_answer(client, about, options) -> None:
    http, manager = client
    _incident(manager)
    assert _out(_ask(http, about=about, options=options)).status == "needs_human"


def test_only_planning_may_ask(client) -> None:
    http, manager = client
    _incident(manager)
    assert _ask(http, requester="marketing").status != TaskStatus.COMPLETED


def test_human_answer_goes_to_planning_with_idempotency_key() -> None:
    q = AnswerPlanningQuestionInput(
        question_id="Q-REQ-7-1", request_id="REQ-7", question=QUESTION, about="evidence"
    )
    meta = RequestMeta(
        context_id=CTX,
        token="mock:orchestrator",
        path=["orchestrator"],
        deadline=datetime.now(UTC) - timedelta(hours=1),
    )
    req = human_answer_request(meta, q, "배포 담당 확인 결과 롤백 대상 맞음", ["pr:42"])
    assert req.skill == "receive_planning_answer"
    assert req.meta.idempotency_key == f"{CTX}:receive_planning_answer:Q-REQ-7-1"
    assert req.meta.deadline > datetime.now(UTC)
    assert ReceivePlanningAnswerInput.model_validate(req.input).answered_by == "human"
