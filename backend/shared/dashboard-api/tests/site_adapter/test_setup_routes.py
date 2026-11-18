"""사이트 변환기 설정 API: 고객사 API를 둘러보고 짝을 제안하고, 확인을 통과한 짝만 저장한다.

키는 변환기 폴더의 파일에만 두고 응답에 내보내지 않는다. 쓰기는 사람 토큰만 된다.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template.config import agent_url
from ax_dashboard_api.main import app
from ax_dashboard_api.site_adapter import upstream as up

SITE = "http://10.0.0.9:3000"
BASE = "/api/dashboard/site-adapter"
CONN = "/api/dashboard/company-connection"
KEY = "site-secret-key"
PATHS = {"orders_path": "/api/v1/bookings", "products_path": "/api/v1/tours"}
OPENAPI = {
    "paths": {
        "/api/v1/tours": {"get": {}},
        "/api/v1/tours/{code}": {"get": {}},
        "/api/v1/bookings": {"get": {}, "post": {}},
        "/api/v1/login": {"post": {}},
    }
}


@pytest.fixture
def seen() -> list[httpx.Request]:
    return []


@pytest.fixture
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    nubi: Any,
    seen: list[httpx.Request],
) -> TestClient:
    folder = tmp_path / "site_adapter"
    monkeypatch.setenv("AX_SITE_ADAPTER_DIR", str(folder))
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash:8020")
    monkeypatch.delenv(up.TOKEN_ENV, raising=False)
    monkeypatch.setattr(cc, "_cache", None)
    pages = {
        "/openapi.json": OPENAPI,
        "/api/v1/tours": nubi("nubi_tours.json"),
        "/api/v1/bookings": nubi("nubi_bookings_p1.json"),
    }

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"detail": "nope"})
        return httpx.Response(200, json=body)

    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(handle))
    return TestClient(app)


def _proposal(client: TestClient) -> dict[str, Any]:
    res = client.post(BASE + "/propose", json={"base_url": SITE, "token": KEY, **PATHS})
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_propose_reads_the_site_and_suggests_the_mapping(client, nubi, seen) -> None:
    data = _proposal(client)
    recorded = nubi("nubi_mapping.json")["resources"]
    orders = data["mapping"]["resources"]["orders"]
    assert orders["path"] == "/api/v1/bookings"
    assert orders["fields"] == recorded["orders"]["fields"]
    assert orders["values"]["status"] == {"deposit_paid": "paid", "cancelled": "cancelled"}
    assert data["mapping"]["resources"]["products"]["fields"] == recorded["products"]["fields"]
    assert data["paths"] == ["/api/v1/bookings", "/api/v1/tours"]
    assert data["samples"]["orders"]["count"] == 2
    assert "code" in data["samples"]["orders"]["keys"]
    assert all(r.headers.get("authorization") == f"Bearer {KEY}" for r in seen)
    assert KEY not in json.dumps(data)


def test_propose_without_paths_lists_what_the_site_offers(client) -> None:
    res = client.post(BASE + "/propose", json={"base_url": SITE})
    data = res.json()["data"]
    assert res.status_code == 200 and data["paths"] == ["/api/v1/bookings", "/api/v1/tours"]
    assert data["mapping"] is None


@pytest.mark.parametrize("url", ["http://169.254.169.254", "ftp://x", "http://u:p@10.0.0.9"])
def test_propose_refuses_bad_addresses(client, url, seen) -> None:
    res = client.post(BASE + "/propose", json={"base_url": url, **PATHS})
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_URL"
    assert seen == []


def test_propose_refuses_paths_that_leave_the_site(client) -> None:
    res = client.post(
        BASE + "/propose",
        json={"base_url": SITE, "orders_path": "//evil.example/x", "products_path": "/a"},
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_PATH"


def test_propose_says_when_the_key_is_refused(client, monkeypatch) -> None:
    monkeypatch.setattr(
        up, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(401, json={}))
    )
    res = client.post(BASE + "/propose", json={"base_url": SITE, **PATHS})
    assert res.status_code == 422 and res.json()["error"]["code"] == "UPSTREAM_AUTH"


def test_save_checks_then_stores_mapping_key_and_connection(client, seen) -> None:
    mapping = _proposal(client)["mapping"]
    res = client.put(BASE, json={"base_url": SITE + "/", "token": KEY, "mapping": mapping})
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["configured"] is True and data["token_saved"] is True
    assert data["counts"] == {"orders": 2, "products": 2}
    assert KEY not in json.dumps(res.json())
    assert data["connection"]["kind"] == "adapter"
    assert data["connection"]["adapter"] == {"base_url": SITE}
    assert agent_url("shop-api") == "http://dash:8020/site-adapter"
    seen.clear()
    up.clear_cache()
    # 저장한 키로 변환기 읽기가 고객사를 부른다
    assert client.get("/site-adapter/api/shop/products").status_code == 200
    assert seen and seen[0].headers["authorization"] == f"Bearer {KEY}"


def test_save_keeps_the_saved_key_when_none_is_given(client) -> None:
    mapping = _proposal(client)["mapping"]
    client.put(BASE, json={"base_url": SITE, "token": KEY, "mapping": mapping})
    res = client.put(BASE, json={"base_url": SITE, "mapping": mapping})
    assert res.status_code == 200 and res.json()["data"]["token_saved"] is True
    assert up.saved_token() == KEY


def test_save_refuses_an_unfilled_field(client) -> None:
    mapping = _proposal(client)["mapping"]
    mapping["resources"]["orders"]["fields"]["paid_amount"] = ""
    res = client.put(BASE, json={"base_url": SITE, "token": KEY, "mapping": mapping})
    assert res.status_code == 422 and res.json()["error"]["code"] == "MAPPING_INCOMPLETE"
    assert client.get(BASE).json()["data"]["configured"] is False


def test_save_refuses_an_unsorted_status(client) -> None:
    mapping = _proposal(client)["mapping"]
    mapping["resources"]["orders"]["values"]["status"]["cancelled"] = ""
    res = client.put(BASE, json={"base_url": SITE, "mapping": mapping})
    assert res.status_code == 422 and res.json()["error"]["code"] == "MAPPING_INCOMPLETE"


def test_save_refuses_a_mapping_the_site_does_not_answer(client) -> None:
    mapping = _proposal(client)["mapping"]
    mapping["resources"]["products"]["fields"]["name"] = "no_such_field"
    res = client.put(BASE, json={"base_url": SITE, "token": KEY, "mapping": mapping})
    assert res.status_code == 422 and res.json()["error"]["code"] == "MAPPING_BROKEN"
    assert "no_such_field" in res.json()["error"]["message"]
    state = client.get(BASE).json()["data"]
    assert state["configured"] is False and state["token_saved"] is False
    assert state["connection"] is None


def test_save_refuses_a_value_that_is_not_a_number(client) -> None:
    mapping = _proposal(client)["mapping"]
    mapping["resources"]["products"]["fields"]["price"] = "name"
    res = client.put(BASE, json={"base_url": SITE, "mapping": mapping})
    assert res.status_code == 422 and res.json()["error"]["code"] == "MAPPING_BROKEN"


def test_state_before_setup(client) -> None:
    data = client.get(BASE).json()["data"]
    assert data == {
        "configured": False,
        "mapping": None,
        "token_saved": False,
        "connection": None,
    }


@pytest.mark.parametrize(("method", "path"), [("post", "/propose"), ("put", "")])
def test_writes_need_a_human_token(client, method, path) -> None:
    client.headers.pop("authorization")
    res = getattr(client, method)(BASE + path, json={"base_url": SITE})
    assert res.status_code == 401


def test_mode_switch_keeps_the_adapter_connection(client, monkeypatch) -> None:
    mapping = _proposal(client)["mapping"]
    client.put(BASE, json={"base_url": SITE, "token": KEY, "mapping": mapping})
    off = client.post(CONN + "/mode", json={"mode": "internal"}).json()["data"]
    assert off["connection"]["kind"] == "adapter" and off["connection"]["enabled"] is False
    on = client.post(CONN + "/mode", json={"mode": "connected"})
    assert on.status_code == 200, on.text
    assert on.json()["data"]["connection"]["kind"] == "adapter"
    assert agent_url("shop-api") == "http://dash:8020/site-adapter"


def test_turning_the_adapter_back_on_rechecks_the_site(client, monkeypatch) -> None:
    mapping = _proposal(client)["mapping"]
    client.put(BASE, json={"base_url": SITE, "mapping": mapping})
    client.post(CONN + "/mode", json={"mode": "internal"})
    up.clear_cache()
    monkeypatch.setattr(
        up, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(500, json={}))
    )
    res = client.post(CONN + "/mode", json={"mode": "connected"})
    assert res.status_code == 422
    assert client.get(CONN).json()["data"]["mode"] == "internal"
