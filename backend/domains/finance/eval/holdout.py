"""재무 AI holdout 평가 (처음 보는 실제 모양 데이터): uv run python domains/finance/eval/holdout.py

`run.py`(`finance_agent.accuracy`)와 다른 점
- 장부를 손으로 만들지 않는다. **shop-api 실제 코드**(`Shop.place_order`·`pay`·`mark_delivered`,
  `Refunds.refund`, `Promotions.issue_coupon`, 장애 주입 `inject_fault`)로 쇼핑몰 DB를 만든다.
- 재무는 **실서버와 같은 경로**로 읽는다: shop-api FastAPI 앱(`create_app`)에
  `GET /api/shop/orders`·`/products`·`/sellers`·`/coupons`(재무 토큰)를 보내고,
  `books.live_books`가 장부로 바꾼다(정규화·환불 기록 만들기·실제 모드 PG 목 포함).
- 정답은 재무 코드가 아니라 **shop-api DB 표**(orders·order_items·payments·refunds·coupons·
  products)를
  SQL로 읽어 정책대로 계산한다.
- 환불 시각: shop-api 주문 응답에는 환불 시각이 없어서 재무는 환불을 주문 시각에 둔다
  (`notes.refund_basis`, 문서화된 한계). 그래서 결산·정산은 두 기준으로 잰다.
  - `actual`: 환불이 실제로 난 시각 기준 (현실 정답)
  - `documented`: 재무 문서 기준 (환불 시각 = 주문 접수 시각)
- 규칙: 하네스는 dev seed로만 고치고, holdout seed(`20261109_02`)는 한 번 돌려 숫자만 본다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

EVAL_TOKEN = "finance-holdout-eval"  # 이 프로세스 안에서만 쓰는 평가용 토큰 (비밀값 아님)
os.environ["AX_AGENT_TOKENS"] = f"finance={EVAL_TOKEN}"
os.environ["FINANCE_TOKEN"] = EVAL_TOKEN
os.environ.setdefault("SHOP_MONITOR_INTERVAL_S", "0")

import httpx  # noqa: E402

from ax_shop_api import service  # noqa: E402
from ax_shop_api.db import Db  # noqa: E402
from ax_shop_api.main import create_app  # noqa: E402
from ax_shop_api.promotions import Promotions  # noqa: E402
from ax_shop_api.refunds import Refunds  # noqa: E402
from ax_shop_api.seed import seed  # noqa: E402
from finance_agent import accuracy as base  # noqa: E402
from finance_agent import finance_config as fc  # noqa: E402
from finance_agent.bank import match as bank_match  # noqa: E402
from finance_agent.books import OrderStore, live_books  # noqa: E402
from finance_agent.closing import compute_totals  # noqa: E402
from finance_agent.reconciliation import reconcile  # noqa: E402
from finance_agent.settlement import statements  # noqa: E402
from finance_agent.tax import income_tax_amount, vat_pack  # noqa: E402

HOLDOUT_SEED = "20261109_02"
# 실제 모드 PG 목은 불일치를 넣지 않는다(2026-11-09, 지어낸 데이터 제거). 그래서 빈 계획이다
LIVE_PLAN: dict[int, dict[str, Any]] = {}
# 시드 상품은 가격이 딱 떨어져서 끝자리 있는 상품을 shop-api 상품 등록 코드로 더한다
EXTRA_PRODUCTS = [
    ("sel_0001", "문고판 고전", "book", 12_345),
    ("sel_0002", "포스트잇 묶음", "stationery", 8_750),
    ("sel_0003", "선물 포장 세트", "gift", 19_990),
    ("sel_0003", "한정판 화집", "book", 47_515),
]


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def day0(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


@contextmanager
def shop_clock():
    original = service._now
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    service._now = lambda: iso(now[0])
    try:
        yield now
    finally:
        service._now = original


class World:
    """shop-api 코드로 만든 쇼핑몰 하나."""

    def __init__(self, rng: random.Random, clock: list[datetime]) -> None:
        self.rng, self.clock = rng, clock
        self.db = Db(":memory:")
        seed(self.db)
        with self.db.tx() as conn:
            conn.execute("UPDATE products SET stock = 1000000")
        self.shop = service.Shop(self.db)
        self.refunds = Refunds(self.db, now=lambda: self.clock[0])
        self.promo = Promotions(self.db, now=lambda: self.clock[0])
        self.real_now = datetime.now(UTC) - timedelta(minutes=5)
        for seller, name, cat, price in EXTRA_PRODUCTS:
            self.shop.create_product(seller, name, cat, price, 1_000_000)
        with self.db.tx() as conn:
            self.products = [
                r["product_id"] for r in conn.execute("SELECT product_id FROM products")
            ]
            self.buyers = [r["buyer_id"] for r in conn.execute("SELECT buyer_id FROM buyers")]
        self.coupons: dict[str, list[Any]] = {}
        self.n = 0

    def at(self, t: datetime) -> None:
        self.clock[0] = t

    def issue_coupon(self, t: datetime) -> None:
        self.at(t)
        buyer = self.rng.choice(self.buyers)
        self.n += 1
        units = self.rng.randint(1, 3)
        c, _ = self.promo.issue_coupon(
            f"eval-cpn-{self.n}", "campaign", buyer, units,
            self.rng.choice([1_000, 2_000, 3_000, 5_000]), "cmp_eval",
        )  # fmt: skip
        self.coupons[buyer] = [c["coupon_id"], units]

    def sale(self, t: datetime) -> None:
        """주문 하나: 주문 → 결제(가끔 실패·장애·재시도) → 배송 완료 → 가끔 환불."""
        rng = self.rng
        self.at(t)
        buyer = rng.choice(self.buyers)
        lines = [(p, rng.randint(1, 3)) for p in rng.sample(self.products, rng.randint(1, 3))]
        held = self.coupons.get(buyer)
        if held and held[1] > 0 and rng.random() < 0.7:
            order = self.shop.place_order(buyer, lines, coupon_id=held[0])[0]
            held[1] -= 1
        else:
            order = self.shop.create_order(buyer, lines)
        oid = order["order_id"]
        pay_at = t + timedelta(seconds=rng.randint(5, 600))
        r = rng.random()
        attempts = [("decline" if r < 0.06 else "fault" if r < 0.09 else "ok", pay_at)]
        if attempts[0][0] != "ok" and rng.random() < 0.6:
            attempts.append(("ok", pay_at + timedelta(minutes=rng.randint(5, 90))))
        paid = None
        for kind, when in attempts:
            if when > self.real_now:
                break
            self.at(when)
            if kind == "fault":
                self.shop.inject_fault("failure")
            got = self.shop.pay(oid, "card", "failure" if kind == "decline" else None)
            if kind == "fault":
                self.shop.release_fault()
            if got["status"] == "paid":
                paid = when
                break
        if paid is None:
            return
        deliver = paid + timedelta(days=rng.randint(1, 4), hours=rng.randint(0, 12))
        if deliver <= self.real_now and rng.random() < 0.85:
            self.at(deliver)
            self.shop.mark_delivered(oid, deliver)
        if rng.random() < 0.18:
            refund_at = paid + timedelta(days=rng.randint(0, 6), hours=rng.randint(0, 20))
            if refund_at <= self.real_now:
                with self.db.tx() as conn:
                    pay = conn.execute(
                        "SELECT payment_id, amount FROM payments WHERE order_id = ? AND "
                        "status = 'succeeded'",
                        (oid,),
                    ).fetchone()
                full = rng.random() < 0.4
                amount = pay["amount"] if full else rng.randint(1, pay["amount"])
                self.at(refund_at)
                self.n += 1
                self.refunds.refund(pay["payment_id"], amount, f"eval-rfd-{self.n}", None)

    def traffic(self, first: date, last: date, per_day: float) -> None:
        """first~last 하루마다 포아송 주문. 시각 순으로 넣는다."""
        events: list[tuple[datetime, str]] = []
        d = first
        while d <= last:
            k = poisson(self.rng, per_day)
            for _ in range(k):
                t = day0(d) + timedelta(seconds=self.rng.randrange(0, 86_400))
                if t < self.real_now:
                    events.append((t, "sale"))
            if self.rng.random() < 0.25:
                t = day0(d) + timedelta(seconds=self.rng.randrange(0, 86_400))
                if t < self.real_now:
                    events.append((t, "coupon"))
            d += timedelta(days=1)
        for t, kind in sorted(events):
            self.issue_coupon(t) if kind == "coupon" else self.sale(t)

    # ---- 정답용 표 (SQL로 직접)
    def tables(self) -> dict[str, Any]:
        with self.db.tx() as conn:
            q = lambda sql: [dict(r) for r in conn.execute(sql)]  # noqa: E731
            return {
                "orders": {r["order_id"]: r for r in q("SELECT * FROM orders")},
                "items": q(
                    "SELECT i.rowid AS rid, i.*, p.category_code FROM order_items i "
                    "JOIN products p ON p.product_id = i.product_id ORDER BY i.rowid"
                ),
                "payments": q("SELECT * FROM payments ORDER BY rowid"),
                "refunds": q("SELECT * FROM refunds"),
                "coupons": q("SELECT * FROM coupons"),
            }

    async def finance_books(self):
        app = create_app(self.db, seed_data=False)
        transport = SafetyStub(httpx.ASGITransport(app=app))
        async with httpx.AsyncClient(transport=transport) as client:
            return await live_books(http_client=client, store=OrderStore(), full=True)


class SafetyStub(httpx.AsyncBaseTransport):
    """shop-api 요청은 실제 앱으로, safety 실행 기록 요청은 빈 목록으로 답한다."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/safety/"):
            return httpx.Response(200, json={"data": [], "meta": {}})
        return await self.inner.handle_async_request(request)


