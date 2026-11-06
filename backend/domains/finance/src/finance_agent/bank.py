"""② 은행 입금 대조: PG가 보내기로 한 정산금이 실제 통장에 들어왔는지 날짜별로 맞춘다.

주문 ↔ PG 대사(`reconciliation.py`)는 "PG가 얼마를 주기로 했나"까지만 본다. 돈이 실제로 들어왔는지는
은행 거래내역이 있어야 안다. 은행 연동(오픈뱅킹·펌뱅킹)은 아직 없으므로 사람이 은행에서 내려받은
거래내역(입금만)을 올리면 그 기간만 맞춘다. 올린 적이 없으면 판정하지 않는다(지어내지 않음).

- 예상 입금: PG 정산 기록의 지급일(`paid_out_date`)별 지급액(`pay_out_amount`) 합.
  지급일이 지난 것만
- 실제 입금: 올린 거래내역의 같은 날 입금 합
  (적요에 `bank.pg_memo` 글자가 있는 줄만, 비우면 모든 입금)
- 상태: `matched`(같음) · `short`(덜 들어옴) · `over`(더 들어옴) · `missing`(예상했는데 입금 없음)
  · `unexpected`(예상 없는 날 입금). 거래내역 기간 밖 날짜는 `not_covered`
- 같은 거래내역을 두 번 올려도 겹치지 않게 (날짜·금액·적요·잔액)으로 한 줄로 센다
- 출금은 음수 금액으로 받아 아래 `outflows`(판매자 이체·항목별·설명 없는 출금)에서 따로 맞춘다.
  입금 대조에는 쓰지 않는다. 마지막 잔액은 `latest_balance`로 현금 흐름 예측의 기초가 된다
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from finance_agent import finance_config as fc
from finance_agent.books import Books, pg_settlements
from finance_agent.rules import applied

STATUS_KO = {
    "matched": "맞음",
    "short": "덜 들어옴",
    "over": "더 들어옴",
    "missing": "입금 없음",
    "unexpected": "예상 없는 입금",
    "not_covered": "거래내역 기간 밖",
}
MAX_ROWS = 5000


def parse_rows(raw: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """올린 거래내역 줄을 검사한다. (쓸 줄, 버린 이유)."""
    if not isinstance(raw, list) or not raw or len(raw) > MAX_ROWS:
        return [], [f"rows는 1~{MAX_ROWS}줄 목록"]
    rows, problems = [], []
    for n, r in enumerate(raw, start=1):
        try:
            d = date.fromisoformat(str(r["date"])[:10])
            amount = r["amount"]
            if isinstance(amount, bool) or not isinstance(amount, int):
                raise ValueError("amount는 정수 원")
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"{n}번째 줄: {exc}")
            continue
        if amount == 0:
            continue  # 출금은 음수로 받는다 (출금 대조 `outflows`)
        memo = str(r.get("memo") or "")[:100]
        balance = r.get("balance") if isinstance(r.get("balance"), int) else None
        rows.append({"date": d.isoformat(), "amount": amount, "memo": memo, "balance": balance})
    return rows, problems


def merge_rows(store: list[dict[str, Any]], rows: list[dict[str, Any]]) -> int:
    """겹치지 않게 더한다. 새로 더한 줄 수."""
    seen = {(r["date"], r["amount"], r["memo"], r.get("balance")) for r in store}
    added = 0
    for r in rows:
        key = (r["date"], r["amount"], r["memo"], r.get("balance"))
        if key not in seen:
            store.append(r)
            seen.add(key)
            added += 1
    return added


def match(
    books: Books, bank_rows: list[dict[str, Any]], today: date | None = None
) -> dict[str, Any]:
    cfg = fc.load_config()
    today = today or fc.today()
    if not bank_rows:
        return {
            "uploaded": False,
            "covered": None,
            "counts": {},
            "days": [],
            "pg_memo": cfg.bank.pg_memo,
        }
    memo = cfg.bank.pg_memo
    actual: dict[str, int] = defaultdict(int)
    dates = sorted(r["date"] for r in bank_rows)
    for r in bank_rows:
        if r["amount"] > 0 and (not memo or memo in r["memo"]):
            actual[r["date"]] += r["amount"]
    lo, hi = dates[0], dates[-1]
    expected: dict[str, int] = defaultdict(int)
    for rec in pg_settlements(books, today).values():
        if rec.get("settled") and rec.get("paid_out_date"):
            expected[str(rec["paid_out_date"])[:10]] += int(rec["pay_out_amount"])

    days = []
    for d in sorted(set(expected) | set(actual), reverse=True):
        exp, got = expected.get(d), actual.get(d)
        if not (lo <= d <= hi):
            status = "not_covered"
        elif exp is None:
            status = "unexpected"
        elif got is None:
            status = "missing"
        elif got == exp:
            status = "matched"
        else:
            status = "short" if got < exp else "over"
        days.append(
            {
                "date": d,
                "expected": exp,
                "deposited": got,
                "diff": (got or 0) - (exp or 0) if status != "not_covered" else None,
                "status": status,
                "status_label": STATUS_KO[status],
            }
        )
    counts = {k: sum(1 for x in days if x["status"] == k) for k in STATUS_KO}
    return {
        "uploaded": True,
        "covered": {"from": lo, "to": hi, "rows": len(bank_rows)},
        "counts": counts,
        "days": days,
        "pg_memo": memo,
    }


def exceptions(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in result["days"] if d["status"] in ("short", "over", "missing", "unexpected")]


# ---------------------------------------------------------------- 출금 대조
OUT_STATUS_KO = {
    "paid": "이체 확인",
    "waiting": "이체 기다림",
    "not_found": "이체 못 찾음",
    "not_covered": "출금 내역 기간 밖",
}
UNEXPLAINED = "설명 없음"


def row_id(r: dict[str, Any]) -> str:
    """거래 줄 하나의 id. 같은 줄을 다시 올려도 같다 (날짜·금액·적요·잔액)."""
    raw = f"{r['date']}|{r['amount']}|{r['memo']}|{r.get('balance')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def add_out_period(periods: list[dict[str, str]], rows: list[dict[str, Any]]) -> None:
    """출금 열이 있는 내역을 올렸을 때 그 기간을 출금 대조 기간으로 남긴다.

    입금만 있던 예전 내역은 출금이 없어서가 아니라 출금 열이 없었던 것이라
    대조하지 않는다.
    """
    if rows:
        dates = sorted(r["date"] for r in rows)
        periods.append({"from": dates[0], "to": dates[-1]})


def _covered(periods: list[dict[str, str]], d: str) -> bool:
    return any(p["from"] <= d <= p["to"] for p in periods)


def latest_balance(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """가장 늦은 날의 마지막 거래 뒤 잔액. 잔액 열이 없으면 None.

    같은 날 여러 줄이면 잔액이 이어지는 순서(앞 줄 잔액 + 이번 금액 = 이번 잔액)로
    마지막 줄을 찾는다.
    순서를 정할 수 없으면 올린 순서의 마지막 줄을 쓰고 `ordered: False`로 알린다.
    """
    with_balance = [r for r in rows if isinstance(r.get("balance"), int)]
    if not with_balance:
        return None
    last_day = max(r["date"] for r in with_balance)
    same = [r for r in with_balance if r["date"] == last_day]
    used_before = {r["balance"] - r["amount"] for r in same}
    tails = [r for r in same if r["balance"] not in used_before] if len(same) > 1 else same
    ordered = len(tails) == 1
    pick = tails[0] if ordered else same[-1]
    return {"as_of": last_day, "balance": pick["balance"], "ordered": ordered}


def _month_of(d: str) -> str:
    return d[:7]


def _month_fully_covered(periods: list[dict[str, str]], month: str) -> bool:
    first = date.fromisoformat(f"{month}-01")
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    day = first
    while day < nxt:
        if not _covered(periods, day.isoformat()):
            return False
        day += timedelta(days=1)
    return True


def outflows(
    bank_rows: list[dict[str, Any]],
    out_periods: list[dict[str, str]],
    batches: dict[str, dict[str, Any]],
    *,
    seller_name: Callable[[str], str],
    other_pnl: dict[str, dict[str, Any]] | None = None,
    campaigns: dict[str, dict[str, Any]] | None = None,
    notes: dict[str, dict[str, Any]] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """통장 출금을 장부와 맞춘다. 장부를 바꾸지 않고 읽기만 한다.

    1. 판매자 이체: 승인된 정산 묶음마다 승인일부터 `bank.transfer_days`일 안에 같은 금액 출금이
       있는지 본다. 묶음 합계 한 건(일괄 이체)이 먼저, 없으면 판매자별 금액을 하나씩 찾는다
    2. 항목별: 남은 출금을 적요 글자(`bank.out_categories`)로 나누고, 출금 기간이 그 달을
       다 덮을 때만 장부 밖 손익 입력값(급여·임차료)과 캠페인 광고비에 견준다
    3. 설명 없는 출금: 1·2에 안 걸린 출금. 사람이 메모를 남기면 닫힌다
    """
    cfg = fc.load_config().bank
    today = today or fc.today()
    notes = notes or {}
    outs = sorted(
        (r | {"id": row_id(r), "out": -r["amount"]} for r in bank_rows if r["amount"] < 0),
        key=lambda r: (r["date"], r["id"]),
    )
    used: set[str] = set()
    if not out_periods:
        return {
            "uploaded": False,
            "covered": [],
            "transfers": [],
            "months": [],
            "unexplained": [],
            "counts": {},
            "transfer_days": cfg.transfer_days,
            "applied_rules": [applied("BNK-001"), applied("BNK-002")],
        }

    def take(amount: int, lo: str, hi: str) -> dict[str, Any] | None:
        for r in outs:
            if r["id"] not in used and r["out"] == amount and lo <= r["date"] <= hi:
                used.add(r["id"])
                return r
        return None

    transfers = []
    approved = [(name, b) for name, b in batches.items() if b.get("status") == "approved"]
    approved.sort(key=lambda x: str(x[1].get("approved_at") or x[1].get("requested_at") or ""))
    for name, b in approved:
        at = fc.parse_dt(b.get("approved_at") or b.get("requested_at"))
        if at is None:
            continue
        lo = at.date().isoformat()
        hi_d = at.date() + timedelta(days=cfg.transfer_days)
        hi = hi_d.isoformat()
        payouts = b.get("payouts") if isinstance(b.get("payouts"), dict) else {}
        lines = [(s, int(a)) for s, a in payouts.items() if int(a) > 0] or [
            ("", int(b.get("total_payout") or 0))
        ]
        total = sum(a for _, a in lines)
        bulk = take(total, lo, hi) if len(lines) > 1 else None
        for seller, amount in lines:
            found = bulk or take(amount, lo, hi)
            if found:
                status = "paid"
            elif not all(_covered(out_periods, d) for d in (lo, min(hi, today.isoformat()))):
                status = "not_covered"
            elif hi_d >= today:
                status = "waiting"
            else:
                status = "not_found"
            transfers.append(
                {
                    "batch": name,
                    "seller_id": seller or None,
                    "name": seller_name(seller) if seller else "묶음 합계",
                    "amount": amount,
                    "approved_on": lo,
                    "due_by": hi,
                    "status": status,
                    "status_label": OUT_STATUS_KO[status],
                    "bank_date": found["date"] if found else None,
                    "bulk": bool(bulk),
                }
            )

    def category(memo: str) -> str:
        for name, words in cfg.out_categories.items():
            if any(w and w in memo for w in words):
                return name
        return UNEXPLAINED

    by_month: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    unexplained = []
    for r in outs:
        if r["id"] in used or not _covered(out_periods, r["date"]):
            continue
        cat = category(r["memo"])
        by_month[_month_of(r["date"])][cat] += r["out"]
        if cat == UNEXPLAINED:
            note = notes.get(r["id"])
            unexplained.append(
                {
                    "id": r["id"],
                    "date": r["date"],
                    "amount": r["out"],
                    "memo": r["memo"],
                    "note": (note or {}).get("note"),
                    "noted_by": (note or {}).get("by"),
                }
            )

    other_pnl = other_pnl or {}
    spend: dict[str, int] = defaultdict(int)
    for c in (campaigns or {}).values():
        if c.get("period_to"):
            spend[str(c["period_to"])[:7]] += int(c.get("spend_krw") or 0)
    entered_field = {"급여": "salary", "임차료": "rent"}
    months = []
    for month in sorted(by_month, reverse=True):
        full = _month_fully_covered(out_periods, month)
        rows = []
        for cat in [*cfg.out_categories, UNEXPLAINED]:
            got = by_month[month].get(cat, 0)
            if cat in entered_field:
                entry = other_pnl.get(month) or {}
                book = entry.get(entered_field[cat]) if entry else None
                source = "장부 밖 손익 입력" if entry else None
            elif cat == "광고비":
                book = spend.get(month)
                source = "캠페인 광고비" if book is not None else None
            else:
                book, source = None, None
            diff = got - book if full and isinstance(book, int) else None
            rows.append(
                {
                    "category": cat,
                    "bank": got,
                    "book": book,
                    "book_source": source,
                    "diff": diff,
                    "judged": full and isinstance(book, int),
                    "comparable": cat in entered_field or cat == "광고비",
                }
            )
        months.append({"month": month, "fully_covered": full, "rows": rows})

    counts = {k: sum(1 for t in transfers if t["status"] == k) for k in OUT_STATUS_KO}
    return {
        "uploaded": True,
        "covered": out_periods,
        "transfers": list(reversed(transfers)),
        "months": months,
        "unexplained": unexplained[::-1],
        "counts": counts | {"unexplained_open": sum(1 for u in unexplained if not u["note"])},
        "transfer_days": cfg.transfer_days,
        "applied_rules": [
            applied(
                "BNK-001",
                f"승인일부터 {cfg.transfer_days}일 안에 같은 금액 출금. "
                f"확인 {counts['paid']}건, 못 찾음 {counts['not_found']}건",
            ),
            applied("BNK-002", f"설명 없는 출금 {len(unexplained)}건"),
        ],
    }


def out_exceptions(result: dict[str, Any], month: str) -> dict[str, int]:
    """그 달 마감 전에 볼 출금 문제 건수 (이체 못 찾음, 메모 없는 설명 없는 출금)."""
    missing = sum(
        1
        for t in result["transfers"]
        if t["status"] == "not_found" and t["approved_on"][:7] == month
    )
    open_items = sum(1 for u in result["unexplained"] if not u["note"] and u["date"][:7] == month)
    return {"transfer_not_found": missing, "unexplained_open": open_items}
