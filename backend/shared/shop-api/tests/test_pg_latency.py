"""시험용 "결제사 쪽 지연"(pg_slow): AI가 고치지 못하는 장애 시연용.

결제마다 실제로 기다리고 그 지연을 표본에 남긴다. 쇼핑몰 장애 스위치(active)가 아니라서 인프라
AI의 해제(DELETE /faults)로는 꺼지지 않고, 사람(admin)이 DELETE /faults/pg로만 끈다.
"""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import EventType, ShopDegradedData
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

ADMIN = {"Authorization": "Bearer mock:admin"}
INFRA = {"Authorization": "Bearer mock:infra"}
FAULTS = "/api/shop/admin/faults"
# 실제로 기다리는 시간이라 짧게. 기준 p95를 평소(180ms)와 지연 결제(180+80ms) 사이로 낮춘다
PG_MS = 80


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")  # 결제마다 판정한다
    monkeypatch.setenv("SHOP_DEGRADED_P95_MS", "220")
    monkeypatch.setenv("SHOP_DEGRADED_SUSTAIN_S", "20")
    events.reset_mock_bus()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(clock: Clock) -> TestClient:
    app = create_app(Db(":memory:"))
    app.state.shop.clock = clock
    return TestClient(app)


def _pay(client: TestClient) -> dict:
    body = {"buyer_id": "buy_0001", "items": [{"product_id": "prd_0001", "qty": 1}]}
    order = client.post("/api/shop/orders", json=body).json()["data"]
    return client.post(f"/api/shop/orders/{order['order_id']}/payments", json={}).json()["data"]


def _shop_events() -> list[str]:
    return [e.type.value for e in events.MOCK_PUBLISHED if e.type.value.startswith("shop.")]


def _state(client: TestClient) -> dict:
    return client.get(FAULTS, headers=ADMIN).json()["data"]


def _slow(client: TestClient) -> dict:
    return client.post(FAULTS, json={"kind": "pg_slow", "delay_ms": PG_MS}, headers=ADMIN).json()


def test_pg_slow_is_not_the_shop_fault_switch(client: TestClient) -> None:
    state = _slow(client)["data"]
    assert state["pg_delay_ms"] == PG_MS
    assert state["active"] is False  # 장애 스위치가 아니다
    order = _pay(client)
    assert order["status"] == "paid"  # 느리지만 결제는 된다


def test_only_a_person_can_turn_pg_slow_on_or_off(client: TestClient) -> None:
    on = {"kind": "pg_slow", "delay_ms": PG_MS}
    assert client.post(FAULTS, json=on, headers=INFRA).status_code == 403
    _slow(client)
    # AI(인프라)의 해제로는 꺼지지 않는다: 켜진 스위치가 없어 아무것도 바뀌지 않는다
    released = client.delete(FAULTS, headers=INFRA).json()["data"]
    assert released["released"] is False and released["pg_delay_ms"] == PG_MS
    assert client.delete(f"{FAULTS}/pg", headers=INFRA).status_code == 403
    off = client.delete(f"{FAULTS}/pg", headers=ADMIN).json()["data"]
    assert off["pg_delay_ms"] == 0


@pytest.mark.parametrize("delay_ms", [0, 10_001])
def test_pg_slow_needs_a_bounded_delay(client: TestClient, delay_ms: int) -> None:
    body = {"kind": "pg_slow", "delay_ms": delay_ms}
    assert client.post(FAULTS, json=body, headers=ADMIN).status_code == 422


def test_monitor_sees_the_real_wait_as_p95_and_recovers_after_pg_ok(
    client: TestClient, clock: Clock
) -> None:
    _slow(client)
    for _ in range(6):  # 5초 간격 결제: 표본 5개가 모이고 기준 초과가 20초 이어진다
        _pay(client)
        clock.now += 5
    assert _state(client)["degraded"] is True
    degraded = next(e for e in events.MOCK_PUBLISHED if e.type == EventType.SHOP_DEGRADED)
    data = ShopDegradedData.model_validate(degraded.data)
    assert data.metric_code == "p95_latency_ms"
    assert data.fault_kind == "pg_slow"
    assert data.value is not None and data.value >= 180 + PG_MS  # 실제로 기다린 지연이 들어간다

    client.delete(f"{FAULTS}/pg", headers=ADMIN)
    state = _state(client)
    assert state["degraded"] is True  # 끈 것만으로는 복구가 아니다
    # 정상 결제가 이어져 느린 표본이 1분 창에서 빠지고, 정상이 20초 이어져야 복구다
    for _ in range(30):
        clock.now += 5
        _pay(client)
        if "shop.recovered" in _shop_events():
            break
    assert _shop_events() == ["shop.degraded", "shop.recovered"]
    recovered = next(e for e in events.MOCK_PUBLISHED if e.type == EventType.SHOP_RECOVERED)
    # 스위치를 끈 쪽이 없다: 따로 조치한 기록 없이 지표가 돌아왔다(해제로 배우지 않게)
    assert (recovered.data["recovery"], recovered.data["released_by"]) == ("metrics_normal", None)
