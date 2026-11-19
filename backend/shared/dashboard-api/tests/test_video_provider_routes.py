"""영상 공급자 목록·선택·커스텀 등록 경로: 읽기는 누구나, 쓰기는 사람 토큰만, 이름은 토큰에서."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

BASE = "/api/dashboard/marketing/video-providers"


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.status = TaskStatus.COMPLETED
        self.outcome = "state"

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append((target, request))
        return AgentResult(
            meta=request.meta, status=self.status, outcome=self.outcome, output={"providers": []}
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


def test_get_calls_the_read_skill(client: TestClient, fake: Fake) -> None:
    assert client.get(BASE).status_code == 200
    assert [(t, r.skill) for t, r in fake.calls] == [("marketing", "list_video_providers")]


def test_select_uses_token_name_not_body(client: TestClient, fake: Fake) -> None:
    body = {"provider": "kling", "model": "kling-4.0", "updated_by": "남의 이름"}
    assert client.post(f"{BASE}/select", json=body).status_code == 200
    _, request = fake.calls[0]
    assert request.skill == "select_video_provider"
    assert request.input["provider"] == "kling"
    assert request.input["updated_by"] and request.input["updated_by"] != "남의 이름"


def test_custom_save_and_delete_forward_to_gated_skills(client: TestClient, fake: Fake) -> None:
    assert client.post(f"{BASE}/custom", json={"spec": {"id": "a-b"}}).status_code == 200
    assert client.post(f"{BASE}/custom/delete", json={"id": "a-b"}).status_code == 200
    assert [r.skill for _, r in fake.calls] == [
        "save_custom_video_provider",
        "delete_custom_video_provider",
    ]
    assert all(r.input["updated_by"] for _, r in fake.calls)


def test_writes_need_a_person_and_a_valid_body(client: TestClient) -> None:
    bad = {"authorization": "Bearer nope"}
    assert client.post(f"{BASE}/select", json={"provider": "x"}, headers=bad).status_code in (
        401,
        403,
    )
    assert client.post(f"{BASE}/select", json={}).status_code == 422
    assert client.post(f"{BASE}/custom", json={"spec": "str"}).status_code == 422


def test_invalid_spec_becomes_400(client: TestClient, fake: Fake) -> None:
    fake.status = TaskStatus.FAILED
    fake.outcome = "invalid_spec"
    response = client.post(f"{BASE}/custom", json={"spec": {}})
    assert response.status_code == 400
