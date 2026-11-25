"""키프레임 한 장 [다시 그리기] 경로 (가짜 마케팅, 11/25).

`POST /marketing/creative-records/{id}/keyframes/{index}/redraw`."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

RID = "CR-0123456789ab"
URL = f"/api/dashboard/marketing/creative-records/{RID}/keyframes/2/redraw"


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


def test_redraw_goes_to_the_keyframe_skill_with_the_index(client: TestClient, fake: Fake) -> None:
    fake.output = {"record_id": RID, "part": "keyframes", "item": {"status": "running"}}
    res = client.post(URL)
    assert res.status_code == 200
    assert res.json()["data"]["item"]["status"] == "running"
    sent = fake.calls[-1]
    assert sent.skill == "creative_keyframe_redraw"
    assert sent.input == {"record_id": RID, "index": 2}
    assert sent.untrusted == {}


def test_refusals_map_and_other_company_is_404(client: TestClient, fake: Fake) -> None:
    fake.status = TaskStatus.FAILED
    cases = (
        ("not_found", 404),
        ("bad_index", 404),
        ("no_keyframes", 409),
        ("in_progress", 409),
        ("video_started", 409),
    )
    for outcome, code in cases:
        fake.outcome = outcome
        assert client.post(URL).status_code == code, outcome


def test_bad_index_id_and_missing_token_are_refused(client: TestClient, fake: Fake) -> None:
    assert client.post(URL.replace("/2/", "/10/")).status_code == 422
    assert client.post(URL.replace(RID, "CR-bad")).status_code == 422
    client.headers.pop("authorization", None)
    assert client.post(URL).status_code == 401
    assert fake.calls == []


def test_keyframe_asset_part_is_allowed() -> None:
    assert marketing_client.KEYFRAME_PART.fullmatch("keyframe_3")
    assert not marketing_client.KEYFRAME_PART.fullmatch("keyframe_10")
