"""기획이 되물었는데 재무 기록으로 못 답한 질문(`needs_human`)을 사람이 화면에서 답한다 (11/13).

질문을 남기고 → 목록·지금 할 일에 보이고 → 사람이 답하면 기획 `receive_planning_answer`로 보낸다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from finance_agent import finance_api, handlers, persist, planning_questions
from finance_agent.main import DECISION_PATHS, app
from finance_agent.sources import MockSources

HUMAN = {"Authorization": "Bearer human:taeyoon"}
BODY = {"title": "PG 정산 파일을 매일 자동으로 받아 오기", "evidence": "수동 업로드 7회"}


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
    def __init__(self, answer_ok: bool = True) -> None:
        self.answer_ok = answer_ok
        self.calls: list[AgentRequest] = []

    async def __call__(self, dept: str, req: AgentRequest) -> AgentResult:
        self.calls.append(req)
        if req.skill == "create_ticket":
            out = {"request_id": "REQ-7", "context_id": "ctx_req_0007", "outcome": "created"}
            return AgentResult(
                meta=req.meta, status=TaskStatus.COMPLETED, outcome="created", output=out
            )
        if not self.answer_ok:
            return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="upstream_error")
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, outcome="applied", output={})


def _ask(client: TestClient, about: str = "done") -> dict:
    body = {
        "meta": {
            "context_id": "ctx_req_0007",
            "token": "mock:planning",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "answer_planning_question",
        "input": {
            "question_id": "Q-REQ-7-1",
            "request_id": "REQ-7",
            "question": "완료 기준을 무엇으로 볼까요?",
            "about": about,
        },
    }
    return client.post("/a2a", json=body).json()


def test_a_question_finance_cannot_answer_waits_for_a_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePlanning()
    monkeypatch.setattr(finance_api, "call_agent", fake)
    client = TestClient(app)
    client.post("/api/finance/planning-requests", json=BODY, headers=HUMAN)
    assert _ask(client)["outcome"] == "needs_human"
    _ask(client)  # 같은 질문이 다시 와도 한 건
    listed = client.get("/api/finance/planning-questions").json()["data"]
    assert listed["waiting"] == 1
    [q] = listed["questions"]
    assert (q["question_id"], q["request_id"], q["status"]) == ("Q-REQ-7-1", "REQ-7", "waiting")
    todo = client.get("/api/finance/overview").json()["data"]["todo"]
    item = next(t for t in todo if t["kind"] == "planning_question")
    assert item["action"] == {"type": "planning_answer"} and item["count"] == 1

    url = "/api/finance/planning-questions/Q-REQ-7-1/answer"
    assert client.post(url, json={"answer": "x"}).status_code == 401
    assert client.post(url, json={"answer": ""}, headers=HUMAN).status_code == 400
    res = client.post(url, json={"answer": "매일 아침 9시 전에 받아 오면 완료"}, headers=HUMAN)
    assert res.status_code == 200, res.text
    sent = fake.calls[-1]
    assert sent.skill == "receive_planning_answer"
    assert sent.input["question_id"] == "Q-REQ-7-1" and sent.input["answered_by"] == "human"
    assert sent.input["answer"] == "매일 아침 9시 전에 받아 오면 완료"
    assert sent.meta.idempotency_key
    after = client.get("/api/finance/planning-questions").json()["data"]
    assert after["waiting"] == 0 and after["questions"][0]["answered_by"] == "taeyoon"
    todo = client.get("/api/finance/overview").json()["data"]["todo"]
    assert all(t["kind"] != "planning_question" for t in todo)
    assert client.post(url, json={"answer": "또"}, headers=HUMAN).status_code == 409


def test_an_answerable_evidence_question_is_not_left_for_a_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finance_api, "call_agent", FakePlanning())
    client = TestClient(app)
    client.post("/api/finance/planning-requests", json=BODY, headers=HUMAN)
    assert _ask(client, about="evidence")["outcome"] == "answered"
    assert client.get("/api/finance/planning-questions").json()["data"]["questions"] == []


def test_planning_down_keeps_the_question_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(finance_api, "call_agent", FakePlanning(answer_ok=False))
    client = TestClient(app)
    client.post("/api/finance/planning-requests", json=BODY, headers=HUMAN)
    _ask(client)
    res = client.post(
        "/api/finance/planning-questions/Q-REQ-7-1/answer", json={"answer": "a"}, headers=HUMAN
    )
    assert res.status_code == 502
    assert client.get("/api/finance/planning-questions").json()["data"]["waiting"] == 1


def test_waiting_questions_survive_a_restart(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(finance_api, "call_agent", FakePlanning())
    client = TestClient(app)
    client.post("/api/finance/planning-requests", json=BODY, headers=HUMAN)
    _ask(client)
    path = tmp_path / "state.json"
    persist.save(handlers.state(), path)
    handlers.reset_state(MockSources())
    assert persist.load(handlers.state(), path)
    assert list(handlers.state().records.planning_questions) == ["Q-REQ-7-1"]


def test_the_answer_path_is_open_to_the_screen() -> None:
    assert DECISION_PATHS.match("/api/finance/planning-questions/Q-REQ-7-1/answer")
    assert not DECISION_PATHS.match("/api/finance/planning-questions")
