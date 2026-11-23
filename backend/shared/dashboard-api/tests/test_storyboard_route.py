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


def test_record_id_goes_with_the_storyboard(client: TestClient, fake: Fake) -> None:
    body = {"story": "들판", "record_id": "CR-0123456789ab"}
    assert client.post(URL, json=body).status_code == 200
    assert fake.calls[0][1].untrusted["record_id"] == "CR-0123456789ab"
    assert client.post(URL, json={"story": "들판", "record_id": "CR-x"}).status_code in (400, 422)


def test_creative_records_list_and_get(client: TestClient, fake: Fake) -> None:
    fake.output = {"items": []}
    assert client.get("/api/dashboard/marketing/creative-records?limit=5").status_code == 200
    assert (fake.calls[0][1].skill, fake.calls[0][1].input) == (
        "creative_records_list",
        {"limit": 5},
    )
    fake.output = {"record_id": "CR-0123456789ab"}
    assert (
        client.get("/api/dashboard/marketing/creative-records/CR-0123456789ab").status_code == 200
    )
    assert fake.calls[1][1].input == {"record_id": "CR-0123456789ab"}
    assert client.get("/api/dashboard/marketing/creative-records/nope").status_code == 422


def test_missing_creative_record_is_404(client: TestClient, fake: Fake) -> None:
    fake.status, fake.outcome, fake.output = TaskStatus.FAILED, "not_found", {}
    assert (
        client.get("/api/dashboard/marketing/creative-records/CR-0123456789ab").status_code == 404
    )


VIDEO_URL = "/api/dashboard/marketing/creative-records/CR-0123456789ab/video"


def test_video_request_goes_to_marketing_with_the_record(client: TestClient, fake: Fake) -> None:
    fake.output = {"record_id": "CR-0123456789ab", "video": {"status": "awaiting_approval"}}
    res = client.post(VIDEO_URL)
    assert res.status_code == 200
    assert res.json()["data"]["video"]["status"] == "awaiting_approval"
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "creative_video_request")
    assert request.input["record_id"] == "CR-0123456789ab"


@pytest.mark.parametrize(
    ("outcome", "status"),
    [("board_image_not_ready", 409), ("model_not_configured", 409), ("not_found", 404)],
)
def test_video_refusals_map_to_http(
    client: TestClient, fake: Fake, outcome: str, status: int
) -> None:
    fake.status, fake.outcome, fake.output = TaskStatus.FAILED, outcome, {"message": outcome}
    assert client.post(VIDEO_URL).status_code == status


def test_asset_without_a_finished_render_is_404(client: TestClient, fake: Fake) -> None:
    fake.output = {"record_id": "CR-0123456789ab", "render": {}}
    url = "/api/dashboard/marketing/creative-records/CR-0123456789ab/asset/sheet_image"
    assert client.get(url).status_code == 404
    bad = "/api/dashboard/marketing/creative-records/CR-0123456789ab/asset/secret"
    assert client.get(bad).status_code == 422


UPLOAD_URL = "/api/dashboard/marketing/creative-records/CR-0123456789ab/upload/final_video"
MP4 = b"\x00\x00\x00\x18ftypmp42" + bytes(64)


def test_upload_saves_the_file_and_asks_marketing_to_attach(
    client: TestClient, fake: Fake, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path))
    fake.output = {
        "record_id": "CR-0123456789ab",
        "part": "final_video",
        "item": {"status": "done"},
    }
    res = client.post(UPLOAD_URL + "?name=promo.mp4", content=MP4)
    assert res.status_code == 200, res.text
    saved = tmp_path / "creative" / "CR-0123456789ab" / "final.mp4"
    assert saved.read_bytes() == MP4
    target, request = fake.calls[-1]
    assert (target, request.skill) == ("marketing", "creative_record_attach")
    assert request.input["sha256"] == hashlib.sha256(MP4).hexdigest()
    assert request.input["label"] == "promo.mp4" and request.input["asset"] == "final.mp4"


def test_upload_rejects_a_non_video_body(
    client: TestClient, fake: Fake, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path))
    fake.output = {"record_id": "CR-0123456789ab"}
    res = client.post(UPLOAD_URL, content=b"<html>not a video</html>")
    assert res.status_code == 415
    assert not list((tmp_path / "creative" / "CR-0123456789ab").iterdir())
    assert all(r.skill != "creative_record_attach" for _, r in fake.calls)
    assert client.post(UPLOAD_URL.replace("final_video", "secret"), content=MP4).status_code == 422


YT = "/api/dashboard/marketing/creative-records/CR-0123456789ab/youtube"


def test_youtube_draft_and_request_go_to_marketing(client: TestClient, fake: Fake) -> None:
    fake.output = {"record_id": "CR-0123456789ab", "youtube": {"status": "draft"}}
    res = client.post(f"{YT}/draft", json={"title": "제목", "privacy": "public", "who": "x"})
    assert res.status_code == 200 and res.json()["data"]["youtube"]["status"] == "draft"
    _, request = fake.calls[-1]
    assert request.skill == "creative_youtube_draft"
    assert request.input == {
        "record_id": "CR-0123456789ab",
        "regenerate": False,
        "title": "제목",
        "privacy": "public",
    }
    assert client.post(f"{YT}/draft", json={"privacy": "secret"}).status_code == 422
    fake.output = {"record_id": "CR-0123456789ab", "youtube": {"status": "awaiting_approval"}}
    assert client.post(YT).status_code == 200
    _, request = fake.calls[-1]
    assert (request.skill, request.input["who"]) == ("creative_youtube_request", "tester")
    fake.status, fake.outcome, fake.output = (
        TaskStatus.FAILED,
        "youtube_not_connected",
        {"message": "x"},
    )
    assert client.post(YT).status_code == 409
