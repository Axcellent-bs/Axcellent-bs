"""재무 → 기획 개선 요청 (`POST /api/finance/planning-requests`).

사람이 정한 요청만 안전 관문을 거쳐 기획 `create_ticket`으로 보내고, 보낸 근거를 기록해
기획이 되물으면(`answer_planning_question`) 그 기록으로 답한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.planning import AnswerPlanningQuestionInput
from finance_agent import finance_api, handlers, persist, planning_questions
from finance_agent.main import app
from finance_agent.sources import MockSources

HUMAN = {"Authorization": "Bearer human:taeyoon"}
BODY = {
    "title": "PG 정산 파일을 매일 자동으로 받아 오기",
    "evidence": "지난주 PG 대사 예외 4건이 파일을 늦게 올려 하루 늦게 잡혔다\n수동 업로드 7회",
    "evidence_ids": ["fin:recon:2026-W45"],
}


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch, fixed_now) -> Iterator[None]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("FINANCE_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("AX_SAFETY_URL", raising=False)
    handlers.reset_state(MockSources())
    planning_questions.STORE.clear()
    yield
    planning_questions.STORE.clear()
    handlers.reset_state(MockSources())


class FakePlanning:
    def __init__(self, outcome: str = "created") -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, AgentRequest]] = []

    async def __call__(self, dept: str, req: AgentRequest) -> AgentResult:
        self.calls.append((dept, req))
        output = {"request_id": "REQ-7", "context_id": "ctx_req_planning_7", "outcome": "created"}
        if self.outcome == "fail":
            return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="upstream_error")
        return AgentResult(
            meta=req.meta, status=TaskStatus.COMPLETED, outcome="created", output=output
        )


def _ask(question_ctx: str, request_id: str) -> str:
    meta = RequestMeta(
        context_id=question_ctx,
        token="mock:planning",
        path=["planning"],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    q = AnswerPlanningQuestionInput.model_validate(
        {
            "question_id": "Q-REQ-7-1",
            "request_id": request_id,
            "question": "근거를 알려 주세요",
            "about": "evidence",
        }
    )
    return planning_questions.answer_question(q, meta.tenant_id, question_ctx).status


def test_a_person_sends_a_request_to_planning_and_planning_questions_get_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePlanning()
    monkeypatch.setattr(finance_api, "call_agent", fake)
    client = TestClient(app)
    res = client.post("/api/finance/planning-requests", json=BODY, headers=HUMAN)
    assert res.status_code == 200, res.text
    row = res.json()["data"]
    assert (row["request_id"], row["outcome"], row["by"]) == ("REQ-7", "created", "taeyoon")
    assert row["facts"] == BODY["evidence"].splitlines()
    dept, req = fake.calls[0]
    assert (dept, req.skill) == ("planning", "create_ticket")
    assert req.input["title"] == BODY["title"] and req.input["source_ref"] == "fin_req_0001"
    listed = client.get("/api/finance/planning-requests").json()["data"]["requests"]
    assert [r["request_id"] for r in listed] == ["REQ-7"]
    # 기획이 되물으면 이제 "모름"이 아니라 보낸 근거로 답한다
    assert _ask("ctx_req_planning_7", "REQ-7") == "answered"


def test_only_a_person_can_send_and_bad_input_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePlanning()
    monkeypatch.setattr(finance_api, "call_agent", fake)
    client = TestClient(app)
    assert client.post("/api/finance/planning-requests", json=BODY).status_code == 401
    bad = client.post("/api/finance/planning-requests", json={"title": ""}, headers=HUMAN)
    assert bad.status_code == 400
    assert fake.calls == []


def test_planning_down_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(finance_api, "call_agent", FakePlanning("fail"))
    client = TestClient(app)
    res = client.post("/api/finance/planning-requests", json=BODY, headers=HUMAN)
    assert res.status_code == 502
    assert handlers.state().records.planning_requests == []


def test_sent_requests_survive_a_restart(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(finance_api, "call_agent", FakePlanning())
    TestClient(app).post("/api/finance/planning-requests", json=BODY, headers=HUMAN)
    path = tmp_path / "state.json"
    persist.save(handlers.state(), path)
    handlers.reset_state(MockSources())
    planning_questions.STORE.clear()
    assert persist.load(handlers.state(), path)
    assert [r["request_id"] for r in handlers.state().records.planning_requests] == ["REQ-7"]
    assert _ask("ctx_req_planning_7", "REQ-7") == "answered"


def test_the_endpoint_is_open_to_the_screen_for_posts() -> None:
    from finance_agent.main import DECISION_PATHS

    for path in ("planning-requests", "pg/file", "pg/clear"):
        assert DECISION_PATHS.match(f"/api/finance/{path}")
