"""판매자 정산(고객지원) 테스트: 정산 공식·보류 사유·환불 시점·지급 기한·지급 승인 요청 skill.

2026-11-12 재무에서 옮겨 왔다. 재무에 있던 정산 공식·보류 사유·지급 기한 테스트와 무작위 장부
정확도 점검(재무 `accuracy.case_settlement`)을 여기 같은 기준으로 옮겼다.
"""

from __future__ import annotations

import asyncio
import copy
import random
import time
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_agent_template import execute as template_execute
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.execute import ExecuteDecision, HoldReason
from support_agent import settlement as st
from support_agent import settlement_batches as batches_mod
from support_agent import settlement_books as sb
from support_agent import settlement_config as sc
from support_agent import settlement_handlers as sh

FIXED_NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)  # 금요일. 지난주 = 2026-W42 (10-12~10-18)
TODAY = FIXED_NOW.date()


@pytest.fixture(autouse=True)
def _fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "now", lambda: FIXED_NOW)
    batches_mod.use_store(batches_mod.BatchStore(None))
    sb.LOADER = None
    yield
    sb.LOADER = None


def at(day: str, hhmm: str = "10:00") -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(hours=hour, minutes=minute)


def order(
    order_id: str,
    when: datetime,
    items: list[tuple[str, str, int, int]],
    *,
    delivered: bool = True,
    payment_status: str = "succeeded",
) -> dict[str, Any]:
    """items: (seller_id, category_code, qty, unit_price)."""
    rows = [
        {
            "product_id": f"prd_{n}",
            "seller_id": seller,
            "category_code": cat,
            "qty": qty,
            "unit_price": price,
            "paid_amount": qty * price,
        }
        for n, (seller, cat, qty, price) in enumerate(items)
    ]
    total = sum(r["paid_amount"] for r in rows)
    return {
        "order_id": order_id,
        "buyer_id": "buy_0001",
        "status": "paid",
        "total_amount": total,
        "refunded_amount": 0,
        "created_at": when,
        "delivered_at": when + timedelta(days=1) if delivered else None,
        "items": rows,
        "payments": [
            {
                "payment_id": order_id.replace("ord_", "pay_"),
                "amount": total,
                "status": payment_status,
                "created_at": when,
            }
        ],
    }


def books(**kw: Any) -> sb.SettlementBooks:
    base: dict[str, Any] = {
        "sellers": [
            {"seller_id": "sel_a", "name": "가게A"},
            {"seller_id": "sel_b", "name": "가게B"},
        ],
    }
    return sb.SettlementBooks(**(base | kw))


def refund(rid: str, order_id: str, seller: str, amount: int, when: datetime) -> dict[str, Any]:
    return {
        "refund_id": rid,
        "order_id": order_id,
        "seller_id": seller,
        "amount": amount,
        "created_at": when,
    }


# ---------------------------------------------------------------- 정산 공식
def test_statement_formula_and_fields() -> None:
    w42 = at("2026-10-14")
    orders = [
        order("ord_1", w42, [("sel_a", "book", 2, 10000)]),
        order("ord_2", w42, [("sel_b", "book", 1, 10000), ("sel_b", "stationery", 1, 5000)]),
        order("ord_3", w42, [("sel_a", "book", 1, 9999)], payment_status="failed"),  # 안 팔림
    ]
    coupons = [
        {
            "coupon_id": "c1",
            "total_value": 2000,
            "funded_by": "seller",
            "seller_id": "sel_b",
            "created_at": w42,
        },
        {
            "coupon_id": "c2",
            "total_value": 3000,
            "funded_by": "company",
            "seller_id": "sel_b",
            "created_at": w42,
        },
    ]
    result = st.statements(books(orders=orders, coupons=coupons), None)
    assert (result["period"], result["pay_date"]) == ("2026-W42", "2026-10-24")
    assert (result["from"], result["to"]) == ("2026-10-12T00:00:00Z", "2026-10-19T00:00:00Z")
    assert result["applied_rules"][0]["rule_id"] == "SET-001"
    rows = {r["seller_id"]: r for r in result["statements"]}
    assert set(rows["sel_a"]) == {
        "seller_id",
        "name",
        "orders",
        "gross",
        "commission_rate",
        "commission",
        "seller_coupon",
        "refunds",
        "payout",
        "hold_reasons",
        "status",
        "review_id",
        "products",
        "pending_confirmation",
    }
    a, b = rows["sel_a"], rows["sel_b"]
    assert (a["gross"], a["commission"], a["payout"], a["commission_rate"]) == (
        20000,
        2000,
        18000,
        0.10,
    )
    # 도서 10% + 문구 8% 섞임 → 실효 수수료율, 판매자 부담 쿠폰만 뺀다
    assert (b["gross"], b["commission"], b["seller_coupon"]) == (15000, 1400, 2000)
    assert b["payout"] == 15000 - 1400 - 2000
    assert 0.08 < b["commission_rate"] < 0.10
    for r in rows.values():
        assert r["payout"] == r["gross"] - r["commission"] - r["seller_coupon"] - r["refunds"]
        assert r["status"] == "ready" and r["hold_reasons"] == []


def test_hold_reasons() -> None:
    when = at("2026-10-14")
    orders = [
        order("ord_1", when, [("sel_a", "book", 1, 20000)], delivered=False),
        order("ord_2", when, [("sel_b", "book", 1, 10000)]),
    ]
    rows = {
        r["seller_id"]: r
        for r in st.statements(
            books(orders=orders, refunds=[refund("rfd_1", "ord_2", "sel_b", 9500, when)]), None
        )["statements"]
    }
    # 확정 전 주문만 있으면 줄 돈이 없어 이번 정산에서 빠지고, 그 주문은 다음 정산으로 넘어간다
    assert [h["code"] for h in rows["sel_a"]["hold_reasons"]] == ["no_confirmed_orders"]
    assert rows["sel_a"]["payout"] == 0
    assert rows["sel_a"]["pending_confirmation"] == {"orders": 1, "gross": 20000}
    assert rows["sel_b"]["payout"] == 10000 - 1000 - 9500
    assert [h["code"] for h in rows["sel_b"]["hold_reasons"]] == ["negative_payout"]
    assert all(r["status"] == "held" for r in rows.values())


def test_refund_follows_purchase_confirmation() -> None:
    """환불은 환불 시각과 구매 확정 시각 중 늦은 주에 뺀다. 확정 안 된 주문의 환불은 빼지 않는다."""
    w42 = at("2026-10-14")
    settled = order("ord_1", at("2026-10-06"), [("sel_a", "book", 1, 30000)])
    early = order("ord_2", w42, [("sel_a", "book", 1, 20000)], delivered=False)
    early["delivered_at"] = at("2026-10-21")  # W42에 환불, W43에 확정
    cancelled = order("ord_3", w42, [("sel_b", "book", 1, 10000)], delivered=False)
    keep = order("ord_4", w42, [("sel_b", "book", 1, 50000)])
    refunds = [
        refund("rfd_1", "ord_1", "sel_a", 3000, w42),  # 이미 정산한 주문 → 환불한 주(W42)
        refund("rfd_2", "ord_2", "sel_a", 5000, w42),  # 확정 전 환불 → 확정되는 주(W43)
        refund("rfd_3", "ord_3", "sel_b", 10000, w42),  # 배송 전 취소 → 빼지 않음
    ]
    bk = books(orders=[settled, early, cancelled, keep], refunds=refunds)
    w42_rows = {r["seller_id"]: r for r in st.statements(bk, "2026-W42")["statements"]}
    w43_rows = {r["seller_id"]: r for r in st.statements(bk, "2026-W43")["statements"]}
    assert w42_rows["sel_a"]["refunds"] == 3000
    assert w42_rows["sel_b"]["refunds"] == 0
    assert w42_rows["sel_b"]["payout"] == 50000 - 5000
    assert (w43_rows["sel_a"]["gross"], w43_rows["sel_a"]["refunds"]) == (20000, 5000)
    assert w43_rows.get("sel_b", {"refunds": 0})["refunds"] == 0


