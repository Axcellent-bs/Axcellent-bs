"""주문이 많이 쌓인 서버에서도 재무 서버가 계산에 묶이지 않게 한 장치들 (2026-11-06 서버, 18만 건).

- 실제 모드는 모아 둔 주문에 최근 주문만 받아 끼우고, 가끔 전체를 다시 읽는다 (`OrderStore`)
- 같은 장부로 하는 무거운 계산(결제 목록·PG 정산·대사)은 한 번만 한다 (`Books.memo`)
- 팔린 주문을 결제 시각 순으로 두고 기간으로 잘라 쓴다 (`Books.sold_between`)
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest

from finance_agent import books as bk
from finance_agent import finance_config as fc
from finance_agent import reconciliation


def _order(order_id: str, created, refunded: int = 0) -> dict:
    at = fc.iso(created)
    return {
        "order_id": order_id,
        "buyer_id": "buy_0001",
        "status": "paid",
        "total_amount": 10000,
        "refunded_amount": refunded,
        "shipping_fee": 0,
        "points_used": 0,
        "created_at": at,
        "delivered_at": None,
        "items": [
            {"product_id": "prd_0001", "seller_id": "sel_0001", "qty": 1, "unit_price": 10000}
        ],
        "payments": [
            {
                "payment_id": f"pay_{order_id}",
                "amount": 10000,
                "status": "succeeded",
                "created_at": at,
            }
        ],
    }


def test_order_store_reads_recent_orders_and_sometimes_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AX_SHOP_API_URL", "http://shop")
    monkeypatch.setenv("AX_SAFETY_URL", "http://safety")
    now = fc.now()
    old = _order("ord_old", now - timedelta(days=10))
    recent = _order("ord_recent", now - timedelta(hours=1))
    gone = _order("ord_gone", now - timedelta(hours=2))
    seen: list[str | None] = []
    shop_orders = {"full": [old, recent, gone], "recent": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/shop/orders":
            since = request.url.params.get("since")
            seen.append(since)
            data = shop_orders["full"] if since is None else shop_orders["recent"]
        elif path == "/api/safety/executions" or path.startswith("/api/shop/"):
            data = []
        else:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data, "meta": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = bk.OrderStore()

    async def run() -> None:
        first = await bk.live_books(client, store=store)
        assert seen == [None]  # 처음에는 전체
        assert {o["order_id"] for o in first.orders} == {"ord_old", "ord_recent", "ord_gone"}

        # 최근 주문만 다시 받는다: 바뀐 주문은 바뀌고, 사라진 주문은 빠지고, 오래된 주문은 남는다
        shop_orders["recent"] = [_order("ord_recent", now - timedelta(hours=1), refunded=3000)]
        shop_orders["recent"].append(_order("ord_new", now - timedelta(minutes=1)))
        second = await bk.live_books(client, store=store)
        assert seen[1] is not None
        since = fc.parse_dt(seen[1])
        assert (
            since is not None
            and abs(now - timedelta(days=bk.RECENT_DAYS) - since).total_seconds() < 60
        )
        by_id = {o["order_id"]: o for o in second.orders}
        assert set(by_id) == {"ord_old", "ord_recent", "ord_new"}
        assert by_id["ord_recent"]["refunded_amount"] == 3000
        assert sum(r["amount"] for r in second.refunds) == 3000

        # 사람이 결정하는 일은 전체를 다시 읽는다
        await bk.live_books(client, store=store, full=True)
        assert seen[2] is None
        # 전체 다시 읽기 간격이 지나면 전체를 읽는다
        monkeypatch.setattr(bk, "FULL_REFRESH_SECONDS", 0.0)
        await bk.live_books(client, store=store)
        assert seen[3] is None

    asyncio.run(run())


def test_live_books_without_a_store_read_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """클라이언트를 넘겨 한 번 쓰는 호출은 모아 둔 주문을 쓰지 않고 매번 전체를 읽는다."""
    monkeypatch.setenv("AX_SHOP_API_URL", "http://shop")
    monkeypatch.setenv("AX_SAFETY_URL", "http://safety")
    queries: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/shop/orders":
            queries.append(request.url.params.get("since"))
        return httpx.Response(200, json={"data": [], "meta": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(bk.live_books(client))
    asyncio.run(bk.live_books(client))
    assert queries == [None, None]


def test_live_books_follow_the_cursor_to_the_last_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """shop-api가 한 페이지씩 주면 next_cursor로 끝까지 넘겨 장부에 모두 넣는다."""
    monkeypatch.setenv("AX_SHOP_API_URL", "http://shop")
    monkeypatch.setenv("AX_SAFETY_URL", "http://safety")
    now = fc.now()
    rows = [_order(f"ord_{n:04d}", now - timedelta(hours=1 + n)) for n in range(5)]
    cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/shop/orders":
            return httpx.Response(200, json={"data": [], "meta": {}})
        params = request.url.params
        assert params["limit"] == str(bk.ORDERS_PAGE)
        after = params.get("cursor")
        cursors.append(after)
        rest = [r for r in rows if after is None or r["order_id"] > after][:2]
        more = bool(rest) and rest[-1]["order_id"] != rows[-1]["order_id"]
        meta = {"next_cursor": rest[-1]["order_id"] if more else None}
        return httpx.Response(200, json={"data": rest, "meta": meta})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    books = asyncio.run(bk.live_books(client))
    assert cursors == [None, "ord_0001", "ord_0003"]
    assert {o["order_id"] for o in books.orders} == {r["order_id"] for r in rows}
    assert "orders" not in books.unavailable


def test_live_books_drop_orders_if_a_later_page_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """중간 페이지가 실패하면 앞 페이지만으로 장부를 만들지 않고 못 받은 자료로 적는다."""
    monkeypatch.setenv("AX_SHOP_API_URL", "http://shop")
    monkeypatch.setenv("AX_SAFETY_URL", "http://safety")
    first = _order("ord_0001", fc.now() - timedelta(hours=1))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/shop/orders":
            return httpx.Response(200, json={"data": [], "meta": {}})
        if request.url.params.get("cursor"):
            return httpx.Response(503, json={"error": {"code": "BUSY"}})
        return httpx.Response(200, json={"data": [first], "meta": {"next_cursor": "ord_0001"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    books = asyncio.run(bk.live_books(client))
    assert "orders" in books.unavailable
    assert books.orders == []


def test_sold_between_picks_the_same_orders_as_a_full_scan() -> None:
    books = bk.seed_books()
    books.mock = False  # 실제 모드처럼 계산 결과를 기억하게 한다
    today = fc.today()
    for days in (1, 3, 7, 30):
        start, end = fc.day_start(today - timedelta(days=days)), fc.day_start(today)
        scanned = [
            o["order_id"]
            for o in books.orders
            if bk.order_sold(o) and bk.in_range(bk.paid_at(o), start, end)
        ]
        picked = [o["order_id"] for o in books.sold_between(start, end)]
        assert sorted(picked) == sorted(scanned)
        times = [bk.paid_at(o) for o in books.sold_between(start, end)]
        assert times == sorted(times)


def test_heavy_results_are_made_once_per_live_books() -> None:
    live = bk.seed_books()
    live.mock = False
    assert live.payments() is live.payments()
    assert bk.pg_settlements(live) is bk.pg_settlements(live)
    first = reconciliation.reconcile(live, None, {})
    assert reconciliation.reconcile(live, None, {}) is first
    # 메모를 붙여도 기억해 둔 결과는 그대로다
    target = first["items"][0]["payment_id"]
    noted = reconciliation.reconcile(live, None, {target: "확인함"})
    assert next(i for i in noted["items"] if i["payment_id"] == target)["note"] == "확인함"
    assert first["items"][0]["note"] is None
    # 주문이 늘면 다시 계산한다
    before = live.payments()
    live.orders = [*live.orders, live.orders[0] | {"order_id": "ord_extra"}]
    assert live.payments() is not before

    mock = bk.seed_books()  # 목 모드는 테스트가 고쳐 쓰므로 매번 새로 계산한다
    assert mock.payments() is not mock.payments()


def test_warm_prepares_shared_results() -> None:
    live = bk.seed_books()
    live.mock = False
    bk.warm(live)
    keys = {k[0] if not isinstance(k[0], tuple) else k[0][0] for k in live._memo}
    assert {"sold", "payments", "pg", "recon"} <= keys
