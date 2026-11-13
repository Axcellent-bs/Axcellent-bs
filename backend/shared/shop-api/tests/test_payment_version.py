"""결제 설정 버전(시연용 "잘못된 배포"): v1 정상, v2는 결제사 승인이 500대로 실패한다.

장애 스위치(active)가 아니라 설정 버전이라 해제로는 안 풀린다. 배포(아무 버전으로)는 사람(admin)만,
이전 버전으로 되돌리기는 인프라 AI 롤백도 부른다. 인프라 토큰은 되돌리기 경로 하나만 통한다.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import EventType, ShopDegradedData
from ax_shop_api.db import Db
from ax_shop_api.main import create_app
from ax_shop_api.service import BAD_CONFIG_PG_CODE

ADMIN = {"Authorization": "Bearer mock:admin"}
INFRA = {"Authorization": "Bearer mock:infra"}
MARKETING = {"Authorization": "Bearer mock:marketing"}
VERSION = "/api/shop/admin/payment-version"
ROLLBACK = f"{VERSION}/rollback"
TEST_KEY = "test_sk_shop_api_unit_only"  # 시험용 가짜 값(실제 키 아님)
LIVE_KEY = "live_sk_shop_api_unit_only"  # 시험용 가짜 값(실제 키 아님)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv("SHOP_DEGRADED_SUSTAIN_S", "20")
    monkeypatch.delenv("PAYMENT_PROVIDER", raising=False)
    monkeypatch.delenv("TOSS_SECRET_KEY", raising=False)
    events.reset_mock_bus()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _client(clock: Clock | None = None, transport: httpx.MockTransport | None = None) -> TestClient:
    app = create_app(Db(":memory:"), toss_transport=transport)
    if clock is not None:
        app.state.shop.clock = clock
    return TestClient(app)


def _order(client: TestClient) -> dict:
    body = {"buyer_id": "buy_0001", "items": [{"product_id": "prd_0001", "qty": 1}]}
    return client.post("/api/shop/orders", json=body).json()["data"]


def _pay(client: TestClient) -> dict:
    order = _order(client)
    return client.post(f"/api/shop/orders/{order['order_id']}/payments", json={}).json()["data"]


def _deploy(client: TestClient, version: str, headers: dict = ADMIN) -> httpx.Response:
    return client.put(VERSION, json={"version": version}, headers=headers)


def test_v2_payments_fail_as_server_errors_without_the_fault_switch(clock: Clock) -> None:
    client = _client(clock)
    assert _deploy(client, "v2").json()["data"]["version"] == "v2"
    for _ in range(6):  # 5초 간격: 표본 5개가 모이고 5xx 초과가 20초 이어진다
        failed = _pay(client)
        clock.now += 5
    assert failed["status"] == "payment_failed"
    assert failed["payments"][0]["failure_code"] == "PG_UNAVAILABLE"
    state = client.get("/api/shop/admin/faults", headers=ADMIN).json()["data"]
    assert state["active"] is False and state["payment_version"] == "v2"
    assert state["degraded"] is True  # 결제 실패가 모니터 표본(5xx)에 잡혔다
    degraded = next(e for e in events.MOCK_PUBLISHED if e.type == EventType.SHOP_DEGRADED)
    assert ShopDegradedData.model_validate(degraded.data).metric_code == "error_rate_5xx"
    # 장애 스위치 해제로는 안 풀린다
    released = client.delete("/api/shop/admin/faults", headers=INFRA).json()["data"]
    assert released["released"] is False and released["payment_version"] == "v2"
    assert _pay(client)["status"] == "payment_failed"


def test_rollback_to_v1_fixes_payments_and_is_idempotent() -> None:
    client = _client()
    _deploy(client, "v2")
    first = client.post(ROLLBACK, json={"to_version": "v1"}, headers=INFRA).json()["data"]
    assert (first["before"], first["version"], first["changed"]) == ("v2", "v1", True)
    assert first["changed_by"] == "infra"
    again = client.post(ROLLBACK, json={"to_version": "v1"}, headers=INFRA).json()["data"]
    assert (again["before"], again["version"], again["changed"]) == ("v1", "v1", False)
    assert _pay(client)["status"] == "paid"


def test_infra_may_only_roll_back() -> None:
    client = _client()
    assert _deploy(client, "v2", headers=INFRA).status_code == 403  # 배포는 사람만
    forward = client.post(ROLLBACK, json={"to_version": "v2"}, headers=INFRA)
    assert forward.status_code == 409  # v1 → v2는 되돌리기가 아니다
    assert client.post(ROLLBACK, json={"to_version": "v1"}, headers=MARKETING).status_code == 403
    assert _deploy(client, "v3").status_code == 422
    assert client.get(VERSION, headers=INFRA).json()["data"]["version"] == "v1"


def test_a_person_resets_to_v1() -> None:
    client = _client()
    _deploy(client, "v2")
    reset = _deploy(client, "v1").json()["data"]
    assert (reset["before"], reset["version"], reset["changed_by"]) == ("v2", "v1", "admin")


def test_v2_is_refused_when_a_live_key_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYMENT_PROVIDER", "toss_test")
    monkeypatch.setenv("TOSS_SECRET_KEY", LIVE_KEY)
    client = _client()
    refused = _deploy(client, "v2")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "LIVE_KEY"
    assert client.get(VERSION, headers=ADMIN).json()["data"]["version"] == "v1"


def test_v2_sends_a_500_test_code_to_toss_with_a_test_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYMENT_PROVIDER", "toss_test")
    monkeypatch.setenv("TOSS_SECRET_KEY", TEST_KEY)
    seen: list[httpx.Request] = []

    def toss(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("TossPayments-Test-Code"):
            return httpx.Response(500, json={"code": request.headers["TossPayments-Test-Code"]})
        return httpx.Response(200, json={"status": "DONE", "method": "카드"})

    client = _client(transport=httpx.MockTransport(toss))

    def confirm() -> dict:
        order = _order(client)
        prepared = client.post(f"/api/shop/orders/{order['order_id']}/payments/toss/prepare")
        p = prepared.json()["data"]
        body = {"toss_order_id": p["toss_order_id"], "payment_key": "tpk_unit_1",
                "amount": p["amount"]}  # fmt: skip
        return client.post("/api/shop/payments/toss/confirm", json=body).json()["data"]

    assert confirm()["status"] == "paid"
    assert "TossPayments-Test-Code" not in seen[-1].headers  # v1은 붙이지 않는다
    _deploy(client, "v2")
    failed = confirm()
    assert seen[-1].headers["TossPayments-Test-Code"] == BAD_CONFIG_PG_CODE
    assert failed["status"] == "payment_failed"
    assert failed["payments"][0]["failure_code"] == "PG_UNAVAILABLE"  # 5xx로 센다