def test_period_parameter_and_quiet_sellers() -> None:
    bk = books(orders=[order("ord_1", at("2026-10-20"), [("sel_a", "book", 1, 1000)])])
    assert st.statements(bk, None)["statements"] == []  # 지난주 거래 없음
    week = st.statements(bk, "2026-W43")
    assert [r["seller_id"] for r in week["statements"]] == ["sel_a"]
    with pytest.raises(ValueError):
        st.statements(bk, "2026-43")


def test_an_unconfirmed_order_waits_without_holding_the_seller() -> None:
    sold = at("2026-10-14")
    orders = [
        order("ord_1", sold, [("sel_a", "book", 1, 20000)]),
        order("ord_2", sold, [("sel_a", "book", 1, 10000)], delivered=False),
    ]
    row = st.statements(books(orders=orders), None)["statements"][0]
    assert (row["status"], row["hold_reasons"]) == ("ready", [])
    assert (row["orders"], row["gross"], row["payout"]) == (1, 20000, 18000)
    assert row["pending_confirmation"] == {"orders": 1, "gross": 10000}
    orders[1]["delivered_at"] = at("2026-10-21")
    later = st.statements(books(orders=orders), "2026-W43")["statements"][0]
    assert (later["orders"], later["gross"]) == (1, 10000)


def test_statement_lists_the_shop_products_the_seller_sold() -> None:
    w42 = at("2026-10-14")
    orders = [
        order("ord_1", w42, [("sel_a", "book", 2, 10000), ("sel_a", "book", 1, 3000)]),
        order("ord_2", w42, [("sel_b", "book", 1, 7000)]),
    ]
    bk = books(orders=orders, products=[{"product_id": "prd_0", "name": "쇼핑몰 이름"}])
    for r in st.statements(bk, None)["statements"]:
        assert sum(p["gross"] for p in r["products"]) == r["gross"]
        assert [p["gross"] for p in r["products"]] == sorted(
            (p["gross"] for p in r["products"]), reverse=True
        )
    assert bk.product_name("prd_0", {}) == "쇼핑몰 이름"
    assert bk.product_name("prd_none", {}) == "prd_none"
    assert bk.product_name("prd_0", {"title": "품목 이름"}) == "품목 이름"


def test_apply_batch_and_ready() -> None:
    w42 = at("2026-10-14")
    orders = [
        order("ord_1", w42, [("sel_a", "book", 1, 10000)]),
        order("ord_2", w42, [("sel_b", "book", 1, 10000)]),
    ]
    result = st.statements(books(orders=orders), None)
    assert st.apply_batch(dict(result), None)["batch"] == {
        "status": "none",
        "review_id": None,
        "execution_id": None,
        "total_payout": 0,
        "seller_ids": [],
    }
    batch = {
        "status": "requested",
        "review_id": "rev_1",
        "execution_id": "exe_1",
        "total_payout": 9000,
        "seller_ids": ["sel_a"],
        "attempt": 1,
    }
    shown = st.apply_batch(st.statements(books(orders=orders), None), batch)
    rows = {r["seller_id"]: r for r in shown["statements"]}
    assert (rows["sel_a"]["status"], rows["sel_a"]["review_id"]) == ("requested", "rev_1")
    assert rows["sel_b"]["status"] == "ready"
    assert "attempt" not in shown["batch"]
    assert [r["seller_id"] for r in st.ready(shown)] == ["sel_b"]
    assert date.fromisoformat(shown["pay_date"]).weekday() == 5
    closed = st.apply_batch(
        st.statements(books(orders=orders), None),
        batch | {"status": "rejected", "closed_reason": "review_missing"},
    )
    assert closed["batch"]["closed_reason"] == "review_missing"
    assert [r["seller_id"] for r in st.ready(closed)] == ["sel_a", "sel_b"]  # 다시 넣는다


# ---------------------------------------------------------------- 무작위 장부 정확도
def _half_up(amount: int, rate: float) -> int:
    return int((Decimal(amount) * Decimal(str(rate))).quantize(Decimal("1"), ROUND_HALF_UP))


def _random_case(seed: int) -> dict[str, Any]:
    """무작위 장부 하나 (정산 공식 대조·예전 계산과 같은지 대조에 같이 쓴다)."""
    rng = random.Random(seed)
    monday = TODAY - timedelta(days=TODAY.weekday() + 7)
    start = datetime.combine(monday, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=7)
    rates = {"book": 0.10, "stationery": 0.08}
    sellers = [f"sel_{k}" for k in range(rng.randint(1, 4))]
    orders = []
    for n in range(rng.randint(1, 8)):
        when = start + timedelta(days=rng.randint(-2, 8), hours=rng.randint(0, 23))
        items = [
            (
                rng.choice(sellers),
                rng.choice(list(rates)),
                rng.randint(1, 3),
                rng.randint(1, 50) * 1000,
            )
            for _ in range(rng.randint(1, 3))
        ]
        o = order(f"ord_{n}", when, items, delivered=rng.random() < 0.85)
        if o["delivered_at"] is not None:
            o["delivered_at"] = when + timedelta(days=rng.randint(0, 3))
        orders.append(o)
    refunds = []
    for o in orders:
        if rng.random() < 0.35:
            item = rng.choice(o["items"])
            big = rng.random() < 0.15
            refunds.append(
                refund(
                    f"rfd_{o['order_id']}",
                    o["order_id"],
                    item["seller_id"],
                    o["total_amount"] * 2 if big else rng.randint(1, o["total_amount"]),
                    start + timedelta(days=rng.randint(-1, 7)),
                )
            )
    coupons = [
        {
            "coupon_id": f"cpn_{k}",
            "funded_by": rng.choice(["seller", "company"]),
            "seller_id": rng.choice(sellers),
            "total_value": rng.randint(1, 20) * 1000,
            "created_at": start + timedelta(days=rng.randint(-1, 7)),
        }
        for k in range(rng.randint(0, 3))
    ]
    bk = books(
        orders=orders,
        refunds=refunds,
        coupons=coupons,
        sellers=[{"seller_id": s, "name": s} for s in sellers],
    )
    return {
        "books": bk,
        "orders": orders,
        "refunds": refunds,
        "coupons": coupons,
        "sellers": sellers,
        "rates": rates,
        "start": start,
        "end": end,
    }


@pytest.mark.parametrize("seed", range(150))
def test_random_books_match_an_independent_calculation(seed: int) -> None:
    """정산 공식을 따로 계산한 값과 맞춘다 (재무 `accuracy.case_settlement`에서 옮김)."""
    case = _random_case(seed)
    bk, orders, refunds, coupons = case["books"], case["orders"], case["refunds"], case["coupons"]
    sellers, rates, start, end = case["sellers"], case["rates"], case["start"], case["end"]
    got = {
        r["seller_id"]: (
            r["gross"],
            r["commission"],
            r["seller_coupon"],
            r["refunds"],
            r["payout"],
            r["status"],
        )
        for r in st.statements(bk, None, TODAY)["statements"]
    }

    def inside(t: datetime | None) -> bool:
        return t is not None and start <= t < end

    delivered_of = {o["order_id"]: o["delivered_at"] for o in orders}
    sold = [o for o in orders if inside(o["delivered_at"])]
    waiting = [o for o in orders if o["created_at"] < end and o["delivered_at"] is None]
    want = {}
    for s in sellers:
        gross = com = 0
        ids = set()
        for o in sold:
            for i in o["items"]:
                if i["seller_id"] == s:
                    ids.add(o["order_id"])
                    g = i["qty"] * i["unit_price"]
                    gross += g
                    com += _half_up(g, rates[i["category_code"]])
        cp = sum(
            c["total_value"]
            for c in coupons
            if c["funded_by"] == "seller" and c["seller_id"] == s and inside(c["created_at"])
        )
        rf = sum(
            r["amount"]
            for r in refunds
            if r["seller_id"] == s
            and delivered_of[r["order_id"]] is not None
            and inside(max(r["created_at"], delivered_of[r["order_id"]]))
        )
        pending = any(i["seller_id"] == s for o in waiting for i in o["items"])
        if not ids and not cp and not rf and not pending:
            continue
        payout = gross - com - cp - rf
        # 줄 돈이 0원 이하면 보류한다. 확정 매출 없이 환불·쿠폰만 있는 판매자도 (#1013 리뷰)
        held = payout <= 0
        want[s] = (gross, com, cp, rf, payout, "held" if held else "ready")
    assert got == want