def poisson(rng: random.Random, mean: float) -> int:
    import math

    limit, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


# ---------------------------------------------------------------- 정답 (shop-api 표 → 정책)
class Truth:
    def __init__(self, tb: dict[str, Any], today: date) -> None:
        self.tb, self.today = tb, today
        self.items_of: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for i in tb["items"]:
            self.items_of[i["order_id"]].append(i)
        self.pays_of: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for p in tb["payments"]:
            p["at"] = parse(p["created_at"])
            self.pays_of[p["order_id"]].append(p)
        ok = [p for p in tb["payments"] if p["status"] == "succeeded"]
        settled = sorted(
            p["payment_id"] for p in ok
            if p["at"].date() + timedelta(days=base.PG_PAYOUT_DAYS) <= today
        )  # fmt: skip
        self.spec = {settled[i]: s for i, s in LIVE_PLAN.items() if i < len(settled)}

    def paid_at(self, oid: str) -> datetime | None:
        ts = [p["at"] for p in self.pays_of[oid] if p["status"] == "succeeded"]
        return min(ts) if ts else None

    def record(self, p: dict[str, Any]) -> dict[str, Any] | None:
        spec = self.spec.get(p["payment_id"], {})
        fake = {"amount": p["amount"], "created_at": p["at"]}
        return base.pg_record(fake, spec, self.today)

    def sold(self, start: datetime, end: datetime) -> list[str]:
        return [o for o in self.tb["orders"] if base.inside(self.paid_at(o), start, end)]

    def item_paid(self, i: dict[str, Any]) -> int:
        return i["qty"] * i["unit_price"] - i["points_share"] - i["coupon_share"]

    def refund_split(self, oid: str, amount: int) -> dict[str, int]:
        """재무 문서: 품목 결제 금액 비율로 나누고(내림) 나머지는 마지막 품목."""
        paid = [i for i in self.items_of[oid] if self.item_paid(i)]
        total = sum(self.item_paid(i) for i in paid) or 1
        out: dict[str, int] = defaultdict(int)
        left = amount
        for k, i in enumerate(paid):
            share = left if k == len(paid) - 1 else amount * self.item_paid(i) // total
            left -= share
            out[i["seller_id"]] += share
        return out

    def refunds_in(self, start: datetime, end: datetime, basis: str) -> dict[str, int]:
        """주문별 기간 안 환불 합계. actual = 환불 시각, documented = 주문 접수 시각."""
        per: dict[str, int] = defaultdict(int)
        for r in self.tb["refunds"]:
            o = self.tb["orders"][r["order_id"]]
            when = parse(r["created_at"]) if basis == "actual" else parse(o["created_at"])
            if base.inside(when, start, end):
                per[r["order_id"]] += r["amount"]
        return per

    def closing(self, start: datetime, end: datetime, basis: str) -> dict[str, int]:
        gmv = com = fee = 0
        sold = self.sold(start, end)
        for oid in sold:
            for i in self.items_of[oid]:
                gross = i["qty"] * i["unit_price"]
                gmv += gross
                com += base.half_up(gross, base.rate_of(i["category_code"]))
            for p in self.pays_of[oid]:
                if p["status"] == "succeeded":
                    rec = self.record(p)
                    fee += rec["fee"] if rec else base.half_up(p["amount"], base.PG_RATE)
        coupon = sum(
            c["total_value"] for c in self.tb["coupons"]
            if base.inside(parse(c["created_at"]), start, end)
        )  # fmt: skip
        refund = sum(self.refunds_in(start, end, basis).values())
        return {"gmv": gmv, "commission": com, "coupon": coupon, "refund": refund,
                "pg_fee": fee, "net": com - coupon - fee, "orders": len(sold)}  # fmt: skip


