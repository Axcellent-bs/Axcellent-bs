"""화면의 기획 AI 대화 경로 (`/api/dashboard/planning-chat` → planning `planning_chat_*`)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import planning_chat
from ax_dashboard_api.main import app

THREAD = {"id": "planchat-1", "status": "answering", "messages": [], "who": "tester"}


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.status = TaskStatus.COMPLETED
        self.outcome: str | None = None
        self.output: dict = {"thread": THREAD, "threads": [THREAD]}

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append((target, request))
        return AgentResult(
            meta=request.meta, status=self.status, outcome=self.outcome, output=self.output
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake()
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setattr(planning_chat, "SEND", f)
    return f


@pytest.fixture
def client(fake: Fake) -> TestClient:
    return TestClient(app)


URL = "/api/dashboard/planning-chat"


def test_send_passes_the_token_owner_not_a_body_field(client: TestClient, fake: Fake) -> None:
    res = client.post(URL, json={"message": "받은 요청 몇 건이야", "who": "someone-else"})
    assert res.status_code == 200 and res.json()["data"]["status"] == "answering"
    target, request = fake.calls[0]
    assert (target, request.skill) == ("planning", "planning_chat_send")
    assert request.input == {"who": "tester", "message": "받은 요청 몇 건이야"}  # 토큰의 사람이 who


def test_list_get_close_go_to_planning(client: TestClient, fake: Fake) -> None:
    assert client.get(URL).json()["data"][0]["id"] == "planchat-1"
    assert client.get(f"{URL}/planchat-1").json()["data"]["id"] == "planchat-1"
    assert client.post(f"{URL}/planchat-1/close").status_code == 200
    assert [c[1].skill for c in fake.calls] == [
        "planning_chat_list",
        "planning_chat_get",
        "planning_chat_close",
    ]
    assert fake.calls[2][1].input == {"who": "tester", "thread_id": "planchat-1"}


def test_reads_need_a_human_token_too(client: TestClient, fake: Fake) -> None:
    bad = {"authorization": "Bearer nope"}
    assert client.get(URL, headers=bad).status_code in (401, 403)
    assert client.get(f"{URL}/planchat-1", headers=bad).status_code in (401, 403)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("outcome", "status", "code"),
    [
        ("llm_not_configured", 409, "LLM_NOT_CONFIGURED"),
        ("forbidden", 403, "FORBIDDEN"),
        ("not_found", 404, "NOT_FOUND"),
        ("thread_closed", 409, "THREAD_CLOSED"),
        ("thread_busy", 409, "THREAD_BUSY"),
    ],
)
def test_refusals_become_error_bodies(
    client: TestClient, fake: Fake, outcome: str, status: int, code: str
) -> None:
    fake.status, fake.outcome, fake.output = TaskStatus.FAILED, outcome, {"message": "이유"}
    res = client.post(URL, json={"message": "안녕"})
    assert res.status_code == status
    assert res.json()["error"] == {"code": code, "message": "이유"}


def test_bad_body_stops_before_planning(client: TestClient, fake: Fake) -> None:
    assert client.post(URL, json={"message": ""}).status_code == 422
    assert client.post(URL, json={"message": "x" * 2001}).status_code == 422
    assert fake.calls == []