# ---------------------------------------------------------------- 지급 기한
def _rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["seller_id"]: r for r in report["sellers"]}


def test_old_unpaid_confirmed_sale_is_overdue() -> None:
    bk = books(orders=[order("ord_1", at("2026-09-20"), [("sel_a", "book", 1, 10000)])])
    report = st.deadline_report(bk, {}, TODAY)
    row = _rows(report)["sel_a"]
    assert (row["deadline"], row["status"], row["days_left"]) == ("2026-10-11", "overdue", -12)
    assert row["amount"] == 9000
    assert st.needs_attention(report) == [row]


def test_recent_sale_paid_on_time_is_ok_and_undelivered_waits() -> None:
    orders = [
        order("ord_1", at("2026-10-14"), [("sel_a", "book", 1, 10000)]),
        order("ord_2", at("2026-10-21"), [("sel_b", "book", 1, 10000)], delivered=False),
    ]
    report = st.deadline_report(books(orders=orders), {}, TODAY)
    row = _rows(report)["sel_a"]
    assert (row["expected_pay_date"], row["deadline"], row["status"]) == (
        "2026-10-24",
        "2026-11-04",
        "ok",
    )
    assert "sel_b" not in _rows(report)
    assert report["waiting_orders"] == 1


def test_approved_batch_counts_as_paid() -> None:
    bk = books(orders=[order("ord_1", at("2026-09-16"), [("sel_a", "book", 1, 10000)])])
    paid = {"2026-W38": {"status": "approved", "seller_ids": ["sel_a"]}}
    assert st.deadline_report(bk, paid, TODAY)["sellers"] == []
    requested = {"2026-W38": {"status": "requested", "seller_ids": ["sel_a"]}}
    assert st.deadline_report(bk, requested, TODAY)["sellers"]  # 승인 전은 미지급


# ---------------------------------------------------------------- 장부 읽기 (shop-api)
@pytest.mark.asyncio
async def test_live_books_pages_orders_and_splits_refunds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_SHOP_API_URL", "http://shop")
    monkeypatch.setattr(sb.shop_client, "_headers", lambda: {"Authorization": "Bearer t"})
    raw_order = {
        "order_id": "ord_1",
        "created_at": "2026-10-14T01:00:00Z",
        "delivered_at": "2026-10-15T01:00:00Z",
        "total_amount": 30000,
        "refunded_amount": 3000,
        "items": [
            {
                "product_id": "prd_1",
                "seller_id": "sel_a",
                "qty": 1,
                "unit_price": 20000,
                "paid_amount": 20000,
            },
            {
                "product_id": "prd_2",
                "seller_id": "sel_b",
                "qty": 1,
                "unit_price": 10000,
                "paid_amount": 10000,
            },
        ],
        "payments": [
            {
                "payment_id": "pay_1",
                "status": "succeeded",
                "amount": 30000,
                "created_at": "2026-10-14T01:00:00Z",
            }
        ],
        "refunds": [
            {
                "refund_id": "rf_1",
                "payment_id": "pay_1",
                "amount": 3000,
                "created_at": "2026-10-16T01:00:00Z",
            }
        ],
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.path}?{request.url.query.decode()}")
        if request.url.path == "/api/shop/orders":
            cursor = request.url.params.get("cursor")
            if cursor is None:
                return httpx.Response(200, json={"data": [], "meta": {"next_cursor": "c2"}})
            return httpx.Response(200, json={"data": [raw_order], "meta": {}})
        if request.url.path == "/api/shop/products":
            return httpx.Response(
                200, json={"data": [{"product_id": "prd_1", "category_code": "book"}]}
            )
        if request.url.path == "/api/shop/sellers":
            return httpx.Response(200, json={"data": [{"seller_id": "sel_a", "name": "가게A"}]})
        if request.url.path == "/api/shop/coupons":
            assert request.headers["Authorization"] == "Bearer t"
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        bk = await sb.live_books(client)
    assert bk.unavailable == [] and bk.mock is False
    assert [o["order_id"] for o in bk.orders] == ["ord_1"]
    assert bk.orders[0]["items"][0]["category_code"] == "book"
    assert sorted((r["seller_id"], r["amount"]) for r in bk.refunds) == [
        ("sel_a", 2000),
        ("sel_b", 1000),
    ]
    assert any("cursor=c2" in s for s in seen)


@pytest.mark.asyncio
async def test_live_books_marks_orders_unavailable_when_a_page_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AX_SHOP_API_URL", "http://shop")
    monkeypatch.setattr(sb.shop_client, "_headers", lambda: {"Authorization": "Bearer t"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/shop/orders":
            return httpx.Response(500)
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        bk = await sb.live_books(client)
    assert "orders" in bk.unavailable and bk.unavailable_core()


# ---------------------------------------------------------------- skill
def _request(skill: str, payload: dict[str, Any]) -> AgentRequest:
    return AgentRequest(
        skill=skill,
        input=payload,
        meta=RequestMeta(
            context_id="ctx_test",
            token="mock:dashboard-api",
            deadline=FIXED_NOW + timedelta(minutes=5),
            idempotency_key="k",
        ),
    )


def _use_books(bk: sb.SettlementBooks) -> list[bool]:
    calls: list[bool] = []

    async def load(fresh: bool) -> sb.SettlementBooks:
        calls.append(fresh)
        return bk

    sb.LOADER = load
    return calls


def _week_books() -> sb.SettlementBooks:
    w42 = at("2026-10-14")
    return books(
        orders=[
            order("ord_1", w42, [("sel_a", "book", 1, 10000)]),
            order("ord_2", w42, [("sel_b", "book", 1, 20000)]),
        ]
    )


def _fake_execute(monkeypatch: pytest.MonkeyPatch, *decisions: ExecuteDecision) -> list[dict]:
    calls: list[dict] = []
    queue = list(decisions)

    async def fake(action: str, params: dict, meta: RequestMeta, evidence: Any) -> ExecuteDecision:
        calls.append({"action": action, "params": params, "key": meta.idempotency_key})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(sh, "execute", fake)
    return calls


HELD = ExecuteDecision(
    execution_id="exe_77",
    grade="C",
    status="held",
    review_id="rev_77",
    hold_reason=HoldReason.OVER_THRESHOLD,
)


def _safety_review(monkeypatch: pytest.MonkeyPatch, respond) -> list[str]:
    monkeypatch.setenv("AX_SAFETY_URL", "http://safety")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return respond(request)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        batches_mod.httpx,
        "AsyncClient",
        lambda *a, **k: real(transport=httpx.MockTransport(handler)),
    )
    return seen


@pytest.mark.asyncio
async def test_seller_settlement_skill_returns_statements_and_meta() -> None:
    _use_books(_week_books())
    result = await sh.seller_settlement(_request("seller_settlement", {}))
    assert result.status == TaskStatus.COMPLETED
    out = result.output
    assert out["period"] == "2026-W42" and out["batch"]["status"] == "none"
    assert {r["seller_id"] for r in out["statements"]} == {"sel_a", "sel_b"}
    assert out["meta"]["unavailable"] == []
    bad = await sh.seller_settlement(_request("seller_settlement", {"period": "2026-42"}))
    assert (bad.status, bad.outcome) == (TaskStatus.FAILED, "invalid_input")


@pytest.mark.asyncio
async def test_skills_refuse_to_answer_without_orders() -> None:
    _use_books(books(unavailable=["orders"]))
    result = await sh.seller_settlement(_request("seller_settlement", {}))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "shop_unavailable")
    result = await sh.seller_settlement_deadlines(_request("seller_settlement_deadlines", {}))
    assert result.outcome == "shop_unavailable"


