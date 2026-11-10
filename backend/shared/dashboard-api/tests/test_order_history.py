"""상담 콘솔 주문내역(#798): 목록 칸·기간·최신순과 상세 중계. 읽기 전용이다."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_dashboard_api import live_views
from ax_dashboard_api.main import app

BASE = "/api/dashboard"


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    return TestClient(app)


@pytest.fixture
def live_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    return TestClient(app)


def _order(order_id: str, created_at: str | None, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "order_id": order_id,
        "buyer_id": "buy_0001",
        "status": "paid",
        "total_amount": 33000,
        "refunded_amount": 0,
        "created_at": created_at,
        "items": [{"title": "책 A"}, {"title": "책 B"}],
    }
    return base | extra


def _fake_shop(monkeypatch: pytest.MonkeyPatch, data: Any) -> list[tuple[str, dict | None]]:
    calls: list[tuple[str, dict | None]] = []

    async def fake(path: str, params: dict | None = None, **_: Any) -> Any:
        calls.append((path, params))
        return data, {}  # 한 페이지로 끝(next_cursor 없음)

    monkeypatch.setattr(live_views, "_shop_get_page", fake)
    return calls


# ---------------------------------------------------------------- 목록
def test_mock_list_has_new_columns_and_keeps_old_ones(mock_client: TestClient) -> None:
    item = mock_client.get(f"{BASE}/orders", params={"user_id": 1}).json()["data"][0]
    assert {"id", "order_no", "status", "status_ko", "title"} <= set(item)  # 예전 칸 그대로
    assert (item["created_at"], item["total_amount"], item["item_count"]) == (
        "2026-11-01T10:00:00Z",
        36000,
        2,
    )


def test_mock_list_filters_by_period(mock_client: TestClient) -> None:
    url = f"{BASE}/orders"
    since = mock_client.get(url, params={"user_id": 1, "since": "2026-11-02T00:00:00Z"})
    assert since.json()["data"] == []
    until = mock_client.get(url, params={"user_id": 1, "until": "2026-11-01T10:00:00Z"})
    assert until.json()["data"] == []


def test_live_list_sorts_newest_first_and_maps_columns(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_shop(
        monkeypatch,
        [
            _order("ord_0001", "2026-10-01T00:00:00Z"),
            _order("ord_0003", "2026-11-03T00:00:00Z", refunded_amount=5000),
            _order("ord_0002", None),  # 시각을 모르면 맨 뒤
        ],
    )
    body = live_client.get(
        f"{BASE}/orders", params={"user_id": 1, "since": "2026-09-01T00:00:00Z"}
    ).json()
    assert [o["order_no"] for o in body["data"]] == ["ord_0003", "ord_0001", "ord_0002"]
    assert body["meta"]["total"] == 3
    newest = body["data"][0]
    assert (newest["total_amount"], newest["refunded_amount"], newest["item_count"]) == (
        33000,
        5000,
        2,
    )
    assert newest["title"] == "책 A 외 1건"
    assert newest["status_ko"] == "결제 완료"
    # 구매자·기간을 판매 서버에 그대로 넘긴다(until은 안 줬으니 없다)
    assert calls == [
        (
            "/api/shop/orders",
            {"buyer_id": "buy_0001", "since": "2026-09-01T00:00:00Z", "limit": 1000},
        )
    ]


def test_live_list_does_not_fill_unknown_values_with_zero(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare = {"order_id": "ord_0009", "status": "paid", "items": []}
    _fake_shop(monkeypatch, [bare])
    item = live_client.get(f"{BASE}/orders", params={"user_id": 1}).json()["data"][0]
    assert item["created_at"] is None
    assert item["total_amount"] is None
    assert item["refunded_amount"] is None
    assert item["title"] == "상품"


def test_live_list_rejects_bad_period_before_calling_shop(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_shop(monkeypatch, [])
    res = live_client.get(f"{BASE}/orders", params={"user_id": 1, "since": "어제"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_PERIOD"
    assert calls == []


# ---------------------------------------------------------------- 상세
def test_mock_detail_and_unknown_and_bad_id(mock_client: TestClient) -> None:
    data = mock_client.get(f"{BASE}/orders/ORD-2026-0101").json()["data"]
    assert data["total_amount"] == 36000
    assert data["items"][0]["title"] == "고양이의 하루"
    assert mock_client.get(f"{BASE}/orders/ORD-9999").status_code == 404
    assert mock_client.get(f"{BASE}/orders/a b").status_code == 422
    assert mock_client.get(f"{BASE}/orders/{'x' * 65}").status_code == 422


def test_live_detail_hides_ids_and_keeps_missing_as_none(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    shop_order = _order(
        "ord_0002",
        "2026-11-02T01:00:00Z",
        shipping_fee=3000,
        points_used=0,
        coupon_id="cpn_0001",
        coupon_discount=1000,
        promised_delivery_at="2026-11-05T01:00:00Z",
        shipped_at=None,
        delivered_at=None,
        items=[
            {
                "order_item_id": "oit_0001",
                "product_id": "prd_0001",
                "seller_id": "sel_0001",
                "title": "책 A",
                "qty": 2,
                "unit_price": 15000,
                "paid_amount": 29000,
            }
        ],
        payments=[
            {
                "payment_id": "pay_0001",
                "amount": 32000,
                "method_code": "card",
                "status": "succeeded",
                "failure_code": None,
                "created_at": "2026-11-02T01:01:00Z",
            }
        ],
    )
    calls = _fake_shop(monkeypatch, shop_order)
    res = live_client.get(f"{BASE}/orders/ord_0002")
    data = res.json()["data"]
    assert calls == [("/api/shop/orders/ord_0002", None)]
    assert (data["shipped_at"], data["delivered_at"]) == (None, None)
    assert data["coupon_discount"] == 1000
    assert data["items"] == [{"title": "책 A", "qty": 2, "unit_price": 15000, "paid_amount": 29000}]
    assert data["payments"][0]["amount"] == 32000
    for hidden in ("buyer_id", "buy_0001", "cpn_0001", "pay_0001", "prd_0001", "sel_0001"):
        assert hidden not in res.text


def test_live_detail_rejects_non_order_body(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_shop(monkeypatch, {"hello": "world"})
    res = live_client.get(f"{BASE}/orders/ord_0002")
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "UPSTREAM_ERROR"


# ---------------------------------------------------------------- 판매 서버 오류
class _Client:
    """`httpx.AsyncClient` 자리에 끼우는 가짜. 정해 둔 응답이나 예외를 돌려준다."""

    outcome: httpx.Response | Exception

    def __init__(self, *_: Any, **__: Any) -> None: ...

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_: Any) -> None: ...

    async def get(self, *_: Any, **__: Any) -> httpx.Response:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _shop_answers(monkeypatch: pytest.MonkeyPatch, outcome: httpx.Response | Exception) -> None:
    _Client.outcome = outcome
    monkeypatch.setattr(live_views.httpx, "AsyncClient", _Client)


def test_live_list_shows_reason_when_shop_is_down(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shop_answers(monkeypatch, httpx.ConnectError("refused"))
    res = live_client.get(f"{BASE}/orders", params={"user_id": 1})
    assert res.status_code == 502  # 빈 목록으로 감추지 않는다
    assert res.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_live_detail_maps_shop_404_to_not_found(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shop_answers(monkeypatch, httpx.Response(404, json={"error": {"code": "ORDER_NOT_FOUND"}}))
    res = live_client.get(f"{BASE}/orders/ord_9999")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_live_detail_shop_error_stays_502(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shop_answers(monkeypatch, httpx.Response(500, json={}))
    res = live_client.get(f"{BASE}/orders/ord_0002")
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "UPSTREAM_ERROR"
