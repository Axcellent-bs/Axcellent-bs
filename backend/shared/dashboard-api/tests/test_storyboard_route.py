"""마케팅 스토리보드 프롬프트 경로 (`POST /api/dashboard/marketing/storyboard`)."""

from __future__ import annotations

from datetime import UTC, datetime

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


RYT = "/api/dashboard/marketing/request-videos/spd_abc/youtube"


def test_request_video_youtube_draft_and_request_go_to_marketing(
    client: TestClient, fake: Fake
) -> None:
    fake.output = {"post_draft_id": "spd_abc", "request_id": "wr_1", "youtube": {"status": "draft"}}
    res = client.post(f"{RYT}/draft", json={"title": "제목", "privacy": "public"})
    assert res.status_code == 200 and res.json()["data"]["youtube"]["status"] == "draft"
    _, request = fake.calls[-1]
    assert request.skill == "request_video_youtube_draft"
    assert request.input == {
        "post_draft_id": "spd_abc",
        "regenerate": False,
        "title": "제목",
        "privacy": "public",
    }
    # 의뢰 영상은 늘 AI 라벨 영상이라 올릴 영상(part)을 고르지 않는다
    assert client.post(f"{RYT}/draft", json={"privacy": "secret"}).status_code == 422
    bad = "/api/dashboard/marketing/request-videos/..%2Fx/youtube"
    assert client.post(bad).status_code in (404, 422)
    fake.output = {"post_draft_id": "spd_abc", "youtube": {"status": "awaiting_approval"}}
    assert client.post(RYT).status_code == 200
    _, request = fake.calls[-1]
    assert (request.skill, request.input) == (
        "request_video_youtube_request",
        {"post_draft_id": "spd_abc", "who": "tester"},
    )
    fake.status, fake.outcome, fake.output = (
        TaskStatus.FAILED,
        "video_not_ready",
        {"message": "x"},
    )
    assert client.post(RYT).status_code == 409
    fake.outcome = "not_found"
    missing = client.post(RYT)
    assert missing.status_code == 404 and "의뢰" in missing.json()["error"]["message"]


def test_youtube_campaign_link_and_campaign_list_go_to_marketing(
    client: TestClient, fake: Fake
) -> None:
    """유튜브 칸의 캠페인 고르기(11/23): campaign_id를 그대로 넘기고, ""(연결 풀기)도 넘긴다."""
    fake.output = {"record_id": "CR-0123456789ab", "youtube": {"campaign_id": "cmp_1"}}
    assert client.post(f"{YT}/draft", json={"campaign_id": "cmp_1"}).status_code == 200
    assert fake.calls[-1][1].input == {
        "record_id": "CR-0123456789ab",
        "regenerate": False,
        "campaign_id": "cmp_1",
    }
    fake.output = {"post_draft_id": "spd_abc", "youtube": {}}
    assert client.post(f"{RYT}/draft", json={"campaign_id": ""}).status_code == 200
    assert fake.calls[-1][1].input["campaign_id"] == ""
    # 캠페인 번호 모양이 아니면 막는다(의뢰 번호 wr_는 고를 수 없다)
    assert client.post(f"{YT}/draft", json={"campaign_id": "wr_1"}).status_code == 422
    fake.status, fake.outcome, fake.output = (
        TaskStatus.FAILED,
        "unknown_campaign",
        {"message": "마케팅이 모르는 캠페인이다: cmp_x"},
    )
    refused = client.post(f"{YT}/draft", json={"campaign_id": "cmp_x"})
    assert refused.status_code == 422 and "cmp_x" in refused.json()["error"]["message"]
    fake.status, fake.outcome = TaskStatus.COMPLETED, None
    fake.output = {"items": [{"campaign_id": "cmp_1"}], "total": 1}
    listed = client.get("/api/dashboard/marketing/campaigns?limit=20")
    assert listed.status_code == 200 and listed.json()["data"]["total"] == 1
    assert (fake.calls[-1][1].skill, fake.calls[-1][1].input) == (
        "list_marketing_campaigns",
        {"limit": 20},
    )


def test_background_answers_at_once_and_still_asks_marketing(
    client: TestClient, fake: Fake
) -> None:
    """11/25 대본대로 다시 만들기는 60초를 넘겨 끊겼다. 바로 답하고 뒤에서 기록에 붙인다."""
    body = {"story": "들판", "shots": 6, "record_id": "CR-0123456789ab", "background": True}
    res = client.post(URL, json=body)
    assert res.status_code == 200
    assert res.json()["data"] == {"record_id": "CR-0123456789ab", "started": True}
    # TestClient의 이벤트 루프에서 뒤 작업이 돌아 마케팅에 같은 요청이 간다
    client.post(URL, json={"story": "들판"})
    skills = [(c[1].skill, c[1].untrusted.get("record_id")) for c in fake.calls]
    assert ("storyboard_prompt", "CR-0123456789ab") in skills
    request = next(c[1] for c in fake.calls if c[1].untrusted.get("record_id"))
    left = request.meta.deadline - datetime.now(UTC)
    assert left.total_seconds() > marketing_client.STORYBOARD_DEADLINE_SECONDS
    assert request.input == {"background": True}  # 마케팅이 LLM 응답 제한을 늘린다


def test_background_needs_a_record(client: TestClient, fake: Fake) -> None:
    res = client.post(URL, json={"story": "들판", "background": True})
    assert res.status_code == 422
    assert fake.calls == []
    assert client.post(URL, json={"story": "들판", "background": "yes"}).status_code in (400, 422)