@pytest.mark.asyncio
async def test_request_goes_through_execute_and_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    loads = _use_books(_week_books())
    calls = _fake_execute(monkeypatch, HELD)
    _safety_review(
        monkeypatch,
        lambda r: httpx.Response(200, json={"data": {"review_id": "rev_77", "status": "pending"}}),
    )
    payload = {"period": "2026-W42", "requested_by": "taeyoon"}
    result = await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    assert result.status == TaskStatus.COMPLETED, result.output
    assert result.output["requested"] is True and loads == [True]  # 돈이 걸린 일은 새로 읽는다
    batch = result.output["settlement"]["batch"]
    assert (batch["status"], batch["review_id"], batch["total_payout"]) == (
        "requested",
        "rev_77",
        9000 + 18000,
    )
    [call] = calls
    assert call["action"] == "confirm_settlement"
    assert call["key"] == "ctx_req_settlement_2026-W42:confirm_settlement:2026-W42:1"
    assert call["params"]["requested_by"] == "taeyoon"
    assert call["params"]["seller_ids"] == ["sel_a", "sel_b"]
    stored = batches_mod.STORE.get("2026-W42")
    assert stored["payouts"] == {"sel_a": 9000, "sel_b": 18000}

    again = await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    assert again.output["requested"] is False and len(calls) == 1  # 같은 주는 한 번만


def _answer(answer: dict[str, httpx.Response], request: httpx.Request) -> httpx.Response:
    """보류 건 조회는 `r`, 실행 기록 조회는 `executions`(없으면 빈 목록)로 답한다."""
    if request.url.path == "/api/safety/executions":
        return answer.get("executions") or httpx.Response(200, json={"data": []})
    return answer["r"]


@pytest.mark.asyncio
async def test_lost_review_reopens_and_can_be_requested_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """안전 장치가 재시작돼 보류 건을 잃으면(404) 끝난 요청으로 보고 다시 요청할 수 있다."""
    _use_books(_week_books())
    calls = _fake_execute(monkeypatch, HELD)
    payload = {"period": "2026-W42", "requested_by": "taeyoon"}
    answer = {"r": httpx.Response(200, json={"data": {"status": "pending"}})}
    _safety_review(monkeypatch, lambda r: _answer(answer, r))
    await sh.request_seller_settlement(_request("request_seller_settlement", payload))

    answer["r"] = httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
    answer["executions"] = httpx.Response(200, json={"data": []})
    view = (
        await sh.seller_settlement(_request("seller_settlement", {"period": "2026-W42"}))
    ).output
    assert (view["batch"]["status"], view["batch"]["closed_reason"]) == (
        "rejected",
        "review_missing",
    )

    answer["r"] = httpx.Response(200, json={"data": {"status": "pending"}})
    again = await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    assert again.output["requested"] is True
    assert [c["key"][-2:] for c in calls] == [":1", ":2"]  # 새 시도 번호로 다시 보낸다


@pytest.mark.asyncio
async def test_unreachable_safety_keeps_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_books(_week_books())
    _fake_execute(monkeypatch, HELD)
    payload = {"period": "2026-W42", "requested_by": "taeyoon"}
    answer = {"r": httpx.Response(200, json={"data": {"status": "pending"}})}
    _safety_review(monkeypatch, lambda r: answer["r"])
    await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    answer["r"] = httpx.Response(503)
    view = (
        await sh.seller_settlement(_request("seller_settlement", {"period": "2026-W42"}))
    ).output
    assert view["batch"]["status"] == "requested" and "closed_reason" not in view["batch"]


@pytest.mark.asyncio
async def test_approved_review_is_reflected_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_books(_week_books())
    _fake_execute(monkeypatch, HELD)
    answer = {"r": httpx.Response(200, json={"data": {"status": "pending"}})}
    seen = _safety_review(monkeypatch, lambda r: answer["r"])
    payload = {"period": "2026-W42", "requested_by": "taeyoon"}
    await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    answer["r"] = httpx.Response(200, json={"data": {"status": "approved"}})
    out = (await sh.seller_settlement_batches(_request("seller_settlement_batches", {}))).output
    batch = out["batches"]["2026-W42"]
    assert batch["status"] == "approved" and batch["approved_at"] == "2026-10-23T12:00:00Z"
    assert "/api/safety/reviews/rev_77" in seen


@pytest.mark.asyncio
async def test_safety_down_and_nothing_to_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_books(_week_books())
    down = ExecuteDecision(execution_id="exe_unavailable", grade="C", status="rejected")
    _fake_execute(monkeypatch, down)
    payload = {"period": "2026-W42", "requested_by": "taeyoon"}
    result = await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "safety_unavailable")
    assert batches_mod.STORE.batches == {}  # 기록 없음

    _use_books(books(orders=[]))
    result = await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "nothing_to_settle")
    missing = await sh.request_seller_settlement(
        _request("request_seller_settlement", {"period": "2026-W42"})
    )
    assert missing.outcome == "invalid_input"  # 누른 사람이 없으면 받지 않는다


@pytest.mark.asyncio
async def test_weeks_and_deadlines_skills() -> None:
    _use_books(_week_books())
    weeks = await sh.seller_settlement_weeks(
        _request("seller_settlement_weeks", {"periods": ["2026-W42", "2026-W43"]})
    )
    assert set(weeks.output["weeks"]) == {"2026-W42", "2026-W43"}
    assert "meta" not in weeks.output["weeks"]["2026-W42"] and weeks.output["meta"]
    deadlines = await sh.seller_settlement_deadlines(_request("seller_settlement_deadlines", {}))
    assert deadlines.status == TaskStatus.COMPLETED
    assert {r["seller_id"] for r in deadlines.output["sellers"]} == {"sel_a", "sel_b"}


def test_batch_store_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "batches.json"
    store = batches_mod.BatchStore(path)
    store.put("2026-W44", {"status": "requested", "review_id": "rev_1", "seller_ids": ["s"]})
    again = batches_mod.BatchStore(path)
    assert again.get("2026-W44")["review_id"] == "rev_1"


def test_batches_are_imported_once_from_the_finance_state(tmp_path: Path) -> None:
    """옮기기: 재무가 남긴 묶음 기록을 처음 한 번 가져와, 승인된 주를 다시 요청하지 않는다."""
    finance_state = tmp_path / "finance_state.json"
    finance_state.write_text(
        '{"version": 3, "ledger": {}, "records": {"batches": {"2026-W45": '
        '{"status": "approved", "seller_ids": ["sel_a"], "payouts": {"sel_a": 9000}}}}}',
        encoding="utf-8",
    )
    path = tmp_path / "support_settlement.json"
    store = batches_mod.BatchStore(path, finance_state)
    assert store.get("2026-W45")["status"] == "approved" and path.exists()
    finance_state.write_text('{"records": {"batches": {}}}', encoding="utf-8")
    again = batches_mod.BatchStore(path, finance_state)  # 이미 옮겼으면 다시 가져오지 않는다
    assert again.get("2026-W45")["payouts"] == {"sel_a": 9000}


