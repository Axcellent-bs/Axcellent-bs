"""게시 일시 중지 스위치 경로: 읽기는 누구나, 쓰기는 사람 토큰만, 이름은 토큰에서."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

URL = "/api/dashboard/marketing/publish-control"


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.status = TaskStatus.COMPLETED
        self.outcome = "updated"

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append((target, request))
        return AgentResult(
            meta=request.meta,
            status=self.status,
            outcome=self.outcome,
            output={"channel": "instagram", "paused": True},
            reason_code="human_input" if self.status == TaskStatus.INPUT_REQUIRED else None,
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake()
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setattr(marketing_client, "SEND", f)
    return f


@pytest.fixture
def client(fake: Fake) -> TestClient:
    return TestClient(app)


def test_get_calls_the_read_skill(client: TestClient, fake: Fake) -> None:
    fake.outcome = "state"
    response = client.get(URL)
    assert response.status_code == 200 and response.json()["data"]["outcome"] == "state"
    assert [(t, r.skill) for t, r in fake.calls] == [("marketing", "get_publish_control")]


def test_set_sends_the_human_name_from_the_token_not_the_body(
    client: TestClient, fake: Fake
) -> None:
    body = {"paused": True, "reason": "계정 정지 재고 대기", "updated_by": "남의 이름"}
    response = client.put(URL, json=body)
    assert response.status_code == 200
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "set_publish_control")
    assert request.input["paused"] is True and request.input["reason"] == "계정 정지 재고 대기"
    assert request.input["updated_by"] != "남의 이름" and request.input["updated_by"]
    assert request.input["channel"] == "instagram"


def test_set_needs_a_person_and_a_valid_body(client: TestClient, fake: Fake) -> None:
    bad = {"authorization": "Bearer nope"}
    assert client.put(URL, json={"paused": True}, headers=bad).status_code in (401, 403)
    assert client.put(URL, json={"paused": "yes-please"}).status_code == 422
    assert client.put(URL, json={"paused": True, "reason": "x" * 201}).status_code == 422
    assert fake.calls == []


def test_held_resume_is_passed_through_as_a_judgement(client: TestClient, fake: Fake) -> None:
    fake.status, fake.outcome = TaskStatus.INPUT_REQUIRED, "held"
    response = client.put(URL, json={"paused": False})
    assert response.status_code == 200 and response.json()["data"]["outcome"] == "held"
    fake.status, fake.outcome = TaskStatus.FAILED, "invalid_input"
    assert client.put(URL, json={"paused": False}).status_code == 400


def test_post_set_is_the_same_as_put(client: TestClient, fake: Fake) -> None:
    """화면은 GET과 다른 주소의 POST로 바꾼다(232 nginx가 메서드별로 연다)."""
    body = {"paused": True, "reason": "계정 정지 재고 대기", "updated_by": "남의 이름"}
    response = client.post(URL + "/set", json=body)
    assert response.status_code == 200
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "set_publish_control")
    assert request.input["paused"] is True and request.input["updated_by"] != "남의 이름"


def test_post_set_needs_a_person_and_a_valid_body(client: TestClient, fake: Fake) -> None:
    bad = {"authorization": "Bearer nope"}
    assert client.post(URL + "/set", json={"paused": True}, headers=bad).status_code in (401, 403)
    assert client.post(URL + "/set", json={"paused": "yes-please"}).status_code == 422
    assert fake.calls == []