# ---------------------------------------------------------------- 영역별 케이스
async def case_closing(rng, clock, today):
    d = today - timedelta(days=rng.randint(3, 40))
    w = World(rng, clock)
    w.traffic(d - timedelta(days=4), d + timedelta(days=1), rng.uniform(3, 12))
    books = await w.finance_books()
    start, end = day0(d), day0(d + timedelta(days=1))
    t = compute_totals(books, start, end)
    got = {"gmv": t.gmv, "commission": t.commission_revenue, "coupon": t.coupon_cost,
           "refund": t.refund_total, "pg_fee": t.pg_fee, "net": t.net_result,
           "orders": t.order_count}  # fmt: skip
    truth = Truth(w.tables(), today)
    return (
        got,
        {b: truth.closing(start, end, b) for b in ("actual", "documented")},
        {
            "day": d.isoformat(),
            "unavailable": books.unavailable,
        },
    )


async def case_reconciliation(rng, clock, today):
    w = World(rng, clock)
    w.traffic(today - timedelta(days=8), today, rng.uniform(1.5, 4))
    books = await w.finance_books()
    truth = Truth(w.tables(), today)
    ok = [p for p in truth.tb["payments"] if p["status"] == "succeeded"
          and p["at"].date() >= today - timedelta(days=6)]  # fmt: skip
    if not ok:
        return None
    special = [p for p in ok if p["payment_id"] in truth.spec]
    p = rng.choice(special) if special and rng.random() < 0.5 else rng.choice(ok)
    res = reconcile(books, p["at"].date(), {}, today)
    item = next((i for i in res["items"] if i["payment_id"] == p["payment_id"]), None)
    got = item["status"] if item else None
    rec = truth.record(p)
    order_amount = truth.tb["orders"][p["order_id"]]["total_amount"] or p["amount"]
    payout = p["at"].date() + timedelta(days=base.PG_PAYOUT_DAYS)
    if rec is None:
        want = "missing_in_pg" if payout <= today else "pending"
    elif not rec["settled"]:
        want = "pending"
    elif rec["amount"] != order_amount:
        want = "mismatch"
    elif abs(rec["fee"] - base.half_up(rec["amount"], base.PG_RATE)) > base.PG_FEE_TOLERANCE:
        want = "fee_anomaly"
    else:
        want = "matched"
    return got, {"actual": want, "documented": want}, {"payment": p["payment_id"]}


