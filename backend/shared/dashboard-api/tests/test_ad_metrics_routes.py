"""캠페인 광고 숫자·추적 링크 경로 (docs/marketing-ad-metrics-design.md C)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

BASE = "/api/dashboard/marketing/campaigns/cmp_PROP-1"


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.status = TaskStatus.COMPLETED
        self.outcome: str | None = None
        self.output: dict = {"status": "unknown", "reason": "아직 입력 없음", "rows": []}

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


def test_get_reads_the_campaign_rows(client: TestClient, fake: Fake) -> None:
    res = client.get(f"{BASE}/ad-metrics")
    assert res.status_code == 200 and res.json()["data"]["status"] == "unknown"
    target, request = fake.calls[0]
    assert (target, request.skill) == ("marketing", "get_campaign_ad_metrics")
    assert request.input == {"campaign_id": "cmp_PROP-1"}
    assert client.get("/api/dashboard/marketing/campaigns/PROP-1/ad-metrics").status_code == 422


def test_post_fills_who_from_the_token_not_the_body(client: TestClient, fake: Fake) -> None:
    fake.output = {"outcome": "recorded", "row": None}
    body = {"date": "2026-11-20", "source": "banner", "medium": "onsite", "spend_krw": 0}
    res = client.post(f"{BASE}/ad-metrics", json=body | {"entered_by": "someone-else"})
    assert res.status_code == 200 and res.json()["data"]["outcome"] == "recorded"
    _, request = fake.calls[-1]
    assert request.skill == "record_campaign_ad_metrics"
    assert request.input["entered_by"] == "tester"  # 토큰의 사람
    assert request.input["spend_krw"] == 0 and request.input["impressions"] is None


def test_judgements_pass_through_and_failures_map(client: TestClient, fake: Fake) -> None:
    fake.output = {"outcome": "rejected", "reason_code": "future_date"}
    body = {"date": "2030-01-01", "source": "banner", "medium": "onsite"}
    res = client.post(f"{BASE}/ad-metrics", json=body)
    assert res.status_code == 200 and res.json()["data"]["reason_code"] == "future_date"
    fake.status, fake.outcome, fake.output = TaskStatus.FAILED, "invalid_input", {"errors": ["x"]}
    assert client.post(f"{BASE}/ad-metrics", json=body).status_code == 400
    negative = body | {"clicks": -1}
    assert client.post(f"{BASE}/ad-metrics", json=negative).status_code == 422


def test_link_goes_to_build_campaign_link(client: TestClient, fake: Fake) -> None:
    fake.output = {"url": "https://shop.example/?utm_source=instagram", "utm_source": "instagram"}
    body = {"base_url": "https://shop.example/", "source": "instagram", "medium": "social"}
    res = client.post(f"{BASE}/link", json=body)
    assert res.status_code == 200 and res.json()["data"]["utm_source"] == "instagram"
    _, request = fake.calls[-1]
    assert request.skill == "build_campaign_link" and request.input["campaign_id"] == "cmp_PROP-1"
