"""수동 게시 경로: 읽기는 누구나, 완료 기록은 사람 토큰만, 기록한 사람 이름은 토큰에서."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

PACKAGE = "/api/dashboard/marketing/manual-publish/spd_abc"
COMPLETE = f"{PACKAGE}/complete"
LINK = "https://www.instagram.com/p/CxAbc123/"


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.status = TaskStatus.COMPLETED
        self.outcome = "published"

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append((target, request))
        return AgentResult(
            meta=request.meta,
            status=self.status,
            outcome=self.outcome,
            output={"post_draft_id": "spd_abc"},
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


def test_package_calls_the_read_skill(client: TestClient, fake: Fake) -> None:
    fake.outcome = "package"
    response = client.get(PACKAGE)
    assert response.status_code == 200
    assert [(t, r.skill) for t, r in fake.calls] == [("marketing", "get_manual_publish_package")]
    assert fake.calls[0][1].input == {"post_draft_id": "spd_abc"}


def test_package_not_waiting_is_404(client: TestClient, fake: Fake) -> None:
    fake.status, fake.outcome = TaskStatus.FAILED, "not_manual_pending"
    assert client.get(PACKAGE).status_code == 404


def test_complete_sends_the_human_name_from_the_token_not_the_body(
    client: TestClient, fake: Fake
) -> None:
    body = {"permalink": LINK, "ai_label_confirmed": True, "posted_by": "남의 이름"}
    response = client.post(COMPLETE, json=body)
    assert response.status_code == 200
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "complete_manual_publish")
    sent = request.input
    assert sent["permalink"] == LINK and sent["ai_label_confirmed"] is True
    assert sent["post_draft_id"] == "spd_abc"
    assert sent["posted_by"] and sent["posted_by"] != "남의 이름"


def test_complete_needs_a_person_and_a_valid_body(client: TestClient, fake: Fake) -> None:
    bad = {"authorization": "Bearer nope"}
    body = {"permalink": LINK, "ai_label_confirmed": True}
    assert client.post(COMPLETE, json=body, headers=bad).status_code in (401, 403)
    assert client.post(COMPLETE, json={"permalink": LINK}).status_code == 422
    assert client.post(COMPLETE, json={**body, "ai_label_confirmed": "yes"}).status_code == 422
    assert client.post(COMPLETE, json={**body, "permalink": "x" * 501}).status_code == 422
    assert client.post(f"{PACKAGE}$bad/complete", json=body).status_code in (404, 422)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        ("invalid_permalink", 400),
        ("ai_label_not_confirmed", 400),
        ("not_manual_pending", 409),
        ("already_published", 409),
        ("permalink_already_used", 409),
        ("not_found", 404),
    ],
)
def test_refusals_become_http_errors_and_never_look_like_success(
    client: TestClient, fake: Fake, outcome: str, status: int
) -> None:
    fake.status, fake.outcome = TaskStatus.FAILED, outcome
    response = client.post(COMPLETE, json={"permalink": "", "ai_label_confirmed": True})
    assert response.status_code == status
    assert "data" not in response.json() or response.json().get("data") is None