def test_execute_module_is_the_template_one() -> None:
    """지급 요청은 agent-template execute()(안전 관문)로만 한다."""
    assert sh.execute is template_execute.execute


# ---------------------------------------------------------------- 계산을 가볍게 (2026-11-12)
# 판매자마다 주문 전체를 다시 훑던 예전 계산(#1010)을 그대로 두고, 바꾼 계산과 같은 답인지 본다.
# 재헌 측정(주문 298,365건·판매자 200곳): statements() 8.8초, deadline_report() 86.6초 → 504


def _statements_before(
    books: sb.SettlementBooks,
    period: str | None,
    today: date | None = None,
) -> dict[str, Any]:
    cfg = sc.load_policy()
    today = today or sc.today()
    name, monday, sunday = sc.week_range(period, today)
    start, end = sc.day_start(monday), sc.day_start(sunday + timedelta(days=1))

    sellers = [s["seller_id"] for s in books.sellers if s.get("seller_id")]
    for o in books.orders:  # 판매자 목록에 없는 판매자의 품목도 빠뜨리지 않는다
        for i in o["items"]:
            if i.get("seller_id") and i["seller_id"] not in sellers:
                sellers.append(i["seller_id"])

    sold = books.confirmed_between(start, end)
    waiting = books.unconfirmed_until(end)
    confirmed_at = {o["order_id"]: o.get("delivered_at") for o in books.orders}
    rows = []
    for seller_id in sellers:
        gross = commission = 0
        order_ids: set[str] = set()
        rates: set[float] = set()
        by_product: dict[str, dict[str, Any]] = {}
        for o in sold:
            mine = [i for i in o["items"] if i.get("seller_id") == seller_id]
            if not mine:
                continue
            order_ids.add(o["order_id"])
            for i in mine:
                g = sb.item_gross(i)
                rate = cfg.commission.rate_for(i.get("category_code"))
                rates.add(rate)
                gross += g
                commission += sc.rate_amount(g, rate)
                pid = str(i.get("product_id") or "")
                if pid:
                    row = by_product.setdefault(
                        pid,
                        {
                            "product_id": pid,
                            "name": books.product_name(pid, i),
                            "qty": 0,
                            "gross": 0,
                        },
                    )
                    row["qty"] += st._qty(i)
                    row["gross"] += g
        seller_coupon = sum(
            c["total_value"]
            for c in books.coupons
            if c.get("funded_by") == "seller"
            and c.get("seller_id") == seller_id
            and sb.in_range(c.get("created_at"), start, end)
        )
        refunds = sum(
            r["amount"]
            for r in books.refunds
            if r.get("seller_id") == seller_id
            and sb.in_range(st._refund_settles_at(r, confirmed_at), start, end)
        )
        payout = gross - commission - seller_coupon - refunds
        if len(rates) == 1:
            rate_shown = rates.pop()
        elif gross:
            rate_shown = round(commission / gross, 4)
        else:
            rate_shown = cfg.commission.default_rate

        pending_orders, pending_gross = 0, 0
        for o in waiting:
            mine = [i for i in o["items"] if i.get("seller_id") == seller_id]
            if mine:
                pending_orders += 1
                pending_gross += sum(sb.item_gross(i) for i in mine)

        reasons = []
        if not order_ids and pending_orders and payout <= 0:
            reasons.append(
                {
                    "code": "no_confirmed_orders",
                    "label": f"이번 주 확정 주문 없음 (확정 대기 {pending_orders}건은 다음 정산)",
                }
            )
        elif payout <= 0:  # 보류 사유만 #1013 리뷰대로 바꿨다 (환불·쿠폰만 있는 판매자)
            label = (
                "정산금이 0원 이하"
                if order_ids
                else "확정 매출 없이 환불·쿠폰만 있어 정산금이 0원 이하"
            )
            reasons.append({"code": "negative_payout", "label": label})

        if not order_ids and not seller_coupon and not refunds and not pending_orders:
            continue  # 이번 기간 거래가 없는 판매자는 내역서를 만들지 않는다
        rows.append(
            {
                "seller_id": seller_id,
                "name": books.seller_name(seller_id),
                "orders": len(order_ids),
                "gross": gross,
                "commission_rate": rate_shown,
                "commission": commission,
                "seller_coupon": seller_coupon,
                "refunds": refunds,
                "payout": payout,
                "hold_reasons": reasons,
                "status": "held" if reasons else "ready",
                "review_id": None,
                # 다음 정산으로 넘어가는 구매 확정 대기 주문 (이번 정산금에 들지 않는다)
                "pending_confirmation": {"orders": pending_orders, "gross": pending_gross},
                # 이번 기간에 판 쇼핑몰 상품 (판매액 큰 순). 쇼핑몰 상품 화면과 잇는 열쇠
                "products": sorted(
                    by_product.values(), key=lambda r: (-r["gross"], r["product_id"])
                )[: st.MAX_PRODUCTS],
            }
        )
    pay_date = sunday + timedelta(days=cfg.settlement.pay_days_after_period_end)
    return {
        "period": name,
        "from": sc.iso(start),
        "to": sc.iso(end),
        "pay_date": pay_date.isoformat(),
        "statements": rows,
        "applied_rules": [sc.applied_rule()],
    }


def _richer(case: dict[str, Any], seed: int, *, live: bool) -> sb.SettlementBooks:
    """무작위 장부에 목록에 없는 판매자·판매자 없는 품목·상품 이름을 더한다.

    `live`면 실제 장부처럼 계산을 붙여 둔다.
    """
    rng = random.Random(seed + 1000)
    orders = copy.deepcopy(case["orders"])
    for o in orders:
        for i in o["items"]:
            if rng.random() < 0.1:
                i["seller_id"] = "sel_unlisted"
            elif rng.random() < 0.05:
                i["seller_id"] = None
            if rng.random() < 0.3:
                i["title"] = f"책 {i['product_id']}"
    products = [{"product_id": f"prd_{n}", "name": f"상품 {n}"} for n in range(2)]
    return sb.SettlementBooks(
        orders=orders,
        refunds=copy.deepcopy(case["refunds"]),
        coupons=copy.deepcopy(case["coupons"]),
        sellers=[{"seller_id": s, "name": s.upper()} for s in case["sellers"]],
        products=products,
        mock=not live,
    )


@pytest.mark.parametrize("seed", range(150))
def test_one_pass_statements_equal_the_previous_calculation(seed: int) -> None:
    """같은 장부면 바꾼 계산(한 번 훑어 판매자별로 모음)이 예전 계산과 글자까지 같다."""
    case = _random_case(seed)
    for live in (False, True):
        bk = _richer(case, seed, live=live)
        for period in (None, "2026-W41", "2026-W43"):
            assert st.statements(bk, period, TODAY) == _statements_before(bk, period, TODAY)
        if live:
            st.warm(bk)
            assert st.cached_statements(bk, None, TODAY) == _statements_before(bk, None, TODAY)


@pytest.mark.parametrize("seed", range(40))
def test_deadline_report_is_the_same_with_shared_week_results(seed: int) -> None:
    case = _random_case(seed)
    bk = _richer(case, seed, live=True)
    before = st.deadline_report(
        bk, {}, TODAY, statements_of=lambda w: _statements_before(bk, w, TODAY)
    )
    assert st.deadline_report(bk, {}, TODAY) == before


