"""③ 판매자 정산 기한 점검: 구매가 확정된 판매 대금을 기한 안에 줄 수 있는지 미리 본다.

오픈마켓(통신판매중개)은 판매자에게 판매 대금을 정해진 날 안에 줘야 한다. 기한(일 수)과 기산점은
`finance_policy.toml [settlement]`에 둔다(법이 바뀌면 PR로 바꾼다). 재무는 경보만 낸다.
지급은 지금처럼 정산 묶음 → 안전 장치 C 보류 → 사람 승인으로만 한다.

- 기산점: 구매 확정. 판매 사이트에 구매확정 시각이 따로 없어 배송 완료 시각을 쓴다
  (배송 전 주문은 기한이 아직 시작되지 않은 것으로 센다)
- 지급 완료: 그 주 정산 묶음이 안전 장치에서 승인된 판매자. 실제 이체 결과는 연동돼 있지 않다
- 지급 예정일: 정산 기간(월~일) 마지막 날 + `pay_days_after_period_end`.
  보류 사유가 있으면 예정일이 없다
- 상태: `overdue`(기한 지남) · `at_risk`(보류로 예정일이 없거나 예정일이 기한 뒤)
  · `due_soon`(기한까지 `deadline_warn_days` 이하) · `ok`
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from finance_agent import finance_config as fc
from finance_agent import settlement
from finance_agent.books import Books, item_gross, order_sold

STATUS_ORDER = {"overdue": 0, "at_risk": 1, "due_soon": 2, "ok": 3}
STATUS_KO = {
    "overdue": "기한 지남",
    "at_risk": "기한 넘길 위험",
    "due_soon": "기한 임박",
    "ok": "정상",
}


def _week_of(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def deadline_report(
    books: Books, batches: dict[str, dict[str, Any]], today: date | None = None
) -> dict[str, Any]:
    cfg = fc.load_config()
    s = cfg.settlement
    today = today or fc.today()
    since = today - timedelta(days=s.deadline_lookback_days)
    statements: dict[str, dict[str, Any]] = {}  # 주 → 판매자 → 정산서 행

    def row_of(week: str, seller_id: str) -> dict[str, Any] | None:
        if week not in statements:
            result = settlement.statements(books, week, today=today)
            statements[week] = {r["seller_id"]: r for r in result["statements"]}
        return statements[week].get(seller_id)

    sellers: dict[str, dict[str, Any]] = {}
    waiting = 0  # 배송 전이라 기한이 아직 시작되지 않은 주문
    for o in books.orders:
        if not order_sold(o):
            continue
        paid_at = settlement._paid_at(o)
        if paid_at is None or paid_at.date() < since:
            continue
        week = _week_of(paid_at.date())
        batch = batches.get(week) or {}
        paid_sellers = (
            set(batch.get("seller_ids") or []) if batch.get("status") == "approved" else set()
        )
        _, _, sunday = fc.week_range(week, today)
        pay_date = sunday + timedelta(days=s.pay_days_after_period_end)
        for item in o["items"]:
            seller_id = item.get("seller_id")
            if not seller_id or seller_id in paid_sellers:
                continue
            if o.get("delivered_at") is None:
                waiting += 1
                continue
            confirmed = o["delivered_at"].date()
            deadline = confirmed + timedelta(days=s.deadline_days)
            row = row_of(week, seller_id)
            held = row is not None and bool(row["hold_reasons"])
            expected = None if held or pay_date < today else pay_date
            if today > deadline:
                status = "overdue"
            elif expected is None or expected > deadline:
                status = "at_risk"
            elif (deadline - today).days <= s.deadline_warn_days:
                status = "due_soon"
            else:
                status = "ok"
            rate = cfg.commission.rate_for(item.get("category_code"))
            gross = item_gross(item)
            amount = gross - fc.rate_amount(gross, rate)  # 판매자 몫 (쿠폰·환불 차감 전)
            cur = sellers.setdefault(
                seller_id,
                {
                    "seller_id": seller_id,
                    "name": books.seller_name(seller_id),
                    "orders": set(),
                    "amount": 0,
                    "oldest_confirmed": confirmed,
                    "deadline": deadline,
                    "expected_pay_date": expected,
                    "status": status,
                    "hold_reasons": [],
                },
            )
            cur["orders"].add(o["order_id"])
            cur["amount"] += amount
            if deadline < cur["deadline"]:
                cur.update(
                    oldest_confirmed=confirmed, deadline=deadline, expected_pay_date=expected
                )
            if STATUS_ORDER[status] < STATUS_ORDER[cur["status"]]:
                cur["status"] = status
            if held and row is not None:
                for reason in row["hold_reasons"]:
                    if reason not in cur["hold_reasons"]:
                        cur["hold_reasons"].append(reason)

    rows = sorted(sellers.values(), key=lambda r: (STATUS_ORDER[r["status"]], r["deadline"]))
    out = [
        {
            "seller_id": r["seller_id"],
            "name": r["name"],
            "orders": len(r["orders"]),
            "amount": r["amount"],
            "oldest_confirmed": r["oldest_confirmed"].isoformat(),
            "deadline": r["deadline"].isoformat(),
            "days_left": (r["deadline"] - today).days,
            "expected_pay_date": r["expected_pay_date"].isoformat()
            if r["expected_pay_date"]
            else None,
            "status": r["status"],
            "status_label": STATUS_KO[r["status"]],
            "hold_reasons": r["hold_reasons"],
        }
        for r in rows
    ]
    counts = {k: sum(1 for r in out if r["status"] == k) for k in STATUS_ORDER}
    return {
        "today": today.isoformat(),
        "deadline_days": s.deadline_days,
        "basis": s.deadline_basis,
        "warn_days": s.deadline_warn_days,
        "since": since.isoformat(),
        "counts": counts,
        "waiting_orders": waiting,
        "sellers": out,
    }


def needs_attention(report: dict[str, Any]) -> list[dict[str, Any]]:
    """사람이 볼 판매자: 기한 지남·위험·임박."""
    return [r for r in report["sellers"] if r["status"] != "ok"]
