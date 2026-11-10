"""주문 관리 화면(/orders)의 페이지 목록과 상세의 환불 기록. 읽기 전용이다.

판매 서버는 고치지 않는다. 판매 서버에 원래 있는 기간 목록(`GET /api/shop/orders`)을 받아
최신순·탭·주문 번호 검색·페이지를 dashboard-api가 나눈다.
"""

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


def _order(order_id: str, created_at: str, **extra: Any) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "buyer_id": "buy_0007",
        "status": "paid",
        "total_amount": 20000,
        "refunded_amount": 0,
        "created_at": created_at,
        "shipped_at": None,
        "delivered_at": None,
        "items": [{"title": "책 A"}, {"title": "책 B"}],
    } | extra


ORDERS = [
    _order("ord_0001", "2026-11-10T01:00:00Z"),
    _order("ord_0002", "2026-11-10T03:00:00Z", refunded_amount=5000),  # 부분 환불
    _order("ord_0003", "2026-11-10T02:00:00Z", refunded_amount=20000),  # 전체 환불
    _order("ord_0004", "2026-11-10T04:00:00Z", status="cancelled"),
    _order("ord_0005", "2026-11-10T05:00:00Z", shipped_at="2026-11-10T06:00:00Z"),
]


def _shop(monkeypatch: pytest.MonkeyPatch, data: Any) -> list[tuple[str, dict | None]]:
    """판매 서버 기간 목록 자리에 끼운다. 받은 요청을 남긴다."""
    calls: list[tuple[str, dict | None]] = []

    async def fake(path: str, params: dict | None = None, **_: Any) -> Any:
        calls.append((path, params))
        return data, {}  # 한 페이지로 끝(next_cursor 없음)

    monkeypatch.setattr(live_views, "_shop_get_page", fake)
    return calls


def _nos(body: dict) -> list[str]:
    return [r["order_no"] for r in body["data"]]


