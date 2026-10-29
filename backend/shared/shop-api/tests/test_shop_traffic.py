"""트래픽 생성기(scripts/shop_traffic.py)의 재고 경고·대기 로그·자동 보충.

실제 shop-api 앱에 붙여 확인한다.
"""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "shop_traffic.py"
_spec = importlib.util.spec_from_file_location("shop_traffic", SCRIPT)
assert _spec and _spec.loader
traffic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(traffic)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("SHOP_MOCK", raising=False)
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=adm-token,marketing=mkt-token")
    events.reset_mock_bus()
    return TestClient(create_app(Db(":memory:")))


def _caller(client: TestClient):
    def call(method, path, body=None, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        res = client.request(method, path, json=body, headers=headers)
        return res.status_code, res.json()

    return call


def _set_all_stock(client: TestClient, stock: int) -> None:
    for p in client.get("/api/shop/products").json()["data"]:
        res = client.patch(
            f"/api/shop/products/{p['product_id']}",
            json={"stock": stock},
            headers={"Authorization": "Bearer adm-token"},
        )
        assert res.status_code == 200


def _products(client: TestClient) -> list[dict]:
    return client.get("/api/shop/products").json()["data"]


def test_low_stock_warns_once_and_waits_every_minute(client: TestClient) -> None:
    logs: list[str] = []
    clock = [0.0]
    watch = traffic.StockWatch(
        _caller(client), low=10, refill=False, refill_to=200, token="",
        log=logs.append, clock=lambda: clock[0],
    )  # fmt: skip
    _set_all_stock(client, 3)
    items = watch.check(_products(client))
    assert items and sum("재고 부족" in line for line in logs) == len(items)
    watch.check(_products(client))  # 같은 상품은 다시 경고하지 않는다
    assert sum("재고 부족" in line for line in logs) == len(items)

    _set_all_stock(client, 0)
    assert watch.check(_products(client)) == []
    clock[0] = 30
    watch.check(_products(client))
    clock[0] = 61
    watch.check(_products(client))
    waits = [line for line in logs if "재고 없음으로 대기 중" in line]
    assert len(waits) == 2  # 0초, 61초 (30초는 건너뜀)
    assert "1분째" in waits[1]


def test_refill_uses_admin_token(client: TestClient) -> None:
    logs: list[str] = []
    watch = traffic.StockWatch(
        _caller(client), low=10, refill=True, refill_to=50, token="adm-token", log=logs.append
    )
    _set_all_stock(client, 0)
    items = watch.check(_products(client))
    assert items and all(p["stock"] == 50 for p in items)
    assert all(p["stock"] == 50 for p in _products(client))
    assert not any("대기 중" in line for line in logs)


def test_refill_with_wrong_token_is_refused_and_logged(client: TestClient) -> None:
    logs: list[str] = []
    watch = traffic.StockWatch(
        _caller(client), low=10, refill=True, refill_to=50, token="mkt-token", log=logs.append
    )
    _set_all_stock(client, 0)
    assert watch.check(_products(client)) == []
    assert any("재고 보충 실패" in line and "403" in line for line in logs)
    assert all(p["stock"] == 0 for p in _products(client))


def test_refill_without_token_warns(client: TestClient) -> None:
    logs: list[str] = []
    traffic.StockWatch(
        _caller(client), low=10, refill=True, refill_to=50, token="", log=logs.append
    )
    assert any("관리자 토큰이 없어" in line for line in logs)


def test_rate_and_admin_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOP_TRAFFIC_ORDERS_PER_MIN", raising=False)
    monkeypatch.delenv("SHOP_TRAFFIC_INTERVAL_S", raising=False)
    assert traffic.interval_s() == 3.0  # 예전 기본값
    monkeypatch.setenv("SHOP_TRAFFIC_INTERVAL_S", "5")
    assert traffic.interval_s() == 5.0
    monkeypatch.setenv("SHOP_TRAFFIC_ORDERS_PER_MIN", "60")
    assert traffic.interval_s() == 1.0
    monkeypatch.setenv("SHOP_TRAFFIC_ORDERS_PER_MIN", "0")
    with pytest.raises(ValueError):
        traffic.interval_s()

    monkeypatch.delenv("SHOP_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("AX_AGENT_TOKENS", "shop=s,admin=adm-token")
    assert traffic.admin_token() == "adm-token"
    monkeypatch.setenv("SHOP_ADMIN_TOKEN", "explicit")
    assert traffic.admin_token() == "explicit"
