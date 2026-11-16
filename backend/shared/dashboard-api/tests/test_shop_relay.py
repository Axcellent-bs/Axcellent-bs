"""판매 서버 관리 읽기 중계(`/api/dashboard/shop/*`). 판매 서버는 MockTransport로 흉내 내요.

- 정해 둔 경로만 판매 서버로 가고, 서버 쪽 열람 토큰을 붙여요 (브라우저 토큰은 넘기지 않아요).
- 주소는 `agent_url("shop-api")`라 회사 연결 파일의 주소를 따라요.
- 판매 서버 오류는 화면 오류(`{error: {code, message}}`)로 바꿔요.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_dashboard_api import shop_relay
from ax_dashboard_api.main import app

VIEWER = "viewer-token-for-tests-0123456789"


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv(cc.CONNECTION_FILE_ENV, raising=False)
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv("AX_SHOP_API_URL", "http://127.0.0.1:8010")
    monkeypatch.setenv("SHOP_VIEWER_TOKEN", VIEWER)
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/shop/admin/orchestrator/departments":
            return httpx.Response(200, json={"data": [{"name": "infra"}], "meta": {"mock": False}})
        return httpx.Response(200, json={"data": {"path": request.url.path}, "meta": {}})

    monkeypatch.setattr(shop_relay, "TRANSPORT", httpx.MockTransport(handle))
    return calls


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _fail_with(monkeypatch: pytest.MonkeyPatch, handler) -> None:  # noqa: ANN001
    monkeypatch.setattr(shop_relay, "TRANSPORT", httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("path", "upstream"),
    [
        ("/orchestrator/incidents", "/api/shop/admin/orchestrator/incidents"),
        ("/orchestrator/departments", "/api/shop/admin/orchestrator/departments"),
        ("/marketing/briefs", "/api/shop/admin/marketing/briefs"),
        ("/marketing/outage-sns-drafts", "/api/shop/admin/marketing/outage-sns-drafts"),
        ("/banners", "/api/shop/banners"),
        ("/faults", "/api/shop/admin/faults"),
    ],
)
def test_each_route_reads_its_fixed_upstream_path_with_the_server_token(
    seen, client, path, upstream
) -> None:
    res = client.get(f"/api/dashboard/shop{path}")
    assert res.status_code == 200, res.text
    assert len(seen) == 1
    sent = seen[0]
    assert sent.method == "GET"
    assert str(sent.url).startswith(f"http://127.0.0.1:8010{upstream}")
    # 브라우저의 사람 토큰이 아니라 서버 쪽 열람 토큰을 보내요
    assert sent.headers["authorization"] == f"Bearer {VIEWER}"


def test_upstream_meta_is_kept(seen, client) -> None:
    body = client.get("/api/dashboard/shop/orchestrator/departments").json()
    assert body["data"] == [{"name": "infra"}]
    assert body["meta"]["mock"] is False


def test_list_queries_are_validated_and_forwarded(seen, client) -> None:
    client.get("/api/dashboard/shop/marketing/outage-sns-drafts?limit=5&include_merged=true")
    assert dict(seen[-1].url.params) == {"limit": "5", "include_merged": "true"}
    client.get("/api/dashboard/shop/marketing/briefs?limit=100")
    assert dict(seen[-1].url.params) == {"limit": "100"}
    assert client.get("/api/dashboard/shop/marketing/briefs?limit=101").status_code == 422
    assert client.get("/api/dashboard/shop/marketing/briefs?limit=0").status_code == 422
    assert len(seen) == 2


@pytest.mark.parametrize(
    "path",
    [
        "/api/dashboard/shop/admin/faults",
        "/api/dashboard/shop/api/shop/orders",
        "/api/dashboard/shop/orchestrator/incidents/ctx-1",
        "/api/dashboard/shop/../../api/shop/admin/inquiries",
    ],
)
def test_paths_outside_the_allowlist_are_not_relayed(seen, client, path) -> None:
    assert client.get(path).status_code == 404
    assert seen == []


def test_fault_writes_are_not_relayed(seen, client) -> None:
    # 사람 토큰이 있어도, 없어도 장애 넣기·빼기는 이 서버로 열리지 않아요 (운영자 명령만)
    anonymous = TestClient(app)
    anonymous.headers.pop("authorization", None)
    for who in (client, anonymous):
        assert who.post("/api/dashboard/shop/faults", json={"kind": "failure"}).status_code == 405
        assert who.delete("/api/dashboard/shop/faults").status_code == 405
        assert who.delete("/api/dashboard/shop/faults/pg").status_code == 404
    assert seen == []


def test_company_connection_address_is_used(
    seen, client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "company_connection.json"
    path.write_text(json.dumps({"shop_api_url": "http://10.0.0.5:9010"}), encoding="utf-8")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    assert client.get("/api/dashboard/shop/faults").status_code == 200
    assert str(seen[-1].url) == "http://10.0.0.5:9010/api/shop/admin/faults"


def test_missing_viewer_token_is_503_without_calling_the_shop(seen, client, monkeypatch) -> None:
    monkeypatch.delenv("SHOP_VIEWER_TOKEN")
    res = client.get("/api/dashboard/shop/faults")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "SHOP_VIEWER_TOKEN_NOT_SET"
    assert seen == []


def test_short_viewer_token_is_503_without_leaking_it(seen, client, monkeypatch) -> None:
    monkeypatch.setenv("SHOP_VIEWER_TOKEN", "short-secret-value")
    res = client.get("/api/dashboard/shop/faults")
    assert res.status_code == 503
    error = res.json()["error"]
    assert error["code"] == "SHOP_VIEWER_TOKEN_TOO_SHORT"
    assert "SHOP_VIEWER_TOKEN" in error["message"] and "24" in error["message"]
    assert "short-secret-value" not in res.text
    assert seen == []


def test_mock_mode_without_a_token_uses_the_mock_viewer(seen, client, monkeypatch) -> None:
    monkeypatch.delenv("SHOP_VIEWER_TOKEN")
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    assert client.get("/api/dashboard/shop/banners").status_code == 200
    assert seen[-1].headers["authorization"] == "Bearer mock:viewer"


@pytest.mark.parametrize(
    ("upstream_status", "status", "code"),
    [
        (401, 503, "UPSTREAM_AUTH"),
        (403, 503, "UPSTREAM_AUTH"),
        (404, 404, "UPSTREAM_NOT_FOUND"),
        (500, 502, "UPSTREAM_ERROR"),
        (400, 502, "UPSTREAM_ERROR"),
    ],
)
def test_upstream_errors_are_mapped(
    seen, client, monkeypatch, upstream_status, status, code
) -> None:
    _fail_with(
        monkeypatch,
        lambda _: httpx.Response(upstream_status, json={"error": {"code": "X", "message": "비밀"}}),
    )
    res = client.get("/api/dashboard/shop/orchestrator/incidents")
    assert res.status_code == status
    assert res.json()["error"]["code"] == code
    assert "비밀" not in res.json()["error"]["message"]


def test_upstream_relay_failures_keep_status_and_message(seen, client, monkeypatch) -> None:
    _fail_with(
        monkeypatch,
        lambda _: httpx.Response(
            504, json={"error": {"code": "ORCHESTRATOR_TIMEOUT", "message": "총괄이 늦어요"}}
        ),
    )
    res = client.get("/api/dashboard/shop/orchestrator/incidents")
    assert res.status_code == 504
    assert res.json()["error"] == {"code": "UPSTREAM_RELAY", "message": "총괄이 늦어요"}


def test_unreachable_and_timeout(seen, client, monkeypatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _fail_with(monkeypatch, refuse)
    res = client.get("/api/dashboard/shop/banners")
    assert res.status_code == 502 and res.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    _fail_with(monkeypatch, slow)
    res = client.get("/api/dashboard/shop/banners")
    assert res.status_code == 504 and res.json()["error"]["code"] == "UPSTREAM_TIMEOUT"


def test_non_envelope_body_is_502(seen, client, monkeypatch) -> None:
    _fail_with(monkeypatch, lambda _: httpx.Response(200, text="<html>"))
    res = client.get("/api/dashboard/shop/faults")
    assert res.status_code == 502 and res.json()["error"]["code"] == "UPSTREAM_ERROR"