def _scale_books(n_orders: int, n_sellers: int) -> sb.SettlementBooks:
    """합성 장부: 주문 n건·판매자 n곳, 70일에 고르게. 배송 완료 85%·환불 5%·판매자 쿠폰 조금."""
    rng = random.Random(7)
    sellers = [{"seller_id": f"sel_{k:03d}", "name": f"가게{k}"} for k in range(n_sellers)]
    first = FIXED_NOW - timedelta(days=70)
    orders, refunds = [], []
    for n in range(n_orders):
        when = first + timedelta(seconds=rng.randrange(70 * 86400))
        items = [
            (sellers[rng.randrange(n_sellers)]["seller_id"], "book", 1, rng.randint(1, 30) * 1000)
            for _ in range(rng.randint(1, 3))
        ]
        o = order(f"ord_{n}", when, items, delivered=rng.random() < 0.85)
        orders.append(o)
        if rng.random() < 0.05:
            refunds.append(refund(f"rfd_{n}", o["order_id"], items[0][0], 1000, when))
    coupons = [
        {
            "coupon_id": f"cpn_{k}",
            "funded_by": "seller",
            "seller_id": sellers[k % n_sellers]["seller_id"],
            "total_value": 1000,
            "created_at": first + timedelta(days=k % 70),
        }
        for k in range(500)
    ]
    return sb.SettlementBooks(
        orders=orders, refunds=refunds, coupons=coupons, sellers=sellers, mock=False
    )


def test_statements_scale_and_deadlines_compute_each_week_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """주문 6만 건·판매자 200곳: 정산서는 넉넉히 잡아도 3초 안, 지급 기한 점검은 주마다 한 번만
    계산한다.

    서버(주문 30만 건)에서 예전 계산은 정산서 8.8초, 지급 기한 점검 86.6초였다(주마다 정산서를 다시
    계산).
    """
    bk = _scale_books(60_000, 200)
    started = time.perf_counter()
    st.warm(bk)
    result = st.statements(bk, None, TODAY)
    assert time.perf_counter() - started < 3.0
    assert len(result["statements"]) == 200

    calls: list[str] = []
    real = st.statements

    def counted(books_: sb.SettlementBooks, period: str | None, today: date | None = None) -> Any:
        calls.append(str(period))
        return real(books_, period, today)

    monkeypatch.setattr(st, "statements", counted)
    st.cached_statements(bk, None, TODAY)  # 조회가 지난주를 먼저 계산해 둔 상태
    report = st.deadline_report(bk, {}, TODAY)
    assert report["sellers"]
    assert len(calls) == len(set(calls))  # 주마다 한 번만, 조회에서 계산한 주는 다시 안 함
    st.deadline_report(bk, {}, TODAY)
    assert len(calls) == len(set(calls))  # 같은 장부로 다시 불러도 계산하지 않는다


@pytest.mark.asyncio
async def test_same_computation_runs_once_while_callers_wait() -> None:
    """스레드 계산은 부른 쪽이 끊겨도 멈추지 않으므로, 같은 열쇠의 계산은 하나만 띄운다."""
    bk = _week_books()
    bk.mock = False
    runs: list[int] = []

    def slow() -> dict[str, Any]:
        runs.append(1)
        time.sleep(0.05)
        return {"n": len(runs)}

    got = await asyncio.gather(*(bk.computed("k", slow) for _ in range(5)))
    assert runs == [1] and all(g == {"n": 1} for g in got)
    assert await bk.computed("k", slow) == {"n": 1} and runs == [1]  # 붙여 둔 결과


@pytest.mark.asyncio
async def test_views_reuse_the_week_result_but_never_keep_batch_status() -> None:
    """같은 장부·같은 주는 한 번 계산한 정산서를 쓴다. 묶음 상태는 붙여 둔 결과에 남지 않는다."""
    bk = _week_books()
    bk.mock = False
    first = await sh._view(bk, "2026-W42")
    batches_mod.STORE.put(
        "2026-W42",
        {
            "status": "requested",
            "review_id": "rev_1",
            "seller_ids": ["sel_a"],
            "total_payout": 9000,
        },
    )
    second = await sh._view(bk, "2026-W42")
    assert {r["seller_id"]: r["status"] for r in first["statements"]} == {
        "sel_a": "ready",
        "sel_b": "ready",
    }
    assert {r["seller_id"]: r["status"] for r in second["statements"]}["sel_a"] == "requested"
    shared = st.cached_statements(bk, "2026-W42", TODAY)
    assert {r["status"] for r in shared["statements"]} == {"ready"}


# ----------------------------------------장부 읽기: 바로 답하고 뒤에서 새로 읽기
class _FakeShop:
    """`live_books` 대신: 부를 때마다 (전체?, 최근만?)을 적고 `gate`가 열릴 때까지 기다린다."""

    def __init__(self) -> None:
        self.calls: list[tuple[bool, bool]] = []
        self.gate = asyncio.Event()
        self.gate.set()

    async def __call__(
        self, http_client: Any = None, store: Any = None, full: bool = False, recent: bool = False
    ) -> sb.SettlementBooks:
        self.calls.append((full, recent))
        await self.gate.wait()
        bk = _week_books()
        bk.mock = False
        return bk


@pytest.fixture
def fake_shop(monkeypatch: pytest.MonkeyPatch) -> _FakeShop:
    fake = _FakeShop()
    monkeypatch.setattr(sb, "live_books", fake)
    sb.clear_cache()
    yield fake
    sb.clear_cache()


