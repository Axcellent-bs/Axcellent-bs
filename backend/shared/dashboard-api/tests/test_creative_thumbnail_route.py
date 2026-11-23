"""제작 기록 영상 썸네일: 만들기 요청·고르기·후보 그림 경로.

`POST /marketing/creative-records/{id}/thumbnails`, `POST .../thumbnail`,
`GET .../thumbnails/{media_id}`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

RID = "CR-0123456789ab"
BASE = f"/api/dashboard/marketing/creative-records/{RID}"
PICK = "thm_0123456789ab"


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


def test_request_goes_to_marketing_with_the_human_name(client: TestClient, fake: Fake) -> None:
    fake.output = {"record_id": RID, "thumbnails": {"status": "awaiting_approval"}}
    res = client.post(f"{BASE}/thumbnails", json={"regenerate": True})
    assert res.status_code == 200
    assert res.json()["data"]["thumbnails"]["status"] == "awaiting_approval"
    sent = fake.calls[-1]
    assert sent.skill == "creative_thumbnails_request"
    assert sent.input == {"record_id": RID, "regenerate": True, "who": "tester"}


def test_request_refusals_map_to_http(client: TestClient, fake: Fake) -> None:
    fake.status = TaskStatus.FAILED
    for outcome, code in (("not_connected", 409), ("video_not_ready", 409), ("not_found", 404)):
        fake.outcome = outcome
        res = client.post(f"{BASE}/thumbnails", json={})
        assert res.status_code == code, outcome
    fake.outcome = "not_connected"
    assert client.post(f"{BASE}/thumbnails", json={}).json()["error"]["code"] == (
        "HIGGSFIELD_NOT_CONNECTED"
    )


def test_no_token_is_401(client: TestClient, fake: Fake) -> None:
    client.headers.pop("authorization", None)
    assert client.post(f"{BASE}/thumbnails", json={}).status_code == 401
    assert client.post(f"{BASE}/thumbnail", json={"media_id": PICK}).status_code == 401
    assert fake.calls == []


def test_pick_outside_candidates_422_other_company_404_locked_409(
    client: TestClient, fake: Fake
) -> None:
    fake.output = {"record_id": RID, "cover_media_id": PICK, "changed": True, "cover_rev": 2,
                   "approvals_reset": ["youtube"]}  # fmt: skip
    res = client.post(f"{BASE}/thumbnail", json={"media_id": PICK})
    assert res.status_code == 200 and res.json()["data"]["approvals_reset"] == ["youtube"]
    assert fake.calls[-1].skill == "creative_thumbnail_select"
    assert fake.calls[-1].input == {"record_id": RID, "media_id": PICK, "who": "tester"}
    # 형식부터 틀린 값(의뢰 영상 후보 번호 포함)은 보내지도 않는다
    before = len(fake.calls)
    assert client.post(f"{BASE}/thumbnail", json={"media_id": "../x"}).status_code == 422
    assert (
        client.post(f"{BASE}/thumbnail", json={"media_id": "cov_0123456789ab"}).status_code == 422
    )
    assert len(fake.calls) == before
    fake.status = TaskStatus.FAILED
    for outcome, code in (("cover_not_candidate", 422), ("not_found", 404), ("cover_locked", 409)):
        fake.outcome = outcome
        assert client.post(f"{BASE}/thumbnail", json={"media_id": PICK}).status_code == code


def test_image_serves_only_the_recorded_file(
    client: TestClient, fake: Fake, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    folder = media / "creative" / RID / "thumbs"
    folder.mkdir(parents=True)
    jpg = folder / f"{PICK}.jpg"
    jpg.write_bytes(b"\xff\xd8\xff-thumb")
    sha = hashlib.sha256(jpg.read_bytes()).hexdigest()
    monkeypatch.setenv(marketing_client.MEDIA_DIR_ENV, str(media))
    fake.output = {
        "record_id": RID,
        "created_at": "2026-11-23T00:00:00+00:00",
        "sheet": {},
        "render": {"thumbnails": {"status": "done",
                                  "candidates": [{"media_id": PICK, "sha256": sha}]}},
    }  # fmt: skip
    url = f"{BASE}/thumbnails/{PICK}"
    res = client.get(url)
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    assert res.content == b"\xff\xd8\xff-thumb"
    assert fake.calls[-1].skill == "creative_record_get"
    # 후보 기록에 없는 그림·바뀐 파일·다른 회사 기록은 내주지 않는다
    assert client.get(f"{BASE}/thumbnails/thm_ffffffffffff").status_code == 404
    jpg.write_bytes(b"swapped")
    assert client.get(url).status_code == 404
    assert client.get(f"{BASE}/thumbnails/x.jpg").status_code == 422
    fake.status, fake.outcome = TaskStatus.FAILED, "not_found"
    assert client.get(url).status_code == 404
