"""영상 대본 경로 (`POST /marketing/creative-records/{id}/script`, `/script/save`)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

RID = "CR-0123456789ab"
URL = f"/api/dashboard/marketing/creative-records/{RID}/script"


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.status = TaskStatus.COMPLETED
        self.outcome: str | None = None
        self.output: dict = {"record_id": RID, "script": {"title_ko": "t", "scenes": []}}

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
    monkeypatch.setattr(marketing_client, "SEND", f)
    return f


@pytest.fixture
def client(fake: Fake) -> TestClient:
    return TestClient(app)


def test_story_goes_as_untrusted_and_counts_as_input(client: TestClient, fake: Fake) -> None:
    res = client.post(URL, json={"story": " 들판의 소녀 ", "scenes": 9, "seconds": 15})
    assert res.status_code == 200 and res.json()["data"]["record_id"] == RID
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "creative_script")
    assert request.input == {"record_id": RID, "scenes": 9, "seconds": 15}
    assert request.untrusted == {"story": "들판의 소녀"}


def test_empty_story_uses_the_record_story_and_defaults(client: TestClient, fake: Fake) -> None:
    assert client.post(URL, json={}).status_code == 200
    request = fake.calls[0][1]
    assert request.input == {"record_id": RID, "scenes": 6, "seconds": 30}
    assert request.untrusted == {}


def test_bad_counts_and_ids_are_refused_before_marketing(client: TestClient, fake: Fake) -> None:
    assert client.post(URL, json={"scenes": 7}).status_code == 422
    assert client.post(URL, json={"seconds": 45}).status_code == 422
    bad = "/api/dashboard/marketing/creative-records/CR-xyz/script"
    assert client.post(bad, json={}).status_code == 422
    assert fake.calls == []


@pytest.mark.parametrize(("outcome", "status"), [("not_found", 404), ("no_story", 422)])
def test_refusals_map_to_http(client: TestClient, fake: Fake, outcome: str, status: int) -> None:
    fake.status, fake.outcome, fake.output = TaskStatus.FAILED, outcome, {"message": "이유"}
    res = client.post(URL, json={"story": "x"})
    assert res.status_code == status


def test_save_sends_renumbered_scenes(client: TestClient, fake: Fake) -> None:
    body = {
        "title_ko": " 고친 제목 ",
        "cta_ko": " 지금 만나요 ",
        "scenes": [
            {"start_s": 0, "end_s": 5, "visual_ko": "화면 가", "line_ko": "안녕"},
            {"start_s": 5, "end_s": 10, "visual_ko": "화면 나", "caption_ko": "자막"},
        ],
    }
    res = client.post(f"{URL}/save", json=body)
    assert res.status_code == 200
    request = fake.calls[0][1]
    assert request.skill == "creative_script_save"
    assert request.input["record_id"] == RID and request.input["title_ko"] == "고친 제목"
    assert request.input["cta_ko"] == "지금 만나요"
    assert [s["no"] for s in request.input["scenes"]] == [1, 2]
    assert request.input["scenes"][1]["caption_ko"] == "자막"


def test_save_refuses_empty_or_too_many_scenes(client: TestClient, fake: Fake) -> None:
    scene = {"start_s": 0, "end_s": 1, "visual_ko": "x"}
    assert client.post(f"{URL}/save", json={"title_ko": "t", "scenes": []}).status_code == 422
    many = {"title_ko": "t", "scenes": [scene] * 13}
    assert client.post(f"{URL}/save", json=many).status_code == 422
    assert fake.calls == []
