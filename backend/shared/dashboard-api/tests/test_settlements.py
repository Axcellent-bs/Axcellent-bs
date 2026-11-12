"""판매자 정산 창구가 support skill을 부르고 오류를 화면 코드로 바꾸는지 본다.

support 출력은 명세(판매자 정산 재무 → 고객지원)의 모양을 본뜬 대역이다.
"""

from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import support_client
from ax_dashboard_api.main import app

BASE = "/api/dashboard/settlements"

META = {
    "mock": False,
    "collected_at": "2026-11-12T00:00:00Z",
    "source": "shop-api",
    "unavailable": [],
}
SETTLEMENT = {
    "period": "2026-W45",
    "from": "2026-11-02T00:00:00Z",
    "to": "2026-11-09T00:00:00Z",
    "pay_date": "2026-11-14",
    "statements": [
        {
            "seller_id": "sel_0001",
            "name": "고양이책방",
            "orders": 3,
            "gross": 45000,
            "commission": 4500,
            "commission_rate": 0.1,
            "seller_coupon": 0,
            "refunds": 0,
            "payout": 40500,
            "hold_reasons": [],
            "status": "ready",
            "review_id": None,
            "pending_confirmation": {"orders": 0, "gross": 0},
            "products": [],
        }
    ],
    "applied_rules": [],
    "batch": {
        "status": "none",
        "review_id": None,
        "execution_id": None,
        "total_payout": 0,
        "seller_ids": [],
    },
    "meta": META,
}
DEADLINES = {
    "today": "2026-11-12",
    "deadline_days": 20,
    "basis": "구매 확정일부터",
    "warn_days": 3,
    "since": "2026-10-01",
    "counts": {"overdue": 0, "at_risk": 0, "due_soon": 0, "ok": 1},
    "waiting_orders": 0,
    "sellers": [],
    "meta": META,
}


