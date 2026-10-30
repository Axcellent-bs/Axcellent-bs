"""① 월 마감 체크리스트: 그 달을 닫기 전에 끝내야 할 일을 장부에서 바로 확인한다.

항목마다 재무 AI가 장부·기록으로 끝났는지 판정하고(사람이 체크하지 않음), 근거와 갈 탭을 준다.
마지막 항목 "월 결산 확정"은 사람이 결산 탭에서 누른다. 모든 항목이 끝나야 마감 준비가 됐다고 본다.

1. 일 결산 확정: 그 달의 지난 날(오늘 전)이 모두 확정됐나
2. 매출·PG 대사: 그 달 불일치 건에 모두 확인 메모가 있나
3. 은행 입금 대조: 그 달 날짜가 올린 거래내역에 들어 있고 차이가 없나 (안 올렸으면 `pending`)
4. 판매자 정산: 그 달에 지급일이 있는 주의 정산 묶음이 모두 승인됐나
   (지급할 판매자가 없는 주는 제외)
5. 판매자 미지급금: 롤포워드가 맞고 더 준 판매자가 없나
6. 감사 의심 건: 처리 전 의심 건이 없나
7. 월 결산 확정: 사람이 그 달 결산을 확정했나
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from finance_agent import bank, payables, reconciliation, settlement
from finance_agent import finance_config as fc
from finance_agent.books import Books


def _item(key: str, label: str, done: bool | None, detail: str, link: str) -> dict[str, Any]:
    status = "unknown" if done is None else ("done" if done else "pending")
    return {"key": key, "label": label, "status": status, "detail": detail, "link": link}


def checklist(
    books: Books,
    records: Any,
    d: date | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or fc.today()
    d = d or today
    start, end = fc.month_range(d)
    last = min(end - timedelta(days=1), today - timedelta(days=1))
    items = []

    # 1. 일 결산
    days = (
        [start + timedelta(days=k) for k in range((last - start).days + 1)] if last >= start else []
    )
    open_days = [x for x in days if ("day", x.isoformat()) not in records.closings]
    items.append(
        _item(
            "daily_closing",
            "일 결산 확정",
            not open_days,
            f"{len(days) - len(open_days)}/{len(days)}일 확정"
            + (
                f" (남은 날: {', '.join(x.isoformat()[5:] for x in open_days[:5])}"
                f"{' 외' if len(open_days) > 5 else ''})"
                if open_days
                else ""
            ),
            "closing",
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
            f"메모 없는 불일치 {open_recon}건",
            "reconciliation",
        )
    )

    # 3. 은행 입금 대조
    b = bank.match(books, records.bank_rows, today)
    if not b["uploaded"]:
        items.append(
            _item(
                "bank",
                "은행 입금 대조",
                False,
                "은행 거래내역을 아직 올리지 않음",
                "reconciliation",
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
            )
        )

    # 4. 판매자 정산 묶음
    pending_weeks = []
    monday = start - timedelta(days=start.weekday() + 7)
    while monday < end:
        name, _, sunday = fc.week_range(
            f"{monday.isocalendar()[0]}-W{monday.isocalendar()[1]:02d}", today
        )
        pay = sunday + timedelta(days=fc.load_config().settlement.pay_days_after_period_end)
        if start <= pay < end and pay <= today:
            st = settlement.statements(books, name, today=today)
            if any(not r["hold_reasons"] and (r["payout"] or 0) > 0 for r in st["statements"]):
                if (records.batches.get(name) or {}).get("status") != "approved":
                    pending_weeks.append(name)
        monday += timedelta(days=7)
    items.append(
        _item(
            "settlement",
            "판매자 정산 지급 승인",
            not pending_weeks,
            f"승인 안 된 주: {', '.join(pending_weeks)}"
            if pending_weeks
            else "지급일이 지난 주는 모두 승인됨",
            "settlements",
        )
    )

    # 5. 미지급금 롤포워드
    p = payables.rollforward(books, records.batches, d)
    items.append(
        _item(
            "payables",
            "판매자 미지급금 롤포워드",
            None if p["balanced"] is None else (p["balanced"] and not p["overpaid"]),
            "자료를 못 받아 확인할 수 없음"
            if p["balanced"] is None
            else f"기말 {fc.won(p['totals']['closing'])}, 더 준 판매자 {len(p['overpaid'])}곳",
            "settlements",
        )
    )

    # 6. 감사 의심 건
    open_findings = sum(1 for e in records.anomalies.values() if e["status"] == "open")
    items.append(
        _item(
            "audit", "감사 의심 건 처리", open_findings == 0, f"처리 전 {open_findings}건", "audit"
        )
    )

    # 7. 월 결산 확정 (사람)
    confirmed = ("month", start.isoformat()) in records.closings
    items.append(
        _item(
            "month_closing",
            "월 결산 확정 (사람)",
            confirmed,
            "확정됨" if confirmed else "결산 탭에서 '한 달'을 골라 확정",
            "closing",
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
