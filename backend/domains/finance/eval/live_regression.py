"""재무 AI 실제 서버 회귀 점검 (읽기 전용, GET만 보낸다).

    cd backend
    uv run python domains/finance/eval/live_regression.py \\
        --base http://<서버>:3090/finance-api --month 2026-10 --month 2026-11 --out result.json

달마다 본다.
1. 월 결산 줄 == 그달 일 결산 줄의 합 (줄 코드마다). 확정한 결산은 확정할 때 숫자(스냅숏)를 보여
   주므로 그 뒤 장부가 바뀌면 합이 맞지 않을 수 있다. 그래서 보고서의 `drift`(확정 숫자와 지금
   숫자 차이)를 함께 적고, 지금 숫자(스냅숏 + drift)로도 한 번 더 맞춘다.
2. 결산 설명(`commentary`)의 숫자가 그 보고서의 손익 줄·날짜에 있는지 (재무 대화 답과 같은 검사
   `finance_agent.chat.verify`). 주문 건수는 결산 보고서 줄에 없어 확인할 수 없으므로
   `unverifiable`로 따로 적는다.
3. 월 마감 체크리스트(`close-checklist`) 상태.
4. 고객지원 비용 기록(`support-costs`): 건수, 환불 합계, 환불 금액이 있는데 기록한 날(또는 전날)
   일 결산의 환불 줄이 그 금액보다 작은 기록. 결산 보고서에는 주문별 환불이 없어 금액으로만 본다.

`verify`는 표준 라이브러리만 쓰므로 `uv run` 없이 돌려도 이 파일 옆 `src/`에서 읽는다.
결산 첫 조회는 40~75초 걸릴 수 있어 기본 제한 시간은 180초다. POST는 보내지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from finance_agent.chat import verify
except ImportError:  # uv 환경 밖: 레포의 src에서 읽는다
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "finance_agent" / "chat"))
    import verify  # type: ignore[no-redef]

CODES = ("gmv", "commission_revenue", "coupon_cost", "refund_total", "pg_fee", "net_result")


class Reader:
    """GET만 하는 작은 클라이언트."""

    def __init__(self, base: str, timeout: float) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.calls: list[dict[str, Any]] = []

    def get(self, path: str, **params: str) -> dict[str, Any] | None:
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            status: int | str = resp.status
        except urllib.error.HTTPError as exc:
            body, status = None, exc.code
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            body, status = None, type(exc).__name__
        took = round(time.monotonic() - started, 2)
        self.calls.append({"url": url, "status": status, "seconds": took})
        print(f"  GET {url} -> {status} ({took}s)", flush=True)
        return body


def month_days(month: str, yesterday: date) -> list[date]:
    first = date.fromisoformat(f"{month}-01")
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    last = min(nxt - timedelta(days=1), yesterday)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def amounts(report: dict[str, Any]) -> dict[str, int | None]:
    return {ln["code"]: ln["amount"] for ln in report.get("lines", [])}


def current_amounts(report: dict[str, Any]) -> dict[str, int | None]:
    """확정 스냅숏에 drift(지금 − 확정)를 더한 지금 숫자."""
    out = amounts(report)
    for d in report.get("drift") or []:
        if out.get(d["code"]) is not None:
            out[d["code"]] = d["current"]
    return out


def compare(month: dict[str, int | None], days: list[dict[str, int | None]]) -> list[dict]:
    rows = []
    for code in CODES:
        values = [d.get(code) for d in days]
        if month.get(code) is None or any(v is None for v in values):
            rows.append({"code": code, "month": month.get(code), "days_sum": None,
                         "diff": None, "note": "확인할 수 없는 줄이 있음"})  # fmt: skip
            continue
        total = sum(v for v in values if v is not None)
        if total != month[code]:
            rows.append({"code": code, "month": month[code], "days_sum": total,
                         "diff": month[code] - total})  # fmt: skip
    return rows


def label_of(report: dict[str, Any]) -> list[str]:
    d = date.fromisoformat(report["date"])
    if report.get("period") == "month":
        return [f"{d.year}년 {d.month}월"]
    return [f"{d.month}월 {d.day}일"]


def check_commentary(report: dict[str, Any]) -> dict[str, Any]:
    allowed = verify.allowed_numbers(
        [ln.get("amount") for ln in report.get("lines", [])],
        report.get("date"),
        label_of(report),
    )
    bad = verify.bad_numbers(report.get("commentary") or "", allowed)
    counts = [b for b in bad if b.endswith("건")]  # 주문 건수는 보고서 줄에 없다
    return {
        "date": report.get("date"),
        "period": report.get("period"),
        "source": report.get("commentary_source"),
        "status": report.get("status"),
        "bad": [b for b in bad if b not in counts],
        "unverifiable": counts,
        "commentary": report.get("commentary"),
    }


def check_month(r: Reader, month: str, yesterday: date) -> dict[str, Any]:
    print(f"[{month}]", flush=True)
    days = month_days(month, yesterday)
    day_reports: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for d in days:
        body = r.get("/api/finance/closing", period="day", date=d.isoformat())
        if body and "data" in body:
            day_reports[d.isoformat()] = body["data"]
        else:
            failed.append(d.isoformat())
    month_body = r.get("/api/finance/closing", period="month", date=f"{month}-01")
    month_report = (month_body or {}).get("data")
    out: dict[str, Any] = {
        "month": month,
        "days_checked": len(day_reports),
        "days_failed": failed,
        "month_report": None,
    }
    if month_report is None:
        out["error"] = "월 결산을 받지 못함"
    else:
        shown = [amounts(x) for x in day_reports.values()]
        now = [current_amounts(x) for x in day_reports.values()]
        complete = not failed
        out["month_report"] = {
            "status": month_report.get("status"),
            "lines": amounts(month_report),
            "drift": month_report.get("drift"),
            "commentary_source": month_report.get("commentary_source"),
        }
        out["month_vs_days"] = {
            "complete": complete,
            "shown_mismatches": compare(amounts(month_report), shown),
            "current_mismatches": compare(current_amounts(month_report), now),
        }
    confirmed = {k: v for k, v in day_reports.items() if v.get("status") == "confirmed"}
    out["confirmed_days"] = sorted(confirmed)
    out["drift"] = [
        {"date": k, "drift": v["drift"]} for k, v in sorted(day_reports.items()) if v.get("drift")
    ]
    reports = list(day_reports.values()) + ([month_report] if month_report else [])
    checks = [check_commentary(x) for x in reports]
    out["commentary"] = {
        "checked": len(checks),
        "sources": {
            s: sum(1 for c in checks if c["source"] == s) for s in {c["source"] for c in checks}
        },
        "violations": [c for c in checks if c["bad"]],
        "unverifiable_counts": sum(1 for c in checks if c["unverifiable"]),
    }
    last = days[-1] if days else date.fromisoformat(f"{month}-01")
    body = r.get("/api/finance/close-checklist", date=last.isoformat())
    cl = (body or {}).get("data")
    out["close_checklist"] = (
        None
        if cl is None
        else {
            "month": cl.get("month"),
            "done": cl.get("done"),
            "total": cl.get("total"),
            "ready": cl.get("ready"),
            "closed": cl.get("closed"),
            "items": [
                {k: i.get(k) for k in ("key", "label", "status", "due", "overdue", "detail")}
                for i in cl.get("items", [])
            ],
        }
    )
    out["_day_refunds"] = {k: amounts(v).get("refund_total") for k, v in day_reports.items()}
    return out


def check_support_costs(r: Reader, day_refunds: dict[str, int | None]) -> dict[str, Any]:
    print("[support-costs]", flush=True)
    body = r.get("/api/finance/support-costs")
    if not body or "data" not in body:
        return {"error": "고객지원 비용 기록을 받지 못함"}
    rows = body["data"]
    refunds = [x for x in rows if (x.get("refund_amount") or 0) > 0]
    missing = []
    for x in refunds:
        when = (x.get("updated_at") or x["recorded_at"])[:10]
        prev = (date.fromisoformat(when) - timedelta(days=1)).isoformat()
        seen = {d: day_refunds.get(d) for d in (when, prev) if d in day_refunds}
        if not seen:
            missing.append({**x, "why": "그날 일 결산을 점검 범위에서 받지 않음"})
        elif all(v is None or -v < x["refund_amount"] for v in seen.values()):
            missing.append({**x, "why": "그날·전날 일 결산 환불 줄이 이 금액보다 작음",
                            "day_refunds": seen})  # fmt: skip
    return {
        "count": len(rows),
        "with_refund": len(refunds),
        "refund_total": sum(x["refund_amount"] for x in refunds),
        "coupon_issued": sum(1 for x in rows if x.get("coupon_issued")),
        "expense_used": sum(1 for x in rows if x.get("expense_used")),
        "unknown_order": [
            x["record_id"] for x in rows if x.get("order_id") and x.get("shipping_fee") is None
        ],
        "refund_not_in_closing": missing,
        "meta": body.get("meta"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="재무 AI 실제 서버 회귀 점검 (GET만)")
    ap.add_argument("--base", required=True, help="예: http://<서버>:3090/finance-api")
    ap.add_argument("--month", action="append", required=True, help="YYYY-MM (여러 번)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--today", default=None, help="기준일(UTC, 기본 오늘). 어제까지 본다")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else datetime.now(UTC).date()
    r = Reader(args.base, args.timeout)
    months = [check_month(r, m, today - timedelta(days=1)) for m in args.month]
    day_refunds: dict[str, int | None] = {}
    for m in months:
        day_refunds |= m.pop("_day_refunds")
    result = {
        "base": args.base,
        "today": today.isoformat(),
        "checked_at": datetime.now(UTC).isoformat(),
        "months": months,
        "support_costs": check_support_costs(r, day_refunds),
        "requests": {
            "count": len(r.calls),
            "slowest": sorted(r.calls, key=lambda c: -c["seconds"])[:5],
        },
    }
    for m in months:
        mv = m.get("month_vs_days", {})
        print(
            f"{m['month']}: 일 {m['days_checked']}개, 월≠일합(보이는 숫자) "
            f"{len(mv.get('shown_mismatches', []))}줄, (지금 숫자) "
            f"{len(mv.get('current_mismatches', []))}줄, 설명 숫자 위반 "
            f"{len(m['commentary']['violations'])}개"
        )
    sc = result["support_costs"]
    print(f"고객지원 비용 기록: {sc.get('count')}건, 환불 {sc.get('refund_total')}원, "
          f"결산에서 못 찾은 환불 {len(sc.get('refund_not_in_closing', []))}건")  # fmt: skip
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), "utf-8")
        print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
