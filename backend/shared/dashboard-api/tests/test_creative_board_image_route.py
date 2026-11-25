"""[Higgsfield 그림으로 다시 그리기] 경로 (가짜 마케팅).

`POST /marketing/creative-records/{id}/board-image`."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

RID = "CR-0123456789ab"
URL = f"/api/dashboard/marketing/creative-records/{RID}/board-image"


class Fake:
    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []
        self.status = TaskStatus.COMPLETED
        self.outcome: str | None = None
        self.output: dict = {}

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        assert target == "marketing"
        self.calls.append(request)
        return AgentResult(
            meta=request.meta, status=self.status, outcome=self.outcome, output=self.output
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


def test_redraw_goes_to_the_dedicated_skill_without_story(client: TestClient, fake: Fake) -> None:
    fake.output = {"record_id": RID, "part": "board_image", "item": {"status": "running"}}
    res = client.post(URL)
    assert res.status_code == 200
    assert res.json()["data"]["item"]["status"] == "running"
    sent = fake.calls[-1]
    # 스토리보드를 처음부터 다시 만들지 않는다(storyboard_prompt·board_higgsfield가 아니다)
    assert sent.skill == "creative_board_redraw"
    assert sent.input == {"record_id": RID}
    assert sent.untrusted == {}


def test_other_company_record_is_404_and_refusals_map(client: TestClient, fake: Fake) -> None:
    fake.status = TaskStatus.FAILED
    for outcome, code in (("not_found", 404), ("no_storyboard", 409), ("in_progress", 409)):
        fake.outcome = outcome
        assert client.post(URL).status_code == code, outcome


def test_bad_id_and_missing_token_are_refused(client: TestClient, fake: Fake) -> None:
    assert client.post(URL.replace(RID, "CR-bad")).status_code == 422
    client.headers.pop("authorization", None)
    assert client.post(URL).status_code == 401
    assert fake.calls == []
