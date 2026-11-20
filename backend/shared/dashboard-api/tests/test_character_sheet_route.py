"""마케팅 캐릭터 시트 프롬프트 경로 (`POST /api/dashboard/marketing/character-sheet`)."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_contracts.schemas.marketing_character import MAX_CHARACTER_IMAGE_BASE64
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

URL = "/api/dashboard/marketing/character-sheet"
IMAGE = {"media_type": "image/png", "data_base64": base64.b64encode(b"\x89PNG....").decode()}


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.status = TaskStatus.COMPLETED
        self.outcome: str | None = None
        self.output: dict = {"prompt_en": "Character reference sheet ...", "panels": []}

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


def test_image_and_note_go_to_marketing_as_untrusted(client: TestClient, fake: Fake) -> None:
    res = client.post(URL, json={"image": IMAGE, "note": "  더 귀엽게 "})
    assert res.status_code == 200
    assert res.json()["data"]["prompt_en"].startswith("Character reference sheet")
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "character_sheet_prompt")
    assert request.input == {}  # 사람이 올린 것은 input이 아니라 untrusted로만
    assert request.untrusted == {"image": IMAGE, "note": "더 귀엽게"}


def test_without_note_only_the_image_goes(client: TestClient, fake: Fake) -> None:
    assert client.post(URL, json={"image": IMAGE}).status_code == 200
    assert fake.calls[0][1].untrusted == {"image": IMAGE}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"image": {"media_type": "image/gif", "data_base64": "AAAA"}},
        {
            "image": {
                "media_type": "image/png",
                "data_base64": "A" * (MAX_CHARACTER_IMAGE_BASE64 + 1),
            }
        },
        {"image": IMAGE, "note": "x" * 201},
    ],
)
def test_bad_body_stops_before_marketing(client: TestClient, fake: Fake, body: dict) -> None:
    assert client.post(URL, json=body).status_code in (400, 422)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        ("llm_not_configured", 409),
        ("image_too_large", 413),
        ("no_character", 422),
        ("llm_failed", 502),
    ],
)
def test_refusals_keep_the_reason(
    client: TestClient, fake: Fake, outcome: str, status: int
) -> None:
    fake.status, fake.outcome, fake.output = TaskStatus.FAILED, outcome, {"message": "이유"}
    res = client.post(URL, json={"image": IMAGE})
    assert res.status_code == status
    assert "이유" in res.text


def test_mock_mode_does_not_pretend(
    client: TestClient, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    assert client.post(URL, json={"image": IMAGE}).status_code == 501
    assert fake.calls == []


def test_thumbnail_goes_untrusted_for_the_record(client: TestClient, fake: Fake) -> None:
    res = client.post(URL, json={"image": IMAGE, "thumbnail_base64": "/9j/4AAQ"})
    assert res.status_code == 200
    assert fake.calls[0][1].untrusted == {"image": IMAGE, "thumbnail": "/9j/4AAQ"}
