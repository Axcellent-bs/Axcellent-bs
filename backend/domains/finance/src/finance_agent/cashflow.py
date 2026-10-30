"""③ 13주 현금 흐름 예측: 앞으로 13주 동안 들어올 돈(PG 정산)과 나갈 돈(판매자 정산)을 주별로 본다.

정산 기한이 짧아지면(온라인플랫폼 정산 법안) 판매자에게 줄 돈이 PG 정산금보다 먼저 나갈 수 있다.
그 주가 언제인지 미리 보는 표다. 숫자는 두 종류로 나눠 표시한다.
- 확정(`scheduled`): 장부에 날짜가 정해진 돈
  · 들어옴: PG 정산 지급 예정(결제일 + `pg.payout_days`)인데 아직 안 들어온 것
  · 나감: 판매자 정산 내역서의 지급 예정일(보류 없는 판매자). 승인된 묶음은 이미 나간 것으로 본다
- 추정(`estimated`): 확정된 돈이 없는 뒤쪽 주는 최근 `cashflow.trailing_weeks`주
  (장부가 온전히 덮는 주만) 평균으로 채운다. 화면에 "추정"으로 표시하고,
  평균을 낼 주가 없으면 비운다(None)
- 회사 순현금 = 들어옴 − 나감. 보통 회사 몫(수수료)만큼 플러스이지만
  지급이 몰리는 주는 마이너스일 수 있다
- 은행 잔액은 모른다(연동 없음). 그래서 "누적 순현금"은 오늘을 0으로 둔 상대값이다
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from finance_agent import finance_config as fc
from finance_agent import settlement
from finance_agent.books import Books, pg_settlements

WEEKS = 13


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def forecast(
    books: Books, batches: dict[str, dict[str, Any]], today: date | None = None
) -> dict[str, Any]:
    cfg = fc.load_config()
    today = today or fc.today()
    first = _monday(today)
    weeks = [first + timedelta(days=7 * k) for k in range(WEEKS)]
    inflow = {w: 0 for w in weeks}
    outflow = {w: 0 for w in weeks}
    orders_known = "orders" not in books.unavailable

    # 들어옴: 아직 안 들어온 PG 정산
    for rec in pg_settlements(books, today).values():
        pay = rec.get("paid_out_date")
        if not pay or rec.get("settled"):
            continue
        d = date.fromisoformat(str(pay)[:10])
        w = _monday(d)
        if w in inflow:
            inflow[w] += int(rec["pay_out_amount"])

    # 나감: 판매자 정산 지급 예정 (지난주·이번 주 정산서, 승인된 묶음은 이미 나감)
    for ref in (today, today + timedelta(days=7)):
        name, _, _ = fc.week_range(None, ref)
        st = settlement.statements(books, name, today=today)
        batch = batches.get(st["period"]) or {}
        paid = set(batch.get("seller_ids") or []) if batch.get("status") == "approved" else set()
        pay_date = date.fromisoformat(st["pay_date"]) if st["pay_date"] else None
        if pay_date is None or pay_date < today:
            continue
        for r in st["statements"]:
            if r["seller_id"] in paid or r["hold_reasons"] or (r["payout"] or 0) <= 0:
                continue
            w = _monday(pay_date)
            if w in outflow:
                outflow[w] += r["payout"]

    # 추정: 장부가 한 주를 온전히 덮는 최근 n주 평균. 같은 주에 결제된 돈 기준으로
    # 들어옴(PG 지급액)과 나감(판매자 정산금)을 맞춘다.
    # 장부가 처음 시작된 주처럼 일부만 있는 주는 넣지 않는다
    n = cfg.cashflow.trailing_weeks
    pg = list(pg_settlements(books, today).values())
    paid_dates = [p["created_at"].date() for p in books.payments()]
    earliest = min(paid_dates) if paid_dates else None
    past_in, past_out = [], []
    for k in range(1, n + 1):
        ref = today - timedelta(days=7 * (k - 1))
        name, mon, sun = fc.week_range(None, ref)
        if earliest is None or mon < earliest:
            break
        st = settlement.statements(books, name, today=today)
        past_out.append(sum(max(r["payout"] or 0, 0) for r in st["statements"]))
        past_in.append(
            sum(
                int(rec["pay_out_amount"])
                for rec in pg
                if mon <= date.fromisoformat(str(rec["sold_date"])[:10]) <= sun
            )
        )
    has_history = orders_known and bool(past_in)
    avg_in = round(sum(past_in) / len(past_in)) if has_history else None
    avg_out = round(sum(past_out) / len(past_out)) if has_history else None
    used_weeks = len(past_in)

    rows, running = [], 0
    last_scheduled = max([w for w in weeks if inflow[w] or outflow[w]], default=None)
    for w in weeks:
        scheduled = last_scheduled is not None and w <= last_scheduled
        if scheduled:
            i, o = inflow[w], outflow[w]
        else:
            i, o = avg_in, avg_out
        net = None if i is None or o is None else i - o
        if net is not None and running is not None:
            running += net
        else:
            running = None
        rows.append(
            {
                "week_start": w.isoformat(),
                "basis": "scheduled" if scheduled else "estimated",
                "inflow": i if orders_known else None,
                "outflow": o if orders_known else None,
                "net": net if orders_known else None,
                "cumulative": running if orders_known else None,
            }
        )
    negative = [r["week_start"] for r in rows if r["net"] is not None and r["net"] < 0]
    return {
        "today": today.isoformat(),
        "weeks": rows,
        "trailing_weeks": used_weeks,
        "average": {"inflow": avg_in, "outflow": avg_out},
        "negative_weeks": negative,
        "note": "은행 잔액은 연동되지 않아 누적은 오늘을 0으로 둔 값입니다",
    }
