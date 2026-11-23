"""의뢰 영상 썸네일 고르기·후보 그림 경로.

`POST /marketing/request-videos/{id}/cover`, `GET .../covers/{media_id}`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

COVER = "/api/dashboard/marketing/request-videos/spd_abc/cover"
PICK = "cov_0123456789ab"


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


def test_pick_goes_to_marketing_with_the_human_name(client: TestClient, fake: Fake) -> None:
    fake.output = {
        "post_draft_id": "spd_abc",
        "cover_media_id": PICK,
        "changed": True,
        "cover_rev": 1,
        "approvals_reset": ["instagram"],
    }
    res = client.post(COVER, json={"media_id": PICK})
    assert res.status_code == 200 and res.json()["data"]["approvals_reset"] == ["instagram"]
    sent = fake.calls[-1]
    assert sent.skill == "request_video_cover_select"
    assert sent.input == {"post_draft_id": "spd_abc", "media_id": PICK, "who": "tester"}


def test_no_token_is_401(client: TestClient, fake: Fake) -> None:
    client.headers.pop("authorization", None)
    assert client.post(COVER, json={"media_id": PICK}).status_code == 401
    assert fake.calls == []


def test_outside_candidates_is_422_and_other_company_is_404(client: TestClient, fake: Fake) -> None:
    # 형식부터 틀린 값은 보내지도 않는다
    assert client.post(COVER, json={"media_id": "../x"}).status_code == 422
    assert fake.calls == []
    fake.status, fake.outcome, fake.output = TaskStatus.FAILED, "cover_not_candidate", {}
    assert client.post(COVER, json={"media_id": PICK}).status_code == 422
    # 다른 회사 초안은 마케팅이 없는 것으로 본다
    fake.outcome = "not_found"
    assert client.post(COVER, json={"media_id": PICK}).status_code == 404
    fake.outcome = "cover_locked"
    assert client.post(COVER, json={"media_id": PICK}).status_code == 409


def test_cover_image_serves_only_the_recorded_file(
    client: TestClient, fake: Fake, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    folder = media / "catbooks" / "ctx_wr_1" / "med_1" / "covers"
    folder.mkdir(parents=True)
    jpg = folder / f"{PICK}.jpg"
    jpg.write_bytes(b"\xff\xd8\xff-cover")
    sha = hashlib.sha256(jpg.read_bytes()).hexdigest()
    monkeypatch.setenv(marketing_client.MEDIA_DIR_ENV, str(media))
    ref = f"catbooks/ctx_wr_1/med_1/covers/{PICK}.jpg"
    fake.output = {
        "items": [{"post_draft_id": "spd_abc", "cover_files": {PICK: {"asset_ref": ref,
                                                                       "asset_sha256": sha}}}]
    }  # fmt: skip
    url = f"/api/dashboard/marketing/request-videos/spd_abc/covers/{PICK}"
    res = client.get(url)
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    assert res.content == b"\xff\xd8\xff-cover"
    # 후보 기록에 없는 그림·바뀐 파일은 내주지 않는다
    base = "/api/dashboard/marketing/request-videos/spd_abc/covers"
    assert client.get(f"{base}/cov_ffffffffffff").status_code == 404
    jpg.write_bytes(b"swapped")
    assert client.get(url).status_code == 404
    assert client.get(f"{base}/x.jpg").status_code == 422


def test_list_hides_cover_file_refs(client: TestClient, fake: Fake) -> None:
    fake.output = {
        "items": [
            {
                "post_draft_id": "spd_abc",
                "labeled": True,
                "prepared": {"asset_ref": "a", "asset_sha256": "b"},
                "covers": [{"media_id": PICK, "at_s": 1.0}],
                "cover_media_id": PICK,
                "cover_files": {PICK: {"asset_ref": "x", "asset_sha256": "y"}},
            }
        ]
    }
    got = client.get("/api/dashboard/marketing/request-videos").json()["data"]["items"][0]
    assert "cover_files" not in got and "prepared" not in got
    assert got["covers"] == [{"media_id": PICK, "at_s": 1.0}] and got["cover_media_id"] == PICK
