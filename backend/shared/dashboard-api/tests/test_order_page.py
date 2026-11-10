"""주문 관리 화면(/orders)의 페이지 목록 중계와 상세의 환불 기록. 읽기 전용이다."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_dashboard_api import live_views
from ax_dashboard_api.main import app

PAGE = "/api/dashboard/orders/page"


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    return TestClient(app)


@pytest.fixture
def live_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    return TestClient(app)


class _Shop:
    """`httpx.AsyncClient` 자리에 끼우는 가짜 판매 서버. 받은 요청을 남긴다."""

    outcome: httpx.Response | Exception
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def __init__(self, *_: Any, **__: Any) -> None: ...

    async def __aenter__(self) -> _Shop:
        return self

    async def __aexit__(self, *_: Any) -> None: ...

    async def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        _Shop.calls.append((url, params))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _shop(monkeypatch: pytest.MonkeyPatch, outcome: httpx.Response | Exception) -> list:
    _Shop.outcome = outcome
    _Shop.calls = []
    monkeypatch.setattr(live_views.httpx, "AsyncClient", _Shop)
    return _Shop.calls


def _row(order_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "buyer_id": "buy_0007",
        "status": "paid",
        "total_amount": 20000,
        "refunded_amount": 5000,
        "created_at": "2026-11-10T01:00:00Z",
        "shipped_at": "2026-11-10T05:00:00Z",
        "delivered_at": None,
        "items": [{"title": "책 A"}, {"title": "책 B"}],
        "refunds": [{"refund_id": "rf_1", "payment_id": "pay_1", "amount": 5000}],
    } | extra


def test_live_page_maps_rows_and_passes_counts(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = {"all": 12, "paid": 7, "partial_refund": 3, "full_refund": 2, "unknown": 9}
    calls = _shop(
        monkeypatch,
        httpx.Response(
            200, json={"data": [_row("ord_0012")], "meta": {"total": 3, "counts": counts}}
        ),
    )
    res = live_client.get(
        PAGE,
        params={
            "status": "partial_refund",
            "since": "2026-11-01T00:00:00Z",
            "q": " 0012 ",
            "buyer_id": "buy_0007",
            "limit": 20,
            "offset": 40,
        },
    )
    body = res.json()
    assert res.status_code == 200
    [row] = body["data"]
    assert (row["order_no"], row["buyer_id"], row["title"]) == (
        "ord_0012",
        "buy_0007",
        "책 A 외 1건",
    )
    assert (row["refunded_amount"], row["shipped_at"], row["delivered_at"]) == (
        5000,
        "2026-11-10T05:00:00Z",
        None,
    )
    assert body["meta"]["total"] == 3
    assert body["meta"]["counts"] == {"all": 12, "paid": 7, "partial_refund": 3, "full_refund": 2}
    assert "rf_1" not in res.text and "pay_1" not in res.text  # 환불·결제 ID는 내보내지 않는다
    [(url, params)] = calls
    assert url.endswith("/api/shop/orders/page")
    assert params == {
        "limit": 20,
        "offset": 40,
        "status": "partial_refund",
        "since": "2026-11-01T00:00:00Z",
        "q": "0012",
        "buyer_id": "buy_0007",
    }


def test_shop_without_page_list_is_unsupported_404(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 예전 판매 서버는 /orders/page를 주문 번호 "page"로 읽어 404를 낸다
    _shop(monkeypatch, httpx.Response(404, json={"error": {"code": "ORDER_NOT_FOUND"}}))
    res = live_client.get(PAGE)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "ORDER_LIST_UNSUPPORTED"


def test_shop_down_is_502_not_empty(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shop(monkeypatch, httpx.ConnectError("refused"))
    res = live_client.get(PAGE)
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_bad_values_are_rejected_before_calling_shop(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _shop(monkeypatch, httpx.Response(200, json={"data": [], "meta": {}}))
    assert live_client.get(PAGE, params={"status": "shipped"}).status_code == 422
    assert live_client.get(PAGE, params={"limit": 101}).status_code == 422
    assert live_client.get(PAGE, params={"buyer_id": "a b"}).status_code == 422
    assert live_client.get(PAGE, params={"since": "어제"}).status_code == 400
    assert calls == []


def test_mock_page_is_empty_not_made_up(mock_client: TestClient) -> None:
    body = mock_client.get(PAGE).json()
    assert (
        body["data"] == [] and body["meta"]["total"] == 0 and body["meta"]["counts"] == {"all": 0}
    )


def test_live_detail_has_refund_rows_without_ids(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = _row(
        "ord_0012",
        refunds=[
            {
                "refund_id": "rf_1",
                "payment_id": "pay_1",
                "amount": 3000,
                "created_at": "2026-11-10T02:00:00Z",
            },
            {
                "refund_id": "rf_2",
                "payment_id": "pay_1",
                "amount": 2000,
                "created_at": "2026-11-10T03:00:00Z",
            },
        ],
    )
    _shop(monkeypatch, httpx.Response(200, json={"data": order}))
    res = live_client.get("/api/dashboard/orders/ord_0012")
    assert res.json()["data"]["refunds"] == [
        {"amount": 3000, "created_at": "2026-11-10T02:00:00Z"},
        {"amount": 2000, "created_at": "2026-11-10T03:00:00Z"},
    ]
    assert "rf_1" not in res.text and "pay_1" not in res.text


def test_live_detail_without_refund_list_is_none(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = _row("ord_0012")
    del order["refunds"]
    _shop(monkeypatch, httpx.Response(200, json={"data": order}))
    assert live_client.get("/api/dashboard/orders/ord_0012").json()["data"]["refunds"] is None
