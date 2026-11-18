"""회사 연결이 회사(설정 폴더)를 같이 정한다.

연결 전에는 어느 회사도 아니고 판매 서버도 "연결 안 됨"이다.
"""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import tenant
from ax_dashboard_api import company_connection as routes
from ax_dashboard_api.main import app

BASE = "/api/dashboard/company-connection"
SHOP = "http://10.20.30.40:8080/shop-api"
GOOD = {
    "/shop-api/health": {"data": {"status": "ok"}},
    "/shop-api/api/shop/products": {"data": [{"product_id": "p1", "name": "책", "price": 1}]},
    "/shop-api/api/shop/users": {"data": [{"buyer_id": "b1"}]},
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv("AX_SHOP_API_URL", raising=False)
    monkeypatch.delenv(tenant.TENANT_ENV, raising=False)
    monkeypatch.delenv(tenant.TENANTS_DIR_ENV, raising=False)
    monkeypatch.setattr(cc, "_cache", None)

    def handle(request: httpx.Request) -> httpx.Response:
        body = GOOD.get(request.url.path)
        return httpx.Response(200, json=body) if body else httpx.Response(404, json={})

    monkeypatch.setattr(routes, "TRANSPORT", httpx.MockTransport(handle))
    return TestClient(app)


def test_before_connecting_there_is_no_company(client) -> None:
    data = client.get(BASE).json()["data"]
    assert data["connected"] is False and data["tenant"] is None
    assert {"id": "catbooks", "name": "캣북스"} in data["tenants"]
    assert data["shop_api_url"].endswith("/site-adapter")  # 캣북스 판매 서버로 빠지지 않음


def test_the_site_adapter_says_not_connected(client) -> None:
    res = client.get("/site-adapter/api/shop/products")
    assert res.status_code == 503
    error = res.json()["error"]
    assert error["code"] == "NOT_CONFIGURED" and "회사 연결" in error["message"]


def test_saving_with_a_company_picks_it(client) -> None:
    res = client.put(BASE, json={"url": SHOP, "tenant": "catbooks"})
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["connected"] is True and data["tenant"] == {"id": "catbooks", "name": "캣북스"}
    assert tenant.current() == "catbooks"
    assert tenant.setting("DATA_TENANT_TIMEZONE") == "Asia/Seoul"


def test_an_unknown_company_is_refused(client) -> None:
    res = client.put(BASE, json={"url": SHOP, "tenant": "nope"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "UNKNOWN_TENANT"
    assert client.get(BASE).json()["data"]["connection"] is None


def test_mode_switch_keeps_the_company(client) -> None:
    client.put(BASE, json={"url": SHOP, "tenant": "catbooks"})
    off = client.post(BASE + "/mode", json={"mode": "internal"}).json()["data"]
    assert off["connection"]["tenant"] == "catbooks"
    on = client.post(BASE + "/mode", json={"mode": "connected"}).json()["data"]
    assert on["tenant"]["id"] == "catbooks"


def test_resaving_without_a_company_keeps_the_one_chosen(client) -> None:
    client.put(BASE, json={"url": SHOP, "tenant": "catbooks"})
    data = client.put(BASE, json={"url": SHOP}).json()["data"]
    assert data["tenant"]["id"] == "catbooks"