@pytest.mark.asyncio
async def test_first_load_answers_preparing_quickly(
    fake_shop: _FakeShop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """서버를 켠 뒤 첫 장부가 아직이면 오래 붙잡지 않고 "준비 중"으로 답한다(30초 504가 아니라)."""
    monkeypatch.setattr(sb, "PREPARING_WAIT_SECONDS", 0.05)
    fake_shop.gate.clear()
    result = await sh.seller_settlement(_request("seller_settlement", {}))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "settlement_preparing")
    again = await sh.seller_settlement_deadlines(_request("seller_settlement_deadlines", {}))
    assert again.outcome == "settlement_preparing"
    assert len(fake_shop.calls) == 1  # 기다리는 동안 읽기를 겹쳐 띄우지 않는다
    fake_shop.gate.set()
    assert sb._inflight is not None
    await sb._inflight
    done = await sh.seller_settlement(_request("seller_settlement", {}))
    assert done.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_reads_answer_from_the_last_books_and_refresh_behind(
    fake_shop: _FakeShop, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await sb.load_books()
    assert fake_shop.calls == [(False, False)]
    # 장부가 오래돼도(10분 넘게) 조회는 기다리지 않고 직전 장부로 답한다. 새로 읽기는 하나만 뒤에서
    stamp, books_ = sb._cache
    sb._cache = (stamp - 3600, books_)
    fake_shop.gate.clear()
    got = await asyncio.wait_for(asyncio.gather(*(sb.load_books() for _ in range(5))), 0.5)
    assert all(g is first for g in got)
    assert fake_shop.calls == [(False, False), (False, False)]
    fake_shop.gate.set()
    assert sb._inflight is not None
    await sb._inflight
    assert sb._cache[1] is not first  # 뒤에서 읽은 새 장부로 바뀌었다


@pytest.mark.asyncio
async def test_payout_request_refreshes_only_recent_orders(fake_shop: _FakeShop) -> None:
    """돈이 걸린 결정은 새로 읽은 장부로 하되, 매번 전체(30만 건)를 다시 읽지 않는다."""
    first = await sb.load_books()
    assert await sb.load_books(fresh=True) is first  # 60초 안에 모은 장부면 그대로
    stamp, books_ = sb._cache
    sb._cache = (stamp - sb.FRESH_SECONDS - 1, books_)
    fresh = await sb.load_books(fresh=True)
    assert fresh is not first
    assert fake_shop.calls == [(False, False), (False, True)]  # 두 번째는 최근 주문만


def test_store_reads_everything_first_then_recent_orders_even_when_a_full_read_is_due() -> None:
    store = sb.OrderStore()
    assert store.since(False, recent=True) is None  # 처음 한 번은 전체
    store.full_at = time.monotonic()
    assert store.since(False) is not None
    store.full_at = time.monotonic() - sb.FULL_REFRESH_SECONDS - 1
    assert store.since(False) is None  # 조회는 10분마다 전체
    assert store.since(False, recent=True) is not None  # 지급 요청은 최근 주문만


def _raw_order(n: int, created: str, *, refunded: int = 0, delivered: str | None = None) -> dict:
    return {
        "order_id": f"ord_{n}",
        "created_at": created,
        "delivered_at": delivered,
        "total_amount": 30000,
        "refunded_amount": refunded,
        "items": [
            {"product_id": "prd_1", "seller_id": "sel_a", "qty": 1, "unit_price": 20000},
            {"product_id": "prd_2", "seller_id": "sel_b", "qty": 1, "unit_price": 10000},
        ],
        "payments": [
            {
                "payment_id": f"pay_{n}",
                "status": "succeeded",
                "amount": 30000,
                "created_at": created,
            }
        ],
        "refunds": [
            {
                "refund_id": f"rf_{n}",
                "payment_id": f"pay_{n}",
                "amount": refunded,
                "created_at": created,
            }
        ]
        if refunded
        else [],
    }


def test_incremental_merge_gives_the_same_books_as_a_full_read() -> None:
    """모아 둔 주문·환불 나눔에 최근 주문만 끼운 결과가 전체를 새로 읽은 결과와 같다."""
    old = [
        _raw_order(n, "2026-10-01T01:00:00Z", delivered="2026-10-02T01:00:00Z") for n in range(5)
    ]
    old.append(
        _raw_order(9, "2026-10-01T01:00:00Z", refunded=3000, delivered="2026-10-02T01:00:00Z")
    )
    recent_before = [_raw_order(20, "2026-10-22T01:00:00Z")]
    recent_after = [
        _raw_order(20, "2026-10-22T01:00:00Z", refunded=6000, delivered="2026-10-23T01:00:00Z"),
        _raw_order(21, "2026-10-23T01:00:00Z"),
    ]
    store = sb.OrderStore()
    store.merge(old + recent_before, {}, None)
    since = (FIXED_NOW - timedelta(days=sb.RECENT_DAYS)).replace(microsecond=0)
    orders, skipped = store.merge(recent_after, {}, since)

    whole = sb.OrderStore()
    want_orders, want_skipped = whole.merge(old + recent_after, {}, None)
    assert sorted(o["order_id"] for o in orders) == sorted(o["order_id"] for o in want_orders)
    assert skipped == want_skipped == 0
    assert store.all_refunds() == sb.refunds_from_orders(orders)
    assert sorted(r["refund_id"] for r in store.all_refunds()) == sorted(
        r["refund_id"] for r in whole.all_refunds()
    )
    a = sb.SettlementBooks(orders=orders, refunds=store.all_refunds(), sellers=[], mock=False)
    b = sb.SettlementBooks(orders=want_orders, refunds=whole.all_refunds(), sellers=[], mock=False)
    for period in ("2026-W40", "2026-W43"):
        assert st.statements(a, period, TODAY) == st.statements(b, period, TODAY)


def test_coupon_read_falls_back_to_the_claims_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """정산 서비스 키가 따로 없으면 쿠폰 읽기(읽기만)에 claims 키를 쓴다. 서버 설정을 안 바꿔도
    된다."""
    monkeypatch.setenv("SUPPORT_AGENT_CARD", "/x/settlement/agent-card.json")
    monkeypatch.delenv("AX_SERVICE_NAME", raising=False)
    monkeypatch.setenv("AX_SHOP_CLIENT_KEYS", "inquiry:k1,claims:k2")
    assert sb.coupon_headers() == {"Authorization": "Bearer k2"}
    monkeypatch.setenv("AX_SHOP_CLIENT_KEYS", "inquiry:k1,claims:k2,settlement:k3")
    assert sb.coupon_headers() == {"Authorization": "Bearer k3"}  # 따로 있으면 그 키
    monkeypatch.setenv("AX_SHOP_CLIENT_KEYS", "inquiry:k1")
    assert sb.coupon_headers() is None  # 없으면 쿠폰을 못 받은 장부(정산금을 내지 않는다)


# ------------------------------------------------- #1013 리뷰 · AI 자동 요청 (2026-11-12)
def test_a_refund_only_seller_is_held_not_ready() -> None:
    """확정 매출 없이 환불만 있는 판매자는 정산금이 0원 이하라 보류한다(예전엔 "지급 준비됨")."""
    sold = order("ord_1", at("2026-10-06"), [("sel_a", "book", 1, 30000)])  # W41에 정산됨
    keep = order("ord_2", at("2026-10-14"), [("sel_b", "book", 1, 10000)])
    bk = books(
        orders=[sold, keep], refunds=[refund("rfd_1", "ord_1", "sel_a", 5000, at("2026-10-14"))]
    )
    result = st.statements(bk, "2026-W42", TODAY)
    rows = {r["seller_id"]: r for r in result["statements"]}
    a = rows["sel_a"]
    assert (a["orders"], a["gross"], a["refunds"], a["payout"]) == (0, 0, 5000, -5000)
    assert a["status"] == "held"
    assert [h["code"] for h in a["hold_reasons"]] == ["negative_payout"]
    assert "환불" in a["hold_reasons"][0]["label"]
    assert rows["sel_b"]["status"] == "ready"
    assert [r["seller_id"] for r in st.ready(result)] == ["sel_b"]


def test_period_closed_and_due_weeks() -> None:
    """오늘 2026-10-23(금): W42(10-12~18)는 끝났지만 지급 예정일(10-24) 전, W41은 지급일이
    지났다."""
    assert st.period_closed("2026-W42", TODAY) and not st.period_closed("2026-W43", TODAY)
    assert not st.period_closed("2026-W44", TODAY)
    weeks = st.due_weeks(TODAY)
    assert weeks[-1] == "2026-W41" and "2026-W42" not in weeks and "2026-W43" not in weeks
    assert weeks == sorted(weeks)
    # 지급 기한 점검 범위(70일) 안의 주만
    since = TODAY - timedelta(days=sc.load_policy().settlement.deadline_lookback_days)
    assert weeks[0] == sc.week_of(since)
    assert "2026-W42" in st.due_weeks(date(2026, 10, 24))  # 지급 예정일이 되면 넣는다


@pytest.mark.asyncio
async def test_an_open_week_is_not_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    loads = _use_books(_week_books())
    calls = _fake_execute(monkeypatch, HELD)
    payload = {"period": "2026-W43", "requested_by": "byunghwan"}  # 이번 주 (아직 안 끝남)
    result = await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "period_not_closed")
    assert calls == [] and loads == [] and batches_mod.STORE.batches == {}


@pytest.mark.asyncio
async def test_partly_unreadable_orders_block_the_request_but_not_the_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bk = _week_books()
    bk.unavailable = ["orders_partial"]
    _use_books(bk)
    calls = _fake_execute(monkeypatch, HELD)
    view = await sh.seller_settlement(_request("seller_settlement", {"period": "2026-W42"}))
    assert view.status == TaskStatus.COMPLETED
    assert view.output["meta"]["unavailable"] == ["orders_partial"]  # 보여 주되 못 읽은 걸 알린다
    payload = {"period": "2026-W42", "requested_by": "byunghwan"}
    result = await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "orders_partial")
    assert calls == [] and batches_mod.STORE.batches == {}


def _executed(period: str, attempt: int, execution_id: str = "exe_77") -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "request": {
            "action": "confirm_settlement",
            "idempotency_key": f"ctx_req_settlement_{period}:confirm_settlement:{period}:{attempt}",
        },
        "decision": {"status": "executed"},
    }


