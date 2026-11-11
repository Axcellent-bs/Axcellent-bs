"""holdout 평가의 가짜 판매 서버(eval/fake_shop.py)가 판매 서버 계약과 규칙을 따르는지.

판매 서버는 `Catbooks-demo/catbooks-demo`로 분리됐다. holdout은 판매 서버 코드를 import하지 않고
가짜 판매 서버를 HTTP로 읽는다. 재무가 보내는 요청이 판매 서버 OpenAPI 고정본에 있는 경로·쿼리인지,
가짜가 판매 서버와 같은 금액 규칙을 쓰는지 잰다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from finance_agent import books as bk

EVAL = Path(__file__).resolve().parents[1] / "eval"
_spec = importlib.util.spec_from_file_location("finance_eval_fake_shop", EVAL / "fake_shop.py")
assert _spec and _spec.loader
fake_shop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_shop)

T0 = datetime(2026, 10, 5, 1, 0, tzinfo=UTC)


def _shop() -> tuple[object, list[datetime]]:
    clock = [T0]
    return fake_shop.FakeShop(now=lambda: clock[0]), clock


def _world():
    """주문 셋: 쿠폰 쓴 결제·배송·부분 환불, 실패 뒤 재결제, 결제 전."""
    shop, clock = _shop()
    coupon = shop.issue_coupon("buy_0001", 2, 3000, "cmp_eval")
    a = shop.place_order("buy_0001", [("prd_0001", 1), ("prd_0004", 2)], coupon["coupon_id"])
    shop.pay(a["order_id"])
    clock[0] = T0 + timedelta(days=2)
    shop.mark_delivered(a["order_id"], clock[0])
    shop.refund(shop.succeeded_payment(a["order_id"])["payment_id"], 5000)
    b = shop.place_order("buy_0002", [("prd_0007", 2)])
    shop.inject_fault("failure")
    assert shop.pay(b["order_id"])["status"] == "payment_failed"
    shop.release_fault()
    assert shop.pay(b["order_id"])["status"] == "paid"
    shop.place_order("buy_0003", [("prd_0002", 1)])
    return shop


def _read(shop, monkeypatch: pytest.MonkeyPatch, page: int = 2):
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("AX_AGENT_TOKENS", "finance=finance-holdout-eval")
    monkeypatch.setenv("FINANCE_TOKEN", "finance-holdout-eval")
    monkeypatch.setattr(bk, "ORDERS_PAGE", page)  # 주문 페이지 넘기기(next_cursor)도 지나게
    transport = fake_shop.FakeShopTransport(shop)

    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await bk.live_books(http_client=client, store=bk.OrderStore(), full=True)

    return asyncio.run(go()), transport


def test_snapshot_is_pinned_to_a_shop_repo_commit() -> None:
    spec = fake_shop.openapi()
    assert spec["info"]["title"] == "ax-shop-api"
    assert re.search(r"Catbooks-demo/catbooks-demo@[0-9a-f]{40} ", fake_shop.OPENAPI_SOURCE)
    for path in ("orders", "products", "sellers", "coupons"):
        assert "get" in spec["paths"][f"/api/shop/{path}"], path


def test_finance_requests_are_in_the_shop_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """재무가 판매 서버에 보내는 요청은 모두 고정본에 있는 GET 경로·쿼리 이름이다."""
    books, transport = _read(_world(), monkeypatch)
    spec = fake_shop.openapi()["paths"]
    shop_calls = [s for s in transport.seen if s[1].startswith("/api/shop/")]
    assert {s[1] for s in shop_calls} == {
        "/api/shop/orders",
        "/api/shop/products",
        "/api/shop/sellers",
        "/api/shop/coupons",
    }
    for method, path, query, _ in shop_calls:
        op = spec[path][method.lower()]
        declared = {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
        assert set(query) <= declared, (path, query)
    assert any("cursor" in q for _, p, q, _ in shop_calls if p == "/api/shop/orders")
    coupons = [s for s in shop_calls if s[1] == "/api/shop/coupons"]
    assert coupons and all(s[3] for s in coupons)  # 쿠폰은 재무 토큰으로 읽는다
    # 판매 서버 응답 모양 그대로 장부가 된다: 빠진 자료 없이 주문 3건·환불 1건·쿠폰 1건
    assert not books.unavailable
    assert len(books.orders) == 3 and len(books.coupons) == 1
    assert sum(r["amount"] for r in books.refunds) == 5000  # 판매자별로 나눠 적는다


def test_fake_shop_answers_like_the_shop_server() -> None:
    shop = _world()
    transport = fake_shop.FakeShopTransport(shop)

    async def get(path: str, **kw):
        async with httpx.AsyncClient(transport=transport, base_url="http://shop") as client:
            return await client.get(path, **kw)

    first = asyncio.run(get("/api/shop/orders", params={"limit": 2})).json()
    assert [o["order_id"] for o in first["data"]] == ["ord_0001", "ord_0002"]
    assert first["meta"]["next_cursor"] == "ord_0002"
    rest = asyncio.run(get("/api/shop/orders", params={"limit": 2, "cursor": "ord_0002"})).json()
    assert [o["order_id"] for o in rest["data"]] == ["ord_0003"] and not rest["meta"]["next_cursor"]
    for bad in ({"limit": 0}, {"limit": 1001}, {"limit": "x"}):
        assert asyncio.run(get("/api/shop/orders", params=bad)).status_code == 422
    assert asyncio.run(get("/api/shop/coupons")).status_code == 401
    assert asyncio.run(get("/api/shop/unknown")).status_code == 404


def test_fake_shop_uses_the_shop_money_rules() -> None:
    shop = _world()
    a = shop.order_view("ord_0001")
    # 18,000 + 7,500×2 = 33,000 − 쿠폰 3,000(1장) + 배송비 3,000 (5만 원 미만)
    assert (a["total_amount"], a["coupon_discount"], a["shipping_fee"]) == (33000, 3000, 3000)
    # 쿠폰 할인은 품목 금액 비율로 나눈다(내림, 남는 몫은 뒤 품목부터)
    assert [i["coupon_share"] for i in a["items"]] == [1636, 1364]
    assert sum(i["paid_amount"] for i in a["items"]) == 30000
    assert a["refunded_amount"] == 5000 and a["delivered_at"] == "2026-10-07T01:00:00Z"
    b = shop.order_view("ord_0002")
    assert b["shipping_fee"] == 0  # 62,000원: 무료 배송
    assert [p["failure_code"] for p in b["payments"]] == ["PG_UNAVAILABLE", None]
    assert shop.coupon_view(shop.coupons[0])["used_units"] == 1
    with pytest.raises(fake_shop.FakeShopError):
        shop.refund(a["payments"][0]["payment_id"], 30000)  # 남은 28,000원보다 많다
    with pytest.raises(fake_shop.FakeShopError):
        shop.mark_delivered("ord_0003", T0)  # 결제 전 주문
    assert fake_shop.split_by_amount(10, [10, 10, 10]) == [3, 3, 4]


def test_holdout_does_not_import_the_shop_server() -> None:
    """판매 서버 코드를 직접 부르지 않는다(레포 분리 뒤에도 돈다)."""
    for name in ("holdout.py", "fake_shop.py"):
        text = (EVAL / name).read_text("utf-8")
        assert not re.search(r"^\s*(from|import)\s+ax_shop_api", text, re.M), name
