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
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from finance_agent import finance_config as fc
from finance_agent.books import Books, pg_settlements

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
        if amount <= 0:
            continue  # 출금은 대조하지 않는다
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
        if not memo or memo in r["memo"]:
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
