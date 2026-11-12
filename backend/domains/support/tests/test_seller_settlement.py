"""판매자 정산(고객지원) 테스트: 정산 공식·보류 사유·환불 시점·지급 기한·지급 승인 요청 skill.

2026-11-12 재무에서 옮겨 왔다. 재무에 있던 정산 공식·보류 사유·지급 기한 테스트와 무작위 장부
정확도 점검(재무 `accuracy.case_settlement`)을 여기 같은 기준으로 옮겼다.
"""

from __future__ import annotations

import random
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


@pytest.mark.parametrize("seed", range(150))
def test_random_books_match_an_independent_calculation(seed: int) -> None:
    """정산 공식을 따로 계산한 값과 맞춘다 (재무 `accuracy.case_settlement`에서 옮김)."""
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
        held = (bool(ids) or pending) and payout <= 0
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


@pytest.mark.asyncio
async def test_lost_review_reopens_and_can_be_requested_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """안전 장치가 재시작돼 보류 건을 잃으면(404) 끝난 요청으로 보고 다시 요청할 수 있다."""
    _use_books(_week_books())
    calls = _fake_execute(monkeypatch, HELD)
    payload = {"period": "2026-W42", "requested_by": "taeyoon"}
    answer = {"r": httpx.Response(200, json={"data": {"status": "pending"}})}
    _safety_review(monkeypatch, lambda r: answer["r"])
    await sh.request_seller_settlement(_request("request_seller_settlement", payload))

    answer["r"] = httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
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
