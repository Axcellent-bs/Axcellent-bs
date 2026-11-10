"""토스페이먼츠 테스트 결제(prepare → confirm).

토스는 httpx MockTransport로 흉내 낸다(실제 네트워크 없음).
"""

import base64
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api import toss
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

KEY = "test_sk_shop_api_unit_only"  # 시험용 가짜 값(실제 키 아님)
ADMIN = {"Authorization": "Bearer mock:admin"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeToss:
    """토스 승인 API 흉내. 받은 요청을 남기고, 정한 응답(또는 예외)을 돌려준다."""

    def __init__(self, status: int = 200, body: dict | None = None, error: Exception | None = None):
        self.status, self.error = status, error
        self.body = body if body is not None else {"status": "DONE", "method": "간편결제"}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status, json=self.body)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv("PAYMENT_PROVIDER", "toss_test")
    monkeypatch.setenv("TOSS_SECRET_KEY", KEY)
    events.reset_mock_bus()


def _client(fake: FakeToss, clock: Clock | None = None) -> TestClient:
    app = create_app(Db(":memory:"), toss_transport=httpx.MockTransport(fake))
    if clock is not None:
        app.state.shop.clock = clock
    return TestClient(app)


def _order(client: TestClient) -> dict:
    body = {"buyer_id": "buy_0001", "items": [{"product_id": "prd_0001", "qty": 1},
                                              {"product_id": "prd_0002", "qty": 2}]}  # fmt: skip
    return client.post("/api/shop/orders", json=body).json()["data"]


def _prepare(client: TestClient, order_id: str) -> dict:
    res = client.post(f"/api/shop/orders/{order_id}/payments/toss/prepare")
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _confirm(client: TestClient, prepared: dict, amount: int | None = None) -> httpx.Response:
    body = {"toss_order_id": prepared["toss_order_id"], "payment_key": "tpk_unit_1",
            "amount": prepared["amount"] if amount is None else amount}  # fmt: skip
    return client.post("/api/shop/payments/toss/confirm", json=body)


def _types() -> list[str]:
    return [e.type.value for e in events.MOCK_PUBLISHED]


def _payment_row(client: TestClient, order_id: str) -> dict:
    db = client.app.state.shop.db
    with db.tx() as conn:
        row = conn.execute(
            "SELECT provider, pg_payment_key, failure_detail, failure_code, method_code, status "
            "FROM payments WHERE order_id = ?", (order_id,),
        ).fetchone()  # fmt: skip
    return dict(row)


# --- 준비 -------------------------------------------------------------------


def test_prepare_uses_the_db_total_and_a_new_id_per_attempt() -> None:
    client = _client(FakeToss())
    order = _order(client)
    first = _prepare(client, order["order_id"])
    second = _prepare(client, order["order_id"])
    assert first["amount"] == order["total_amount"]  # 배송비 포함 DB 합계
    assert first["toss_order_id"] == f"{order['order_id']}-a1"
    assert second["toss_order_id"] == f"{order['order_id']}-a2"
    assert first["order_name"].endswith(" 외 1건")
    assert first["order_name"].startswith(order["items"][0]["title"])


def test_prepare_only_for_payable_orders() -> None:
    client = _client(FakeToss())
    order = _order(client)
    client.post(f"/api/shop/orders/{order['order_id']}/cancel", json={})
    res = client.post(f"/api/shop/orders/{order['order_id']}/payments/toss/prepare")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "ORDER_NOT_PAYABLE"


# --- 승인 -------------------------------------------------------------------


def test_confirm_success_records_like_the_fake_payment() -> None:
    fake = FakeToss()
    client = _client(fake)
    order = _order(client)
    prepared = _prepare(client, order["order_id"])
    res = _confirm(client, prepared)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["status"] == "paid"
    assert data["payments"][0]["method_code"] == "easy_pay"
    assert res.json()["meta"]["replayed"] is False
    row = _payment_row(client, order["order_id"])
    assert row["provider"] == "toss_test" and row["pg_payment_key"] == "tpk_unit_1"
    assert "payment.succeeded" in _types()
    (request,) = fake.requests
    assert str(request.url) == toss.CONFIRM_URL
    assert request.headers["Authorization"] == "Basic " + base64.b64encode(
        f"{KEY}:".encode()).decode()  # fmt: skip
    assert request.headers["Idempotency-Key"] == prepared["toss_order_id"]
    assert json.loads(request.content) == {"paymentKey": "tpk_unit_1",
                                           "orderId": prepared["toss_order_id"],
                                           "amount": prepared["amount"]}  # fmt: skip


