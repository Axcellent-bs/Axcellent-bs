"""재무 AI 계산 정확도 평가 (정답 계산·무작위 장부). 평가 CLI `eval/run.py`·자기 테스트가 쓴다.

- 무작위 장부를 만들고, 재무의 **실제 계산 코드**(결산·PG 대사·판매자 정산·은행 입금 대조·
  부가세·법인세·캠페인 예산 의견)로 돌린 값을 **정답**과 비교한다.
- 정답은 이 파일 안에서 정책(`finance_policy.toml`)·규칙(`finance_rules.toml`)과 문서(각 모듈
  docstring)대로 따로 계산한다. 계산 모듈의 함수를 정답 계산에 쓰지 않는다.
  - 원 단위 반올림은 정책대로 0.5 올림(`Decimal` ROUND_HALF_UP). 법인세는 원 미만 버림.
- LLM이 쓰는 대화·설명 문장은 재지 않는다(계산 숫자·판단만 잰다).
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from finance_agent import campaign as cp
from finance_agent import finance_config as fc
from finance_agent.bank import match as bank_match
from finance_agent.books import Books
from finance_agent.closing import compute_totals
from finance_agent.reconciliation import reconcile
from finance_agent.settlement import statements
from finance_agent.tax import income_tax_amount, vat_pack

# ---------------------------------------- 정책 (정답 계산용, 코드와 따로 적는다)
COMMISSION = {"book": Decimal("0.10"), "stationery": Decimal("0.08")}
COMMISSION_DEFAULT = Decimal("0.10")
PG_RATE = Decimal("0.031")
PG_PAYOUT_DAYS = 2
PG_FEE_TOLERANCE = 1
VAT_RATE = Decimal("0.1")
INCOME_TAX = [  # 법인세법 제55조 ① (2026-01-01 이후 개시 사업연도)
    (200_000_000, Decimal("0.10")),
    (20_000_000_000, Decimal("0.20")),
    (300_000_000_000, Decimal("0.22")),
    (None, Decimal("0.25")),
]
CATEGORIES = ["book", "stationery", "toy", None]

SHARES = [
    ("closing", 0.18),
    ("reconciliation", 0.17),
    ("settlement", 0.13),
    ("bank_deposit", 0.13),
    ("vat", 0.13),
    ("income_tax", 0.13),
    ("campaign_opinion", 0.13),
]
# 캠페인 예산 의견 규칙 (finance_rules.toml [brake]·[lessons], 정답 계산용으로 따로 적는다)
BRAKE_MIN_VIABLE = 50_000
BRAKE_LOSS_LIMIT = 500_000
BRAKE_LOSS_STREAK = 2
LESSON_FACTOR, LESSON_FLOOR = Decimal("0.5"), Decimal("0.25")
LABEL = {
    "closing": "결산 (총거래액·수수료·쿠폰·환불·PG 수수료·영업 결과)",
    "reconciliation": "PG 대사 상태 분류",
    "settlement": "판매자 정산금·보류",
    "bank_deposit": "은행 입금 대조",
    "vat": "부가세 신고 보조 (매출·매입 세액, 낼 세액)",
    "income_tax": "법인세 누진 계산",
    "campaign_opinion": "캠페인 예산 의견 (권장·줄여서 권장·권하지 않음, 권장 금액)",
}


def half_up(amount: int, rate: Decimal) -> int:
    return int((Decimal(amount) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def rate_of(category: str | None) -> Decimal:
    return COMMISSION.get(category or "", COMMISSION_DEFAULT)


# ---------------------------------------------------------------- 무작위 장부
class Gen:
    def __init__(self, rng: random.Random, today: date) -> None:
        self.rng = rng
        self.today = today
        self.n = 0

    def uid(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_{self.n}"

    def price(self) -> int:
        r = self.rng.random()
        if r < 0.5:
            return self.rng.randrange(1_000, 60_000, 100)
        if r < 0.85:
            return self.rng.randint(1_000, 99_999)  # 끝자리가 있는 값 (반올림 경계)
        return self.rng.randrange(100_000, 2_000_000, 10)

    def at(self, d: date) -> datetime:
        return datetime.combine(d, time.min, tzinfo=UTC) + timedelta(
            seconds=self.rng.randrange(0, 86_400)
        )

    def order(
        self, d: date, sellers: list[str], *, delivered: bool | None = None
    ) -> dict[str, Any]:
        at = self.at(d)
        items = []
        for _ in range(self.rng.randint(1, 3)):
            items.append(
                {
                    "product_id": self.uid("prd"),
                    "seller_id": self.rng.choice(sellers),
                    "category_code": self.rng.choice(CATEGORIES),
                    "qty": self.rng.randint(1, 5),
                    "unit_price": self.price(),
                    "points_share": 0,
                }
            )
        total = sum(i["qty"] * i["unit_price"] for i in items)
        for i in items:
            i["paid_amount"] = i["qty"] * i["unit_price"]
        payments = []
        kind = self.rng.random()
        if kind < 0.08:  # 결제 실패만 (팔리지 않은 주문)
            payments.append(
                {
                    "payment_id": self.uid("pay"),
                    "amount": total,
                    "status": "failed",
                    "created_at": at,
                }
            )
        elif kind < 0.18:  # 실패 뒤 성공 (첫 성공 시각이 판매 시각)
            payments.append(
                {
                    "payment_id": self.uid("pay"),
                    "amount": total,
                    "status": "failed",
                    "created_at": at,
                }
            )
            payments.append(
                {
                    "payment_id": self.uid("pay"),
                    "amount": total,
                    "status": "succeeded",
                    "created_at": at + timedelta(minutes=self.rng.randint(1, 90)),
                }
            )
        else:
            payments.append(
                {
                    "payment_id": self.uid("pay"),
                    "amount": total,
                    "status": "succeeded",
                    "created_at": at,
                }
            )
        if delivered is None:
            delivered = self.rng.random() < 0.8
        return {
            "order_id": self.uid("ord"),
            "buyer_id": self.uid("buyer"),
            "status": "paid",
            "total_amount": total,
            "refunded_amount": 0,
            "shipping_fee": 0,
            "points_used": 0,
            "created_at": at,
            "delivered_at": at + timedelta(days=1) if delivered else None,
            "items": items,
            "payments": payments,
        }

    def overrides(self, orders: list[dict[str, Any]], p: float = 0.3) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for o in orders:
            for pay in o["payments"]:
                if pay["status"] != "succeeded" or self.rng.random() > p:
                    continue
                kind = self.rng.choice(["mismatch", "missing", "fee"])
                if kind == "mismatch":
                    out[pay["payment_id"]] = {
                        "kind": "mismatch",
                        "delta": self.rng.choice([-1, 1]) * self.rng.randint(1, 5_000),
                    }
                elif kind == "missing":
                    out[pay["payment_id"]] = {"kind": "missing"}
                else:
                    out[pay["payment_id"]] = {
                        "kind": "fee",
                        "fee_rate": self.rng.choice([0.029, 0.0305, 0.0312, 0.033, 0.035, 0.04]),
                    }
        return out


def paid_at(o: dict[str, Any]) -> datetime | None:
    times = [p["created_at"] for p in o["payments"] if p["status"] == "succeeded"]
    return min(times) if times else None


def inside(dt: datetime | None, start: datetime, end: datetime) -> bool:
    return dt is not None and start <= dt < end


def day0(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


def pg_record(pay: dict[str, Any], spec: dict[str, Any], today: date) -> dict[str, Any] | None:
    """정답용 PG 정산 기록 (토스 형식 목의 문서대로).

    지급 예정일 = 결제일 + 2일, 그날부터 정산됨.
    """
    payout = pay["created_at"].date() + timedelta(days=PG_PAYOUT_DAYS)
    settled = payout <= today
    spec = spec if settled else {}
    if spec.get("kind") == "missing":
        return None
    amount = (
        pay["amount"] + spec.get("delta", 0) if spec.get("kind") == "mismatch" else pay["amount"]
    )
    rate = Decimal(str(spec["fee_rate"])) if spec.get("kind") == "fee" else PG_RATE
    fee = half_up(amount, rate)
    return {"amount": amount, "fee": fee, "payout": payout, "settled": settled}


def make_books(orders, *, overrides=None, refunds=None, coupons=None, sellers=None) -> Books:
    return Books(
        orders=orders,
        sellers=[{"seller_id": s, "name": s} for s in (sellers or [])],
        refunds=refunds or [],
        coupons=coupons or [],
        executions=[],
        pg_overrides={
            k: (
                {"kind": "mismatch", "delta": v["delta"]}
                if v["kind"] == "mismatch"
                else {"kind": "missing"}
                if v["kind"] == "missing"
                else {"fee_rate": v["fee_rate"]}
            )
            for k, v in (overrides or {}).items()
        },
        mock=True,
        source="seed",
    )


# ---------------------------------------------------------------- 영역별 케이스
def case_closing(g: Gen) -> tuple[Any, Any, dict[str, Any]]:
    d = g.today - timedelta(days=g.rng.randint(1, 40))
    start, end = day0(d), day0(d + timedelta(days=1))
    sellers = [g.uid("sel") for _ in range(g.rng.randint(1, 3))]
    orders = [
        g.order(d + timedelta(days=g.rng.choice([-1, 0, 0, 0, 1])), sellers)
        for _ in range(g.rng.randint(1, 8))
    ]
    ov = g.overrides(orders)
    refunds = [
        {
            "refund_id": g.uid("rfd"),
            "order_id": o["order_id"],
            "seller_id": o["items"][0]["seller_id"],
            "amount": g.rng.randint(1, o["total_amount"]),
            "created_at": g.at(d + timedelta(days=g.rng.choice([-1, 0, 0, 1]))),
        }
        for o in orders
        if g.rng.random() < 0.3
    ]
    coupons = [
        {
            "coupon_id": g.uid("cpn"),
            "funded_by": g.rng.choice(["company", "company", "seller"]),
            "seller_id": g.rng.choice(sellers),
            "total_value": g.rng.randint(1, 30) * 1_000,
            "created_at": g.at(d + timedelta(days=g.rng.choice([-1, 0, 0, 1]))),
        }
        for _ in range(g.rng.randint(0, 3))
    ]
    books = make_books(orders, overrides=ov, refunds=refunds, coupons=coupons, sellers=sellers)
    t = compute_totals(books, start, end)
    got = {
        "gmv": t.gmv,
        "commission": t.commission_revenue,
        "coupon": t.coupon_cost,
        "refund": t.refund_total,
        "pg_fee": t.pg_fee,
        "net": t.net_result,
        "orders": t.order_count,
    }
    # 정답
    gmv = com = fee = n = 0
    for o in orders:
        if not inside(paid_at(o), start, end):
            continue
        n += 1
        for i in o["items"]:
            gross = i["qty"] * i["unit_price"]
            gmv += gross
            com += half_up(gross, rate_of(i["category_code"]))
        for p in o["payments"]:
            if p["status"] != "succeeded":
                continue
            rec = pg_record(p, ov.get(p["payment_id"], {}), fc.today())
            fee += rec["fee"] if rec else half_up(p["amount"], PG_RATE)
    coupon = sum(
        c["total_value"]
        for c in coupons
        if c["funded_by"] == "company" and inside(c["created_at"], start, end)
    )
    refund = sum(r["amount"] for r in refunds if inside(r["created_at"], start, end))
    want = {
        "gmv": gmv,
        "commission": com,
        "coupon": coupon,
        "refund": refund,
        "pg_fee": fee,
        "net": com - coupon - fee,
        "orders": n,
    }
    return got, want, {"day": d.isoformat(), "orders": len(orders)}


def case_reconciliation(g: Gen) -> tuple[Any, Any, dict[str, Any]]:
    today = g.today
    d = today - timedelta(days=g.rng.randint(0, 6))
    o = g.order(d, ["sel_r"])
    pays = [p for p in o["payments"] if p["status"] == "succeeded"]
    if not pays:  # 실패만 있는 주문은 대사 대상이 아니다 → 성공 결제로 바꾼다
        o["payments"][-1]["status"] = "succeeded"
        pays = [o["payments"][-1]]
    pay = pays[0]
    r = g.rng.random()
    spec: dict[str, Any] = {}
    if r < 0.2:
        spec = {"kind": "mismatch", "delta": g.rng.choice([-1, 1]) * g.rng.randint(1, 5_000)}
    elif r < 0.4:
        spec = {"kind": "missing"}
    elif r < 0.6:
        spec = {
            "kind": "fee",
            "fee_rate": g.rng.choice([0.0305, 0.0309, 0.0311, 0.0315, 0.033, 0.04]),
        }
    ov = {pay["payment_id"]: spec} if spec else {}
    books = make_books([o], overrides=ov, sellers=["sel_r"])
    res = reconcile(books, pay["created_at"].date(), {}, today)
    item = next((i for i in res["items"] if i["payment_id"] == pay["payment_id"]), None)
    got = item["status"] if item else None
    # 정답 (문서의 순서대로)
    rec = pg_record(pay, spec, today)
    payout = pay["created_at"].date() + timedelta(days=PG_PAYOUT_DAYS)
    if rec is None:
        want = "missing_in_pg" if payout <= today else "pending"
    elif not rec["settled"]:
        want = "pending"
    elif rec["amount"] != o["total_amount"]:
        want = "mismatch"
    elif abs(rec["fee"] - half_up(rec["amount"], PG_RATE)) > PG_FEE_TOLERANCE:
        want = "fee_anomaly"
    else:
        want = "matched"
    return got, want, {"paid": pay["created_at"].isoformat(), "amount": pay["amount"], "spec": spec}


def case_settlement(g: Gen) -> tuple[Any, Any, dict[str, Any]]:
    today = g.today
    monday = today - timedelta(days=today.weekday() + 7)
    start, end = day0(monday), day0(monday + timedelta(days=7))
    sellers = [g.uid("sel") for _ in range(g.rng.randint(1, 4))]
    orders = [
        g.order(
            monday + timedelta(days=g.rng.randint(-2, 8)), sellers, delivered=g.rng.random() < 0.85
        )
        for _ in range(g.rng.randint(1, 8))
    ]
    refunds = []
    for o in orders:
        if g.rng.random() < 0.35:
            item = g.rng.choice(o["items"])
            big = g.rng.random() < 0.15  # 정산금이 0원 이하가 되는 큰 환불
            refunds.append(
                {
                    "refund_id": g.uid("rfd"),
                    "order_id": o["order_id"],
                    "seller_id": item["seller_id"],
                    "amount": o["total_amount"] * 2 if big else g.rng.randint(1, o["total_amount"]),
                    "created_at": g.at(monday + timedelta(days=g.rng.randint(-1, 7))),
                }
            )
    coupons = [
        {
            "coupon_id": g.uid("cpn"),
            "funded_by": g.rng.choice(["seller", "company"]),
            "seller_id": g.rng.choice(sellers),
            "total_value": g.rng.randint(1, 20) * 1_000,
            "created_at": g.at(monday + timedelta(days=g.rng.randint(-1, 7))),
        }
        for _ in range(g.rng.randint(0, 3))
    ]
    books = make_books(orders, refunds=refunds, coupons=coupons, sellers=sellers)
    res = statements(books, None, today)
    got = {
        r["seller_id"]: (
            r["gross"],
            r["commission"],
            r["seller_coupon"],
            r["refunds"],
            r["payout"],
            r["status"],
        )
        for r in res["statements"]
    }
    want = {}
    sold = [o for o in orders if inside(paid_at(o), start, end)]
    for s in sellers:
        gross = com = 0
        ids: set[str] = set()
        unconfirmed = False
        for o in sold:
            mine = [i for i in o["items"] if i["seller_id"] == s]
            if not mine:
                continue
            ids.add(o["order_id"])
            unconfirmed |= o["delivered_at"] is None
            for i in mine:
                gr = i["qty"] * i["unit_price"]
                gross += gr
                com += half_up(gr, rate_of(i["category_code"]))
        cp = sum(
            c["total_value"]
            for c in coupons
            if c["funded_by"] == "seller"
            and c["seller_id"] == s
            and inside(c["created_at"], start, end)
        )
        rf = sum(
            r["amount"]
            for r in refunds
            if r["seller_id"] == s and inside(r["created_at"], start, end)
        )
        if not ids and not cp and not rf:
            continue
        payout = gross - com - cp - rf
        held = unconfirmed or (bool(ids) and payout <= 0)
        want[s] = (gross, com, cp, rf, payout, "held" if held else "ready")
    return got, want, {"week_monday": monday.isoformat(), "sellers": len(sellers)}


def case_bank(g: Gen) -> tuple[Any, Any, dict[str, Any]]:
    today = g.today
    orders = [
        g.order(today - timedelta(days=g.rng.randint(0, 12)), ["sel_b"])
        for _ in range(g.rng.randint(2, 12))
    ]
    ov = g.overrides(orders, p=0.15)
    books = make_books(orders, overrides=ov, sellers=["sel_b"])
    expected: dict[str, int] = defaultdict(int)
    for o in orders:
        for p in o["payments"]:
            if p["status"] != "succeeded":
                continue
            rec = pg_record(p, ov.get(p["payment_id"], {}), today)
            if rec and rec["settled"]:
                expected[rec["payout"].isoformat()] += rec["amount"] - rec["fee"]
    lo = today - timedelta(days=g.rng.randint(3, 12))
    hi = today - timedelta(days=g.rng.randint(0, 2))
    rows = []
    for d, amt in sorted(expected.items()):
        if not (lo.isoformat() <= d <= hi.isoformat()):
            continue  # 거래내역 파일은 자기 기간 안 줄만 담는다
        r = g.rng.random()
        if r < 0.55:
            dep = amt
        elif r < 0.7:
            dep = amt - g.rng.randint(1, max(1, amt // 10))
        elif r < 0.8:
            dep = amt + g.rng.randint(1, 50_000)
        else:
            dep = None  # 입금 없음
        if dep is not None and dep > 0:
            parts = g.rng.randint(1, 2)  # 같은 날 두 번에 나눠 들어오기도 한다
            first = dep if parts == 1 else g.rng.randint(1, dep - 1) if dep > 1 else dep
            rows.append({"date": d, "amount": first, "memo": "PG정산"})
            if parts == 2 and dep - first > 0:
                rows.append({"date": d, "amount": dep - first, "memo": "PG정산"})
    for _ in range(g.rng.randint(0, 2)):  # 예상 없는 입금
        d = (lo + timedelta(days=g.rng.randint(0, (hi - lo).days))).isoformat()
        rows.append({"date": d, "amount": g.rng.randint(1_000, 300_000), "memo": "기타입금"})
    for _ in range(g.rng.randint(0, 3)):  # 출금 (입금 대조에서 빠져야 한다)
        d = (lo + timedelta(days=g.rng.randint(0, (hi - lo).days))).isoformat()
        rows.append({"date": d, "amount": -g.rng.randint(1_000, 500_000), "memo": "급여"})
    rows.append({"date": lo.isoformat(), "amount": -1, "memo": "기간 시작 표시"})
    rows.append({"date": hi.isoformat(), "amount": -1, "memo": "기간 끝 표시"})
    res = bank_match(books, rows, today)
    got = {x["date"]: (x["status"], x["diff"]) for x in res["days"]}
    actual: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["amount"] > 0:
            actual[r["date"]] += r["amount"]
    want = {}
    lo_s, hi_s = lo.isoformat(), hi.isoformat()
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
    return got, want, {"covered": [lo_s, hi_s], "days": len(want)}


def case_vat(g: Gen) -> tuple[Any, Any, dict[str, Any]]:
    today = g.today
    q_end = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)  # 이번 분기 첫날
    prev = q_end - timedelta(days=1)
    q_start = date(prev.year, ((prev.month - 1) // 3) * 3 + 1, 1)  # 지난 분기 첫날
    days = (q_end - q_start).days
    sellers = [g.uid("sel") for _ in range(g.rng.randint(1, 3))]
    orders = [
        g.order(q_start + timedelta(days=g.rng.randint(-3, days + 2)), sellers)
        for _ in range(g.rng.randint(1, 12))
    ]
    coupons = [
        {
            "coupon_id": g.uid("cpn"),
            "funded_by": "company",
            "seller_id": sellers[0],
            "total_value": g.rng.randint(1, 50) * 1_000,
            "created_at": g.at(q_start + timedelta(days=g.rng.randint(0, days - 1))),
        }
        for _ in range(g.rng.randint(0, 2))
    ]
    books = make_books(orders, coupons=coupons, sellers=sellers)
    res = vat_pack(books, q_start + timedelta(days=10))
    got = {
        "output_vat": res["output_vat"],
        "input_vat": res["input_vat"],
        "payable": res["payable"],
    }
    start, end = day0(q_start), day0(q_end)
    com = fee = 0
    for o in orders:
        if not inside(paid_at(o), start, end):
            continue
        for i in o["items"]:
            com += half_up(i["qty"] * i["unit_price"], rate_of(i["category_code"]))
        for p in o["payments"]:
            if p["status"] == "succeeded":
                rec = pg_record(p, {}, fc.today())
                fee += rec["fee"] if rec else half_up(p["amount"], PG_RATE)
    out_vat, in_vat = half_up(com, VAT_RATE), half_up(fee, VAT_RATE)  # 부가세 별도, 쿠폰 공제 안 함
    want = {"output_vat": out_vat, "input_vat": in_vat, "payable": out_vat - in_vat}
    return got, want, {"quarter_start": q_start.isoformat(), "commission": com, "pg_fee": fee}


def case_income_tax(g: Gen) -> tuple[Any, Any, dict[str, Any]]:
    r = g.rng.random()
    if r < 0.2:  # 구간 경계 ±2원
        edge = g.rng.choice([200_000_000, 20_000_000_000, 300_000_000_000])
        base = edge + g.rng.randint(-2, 2)
    elif r < 0.25:
        base = g.rng.randint(-1_000_000, 0)
    else:
        base = int(10 ** g.rng.uniform(4, 12))
    got = income_tax_amount(base)
    tax, lower = Decimal(0), 0
    for upto, rate in INCOME_TAX:
        top = base if upto is None else upto
        if base > lower:
            tax += (Decimal(min(base, top) - lower)) * rate
        lower = top
        if upto is None or base <= upto:
            break
    want = int(tax.quantize(Decimal("1"), rounding=ROUND_FLOOR)) if base > 0 else 0
    return got, want, {"base": base}


def case_campaign_opinion(g: Gen) -> tuple[Any, Any, dict[str, Any]]:
    """캠페인 예산 의견 (`campaign.decide`): 연속 적자·이달 손실·현금 흐름·남은 예산·실패 교훈."""
    rng, today = g.rng, g.today
    month = today.isoformat()[:7]
    last_month = (today.replace(day=1) - timedelta(days=1)).isoformat()[:7]
    channels = ["naver", "meta", "kakao"]
    channel = rng.choice(channels)
    campaigns: dict[str, dict[str, Any]] = {}
    # 겹치지 않는 날짜 (연속 적자 순서가 분명하게)
    for k, back in enumerate(rng.sample(range(1, 70), rng.randint(0, 7))):
        cid = g.uid("cmp")
        campaigns[cid] = {
            "campaign_id": cid,
            "campaign_name": f"캠페인 {k}",
            "channel": rng.choice(channels) if rng.random() < 0.4 else channel,
            "period_to": (today - timedelta(days=back)).isoformat(),
            "spend_krw": rng.randrange(50_000, 1_500_000, 10_000),
            "profit_krw": rng.randint(-600_000, 600_000),
            "verdict": None if rng.random() < 0.1 else rng.choice(["good", "neutral", "bad"]),
        }
    started: dict[str, dict[str, Any]] | None = None if rng.random() < 0.07 else {}
    if started is not None:
        for _ in range(rng.randint(0, 2)):
            started[g.uid("cmp")] = {
                "month": month if rng.random() < 0.8 else last_month,
                "active_krw": rng.randrange(10_000, 600_000, 10_000),
            }
    lessons = [
        {
            "channel": rng.choice(channels) if rng.random() < 0.3 else channel,
            "status": rng.choice(["open", "open", "closed"]),
            "spend_krw": rng.randrange(50_000, 2_000_000, 10_000),
            "campaign_name": f"실패 {k}",
        }
        for k in range(rng.randint(0, 3))
    ]
    r = rng.random()
    cash = None if r < 0.12 else -rng.randint(0, 500_000) if r < 0.25 else rng.randint(1, 3_000_000)
    budget = {
        "amount_krw": rng.randrange(0, 3_000_000, 10_000),
        "basis": "평가",
        "provisional": False,
    }
    again = campaigns and rng.random() < 0.15  # 같은 캠페인을 다시 묻는 경우
    request = {
        "campaign_id": rng.choice(list(campaigns)) if again else g.uid("cmp"),
        "campaign_name": "새 캠페인",
        "channel": channel,
        "requested_krw": rng.randrange(10_000, 2_000_000, 1_000),
    }
    out = cp.decide(
        request,
        campaigns=campaigns,
        reviews=[],
        lessons=lessons,
        cash_net_krw=cash,
        today=today,
        monthly_budget=budget,
        started=started,
    )
    got = (out["decision"], out["approved_limit_krw"])

    # 정답 (규칙 문서대로)
    requested = limit = request["requested_krw"]
    rejects = 0
    rows = sorted(
        (c for c in campaigns.values() if c["channel"] == channel and c["verdict"] is not None),
        key=lambda c: c["period_to"],
        reverse=True,
    )
    streak = 0
    for c in rows:
        if c["profit_krw"] >= 0:
            break
        streak += 1
    rejects += streak >= BRAKE_LOSS_STREAK
    loss = sum(
        -c["profit_krw"]
        for c in campaigns.values()
        if c["verdict"] and c["period_to"][:7] == month and c["profit_krw"] < 0
    )
    rejects += loss >= BRAKE_LOSS_LIMIT
    if cash is None:
        limit = min(limit, BRAKE_MIN_VIABLE)
    elif cash <= 0:
        rejects += 1
    else:
        limit = min(limit, cash)
    committed = sum(
        c["spend_krw"]
        for c in campaigns.values()
        if c["period_to"][:7] == month and c["campaign_id"] != request["campaign_id"]
    )
    for cid, row in (started or {}).items():
        if cid != request["campaign_id"] and cid not in campaigns and row["month"] == month:
            committed += row["active_krw"]
    remaining = max(budget["amount_krw"] - committed, 0)
    rejects += started is None
    if remaining < BRAKE_MIN_VIABLE:
        rejects += 1
    else:
        limit = min(limit, remaining)
    open_lessons = [x for x in lessons if x["channel"] == channel and x["status"] == "open"]
    if open_lessons:
        factor = max(LESSON_FACTOR ** len(open_lessons), LESSON_FLOOR)
        limit = min(limit, half_up(max(x["spend_krw"] for x in open_lessons), factor))
    if not rejects and limit < BRAKE_MIN_VIABLE:
        rejects += 1
    if rejects:
        want = ("rejected", 0)
    elif limit < requested:
        want = ("conditional", limit)
    else:
        want = ("approved", requested)
    return got, want, {"request": request, "cash": cash, "budget": budget["amount_krw"]}


CASES = {
    "closing": case_closing,
    "reconciliation": case_reconciliation,
    "settlement": case_settlement,
    "bank_deposit": case_bank,
    "vat": case_vat,
    "income_tax": case_income_tax,
    "campaign_opinion": case_campaign_opinion,
}


def allocate(n: int, keys: list[str] | None = None) -> dict[str, int]:
    """표본 n개를 영역에 나눈다. `keys`를 주면 그 영역만 비율을 다시 맞춰 나눈다."""
    shares = [(k, s) for k, s in SHARES if keys is None or k in keys]
    total = sum(s for _, s in shares)
    counts = {k: int(n * s / total) for k, s in shares}
    for k, _ in shares[: n - sum(counts.values())]:
        counts[k] += 1
    return counts


def evaluate(n: int, seed: int | str, today: date | None = None) -> dict[str, Any]:
    """표본 n개로 잰다. 영역별 정확도와 틀린 케이스(받은 값·정답·조건)를 돌려준다."""
    g = Gen(random.Random(seed), today or fc.today())
    results: dict[str, Any] = {}
    total_ok = total = 0
    for area, count in allocate(n).items():
        ok, wrong, errors = 0, [], 0
        for k in range(count):
            try:
                got, want, info = CASES[area](g)
            except Exception as exc:  # 코드가 깨져도 틀린 것으로 센다
                errors += 1
                wrong.append({"case": k, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if got == want:
                ok += 1
            else:
                wrong.append({"case": k, "got": got, "want": want, "info": info})
        results[area] = {
            "label": LABEL[area],
            "n": count,
            "correct": ok,
            "accuracy": ok / count if count else None,
            "errors": errors,
            "wrong": wrong,
        }
        total_ok += ok
        total += count
    return {
        "seed": seed,
        "n": total,
        "correct": total_ok,
        "accuracy": total_ok / total if total else None,
        "today": g.today.isoformat(),
        "areas": results,
    }
