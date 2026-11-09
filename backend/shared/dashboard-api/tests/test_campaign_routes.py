"""화면의 캠페인 시작·결과·취소 경로."""

from __future__ import annotations

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
        return AgentResult(
            meta=request.meta, status=self.status, outcome=self.outcome, output={"grade": "B"}
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
