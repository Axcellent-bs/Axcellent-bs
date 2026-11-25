"""제작 기록 자막 다시 얹기: `POST /marketing/creative-records/{id}/overlay` (가짜 마케팅)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

RID = "CR-0123456789ab"
URL = f"/api/dashboard/marketing/creative-records/{RID}/overlay"
BODY = {"captions": [{"no": 1, "caption_ko": "고친 자막"}, {"no": 2, "caption_ko": ""}]}


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


def test_redo_goes_to_marketing_with_the_human_name(client: TestClient, fake: Fake) -> None:
    fake.output = {
        "record_id": RID,
        "overlay": {"status": "applied", "captions": 1, "source_part": "video"},
        "overlay_video": {"status": "done", "asset": "overlay.mp4"},
    }
    res = client.post(URL, json=BODY)
    assert res.status_code == 200
    assert res.json()["data"]["overlay"]["status"] == "applied"
    sent = fake.calls[-1]
    assert sent.skill == "creative_overlay_redo"
    assert sent.input == {"record_id": RID, "captions": BODY["captions"], "who": "tester"}
    # ffmpeg가 도는 시간을 기다릴 수 있게 마감이 길다
    assert (sent.meta.deadline - sent.meta.deadline.now(sent.meta.deadline.tzinfo)).seconds > 60


def test_refusals_map_to_http(client: TestClient, fake: Fake) -> None:
    fake.status = TaskStatus.FAILED
    for outcome, code in (("video_not_ready", 409), ("in_progress", 409), ("not_found", 404)):
        fake.outcome = outcome
        assert client.post(URL, json=BODY).status_code == code, outcome


def test_body_and_token_are_checked(client: TestClient, fake: Fake) -> None:
    assert client.post(URL, json={"captions": []}).status_code == 422
    too_long = {"captions": [{"no": 1, "caption_ko": "가" * 61}]}
    assert client.post(URL, json=too_long).status_code == 422
    assert client.post(URL.replace(RID, "CR-bad"), json=BODY).status_code == 422
    client.headers.pop("authorization", None)
    assert client.post(URL, json=BODY).status_code == 401
    assert fake.calls == []


def test_overlay_video_is_served_as_an_asset_part(client: TestClient, fake: Fake) -> None:
    res = client.get(f"/api/dashboard/marketing/creative-records/{RID}/asset/overlay_video")
    assert res.status_code != 422  # 칸 이름으로 받아들인다(파일이 없으면 404)
