"""① 월 마감 체크리스트: 그 달을 닫기 전에 끝내야 할 일을 장부에서 바로 확인한다.

항목마다 재무 AI가 장부·기록으로 끝났는지 판정하고(사람이 체크하지 않음), 근거와 갈 탭을 준다.
마지막 항목 "월 결산 확정"은 사람이 결산 탭에서 누른다. 모든 항목이 끝나야 마감 준비가 됐다고 본다.

1. 일 결산 확정: 그 달의 지난 날(오늘 전)이 모두 확정됐나
2. 매출·PG 대사: 그 달 불일치 건에 모두 확인 메모가 있나
3. 은행 입금 대조: 그 달 날짜가 올린 거래내역에 들어 있고 차이가 없나 (안 올렸으면 `pending`)
4. 판매자 정산: 그 달에 지급일이 있는 주의 정산 묶음이 모두 승인됐나
   (지급할 판매자가 없는 주는 제외). 정산 내역서·묶음은 고객지원이 계산해 준 것을 읽기만 한다
   (`settlement_feed`). 못 받았으면 `unknown`이다
   4-1. 은행 출금 대조: 승인한 이체가 통장에서 확인되고, 설명 없는 출금에 모두 메모가 있나
5. 판매자 미지급금: 롤포워드가 맞고 더 준 판매자가 없나
6. 월 결산 확정: 사람이 그 달 결산을 확정했나

항목마다 기한(`due`, YYYY-MM-DD)과 기한 지남(`overdue`: 안 끝났는데 오늘이 기한 뒤)을 준다.
기한 규칙은 `finance_policy.toml [closing]`(규칙 CLS-005). 일 결산은 가장 이른 남은 날의 다음 날,
판매자 정산은 가장 이른 승인 안 된 주의 지급 예정일(없으면 그 달 마지막 지급 예정일),
나머지는 다음 달 며칠이다. 기한은 알림이고 확정을 막지 않는다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from finance_agent import bank, payables, reconciliation
from finance_agent import finance_config as fc
from finance_agent.books import Books

BANK_UPLOAD_HINT = "(② 매출·PG 대사 > 은행 거래내역 파일 올리기)"
SHOWN_OPEN_DAYS = 5  # 남은 날을 이만큼만 적고 나머지는 "외 N일"


def _item(
    key: str,
    label: str,
    done: bool | None,
    detail: str,
    link: str,
    due: date,
    today: date,
) -> dict[str, Any]:
    status = "unknown" if done is None else ("done" if done else "pending")
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "link": link,
        "due": due.isoformat(),
        "overdue": status != "done" and today > due,
    }


def _next_month_day(end: date, day: int) -> date:
    """`end`(다음 달 1일)가 속한 달의 `day`일."""
    return end.replace(day=day)


def daily_detail(days: list[date], open_days: list[date]) -> str:
    """일 결산 설명. 예: 11일 중 5일 확정 · 남은 날 6일: 11-01, 11-02, … 외 1일"""
    if not days:
        return "확정할 지난 날 없음"
    text = f"{len(days)}일 중 {len(days) - len(open_days)}일 확정"
    if not open_days:
        return text
    shown = ", ".join(x.isoformat()[5:] for x in open_days[:SHOWN_OPEN_DAYS])
    rest = len(open_days) - SHOWN_OPEN_DAYS
    return f"{text} · 남은 날 {len(open_days)}일: {shown}" + (f" 외 {rest}일" if rest > 0 else "")


def _weeks(d: date) -> list[tuple[str, date]]:
    """지급일이 그 달에 들 수 있는 정산 주(월요일)들. 지급일은 내역서의 `pay_date`로 거른다."""
    start, end = fc.month_range(d)
    out = []
    monday = start - timedelta(days=start.weekday() + 7)
    while monday < end:
        year, week, _ = monday.isocalendar()
        out.append((f"{year}-W{week:02d}", monday))
        monday += timedelta(days=7)
    return out


def settlement_periods(d: date | None = None) -> list[str]:
    """`checklist`가 읽는 정산 주 (고객지원 `seller_settlement_weeks`로 미리 받는다)."""
    return [name for name, _ in _weeks(d or fc.today())]


def checklist(
    books: Books,
    records: Any,
    d: date | None = None,
    today: date | None = None,
    *,
    settlements: dict[str, dict[str, Any]] | None = None,
    batches: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """`settlements`(기간 → 내역서)·`batches`(기간 → 묶음)는 고객지원에서 받은 것. 없으면 None."""
    today = today or fc.today()
    d = d or today
    start, end = fc.month_range(d)
    last = min(end - timedelta(days=1), today - timedelta(days=1))
    rule = fc.load_config().closing
    items = []

    # 1. 일 결산
    days = (
        [start + timedelta(days=k) for k in range((last - start).days + 1)] if last >= start else []
    )
    open_days = [x for x in days if ("day", x.isoformat()) not in records.closings]
    # 기한: 가장 이른 남은 날의 다음 날. 남은 날이 없으면 그 달 마지막 날의 다음 날
    daily_due = (open_days[0] if open_days else end - timedelta(days=1)) + timedelta(
        days=rule.daily_closing_due_days
    )
    items.append(
        _item(
            "daily_closing",
            "일 결산 확정",
            not open_days,
            daily_detail(days, open_days),
            "closing",
            daily_due,
            today,
        )
    )

    # 2. 대사 메모 (그 달 하루씩)
    open_recon = 0
    for x in days:
        result = reconciliation.reconcile(books, x, records.recon_notes, today)
        open_recon += sum(1 for i in reconciliation.exceptions(result) if not i["note"])
    items.append(
        _item(
            "reconciliation",
            "매출·PG 대사 메모",
            open_recon == 0,
            f"이 달 메모 없는 불일치 {open_recon}건",
            "reconciliation",
            _next_month_day(end, rule.reconciliation_due_day),
            today,
        )
    )

    # 3. 은행 입금 대조
    b = bank.match(books, records.bank_rows, today)
    bank_due = _next_month_day(end, rule.bank_due_day)
    if not b["uploaded"]:
        items.append(
            _item(
                "bank",
                "은행 입금 대조",
                False,
                f"이 달 은행 거래내역 파일을 올리면 대조합니다{BANK_UPLOAD_HINT}",
                "reconciliation",
                bank_due,
                today,
            )
        )
    else:
        month_days = [x for x in b["days"] if start.isoformat() <= x["date"] < end.isoformat()]
        uncovered = [x for x in month_days if x["status"] == "not_covered"]
        problems = [
            x for x in month_days if x["status"] in ("short", "over", "missing", "unexpected")
        ]
        items.append(
            _item(
                "bank",
                "은행 입금 대조",
                not uncovered and not problems,
                f"차이 {len(problems)}일, 거래내역 기간 밖 {len(uncovered)}일",
                "reconciliation",
                bank_due,
                today,
            )
        )

    # 4. 판매자 정산 묶음 (고객지원이 계산·요청한 것을 읽기만 한다)
    pending_weeks = []
    pending_pays: list[date] = []
    weeks_known = settlements is not None and all(name in settlements for name, _ in _weeks(d))
    # 지급 예정일: 고객지원 내역서의 지급일, 없으면 정산 기간 끝(일요일) + settlement_pay_after_days
    pays: dict[str, date] = {}
    for name, monday in _weeks(d):
        st = (settlements or {}).get(name) or {}
        pays[name] = (
            date.fromisoformat(st["pay_date"])
            if st.get("pay_date")
            else monday + timedelta(days=6 + rule.settlement_pay_after_days)
        )
    month_pays = sorted(x for x in pays.values() if start <= x < end)
    for name, _ in _weeks(d) if weeks_known and settlements is not None else []:
        st = settlements[name]
        pay = date.fromisoformat(st["pay_date"]) if st.get("pay_date") else None
        if pay is None or not (start <= pay < end and pay <= today):
            continue
        if any(not r["hold_reasons"] and (r["payout"] or 0) > 0 for r in st["statements"]):
            if (st.get("batch") or {}).get("status") != "approved":
                pending_weeks.append(name)
                pending_pays.append(pay)
    settlement_due = (
        min(pending_pays)
        if pending_pays
        else (month_pays[-1] if month_pays else end - timedelta(days=1))
    )
    items.append(
        _item(
            "settlement",
            "판매자 정산 지급 승인 (고객지원)",
            (not pending_weeks) if weeks_known else None,
            (
                "고객지원에서 판매자 정산을 받지 못해 확인할 수 없음"
                if not weeks_known
                else f"승인 안 된 주: {', '.join(pending_weeks)}"
                if pending_weeks
                else "지급일이 지난 주는 모두 승인됨"
            ),
            "settlements",
            settlement_due,
            today,
        )
    )

    # 4-1. 은행 출금 대조 (판매자 이체 확인, 설명 없는 출금 메모)
    periods = getattr(records, "bank_out_periods", [])
    month = start.isoformat()[:7]
    out_due = _next_month_day(end, rule.bank_out_due_day)
    if not periods:
        items.append(
            _item(
                "bank_out",
                "은행 출금 대조",
                False,
                f"이 달 출금 열이 있는 은행 거래내역 파일을 올리면 대조합니다{BANK_UPLOAD_HINT}",
                "reconciliation",
                out_due,
                today,
            )
        )
    elif batches is None:
        items.append(
            _item(
                "bank_out",
                "은행 출금 대조",
                None,
                "고객지원에서 정산 묶음을 받지 못해 판매자 이체를 확인할 수 없음",
                "reconciliation",
                out_due,
                today,
            )
        )
    else:
        out = bank.outflows(
            records.bank_rows,
            periods,
            batches,
            seller_name=books.seller_name,
            other_pnl=getattr(records, "other_pnl", {}),
            campaigns=getattr(records, "campaigns", {}),
            notes=getattr(records, "bank_notes", {}),
            today=today,
        )
        n = bank.out_exceptions(out, month)
        items.append(
            _item(
                "bank_out",
                "은행 출금 대조",
                n["transfer_not_found"] == 0 and n["unexplained_open"] == 0,
                f"이체 못 찾음 {n['transfer_not_found']}건, "
                f"메모 없는 설명 없는 출금 {n['unexplained_open']}건",
                "reconciliation",
                out_due,
                today,
            )
        )

    # 5. 미지급금 롤포워드
    p = payables.rollforward(books, batches, d)
    items.append(
        _item(
            "payables",
            "판매자 미지급금 롤포워드",
            None if p["balanced"] is None else (p["balanced"] and not p["overpaid"]),
            "자료를 못 받아 확인할 수 없음"
            if p["balanced"] is None
            else f"기말 {fc.won(p['totals']['closing'])}, 더 준 판매자 {len(p['overpaid'])}곳",
            "settlements",
            _next_month_day(end, rule.payables_due_day),
            today,
        )
    )

    # 6. 월 결산 확정 (사람)
    confirmed = ("month", start.isoformat()) in records.closings
    items.append(
        _item(
            "month_closing",
            "월 결산 확정 (사람)",
            confirmed,
            "확정됨" if confirmed else "결산 탭에서 '한 달'을 골라 확정",
            "closing",
            _next_month_day(end, rule.month_closing_due_day),
            today,
        )
    )

    done = sum(1 for i in items if i["status"] == "done")
    return {
        "month": start.isoformat()[:7],
        "items": items,
        "done": done,
        "total": len(items),
        "ready": all(i["status"] == "done" for i in items[:-1]),
        "closed": confirmed,
    }
