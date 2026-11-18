"""저장·해제를 POST로도 부른다.

운영 서버 nginx 게이트가 POST만 열 수 있어서다(PUT·DELETE는 그대로 둔다).
"""

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_dashboard_api import company_connection as routes
from ax_dashboard_api.main import app
from ax_dashboard_api.site_adapter import upstream as up

BASE = "/api/dashboard/company-connection"
ADAPTER = "/api/dashboard/site-adapter"
SHOP = "http://10.20.30.40:8080/shop-api"
SITE = "http://10.0.0.9:3000"
GOOD = {
    "/shop-api/health": {"data": {"status": "ok"}},
    "/shop-api/api/shop/products": {"data": [{"product_id": "p1", "name": "책", "price": 1}]},
    "/shop-api/api/shop/users": {"data": [{"buyer_id": "b1"}]},
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nubi: Any) -> TestClient:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.setenv("AX_SITE_ADAPTER_DIR", str(tmp_path / "site_adapter"))
    monkeypatch.delenv("AX_SHOP_API_URL", raising=False)
    monkeypatch.delenv(up.TOKEN_ENV, raising=False)
    monkeypatch.setattr(cc, "_cache", None)

    def shop(request: httpx.Request) -> httpx.Response:
        body = GOOD.get(request.url.path)
        return httpx.Response(200, json=body) if body else httpx.Response(404, json={})

    pages = {
        "/api/v1/tours": nubi("nubi_tours.json"),
        "/api/v1/bookings": nubi("nubi_bookings_p1.json"),
    }

    def site(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        return httpx.Response(200, json=body) if body else httpx.Response(404, json={})

    monkeypatch.setattr(routes, "TRANSPORT", httpx.MockTransport(shop))
    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(site))
    return TestClient(app)


def test_post_save_works_like_put(client) -> None:
    res = client.post(BASE + "/save", json={"url": SHOP})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["connection"]["shop_api_url"] == SHOP


def test_post_clear_works_like_delete(client) -> None:
    client.post(BASE + "/save", json={"url": SHOP})
    res = client.post(BASE + "/clear")
    assert res.status_code == 200 and res.json()["data"]["connection"] is None


def test_post_adapter_save_works_like_put(client, nubi) -> None:
    mapping = nubi("nubi_mapping.json")
    mapping["resources"]["orders"]["values"]["status"] = {
        "deposit_paid": "paid",
        "cancelled": "cancelled",
    }
    res = client.post(ADAPTER + "/save", json={"base_url": SITE, "mapping": mapping})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["connection"]["kind"] == "adapter"


@pytest.mark.parametrize("path", [BASE + "/save", BASE + "/clear", ADAPTER + "/save"])
def test_post_aliases_need_a_human_token(client, path) -> None:
    client.headers.pop("authorization")
    res = client.post(path, json={"url": SHOP})
    assert res.status_code == 401