async def case_settlement(rng, clock, today):
    monday = today - timedelta(days=today.weekday() + 7)
    w = World(rng, clock)
    w.traffic(monday - timedelta(days=4), monday + timedelta(days=8), rng.uniform(2, 8))
    books = await w.finance_books()
    res = statements(books, None, today)
    got = {r["seller_id"]: (r["gross"], r["commission"], r["seller_coupon"], r["refunds"],
                            r["payout"], r["status"]) for r in res["statements"]}  # fmt: skip
    truth = Truth(w.tables(), today)
    start, end = day0(monday), day0(monday + timedelta(days=7))
    sold = truth.sold(start, end)
    wants = {}
    for basis in ("actual", "documented"):
        refunds: dict[str, int] = defaultdict(int)
        for oid, amount in truth.refunds_in(start, end, basis).items():
            for s, share in truth.refund_split(oid, amount).items():
                refunds[s] += share
        want = {}
        sellers = sorted({i["seller_id"] for i in truth.tb["items"]} | set(refunds))
        for s in sellers:
            gross = com = 0
            ids, unconfirmed = set(), False
            for oid in sold:
                mine = [i for i in truth.items_of[oid] if i["seller_id"] == s]
                if not mine:
                    continue
                ids.add(oid)
                unconfirmed |= truth.tb["orders"][oid]["delivered_at"] is None
                for i in mine:
                    g = i["qty"] * i["unit_price"]
                    gross += g
                    com += base.half_up(g, base.rate_of(i["category_code"]))
            rf = refunds.get(s, 0)
            if not ids and not rf:
                continue
            payout = gross - com - rf
            held = unconfirmed or (bool(ids) and payout <= 0)
            want[s] = (gross, com, 0, rf, payout, "held" if held else "ready")
        wants[basis] = want
    return got, wants, {"week_monday": monday.isoformat()}


