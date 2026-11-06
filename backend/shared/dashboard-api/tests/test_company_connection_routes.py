"""회사 연결 API: 연결 확인은 그 주소를 실제로 부르고, 모두 통과해야 저장한다.

쓰기는 사람 토큰만 된다.
"""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_dashboard_api import company_connection as routes
from ax_dashboard_api.main import app

BASE = "/api/dashboard/company-connection"
SHOP = "http://192.168.0.251:8080/shop-api"

GOOD = {
    "/shop-api/health": {"data": {"status": "ok"}, "meta": {}},
    "/shop-api/api/shop/products": {
        "data": [{"product_id": "prd_0001", "name": "파이썬 입문서", "price": 18000}],
        "meta": {},
    },
    "/shop-api/api/shop/users": {"data": [{"buyer_id": "buy_0001"}, {"buyer_id": "buy_0002"}]},
}


def _transport(answers: dict[str, object]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        answer = answers.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    return httpx.MockTransport(handle)


@pytest.fixture
def conn_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "company_connection.json"
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.delenv("AX_SHOP_API_URL", raising=False)
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setattr(routes, "TRANSPORT", _transport(GOOD))
    return path


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_without_a_connection_the_default_address_is_reported(conn_file, client) -> None:
    body = client.get(BASE).json()["data"]
    assert body == {
        "shop_api_url": "http://shop-api:8010",
        "source": "default",
        "mode": "internal",
        "internal_url": "http://shop-api:8010",
        "connection": None,
        "editable": True,
    }


def test_the_env_address_is_reported_as_env(conn_file, client, monkeypatch) -> None:
    monkeypatch.setenv("AX_SHOP_API_URL", "http://127.0.0.1:8010")
    body = client.get(BASE).json()["data"]
    assert body["source"] == "env" and body["shop_api_url"] == "http://127.0.0.1:8010"


def test_check_reports_what_it_read_without_saving(conn_file, client) -> None:
    res = client.post(BASE + "/test", json={"url": SHOP + "/"})
    data = res.json()["data"]
    assert res.status_code == 200 and data["ok"] is True and data["url"] == SHOP
    assert [c["detail"] for c in data["checks"]] == [
        "응답 정상",
        "상품 1건 읽음",
        "구매자 2건 읽음",
    ]
    assert not conn_file.exists()


@pytest.mark.parametrize(
    ("answers", "key", "detail"),
    [
        ({**GOOD, "/shop-api/health": {"data": {"status": "down"}}}, "health", "상태가 ok가 아님"),
        (
            {**GOOD, "/shop-api/api/shop/products": {"data": {"x": 1}}},
            "products",
            "응답의 data가 목록이 아님",
        ),
        (
            {**GOOD, "/shop-api/api/shop/users": {"data": [{"id": 1}]}},
            "users",
            "buyer_id 칸이 없음",
        ),
        ({k: v for k, v in GOOD.items() if "users" not in k}, "users", "HTTP 404"),
        (
            {**GOOD, "/shop-api/health": httpx.Response(200, text="<html>")},
            "health",
            "JSON 응답이 아님",
        ),
        (
            {**GOOD, "/shop-api/health": httpx.ConnectError("refused")},
            "health",
            "연결할 수 없음 (ConnectError)",
        ),
        ({**GOOD, "/shop-api/health": httpx.ReadTimeout("slow")}, "health", "5초 안에 답이 없음"),
    ],
)
def test_each_failed_check_says_why(conn_file, client, monkeypatch, answers, key, detail) -> None:
    monkeypatch.setattr(routes, "TRANSPORT", _transport(answers))
    data = client.post(BASE + "/test", json={"url": SHOP}).json()["data"]
    assert data["ok"] is False
    failed = {c["key"]: c["detail"] for c in data["checks"] if not c["ok"]}
    assert failed == {key: detail}


def test_save_writes_the_file_and_agent_url_follows(conn_file, client) -> None:
    res = client.put(BASE, json={"url": SHOP})
    data = res.json()["data"]
    assert (
        res.status_code == 200 and data["source"] == "connection" and data["shop_api_url"] == SHOP
    )
    assert data["connection"]["updated_by"] == "tester"
    assert json.loads(conn_file.read_text(encoding="utf-8"))["shop_api_url"] == SHOP
    assert client.get(BASE).json()["data"]["shop_api_url"] == SHOP


def test_a_failing_check_does_not_save(conn_file, client, monkeypatch) -> None:
    monkeypatch.setattr(routes, "TRANSPORT", _transport({}))
    res = client.put(BASE, json={"url": SHOP})
    assert res.status_code == 422 and res.json()["error"]["code"] == "CONNECTION_CHECK_FAILED"
    assert not conn_file.exists()


def test_delete_goes_back_to_the_default(conn_file, client) -> None:
    client.put(BASE, json={"url": SHOP})
    data = client.delete(BASE).json()["data"]
    assert data["source"] == "default" and data["connection"] is None and not conn_file.exists()


@pytest.mark.parametrize(
    "url", ["ftp://x.example.com", "http://user:pw@x.example.com", "http://x.example.com/?a=1"]
)
def test_bad_addresses_are_refused_before_calling(conn_file, client, url) -> None:
    res = client.post(BASE + "/test", json={"url": url})
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_URL"


def test_without_the_file_env_saving_is_refused(conn_file, client, monkeypatch) -> None:
    monkeypatch.delenv(cc.CONNECTION_FILE_ENV)
    assert client.get(BASE).json()["data"]["editable"] is False
    res = client.put(BASE, json={"url": SHOP})
    assert res.status_code == 409 and res.json()["error"]["code"] == "CONNECTION_NOT_EDITABLE"


@pytest.mark.parametrize(("method", "path"), [("post", "/test"), ("put", ""), ("delete", "")])
def test_writes_need_a_human_token(conn_file, client, method, path) -> None:
    client.headers.pop("authorization")
    kwargs = {} if method == "delete" else {"json": {"url": SHOP}}
    res = getattr(client, method)(BASE + path, **kwargs)
    assert res.status_code == 401 and res.json()["error"]["code"] == "AUTH_REQUIRED"
    assert not conn_file.exists()


def test_mode_switches_between_internal_and_connected_without_losing_the_address(
    conn_file, client, monkeypatch
) -> None:
    monkeypatch.setenv("AX_SHOP_API_URL", "http://127.0.0.1:8010")
    client.put(BASE, json={"url": SHOP})
    internal = client.post(BASE + "/mode", json={"mode": "internal"}).json()["data"]
    assert internal["mode"] == "internal" and internal["source"] == "env"
    assert internal["shop_api_url"] == internal["internal_url"] == "http://127.0.0.1:8010"
    assert internal["connection"]["shop_api_url"] == SHOP
    assert internal["connection"]["enabled"] is False
    connected = client.post(BASE + "/mode", json={"mode": "connected"}).json()["data"]
    assert connected["mode"] == "connected" and connected["shop_api_url"] == SHOP


def test_switching_to_connected_rechecks_and_refuses_a_dead_server(
    conn_file, client, monkeypatch
) -> None:
    client.put(BASE, json={"url": SHOP})
    client.post(BASE + "/mode", json={"mode": "internal"})
    monkeypatch.setattr(routes, "TRANSPORT", _transport({}))
    res = client.post(BASE + "/mode", json={"mode": "connected"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "CONNECTION_CHECK_FAILED"
    assert client.get(BASE).json()["data"]["mode"] == "internal"


def test_mode_needs_a_saved_connection(conn_file, client) -> None:
    res = client.post(BASE + "/mode", json={"mode": "connected"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_CONNECTION"


def test_mode_needs_a_human_token(conn_file, client) -> None:
    client.headers.pop("authorization")
    res = client.post(BASE + "/mode", json={"mode": "internal"})
    assert res.status_code == 401