METHODS = [("카드", "card"), ("계좌이체", "transfer"), ("휴대폰", "phone"), ("가상계좌", "other")]


@pytest.mark.parametrize(("method", "code"), METHODS)
def test_toss_method_maps_to_our_method_code(method: str, code: str) -> None:
    client = _client(FakeToss(body={"status": "DONE", "method": method}))
    order = _order(client)
    data = _confirm(client, _prepare(client, order["order_id"])).json()["data"]
    assert data["payments"][0]["method_code"] == code


def test_amount_mismatch_is_400_and_toss_is_not_called() -> None:
    fake = FakeToss()
    client = _client(fake)
    order = _order(client)
    prepared = _prepare(client, order["order_id"])
    res = _confirm(client, prepared, amount=prepared["amount"] - 1)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "AMOUNT_MISMATCH"
    assert fake.requests == []
    assert client.get(f"/api/shop/orders/{order['order_id']}").json()["data"]["status"] == (
        "pending_payment")  # fmt: skip


def test_unknown_attempt_is_404() -> None:
    client = _client(FakeToss())
    body = {"toss_order_id": "ord_9999-a1", "payment_key": "x", "amount": 1000}
    res = client.post("/api/shop/payments/toss/confirm", json=body)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "PAYMENT_ATTEMPT_NOT_FOUND"


def test_toss_4xx_is_declined_not_server_error() -> None:
    fake = FakeToss(400, {"code": "REJECT_CARD_COMPANY", "message": "거절"})
    client = _client(fake)
    order = _order(client)
    data = _confirm(client, _prepare(client, order["order_id"])).json()["data"]
    assert data["status"] == "payment_failed"
    assert data["payments"][0]["failure_code"] == "PG_DECLINED"
    assert _payment_row(client, order["order_id"])["failure_detail"] == "REJECT_CARD_COMPANY"
    samples = list(client.app.state.shop.monitor._samples)
    assert [error for *_, error in samples] == [False]
    assert "payment.failed" in _types()


def test_toss_5xx_is_unavailable() -> None:
    client = _client(FakeToss(500, {"code": "FAILED_INTERNAL_SYSTEM_PROCESSING"}))
    order = _order(client)
    data = _confirm(client, _prepare(client, order["order_id"])).json()["data"]
    assert data["payments"][0]["failure_code"] == "PG_UNAVAILABLE"
    assert _payment_row(client, order["order_id"])["failure_detail"] == (
        "FAILED_INTERNAL_SYSTEM_PROCESSING")  # fmt: skip
    assert [error for *_, error in client.app.state.shop.monitor._samples] == [True]


def test_toss_timeout_is_unavailable() -> None:
    client = _client(FakeToss(error=httpx.ReadTimeout("slow")))
    order = _order(client)
    data = _confirm(client, _prepare(client, order["order_id"])).json()["data"]
    assert data["payments"][0]["failure_code"] == "PG_UNAVAILABLE"
    assert _payment_row(client, order["order_id"])["failure_detail"] == "TIMEOUT"


def test_toss_connection_error_is_unavailable() -> None:
    client = _client(FakeToss(error=httpx.ConnectError("down")))
    order = _order(client)
    data = _confirm(client, _prepare(client, order["order_id"])).json()["data"]
    assert data["payments"][0]["failure_code"] == "PG_UNAVAILABLE"
    assert _payment_row(client, order["order_id"])["failure_detail"] == "CONNECT_ERROR"


def test_fault_injected_failure_does_not_call_toss() -> None:
    fake = FakeToss()
    client = _client(fake)
    client.post("/api/shop/admin/faults", json={"kind": "failure"}, headers=ADMIN)
    order = _order(client)
    data = _confirm(client, _prepare(client, order["order_id"])).json()["data"]
    assert fake.requests == []
    assert data["payments"][0]["failure_code"] == "PG_UNAVAILABLE"
    assert _payment_row(client, order["order_id"])["failure_detail"] == "FAULT_INJECTED"


def test_injected_latency_is_added_to_the_measured_time() -> None:
    client = _client(FakeToss())
    client.post("/api/shop/admin/faults", json={"kind": "latency", "delay_ms": 2000},
                headers=ADMIN)  # fmt: skip
    order = _order(client)
    _confirm(client, _prepare(client, order["order_id"]))
    ((_, latency, _),) = client.app.state.shop.monitor._samples
    assert latency >= 2000