def test_reads_only_the_existing_order_list(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _shop(monkeypatch, ORDERS)
    live_client.get(PAGE, params={"since": "2026-11-01T00:00:00Z", "buyer_id": "buy_0007"})
    # 판매 서버에 새 경로를 묻지 않고, 원래 있는 기간 목록만 같은 기간·구매자로 부른다
    assert calls == [
        (
            "/api/shop/orders",
            {"buyer_id": "buy_0007", "since": "2026-11-01T00:00:00Z", "limit": 1000},
        )
    ]


def test_follows_cursor_until_the_last_page(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """판매 서버가 한 페이지씩 주면 next_cursor로 끝까지 넘겨 모두 센다."""
    pages = {None: (ORDERS[:2], {"next_cursor": "ord_0002"}), "ord_0002": (ORDERS[2:], {})}
    calls: list[dict] = []

    async def fake(path: str, params: dict | None = None, **_: Any) -> Any:
        calls.append(dict(params or {}))
        return pages[(params or {}).get("cursor")]

    monkeypatch.setattr(live_views, "_shop_get_page", fake)
    body = live_client.get(PAGE).json()
    assert body["meta"]["total"] == 5
    assert [c.get("cursor") for c in calls] == [None, "ord_0002"]


def test_stops_paging_once_past_the_limit(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """상한을 넘으면 남은 페이지를 더 읽지 않고 기간을 줄여 달라고 한다."""
    monkeypatch.setattr(live_views, "MAX_ORDERS", 3)
    calls: list[dict] = []

    async def fake(path: str, params: dict | None = None, **_: Any) -> Any:
        calls.append(dict(params or {}))
        return ORDERS, {"next_cursor": "ord_0005"}  # 끝없이 다음 페이지가 있다

    monkeypatch.setattr(live_views, "_shop_get_page", fake)
    res = live_client.get(PAGE)
    assert res.status_code == 422
    assert len(calls) == 1


def test_newest_first_with_counts_per_tab(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shop(monkeypatch, ORDERS)
    body = live_client.get(PAGE, params={"limit": 2}).json()
    assert _nos(body) == ["ord_0005", "ord_0004"]
    assert body["meta"]["total"] == 5
    assert body["meta"]["counts"] == {
        "all": 5,
        "paid": 2,
        "partial_refund": 1,
        "full_refund": 1,
        "cancelled": 1,
    }
    newest = body["data"][0]
    assert (newest["title"], newest["buyer_id"], newest["shipped_at"]) == (
        "책 A 외 1건",
        "buy_0007",
        "2026-11-10T06:00:00Z",
    )


@pytest.mark.parametrize(
    ("tab", "expected"),
    [
        ("partial_refund", ["ord_0002"]),
        ("full_refund", ["ord_0003"]),
        ("cancelled", ["ord_0004"]),
        ("paid", ["ord_0005", "ord_0001"]),  # 환불된 주문은 결제 완료에 넣지 않는다
    ],
)
def test_each_tab_lists_only_its_orders(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch, tab: str, expected: list[str]
) -> None:
    _shop(monkeypatch, ORDERS)
    body = live_client.get(PAGE, params={"status": tab}).json()
    assert _nos(body) == expected
    assert body["meta"]["total"] == len(expected)
    assert body["meta"]["counts"]["all"] == 5  # 건수는 탭과 상관없이 같다


def test_paging_and_order_number_search(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shop(monkeypatch, ORDERS)
    pages = [
        _nos(live_client.get(PAGE, params={"limit": 2, "offset": o}).json()) for o in (0, 2, 4)
    ]
    assert pages == [["ord_0005", "ord_0004"], ["ord_0002", "ord_0003"], ["ord_0001"]]
    found = live_client.get(PAGE, params={"q": " 0003 "}).json()
    assert _nos(found) == ["ord_0003"]
    assert found["meta"]["counts"] == {"all": 1, "full_refund": 1}


def test_too_many_orders_asks_for_a_shorter_period(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_views, "MAX_ORDERS", 3)
    _shop(monkeypatch, ORDERS)
    res = live_client.get(PAGE)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "PERIOD_TOO_LARGE"


def test_bad_values_are_rejected_before_calling_shop(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _shop(monkeypatch, [])
    assert live_client.get(PAGE, params={"status": "shipped"}).status_code == 422
    assert live_client.get(PAGE, params={"limit": 101}).status_code == 422
    assert live_client.get(PAGE, params={"buyer_id": "a b"}).status_code == 422
    assert live_client.get(PAGE, params={"since": "어제"}).status_code == 400
    assert calls == []


def _shop_raises(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    class Down:
        def __init__(self, *_: Any, **__: Any) -> None: ...

        async def __aenter__(self) -> Down:
            return self

        async def __aexit__(self, *_: Any) -> None: ...

        async def get(self, *_: Any, **__: Any) -> httpx.Response:
            raise error

    monkeypatch.setattr(live_views.httpx, "AsyncClient", Down)


def test_shop_down_is_502_not_empty(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shop_raises(monkeypatch, httpx.ConnectError("refused"))
    res = live_client.get(PAGE)
    assert res.status_code == 502
    error = res.json()["error"]
    assert error["code"] == "UPSTREAM_UNAVAILABLE"
    assert error["message"].startswith("판매 서버에 연결하지 못했습니다")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ReadTimeout(""), "판매 서버가 10초 안에 응답하지 않았습니다"),  # 문구가 빈 예외
        (httpx.ConnectTimeout(""), "판매 서버가 10초 안에 응답하지 않았습니다"),
        (httpx.RemoteProtocolError(""), "판매 서버와 통신하지 못했습니다(RemoteProtocolError)"),
    ],
)
def test_unreachable_message_is_plain_words_and_never_empty(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
) -> None:
    _shop_raises(monkeypatch, error)
    message = live_client.get(PAGE).json()["error"]["message"]
    assert message == expected
    assert "shop-api" not in message and "()" not in message


def test_mock_page_is_empty_not_made_up(mock_client: TestClient) -> None:
    body = mock_client.get(PAGE).json()
    assert (
        body["data"] == [] and body["meta"]["total"] == 0 and body["meta"]["counts"] == {"all": 0}
    )


def test_live_detail_has_refund_rows_without_ids(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = _order(
        "ord_0012",
        "2026-11-10T01:00:00Z",
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
    _shop(monkeypatch, order)
    res = live_client.get("/api/dashboard/orders/ord_0012")
    assert res.json()["data"]["refunds"] == [
        {"amount": 3000, "created_at": "2026-11-10T02:00:00Z"},
        {"amount": 2000, "created_at": "2026-11-10T03:00:00Z"},
    ]
    assert "rf_1" not in res.text and "pay_1" not in res.text


def test_live_detail_without_refund_list_is_none(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shop(monkeypatch, _order("ord_0012", "2026-11-10T01:00:00Z"))
    assert live_client.get("/api/dashboard/orders/ord_0012").json()["data"]["refunds"] is None