class Fake:
    """support 대역. skill별 출력·결과 상태를 넣어 둔다."""

    def __init__(self) -> None:
        self.sent: list[AgentRequest] = []
        self.outputs: dict[str, dict[str, Any]] = {}
        self.failures: dict[str, tuple[TaskStatus, str]] = {}

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "support"
        self.sent.append(req)
        status, outcome = self.failures.get(req.skill, (TaskStatus.COMPLETED, None))
        return AgentResult(
            meta=req.meta, status=status, outcome=outcome, output=self.outputs.get(req.skill, {})
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    fake = Fake()
    monkeypatch.setattr(support_client, "SEND", fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_statements_come_from_support_with_its_meta(client: TestClient, fake: Fake) -> None:
    fake.outputs["seller_settlement"] = deepcopy(SETTLEMENT)
    response = client.get(BASE + "?period=2026-W45")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["period"] == "2026-W45"
    assert body["data"]["statements"][0]["payout"] == 40500
    assert "meta" not in body["data"]
    assert body["meta"]["collected_at"] == META["collected_at"]
    assert body["meta"]["tenant_id"] == "shop-demo"
    assert fake.sent[0].skill == "seller_settlement"
    assert fake.sent[0].input == {"period": "2026-W45"}


def test_without_period_support_picks_last_week(client: TestClient, fake: Fake) -> None:
    fake.outputs["seller_settlement"] = deepcopy(SETTLEMENT)
    assert client.get(BASE).status_code == 200
    assert fake.sent[0].input == {"period": None}


def test_bad_period_is_a_400(client: TestClient, fake: Fake) -> None:
    fake.failures["seller_settlement"] = (TaskStatus.FAILED, "invalid_input")
    fake.outputs["seller_settlement"] = {"message": "period는 YYYY-Www"}
    response = client.get(BASE + "?period=2026-45")
    assert response.status_code == 400
    assert response.json()["error"] == {"code": "INVALID_INPUT", "message": "period는 YYYY-Www"}


def test_contract_style_invalid_input_code_is_also_a_400(client: TestClient, fake: Fake) -> None:
    fake.failures["seller_settlement"] = (TaskStatus.REJECTED, "INVALID_INPUT")
    assert client.get(BASE + "?period=x").status_code == 400


def test_deadlines_come_from_support(client: TestClient, fake: Fake) -> None:
    fake.outputs["seller_settlement_deadlines"] = deepcopy(DEADLINES)
    response = client.get(BASE + "/deadlines")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["counts"]["ok"] == 1 and "meta" not in body["data"]
    assert body["meta"]["source"] == "shop-api"
    assert fake.sent[0].skill == "seller_settlement_deadlines"


def test_a_disabled_support_is_not_shown_as_nothing_to_settle(
    client: TestClient, fake: Fake
) -> None:
    fake.failures["seller_settlement"] = (TaskStatus.REJECTED, "service_disabled")
    fake.outputs["seller_settlement"] = {"to_service": "claims"}
    response = client.get(BASE)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_DISABLED"


def test_batch_request_sends_the_human_from_the_token(client: TestClient, fake: Fake) -> None:
    requested = deepcopy(SETTLEMENT)
    requested["batch"] = {
        "status": "requested",
        "review_id": "rev_1",
        "execution_id": "exe_1",
        "total_payout": 40500,
        "seller_ids": ["sel_0001"],
    }
    fake.outputs["request_seller_settlement"] = {"settlement": requested, "requested": True}
    response = client.post(
        BASE + "/batch", json={"period": "2026-W45", "requested_by": "남이 보낸 이름"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["requested"] is True
    assert body["data"]["settlement"]["batch"]["review_id"] == "rev_1"
    assert "meta" not in body["data"]["settlement"]
    assert body["meta"]["collected_at"] == META["collected_at"]
    sent = fake.sent[0]
    assert sent.skill == "request_seller_settlement"
    assert sent.input == {"period": "2026-W45", "requested_by": "tester"}  # 본문 이름은 쓰지 않는다
    assert sent.meta.idempotency_key.endswith(":request_seller_settlement:settlement:2026-W45")


def test_an_already_requested_week_is_not_requested_again(client: TestClient, fake: Fake) -> None:
    fake.outputs["request_seller_settlement"] = {
        "settlement": deepcopy(SETTLEMENT),
        "requested": False,
    }
    response = client.post(BASE + "/batch", json={"period": "2026-W45"})
    assert response.status_code == 200 and response.json()["data"]["requested"] is False


@pytest.mark.parametrize(
    ("outcome", "status", "code"),
    [
        ("nothing_to_settle", 409, "NOTHING_TO_SETTLE"),
        ("period_not_closed", 409, "PERIOD_NOT_CLOSED"),
        ("orders_partial", 503, "ORDERS_PARTIAL"),
        ("safety_unavailable", 503, "SAFETY_UNAVAILABLE"),
        ("invalid_input", 400, "INVALID_INPUT"),
    ],
)
def test_batch_failures_map_to_screen_codes(
    client: TestClient, fake: Fake, outcome: str, status: int, code: str
) -> None:
    fake.failures["request_seller_settlement"] = (TaskStatus.FAILED, outcome)
    fake.outputs["request_seller_settlement"] = {"message": "안 됐다"}
    response = client.post(BASE + "/batch", json={"period": "2026-W45"})
    assert response.status_code == status
    assert response.json()["error"] == {"code": code, "message": "안 됐다"}


def test_batch_needs_a_human_token(client: TestClient, fake: Fake) -> None:
    client.headers.pop("authorization")
    response = client.post(BASE + "/batch", json={"period": "2026-W45"})
    assert response.status_code == 401
    assert not fake.sent


def test_batch_refuses_an_agent_token(client: TestClient, fake: Fake) -> None:
    client.headers["authorization"] = "Bearer mock:finance"
    response = client.post(BASE + "/batch", json={"period": "2026-W45"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "HUMAN_TOKEN_REQUIRED"
    assert not fake.sent


def test_reading_needs_no_token(client: TestClient, fake: Fake) -> None:
    client.headers.pop("authorization")
    fake.outputs["seller_settlement"] = deepcopy(SETTLEMENT)
    assert client.get(BASE).status_code == 200


def test_preparing_books_are_a_503_preparing(client: TestClient, fake: Fake) -> None:
    """정산 서비스가 켠 뒤 첫 장부를 모으는 중이면 30초 기다려 504가 아니라 바로 503 준비 중."""
    fake.failures["seller_settlement"] = (TaskStatus.FAILED, "settlement_preparing")
    fake.outputs["seller_settlement"] = {"message": "판매자 정산 장부를 모으는 중이다"}
    response = client.get(BASE)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SETTLEMENT_PREPARING"