async def case_bank(rng, clock, today):
    w = World(rng, clock)
    w.traffic(today - timedelta(days=14), today, rng.uniform(2, 6))
    books = await w.finance_books()
    truth = Truth(w.tables(), today)
    expected: dict[str, int] = defaultdict(int)
    for p in truth.tb["payments"]:
        if p["status"] == "succeeded":
            rec = truth.record(p)
            if rec and rec["settled"]:
                expected[rec["payout"].isoformat()] += rec["amount"] - rec["fee"]
    lo = today - timedelta(days=rng.randint(4, 12))
    hi = today - timedelta(days=rng.randint(0, 2))
    lo_s, hi_s = lo.isoformat(), hi.isoformat()
    rows = []
    for d, amt in sorted(expected.items()):
        if not (lo_s <= d <= hi_s):
            continue
        r = rng.random()
        dep = amt if r < 0.55 else amt - rng.randint(1, max(1, amt // 10)) if r < 0.7 else (
            amt + rng.randint(1, 50_000) if r < 0.8 else None)  # fmt: skip
        if dep and dep > 0:
            if dep > 1 and rng.random() < 0.3:
                first = rng.randint(1, dep - 1)
                rows += [{"date": d, "amount": first, "memo": "토스페이먼츠"},
                         {"date": d, "amount": dep - first, "memo": "토스페이먼츠"}]  # fmt: skip
            else:
                rows.append({"date": d, "amount": dep, "memo": "토스페이먼츠"})
    span = (hi - lo).days
    for _ in range(rng.randint(0, 2)):
        d = (lo + timedelta(days=rng.randint(0, span))).isoformat()
        rows.append({"date": d, "amount": rng.randint(1_000, 300_000), "memo": "이자"})
    for _ in range(rng.randint(0, 3)):
        d = (lo + timedelta(days=rng.randint(0, span))).isoformat()
        rows.append({"date": d, "amount": -rng.randint(1_000, 900_000), "memo": "광고 네이버"})
    rows += [{"date": lo_s, "amount": -100, "memo": "수수료"},
             {"date": hi_s, "amount": -100, "memo": "수수료"}]  # fmt: skip
    res = bank_match(books, rows, today)
    got = {x["date"]: (x["status"], x["diff"]) for x in res["days"]}
    actual: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["amount"] > 0:
            actual[r["date"]] += r["amount"]
    want = {}
    for d in set(expected) | set(actual):
        exp, dep = expected.get(d), actual.get(d)
        if not (lo_s <= d <= hi_s):
            want[d] = ("not_covered", None)
        elif exp is None:
            want[d] = ("unexpected", dep)
        elif dep is None:
            want[d] = ("missing", -exp)
        elif dep == exp:
            want[d] = ("matched", 0)
        else:
            want[d] = ("short" if dep < exp else "over", dep - exp)
    return got, {"actual": want, "documented": want}, {"covered": [lo_s, hi_s]}


async def case_vat(rng, clock, today):
    q_end = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    prev = q_end - timedelta(days=1)
    q_start = date(prev.year, ((prev.month - 1) // 3) * 3 + 1, 1)
    w = World(rng, clock)
    w.traffic(q_start - timedelta(days=3), q_end + timedelta(days=3), rng.uniform(0.3, 1.5))
    books = await w.finance_books()
    res = vat_pack(books, q_start + timedelta(days=20))
    got = {"output_vat": res["output_vat"], "input_vat": res["input_vat"],
           "payable": res["payable"]}  # fmt: skip
    truth = Truth(w.tables(), today)
    c = truth.closing(day0(q_start), day0(q_end), "actual")
    out_vat = base.half_up(c["commission"], base.VAT_RATE)
    in_vat = base.half_up(c["pg_fee"], base.VAT_RATE)
    want = {"output_vat": out_vat, "input_vat": in_vat, "payable": out_vat - in_vat}
    return (
        got,
        {"actual": want, "documented": want},
        {
            "commission": c["commission"],
            "pg_fee": c["pg_fee"],
        },
    )


async def case_income_tax(rng, clock, today):
    r = rng.random()
    if r < 0.6:  # 구간마다 고르게
        lo, hi = rng.choice([(1, 2 * 10**8), (2 * 10**8, 2 * 10**10), (2 * 10**10, 3 * 10**11),
                             (3 * 10**11, 10**13)])  # fmt: skip
        income = rng.randint(lo, hi)
    elif r < 0.9:
        income = rng.choice([2 * 10**8, 2 * 10**10, 3 * 10**11]) + rng.randint(-1, 1)
    else:
        income = rng.randint(-(10**9), 0)
    got = income_tax_amount(income)
    tax, lower = Decimal(0), 0
    for upto, rate in base.INCOME_TAX:
        top = income if upto is None else upto
        if income > lower:
            tax += Decimal(min(income, top) - lower) * rate
        lower = top
        if upto is None or income <= upto:
            break
    want = int(tax) if income > 0 else 0
    return got, {"actual": want, "documented": want}, {"income": income}


CASES = {
    "closing": case_closing,
    "reconciliation": case_reconciliation,
    "settlement": case_settlement,
    "bank_deposit": case_bank,
    "vat": case_vat,
    "income_tax": case_income_tax,
}


async def main_async(args) -> dict[str, Any]:
    rng = random.Random(args.seed)
    today = fc.today()
    areas: dict[str, Any] = {}
    with shop_clock() as clock:
        for area, count in base.allocate(args.n, list(CASES)).items():
            stats = {b: 0 for b in ("actual", "documented")}
            wrong: list[dict[str, Any]] = []
            done = errors = 0
            while done < count:
                try:
                    out = await CASES[area](rng, clock, today)
                except Exception as exc:  # 코드가 깨지면 틀린 것으로 센다
                    errors += 1
                    done += 1
                    wrong.append({"error": f"{type(exc).__name__}: {exc}"})
                    continue
                if out is None:  # 고를 결제가 없는 세계는 다시 만든다
                    continue
                got, wants, info = out
                done += 1
                for b, want in wants.items():
                    stats[b] += got == want
                if got != wants["actual"]:
                    wrong.append({"got": got, "want": wants, "info": info})
            areas[area] = {
                "label": base.LABEL[area], "n": count, "errors": errors,
                "accuracy": {b: v / count for b, v in stats.items()},
                "correct": stats, "wrong": wrong,
            }  # fmt: skip
    return {"seed": args.seed, "n": args.n, "today": today.isoformat(), "areas": areas}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", default=HOLDOUT_SEED)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    result = asyncio.run(main_async(args))
    areas = result["areas"]
    total = sum(a["n"] for a in areas.values())
    print(f"재무 AI holdout  seed={args.seed}  표본 {total}개  기준일 {result['today']}")
    print(f"{'영역':<40}{'표본':>6}{'실제 기준':>12}{'문서 기준':>12}")
    sums = {"actual": 0, "documented": 0}
    for a in areas.values():
        for b in sums:
            sums[b] += a["correct"][b]
        print(f"{a['label']:<40}{a['n']:>6}{a['accuracy']['actual']:>12.1%}"
              f"{a['accuracy']['documented']:>12.1%}")  # fmt: skip
    print(
        f"{'전체':<40}{total:>6}{sums['actual'] / total:>12.1%}{sums['documented'] / total:>12.1%}"
    )
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
