"""마케팅 스토리보드 프롬프트 경로 (`POST /api/dashboard/marketing/storyboard`)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

URL = "/api/dashboard/marketing/storyboard"


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.status = TaskStatus.COMPLETED
        self.outcome: str | None = None
        self.output: dict = {"sheet_prompt_en": "A storyboard sheet ...", "shots": []}

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


def test_story_and_sheet_sentence_go_to_marketing_as_untrusted(
    client: TestClient, fake: Fake
) -> None:
    body = {"story": " 소녀와 고양이 ", "character_en": " same girl ", "shots": 9}
    res = client.post(URL, json=body)
    assert res.status_code == 200
    assert res.json()["data"]["sheet_prompt_en"].startswith("A storyboard sheet")
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "storyboard_prompt")
    assert request.input == {}  # 사람이 넣은 것은 input이 아니라 untrusted로만
    assert request.untrusted == {"story": "소녀와 고양이", "shots": 9, "character_en": "same girl"}


def test_defaults_to_twelve_shots_without_optional_fields(client: TestClient, fake: Fake) -> None:
    assert client.post(URL, json={"story": "들판"}).status_code == 200
    assert fake.calls[0][1].untrusted == {"story": "들판", "shots": 12}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"story": ""},
        {"story": "   "},
        {"story": "a", "shots": 7},
        {"story": "x" * 1001},
        {"story": "a", "style_en": "s" * 201},
    ],
)
def test_bad_body_stops_before_marketing(client: TestClient, fake: Fake, body: dict) -> None:
    assert client.post(URL, json=body).status_code in (400, 422)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("outcome", "status"),
    [("no_story", 422), ("llm_not_configured", 409), ("llm_failed", 502), ("weird", 502)],
)
def test_refusals_map_to_http(client: TestClient, fake: Fake, outcome: str, status: int) -> None:
    fake.status, fake.outcome, fake.output = TaskStatus.FAILED, outcome, {"message": "이유"}
    assert client.post(URL, json={"story": "들판"}).status_code == status


HF_URL = "/api/dashboard/marketing/higgsfield/status"


def test_higgsfield_status_is_a_read_skill_call(client: TestClient, fake: Fake) -> None:
    fake.output = {"connection": "ok", "key_configured": True}
    res = client.get(HF_URL)
    assert res.status_code == 200 and res.json()["data"]["connection"] == "ok"
    target, request = fake.calls[0]
    assert (target, request.skill, request.input) == ("marketing", "higgsfield_status", {})


def test_higgsfield_status_failure_is_502(client: TestClient, fake: Fake) -> None:
    fake.status, fake.outcome, fake.output = TaskStatus.FAILED, "boom", {}
    assert client.get(HF_URL).status_code == 502
