"""개인정보 삭제 API: 미리보기, 고객 요청 삭제(사람 승인), 하루 한 번 도는 3년 삭제."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import main, support_client
from ax_dashboard_api.main import app

BASE = "/api/dashboard"
HUMAN = {"Authorization": "Bearer tok-gongyong"}


class Fake:
    def __init__(self) -> None:
        self.sent: list[AgentRequest] = []
        self.outputs: dict[str, dict[str, Any]] = {}
        self.outcomes: dict[str, str] = {}
        self.fail = False

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "support"
        self.sent.append(req)
        if self.fail:
            raise ConnectionError("support down")
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            outcome=self.outcomes.get(req.skill),
            output=self.outputs.get(req.skill, {}),
        )

    def last(self, skill: str) -> AgentRequest:
        return next(r for r in reversed(self.sent) if r.skill == skill)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:tok-gongyong")
    fake = Fake()
    fake.outputs = {
        "preview_data_purge": {"mode": "customer_request", "items": [], "purge_count": 0},
        "purge_customer_data": {"mode": "customer_request", "items": [], "applied": True},
        "purge_expired_inquiries": {"mode": "expired", "items": [], "applied": True},
    }
    monkeypatch.setattr(support_client, "SEND", fake)
    return fake


@pytest.fixture
def client(fake: Fake) -> TestClient:
    return TestClient(app)


def test_preview_passes_the_mode_and_the_request_inquiry(client: TestClient, fake: Fake) -> None:
    res = client.get(
        f"{BASE}/privacy/purge-preview",
        params={"mode": "customer_request", "inquiry_id": 5},
        headers=HUMAN,
    )
    assert res.status_code == 200 and res.json()["data"]["mode"] == "customer_request"
    assert fake.last("preview_data_purge").input == {"mode": "customer_request", "inquiry_id": 5}


def test_preview_of_expired_needs_no_inquiry(client: TestClient, fake: Fake) -> None:
    client.get(f"{BASE}/privacy/purge-preview", params={"mode": "expired"}, headers=HUMAN)
    assert fake.last("preview_data_purge").input == {"mode": "expired"}


def test_preview_rejects_an_unknown_mode(client: TestClient) -> None:
    res = client.get(f"{BASE}/privacy/purge-preview", params={"mode": "all"}, headers=HUMAN)
    assert res.status_code == 422


def test_customer_data_purge_needs_a_human_token(client: TestClient, fake: Fake) -> None:
    res = client.post(f"{BASE}/conversations/5/customer-data-purge")
    assert res.status_code in (401, 403) and fake.sent == []


def test_customer_data_purge_uses_one_key_before_and_after_approval(
    client: TestClient, fake: Fake
) -> None:
    """승인 전후로 같은 버튼을 눌러도 같은 건이어야 한다(키가 같다)."""
    client.post(f"{BASE}/conversations/5/customer-data-purge", headers=HUMAN)
    client.post(f"{BASE}/conversations/5/customer-data-purge", headers=HUMAN)
    keys = {r.meta.idempotency_key for r in fake.sent if r.skill == "purge_customer_data"}
    assert keys == {"customer-deletion:5"}
    assert fake.last("purge_customer_data").input == {"inquiry_id": 5}


def test_a_held_deletion_is_shown_as_waiting_for_approval(client: TestClient, fake: Fake) -> None:
    fake.outcomes["purge_customer_data"] = "not_executed_held"
    res = client.post(f"{BASE}/conversations/5/customer-data-purge", headers=HUMAN)
    assert res.status_code == 409 and res.json()["error"]["code"] == "NOT_EXECUTED"


def test_purge_is_not_supported_in_mock_mode(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    res = client.post(f"{BASE}/conversations/5/customer-data-purge", headers=HUMAN)
    assert res.status_code == 501


@pytest.mark.asyncio
async def test_the_daily_sweep_calls_the_expiry_skill_with_a_per_day_key(fake: Fake) -> None:
    assert await main.retention_sweep_once() is True
    request = fake.last("purge_expired_inquiries")
    today = datetime.now(UTC).date().isoformat()
    assert request.meta.idempotency_key == f"retention-expire:{today}"
    # 클레임 기록(팀 기준 5년)도 같은 주기에 부른다. 켜기 전에는 건수만 세는 것은 support가 정한다
    claims = fake.last("purge_expired_claims")
    assert claims.meta.idempotency_key == f"claim-retention:{today}"


@pytest.mark.asyncio
async def test_a_failing_sweep_does_not_raise(fake: Fake) -> None:
    fake.fail = True
    assert await main.retention_sweep_once() is False  # 다음 주기에 다시 부른다


@pytest.mark.asyncio
async def test_the_daily_sweep_sends_the_idea_reports_for_the_last_days(fake: Fake) -> None:
    assert await main.retention_sweep_once() is True
    today = datetime.now(UTC).date()
    sent = [r for r in fake.sent if r.skill == "send_idea_report"]
    days = [(today - timedelta(days=back)).isoformat() for back in range(1, 4)]
    assert [r.input["report_date"] for r in sent] == days  # 어제부터 거꾸로
    assert [r.meta.idempotency_key for r in sent] == [f"idea-report:{d}" for d in days]


@pytest.mark.asyncio
async def test_a_failing_idea_report_does_not_stop_the_sweep(fake: Fake) -> None:
    fake.fail = True
    await main.idea_report_sweep_once()  # 예외 없이 끝난다 (다음 주기에 같은 날을 다시 보낸다)