def test_same_confirm_twice_calls_toss_once_and_returns_the_first_result() -> None:
    fake = FakeToss()
    client = _client(fake)
    order = _order(client)
    prepared = _prepare(client, order["order_id"])
    first = _confirm(client, prepared).json()
    second = _confirm(client, prepared).json()
    assert len(fake.requests) == 1
    assert second["meta"]["replayed"] is True
    assert second["data"]["payments"] == first["data"]["payments"]
    assert len(second["data"]["payments"]) == 1


def test_confirm_failures_under_fault_count_in_the_5xx_rate() -> None:
    """장애 주입 중 토스 승인 실패도 5xx 비율에 들어가 `shop.degraded`(error_rate_5xx)가 난다."""
    clock = Clock()
    client = _client(FakeToss(), clock)
    client.post("/api/shop/admin/faults", json={"kind": "failure"}, headers=ADMIN)
    for _ in range(5):  # 최소 표본 5개
        _confirm(client, _prepare(client, _order(client)["order_id"]))
    assert "shop.degraded" not in _types()
    clock.now += 60  # 1분 지속
    client.app.state.shop.evaluate_monitor()
    with client.app.state.shop.db.tx() as conn:
        payloads = [json.loads(r["payload"]) for r in conn.execute(
            "SELECT payload FROM outbox_events")]  # fmt: skip
    degraded = [p for p in payloads if p["type"] == "shop.degraded"]
    assert degraded and degraded[0]["data"]["metric_code"] == "error_rate_5xx"


# --- 설정 -------------------------------------------------------------------


def test_payment_config_does_not_carry_the_key() -> None:
    client = _client(FakeToss())
    res = client.get("/api/shop/payment-config")
    assert res.json()["data"] == {"provider": "toss_test"}
    assert KEY not in res.text and KEY[:5] not in res.text.replace("toss_test", "")


@pytest.mark.parametrize("key", ["live_sk_shop_api_unit_only", "live_gsk_x"])
def test_live_key_is_refused(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
                             key: str) -> None:  # fmt: skip
    monkeypatch.setenv("TOSS_SECRET_KEY", key)
    fake = FakeToss()
    with caplog.at_level(logging.WARNING):
        client = _client(fake)
    assert client.get("/api/shop/payment-config").json()["data"] == {"provider": "fake"}
    order = _order(client)
    res = client.post(f"/api/shop/orders/{order['order_id']}/payments/toss/prepare")
    assert res.status_code == 409 and res.json()["error"]["code"] == "TOSS_DISABLED"
    assert "라이브 키" in caplog.text
    assert key not in caplog.text and key[:5] not in caplog.text
    assert fake.requests == []


def test_missing_key_falls_back_to_fake(monkeypatch: pytest.MonkeyPatch,
                                        caplog: pytest.LogCaptureFixture) -> None:  # fmt: skip
    monkeypatch.delenv("TOSS_SECRET_KEY", raising=False)
    with caplog.at_level(logging.WARNING):
        client = _client(FakeToss())
    assert client.get("/api/shop/payment-config").json()["data"] == {"provider": "fake"}
    assert len([r for r in caplog.records if "TOSS_SECRET_KEY" in r.getMessage()]) == 1


def test_non_test_key_falls_back_to_fake() -> None:
    config = toss.load_config({"PAYMENT_PROVIDER": "toss_test", "TOSS_SECRET_KEY": "sk_unknown"})
    assert config.provider == "fake" and config.warning
    assert "sk_unknown" not in config.warning and "sk_unknown" not in repr(config)


def test_default_is_fake_without_warning() -> None:
    assert toss.load_config({}) == toss.PaymentConfig("fake")


def test_config_repr_hides_the_key() -> None:
    config = toss.load_config({"PAYMENT_PROVIDER": "toss_test", "TOSS_SECRET_KEY": KEY})
    assert config.provider == "toss_test"
    assert KEY not in repr(config) and KEY not in repr(toss.TossClient(KEY))


def test_fake_payment_still_works_and_is_marked_fake() -> None:
    client = _client(FakeToss())
    order = _order(client)
    paid = client.post(f"/api/shop/orders/{order['order_id']}/payments", json={}).json()["data"]
    assert paid["status"] == "paid"
    assert _payment_row(client, order["order_id"])["provider"] == "fake"
