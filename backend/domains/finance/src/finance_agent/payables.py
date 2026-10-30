"""③ 판매자 미지급금(판매자 예수금) 롤포워드: 기초 + 발생 − 지급 = 기말.

판매자에게 줄 돈이 장부와 맞는지 보는 마감 통제다. 숫자는 정산 공식과 같은 방법으로 코드가 계산한다.
- 발생: 그 달에 결제된 주문의 판매자 몫 − 판매자 부담 쿠폰 − 환불 (판매자 정산 내역서와 같은 공식)
- 지급: 정산 묶음이 안전 장치에서 승인된 금액.
  승인 시각(`approved_at`, 없으면 요청 시각)이 그 달인 것.
  실제 은행 이체 결과는 연동돼 있지 않아 "승인 = 지급"으로 본다
- 기초: 그 달 이전의 발생 합 − 지급 합 (장부 처음부터 누적)
- 판매자별 기말 잔액도 같이 보여 준다. 기말이 음수(더 준 돈)인 판매자는 확인 대상이다
- 판매자별 지급액이 없는 예전 묶음은 판매자에게 나누지 않고 "판매자 미배분 지급"으로 따로 센다
- 주문 자료를 못 받았으면 발생·기말은 `None`이다(0으로 두지 않는다)
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from finance_agent import closing
from finance_agent import finance_config as fc
from finance_agent.books import Books, in_range, item_gross, order_sold

UNALLOCATED = "_unallocated"


def _accrual(books: Books, start: datetime | None, end: datetime) -> dict[str, int]:
    """판매자별 발생액. `start`가 None이면 장부 처음부터."""
    cfg = fc.load_config()
    lo = start or datetime.min.replace(tzinfo=end.tzinfo)
    out: dict[str, int] = defaultdict(int)
    for o in books.orders:
        if not order_sold(o):
            continue
        paid = [p["created_at"] for p in o["payments"] if p.get("status") == "succeeded"]
        if not paid or not in_range(min(paid), lo, end):
            continue
        for item in o["items"]:
            seller = item.get("seller_id")
            if not seller:
                continue
            gross = item_gross(item)
            rate = cfg.commission.rate_for(item.get("category_code"))
            out[seller] += gross - fc.rate_amount(gross, rate)
    for c in books.coupons:
        if (
            c.get("funded_by") == "seller"
            and c.get("seller_id")
            and in_range(c.get("created_at"), lo, end)
        ):
            out[c["seller_id"]] -= int(c.get("total_value") or 0)
    for r in books.refunds:
        if r.get("seller_id") and in_range(r.get("created_at"), lo, end):
            out[r["seller_id"]] -= int(r["amount"])
    return dict(out)


def _paid(
    batches: dict[str, dict[str, Any]], start: datetime | None, end: datetime
) -> dict[str, int]:
    """판매자별 지급액(승인된 정산 묶음)."""
    lo = start or datetime.min.replace(tzinfo=end.tzinfo)
    out: dict[str, int] = defaultdict(int)
    for b in batches.values():
        if b.get("status") != "approved":
            continue
        at = fc.parse_dt(b.get("approved_at") or b.get("requested_at"))
        if not in_range(at, lo, end):
            continue
        payouts = b.get("payouts")
        if isinstance(payouts, dict) and payouts:
            for seller, amount in payouts.items():
                out[seller] += int(amount)
        else:
            out[UNALLOCATED] += int(b.get("total_payout") or 0)
    return dict(out)


def rollforward(
    books: Books, batches: dict[str, dict[str, Any]], d: date | None = None
) -> dict[str, Any]:
    d = d or fc.today()
    start_d, end_d = fc.month_range(d)
    start, end = fc.day_start(start_d), fc.day_start(end_d)
    orders_known = "gmv" not in closing.unknown_codes(books)
    coupons_known = "coupon_cost" not in closing.unknown_codes(books)
    known = orders_known and coupons_known

    acc_before = _accrual(books, None, start)
    acc_month = _accrual(books, start, end)
    paid_before = _paid(batches, None, start)
    paid_month = _paid(batches, start, end)

    sellers = sorted(set(acc_before) | set(acc_month) | set(paid_before) | set(paid_month))
    rows = []
    for s in sellers:
        opening = acc_before.get(s, 0) - paid_before.get(s, 0)
        accrued = acc_month.get(s, 0)
        paid = paid_month.get(s, 0)
        closing_bal = opening + accrued - paid
        rows.append(
            {
                "seller_id": s,
                "name": "판매자 미배분 지급" if s == UNALLOCATED else books.seller_name(s),
                "opening": opening if known else None,
                "accrued": accrued if known else None,
                "paid": paid,
                "closing": closing_bal if known else None,
                "overpaid": known and closing_bal < 0 and s != UNALLOCATED,
            }
        )
    rows.sort(key=lambda r: -(r["closing"] or 0))

    def total(key: str) -> int | None:
        if key != "paid" and not known:
            return None
        return sum(r[key] or 0 for r in rows)

    totals = {k: total(k) for k in ("opening", "accrued", "paid", "closing")}
    balanced = (
        None
        if not known
        else totals["opening"] + totals["accrued"] - totals["paid"] == totals["closing"]  # type: ignore[operator]
    )
    return {
        "month": start_d.isoformat()[:7],
        "from": fc.iso(start),
        "to": fc.iso(end),
        "totals": totals,
        "balanced": balanced,
        "overpaid": [r["seller_id"] for r in rows if r["overpaid"]],
        "sellers": rows,
        "basis": "지급 = 안전 장치가 승인한 정산 묶음 (실제 이체 결과 미연동)",
    }
