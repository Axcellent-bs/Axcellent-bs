"""화면의 캠페인 시작·결과·취소 경로."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.status = TaskStatus.COMPLETED
        self.outcome = "executed"

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append((target, request))
        output: dict = {"grade": "B"}
        if self.status == TaskStatus.INPUT_REQUIRED:
            output = {"status": "pending", "review_id": "rev_1"}
        return AgentResult(
            meta=request.meta,
            status=self.status,
            outcome=self.outcome,
            output=output,
            reason_code="human_input" if self.status == TaskStatus.INPUT_REQUIRED else None,
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


START = {"campaign_id": "cmp_1", "campaign_name": "가을", "channel": "검색", "budget_krw": 50_000}


def test_start_goes_to_marketing_with_a_key_per_amount(client: TestClient, fake: Fake) -> None:
    response = client.post("/api/dashboard/campaigns/start", json=START)
    assert response.status_code == 200
    assert response.json()["data"]["outcome"] == "executed"
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "start_campaign")
    assert request.meta.context_id == "ctx_cmp_cmp_1"
    assert request.meta.idempotency_key.endswith(":50000")


def test_start_needs_a_person(client: TestClient, fake: Fake) -> None:
    bad = {"authorization": "Bearer nope"}
    response = client.post("/api/dashboard/campaigns/start", json=START, headers=bad)
    assert response.status_code in (401, 403)
    assert fake.calls == []


@pytest.mark.parametrize(
    "bad",
    [START | {"budget_krw": 0}, START | {"campaign_id": "../x"}, START | {"channel": ""}],
)
def test_bad_start_stops_before_marketing(client: TestClient, fake: Fake, bad: dict) -> None:
    assert client.post("/api/dashboard/campaigns/start", json=bad).status_code == 422
    assert fake.calls == []


def test_held_or_failed_is_a_result_not_an_error(client: TestClient, fake: Fake) -> None:
    fake.status, fake.outcome = TaskStatus.FAILED, "not_started"
    body = {
        "campaign_name": "가을",
        "channel": "검색",
        "period_from": "2026-11-01",
        "period_to": "2026-11-03",
        "spend_krw": 1000,
        "source": "marketing_screen",
    }
    response = client.post("/api/dashboard/campaigns/cmp_1/result", json=body)
    assert response.status_code == 200 and response.json()["data"]["outcome"] == "not_started"
    payload = fake.calls[0][1].input
    assert payload["campaign_id"] == "cmp_1" and "gmv_krw" not in payload  # 모르면 보내지 않는다


def test_effect_numbers_reach_marketing_and_unknown_stays_out(
    client: TestClient, fake: Fake
) -> None:
    body = {
        "campaign_name": "가을",
        "channel": "검색",
        "period_from": "2026-11-01",
        "period_to": "2026-11-03",
        "spend_krw": 1000,
        "gmv_krw": 0,
        "orders": 0,
        "measured_gmv_krw": -3000,
        "measured_orders": -2,
        "effect_generated_at": "2026-11-04T00:00:00Z",
        "source": "data.campaign_effect.v2",
    }
    url = "/api/dashboard/campaigns/cmp_1/result"
    assert client.post(url, json=body).status_code == 200
    sent = fake.calls[0][1].input
    assert (sent["gmv_krw"], sent["orders"]) == (0, 0)  # 0은 아는 값이라 보낸다
    assert (sent["measured_gmv_krw"], sent["measured_orders"]) == (-3000, -2)

    unknown = {k: v for k, v in body.items() if not k.startswith(("gmv", "orders", "measured"))}
    assert client.post(url, json=unknown | {"orders": None}).status_code == 200
    sent = fake.calls[1][1].input
    assert "orders" not in sent and "gmv_krw" not in sent  # 모르면 보내지 않는다
    assert client.post(url, json=body | {"orders": -1}).status_code == 422


def test_rejected_by_acl_is_403(client: TestClient, fake: Fake) -> None:
    fake.status, fake.outcome = TaskStatus.REJECTED, "FORBIDDEN_SKILL"
    response = client.post("/api/dashboard/campaigns/start", json=START)
    assert response.status_code == 403


def test_cancel_needs_a_reason(client: TestClient, fake: Fake) -> None:
    url = "/api/dashboard/campaigns/cmp_1/cancel"
    assert client.post(url, json={"reason": ""}).status_code == 422
    assert client.post(url, json={"reason": "중단"}).status_code == 200
    assert fake.calls[0][1].skill == "cancel_campaign"


HANDOFF = "/api/dashboard/marketing/briefs/PROP-1/sns-handoff"


def test_sns_handoff_goes_to_marketing_with_a_stable_key(client: TestClient, fake: Fake) -> None:
    fake.outcome = "handed"
    first = client.post(HANDOFF, json={})
    again = client.post(HANDOFF, json={})
    assert first.status_code == again.status_code == 200
    assert first.json()["data"]["outcome"] == "handed"
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "handoff_brief_to_sns")
    assert request.input == {"proposal_id": "PROP-1", "media_kind": "image"}
    assert fake.calls[0][1].meta.idempotency_key == fake.calls[1][1].meta.idempotency_key


def test_sns_handoff_needs_a_person_and_valid_input(client: TestClient, fake: Fake) -> None:
    bad = {"authorization": "Bearer nope"}
    assert client.post(HANDOFF, json={}, headers=bad).status_code in (401, 403)
    assert client.post(HANDOFF.replace("PROP-1", "a%20b"), json={}).status_code == 422
    assert client.post(HANDOFF, json={"media_kind": "audio"}).status_code == 422
    assert fake.calls == []


def test_sns_handoff_failure_codes_use_the_error_envelope(client: TestClient, fake: Fake) -> None:
    fake.status, fake.outcome = TaskStatus.FAILED, "brief_not_found"
    response = client.post(HANDOFF, json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    fake.status, fake.outcome = TaskStatus.REJECTED, "FORBIDDEN_SKILL"
    assert client.post(HANDOFF, json={}).status_code == 403
    fake.status, fake.outcome = TaskStatus.CANCELED, None
    assert client.post(HANDOFF, json={}).status_code == 504


VIDEO_PREPARE = "/api/dashboard/marketing/video-post/prepare"
VIDEO_PUBLISH = "/api/dashboard/marketing/video-post/publish"
VIDEO_BODY = {
    "notice_id": "ntc_1",
    "scene_template_id": "cat_bookstore_video",
    "fields": {"a": "b"},
}


def test_video_prepare_goes_to_marketing_with_long_deadline(
    client: TestClient, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DASHBOARD_VIDEO_TIMEOUT_S", raising=False)
    fake.outcome = "draft_ready"
    response = client.post(VIDEO_PREPARE, json=VIDEO_BODY)
    assert response.status_code == 200
    assert response.json()["data"]["outcome"] == "draft_ready"
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "prepare_sns_post_from_media")
    assert request.input["media_kind"] == "video" and request.input["notice_id"] == "ntc_1"
    remaining = (request.meta.deadline - datetime.now(UTC)).total_seconds()
    assert 300 < remaining <= 360  # 기본 360초


def test_video_deadline_is_configurable(
    client: TestClient, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_VIDEO_TIMEOUT_S", "90")
    client.post(VIDEO_PREPARE, json=VIDEO_BODY)
    remaining = (fake.calls[0][1].meta.deadline - datetime.now(UTC)).total_seconds()
    assert 60 < remaining <= 90


def test_video_endpoints_need_a_person_and_valid_input(client: TestClient, fake: Fake) -> None:
    bad = {"authorization": "Bearer nope"}
    assert client.post(VIDEO_PREPARE, json=VIDEO_BODY, headers=bad).status_code in (401, 403)
    publish_body = {"publish_input": {}}
    assert client.post(VIDEO_PUBLISH, json=publish_body, headers=bad).status_code in (401, 403)
    both = VIDEO_BODY | {"coupon": {"campaign_id": "c", "unit_value": 1000}}
    assert client.post(VIDEO_PREPARE, json=both).status_code == 422  # 둘 다
    neither = {k: v for k, v in VIDEO_BODY.items() if k != "notice_id"}
    assert client.post(VIDEO_PREPARE, json=neither).status_code == 422  # 둘 다 없음
    assert client.post(VIDEO_PREPARE, json=VIDEO_BODY | {"media_kind": "image"}).status_code == 422
    assert (
        client.post(VIDEO_PREPARE, json=VIDEO_BODY | {"scene_template_id": ""}).status_code == 422
    )
    assert client.post(VIDEO_PUBLISH, json={}).status_code == 422
    assert fake.calls == []


def test_video_prepare_error_mapping(client: TestClient, fake: Fake) -> None:
    fake.status, fake.outcome = TaskStatus.FAILED, "invalid_input"
    assert client.post(VIDEO_PREPARE, json=VIDEO_BODY).status_code == 400
    fake.status, fake.outcome = TaskStatus.FAILED, "provider_failed"  # 판단 결과: 그대로 전달
    response = client.post(VIDEO_PREPARE, json=VIDEO_BODY)
    assert response.status_code == 200 and response.json()["data"]["outcome"] == "provider_failed"
    fake.status, fake.outcome = TaskStatus.REJECTED, "FORBIDDEN_SKILL"
    assert client.post(VIDEO_PREPARE, json=VIDEO_BODY).status_code == 403
    fake.status, fake.outcome = TaskStatus.CANCELED, None
    assert client.post(VIDEO_PREPARE, json=VIDEO_BODY).status_code == 504


def test_video_publish_passes_input_required_with_review_id(client: TestClient, fake: Fake) -> None:
    fake.status, fake.outcome = TaskStatus.INPUT_REQUIRED, "pending_human"
    publish_input = {"post_draft_id": "pd_1", "media_kind": "video", "caption": "x"}
    response = client.post(VIDEO_PUBLISH, json={"publish_input": publish_input})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == TaskStatus.INPUT_REQUIRED.value
    assert data["outcome"] == "pending_human"
    assert data["output"]["review_id"] == "rev_1"
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "publish_sns")
    assert request.input == publish_input