@pytest.mark.asyncio
async def test_lost_review_with_an_executed_record_becomes_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """보류 건은 잃었어도(404) 실행 기록이 있으면 승인된 것이다. review_missing으로 열지 않는다."""
    _use_books(_week_books())
    _fake_execute(monkeypatch, HELD)
    answer = {"r": httpx.Response(200, json={"data": {"status": "pending"}})}
    seen = _safety_review(monkeypatch, lambda r: _answer(answer, r))
    payload = {"period": "2026-W42", "requested_by": "taeyoon"}
    await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    answer["r"] = httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
    answer["executions"] = httpx.Response(200, json={"data": [_executed("2026-W42", 1)]})
    view = (
        await sh.seller_settlement(_request("seller_settlement", {"period": "2026-W42"}))
    ).output
    assert view["batch"]["status"] == "approved" and "closed_reason" not in view["batch"]
    assert "/api/safety/executions" in seen


@pytest.mark.asyncio
async def test_404_never_reopens_an_approved_batch_and_unknown_keeps_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_books(_week_books())
    answer = {
        "r": httpx.Response(404, json={"error": {"code": "NOT_FOUND"}}),
        "executions": httpx.Response(503),
    }
    _safety_review(monkeypatch, lambda r: _answer(answer, r))
    approved = {"status": "approved", "review_id": "rev_1", "seller_ids": ["sel_a"], "attempt": 1}
    batches_mod.STORE.put("2026-W42", dict(approved))
    out = (await sh.seller_settlement_batches(_request("seller_settlement_batches", {}))).output
    assert out["batches"]["2026-W42"]["status"] == "approved"
    # 요청 중인데 보류 건 404 + 실행 기록을 못 읽으면 모르는 채로 다시 열지 않는다
    batches_mod.STORE.put("2026-W42", approved | {"status": "requested"})
    out = (await sh.seller_settlement_batches(_request("seller_settlement_batches", {}))).output
    assert out["batches"]["2026-W42"]["status"] == "requested"


@pytest.mark.asyncio
async def test_re_request_after_review_missing_checks_executions_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """review_missing 뒤 다시 요청하기 전에 실행 기록을 본다. 실행됐으면 요청하지 않고 승인으로
    둔다."""
    _use_books(_week_books())
    calls = _fake_execute(monkeypatch, HELD)
    answer = {"executions": httpx.Response(200, json={"data": [_executed("2026-W42", 1)]})}
    _safety_review(monkeypatch, lambda r: _answer(answer, r))
    batches_mod.STORE.put(
        "2026-W42",
        {
            "status": "rejected",
            "closed_reason": "review_missing",
            "review_id": "rev_1",
            "execution_id": "exe_77",
            "seller_ids": ["sel_a", "sel_b"],
            "attempt": 1,
        },
    )
    payload = {"period": "2026-W42", "requested_by": "byunghwan"}
    result = await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    assert result.status == TaskStatus.COMPLETED and result.output["requested"] is False
    assert calls == []
    assert batches_mod.STORE.get("2026-W42")["status"] == "approved"

    # 실행 기록을 못 읽으면 다시 요청하지 않는다 (두 번 지급될 수 있다)
    batches_mod.STORE.put(
        "2026-W42",
        {"status": "rejected", "closed_reason": "review_missing", "attempt": 1, "seller_ids": []},
    )
    answer["executions"] = httpx.Response(503)
    result = await sh.request_seller_settlement(_request("request_seller_settlement", payload))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "safety_unavailable")
    assert calls == []


def _due_books() -> sb.SettlementBooks:
    """지급일이 지난 W41(10-05~11)에 줄 판매자 둘, W42는 아직 지급일 전."""
    return books(
        orders=[
            order("ord_1", at("2026-10-06"), [("sel_a", "book", 1, 10000)]),
            order("ord_2", at("2026-10-07"), [("sel_b", "book", 1, 20000)]),
            order("ord_3", at("2026-10-14"), [("sel_a", "book", 1, 30000)]),
        ]
    )


@pytest.mark.asyncio
async def test_support_ai_requests_a_due_week_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from support_agent import settlement_auto as auto

    _use_books(_due_books())
    calls = _fake_execute(monkeypatch, HELD)
    answer = {"r": httpx.Response(200, json={"data": {"status": "pending"}})}
    _safety_review(monkeypatch, lambda r: _answer(answer, r))
    results = await auto.run_once()
    assert results["2026-W41"] == "requested"
    assert "2026-W42" not in results  # 지급 예정일 전
    [call] = calls
    assert call["params"]["period"] == "2026-W41"
    assert call["params"]["requested_by"] == "support-ai"  # 사람이 아니라 어느 담당이든 승인한다
    assert call["key"] == "ctx_req_settlement_2026-W41:confirm_settlement:2026-W41:1"
    assert "고객지원 AI" in call["params"]["summary"]
    stored = batches_mod.STORE.get("2026-W41")
    assert (stored["status"], stored["requested_by"]) == ("requested", "support-ai")
    view = (
        await sh.seller_settlement(_request("seller_settlement", {"period": "2026-W41"}))
    ).output
    assert view["batch"]["requested_by"] == "support-ai"  # 화면이 "고객지원 AI(자동)"로 보여 준다

    # 다음 회차: 요청 중이면 다시 요청하지 않는다
    again = await auto.run_once()
    assert again["2026-W41"] == "requested" and len(calls) == 1


@pytest.mark.asyncio
async def test_support_ai_re_requests_expired_but_not_human_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from support_agent import settlement_auto as auto

    _use_books(_due_books())
    second = HELD.model_copy(update={"execution_id": "exe_78", "review_id": "rev_78"})
    calls = _fake_execute(monkeypatch, HELD, second)
    status = {"rev_77": "pending", "rev_78": "pending"}

    def review(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/safety/executions":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": {"status": status[request.url.path[-6:]]}})

    _safety_review(monkeypatch, review)
    await auto.run_once()
    status["rev_77"] = "expired"  # 아무도 결정하지 않고 시간이 지났다
    first = await auto.run_once()
    assert first["2026-W41"] == "requested"
    assert [c["key"][-2:] for c in calls] == [":1", ":2"]  # 시도 번호를 올려 다시 요청
    assert batches_mod.STORE.get("2026-W41")["review_id"] == "rev_78"

    status["rev_78"] = "rejected"  # 고객지원 담당이 멈췄다
    later = await auto.run_once()
    assert later["2026-W41"] == "rejected_by_human" and len(calls) == 2  # 사람이 멈춘 주는 그대로


@pytest.mark.asyncio
async def test_support_ai_skips_weeks_with_nothing_to_pay_and_waits_for_books(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from support_agent import settlement_auto as auto

    _use_books(books(orders=[]))
    calls = _fake_execute(monkeypatch, HELD)
    results = await auto.run_once()
    assert set(results.values()) == {"nothing_to_settle"} and calls == []

    async def preparing(fresh: bool) -> sb.SettlementBooks:
        raise sb.Preparing("모으는 중")

    sb.LOADER = preparing
    with pytest.raises(sb.Preparing):
        await auto.run_once()


def test_auto_request_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    from support_agent import settlement_auto as auto

    monkeypatch.delenv(auto.ENV_SWITCH, raising=False)
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    assert auto.enabled() is False  # 테스트(목 모드)에서는 기본으로 끈다
    monkeypatch.setenv(auto.ENV_SWITCH, "on")
    assert auto.enabled() is True
    monkeypatch.setenv(auto.ENV_SWITCH, "off")
    assert auto.enabled() is False
    assert auto.start(frozenset({"request_seller_settlement"})) is None  # 꺼져 있으면 안 돈다
    monkeypatch.setenv(auto.ENV_SECONDS, "abc")
    assert auto.interval_seconds() == auto.DEFAULT_SECONDS
