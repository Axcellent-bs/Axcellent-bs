"""임시 예시 값 (2026-11-26, 태윤 요청 "임시로 알맞게 넣어도 된다").

실제 자료 출처가 아직 없는 두 칸을 화면이 비워 두지 않도록, **예시 값**을 따로 붙인다.
실제 숫자와 섞지 않는다: 원래 칸(`uploaded`, `actual_spend`)은 그대로 두고 `example` 칸에만 넣고,
화면은 "임시 예시 · 실제 자료 아님"으로 표시한다.
결산 체크리스트·재무 대화·정확도 검사는 이 값을 쓰지 않는다.

- 은행 입금 대조: 거래내역을 올리기 전이면 최근 `BANK_DAYS`일의 PG 지급 예정액이 그날 그대로
  들어왔다고 본 표(`bank_example`). 거래내역을 올리면 사라진다
- 팀별 실제 지출: 팀 예산의 70~105%를 팀·달마다 정해진 비율(해시)로 정한 값(`team_spend_example`).
  1,000원 단위. 지출 자료가 생기면 이 함수를 지운다
- 끄기: `FINANCE_EXAMPLE_DATA=0` (기본은 켬)

숫자는 이 파일의 코드만 정한다. LLM은 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from finance_agent.books import Books, pg_settlements

ENV = "FINANCE_EXAMPLE_DATA"
BANK_DAYS = 14
SPEND_MIN_PCT = 70
SPEND_SPAN_PCT = 36  # 70% ~ 105%
SPEND_UNIT = 1000
BANK_NOTE = (
    "임시 예시예요. 은행 거래내역을 올리기 전이라 PG가 보내기로 한 금액이 그날 그대로 들어왔다고 "
    "보고 보여 줘요. 실제 입금이 아니에요. 거래내역을 올리면 실제 대조로 바뀌어요."
)
SPEND_NOTE = (
    "임시 예시예요. 팀별 지출 자료가 아직 없어 팀 예산의 70~105% 사이 값을 넣어 뒀어요. "
    "실제 지출이 아니에요."
)


def enabled() -> bool:
    return os.environ.get(ENV, "1").strip().lower() not in ("0", "false", "off", "no")


def bank_example(books: Books, today: date) -> dict[str, Any] | None:
    """최근 `BANK_DAYS`일 PG 지급 예정액을 그대로 입금된 것으로 본 예시 표.

    지급 예정이 없으면 None."""
    lo = (today - timedelta(days=BANK_DAYS - 1)).isoformat()
    hi = today.isoformat()
    expected: dict[str, int] = defaultdict(int)
    for rec in pg_settlements(books, today).values():
        day = str(rec.get("paid_out_date") or "")[:10]
        if rec.get("settled") and day and lo <= day <= hi:
            expected[day] += int(rec["pay_out_amount"])
    if not expected:
        return None
    days = [
        {
            "date": d,
            "expected": amount,
            "deposited": amount,
            "diff": 0,
            "status": "matched",
            "status_label": "맞음(예시)",
        }
        for d, amount in sorted(expected.items(), reverse=True)
    ]
    return {"note": BANK_NOTE, "from": lo, "to": hi, "counts": {"matched": len(days)}, "days": days}


def _spend_pct(team: str, month: str) -> int:
    digest = hashlib.sha256(f"{month}:{team}".encode()).digest()
    return SPEND_MIN_PCT + digest[0] % SPEND_SPAN_PCT


def team_spend_example(month: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """팀 예산 줄마다 예시 지출. 예산이 0원인 팀은 0원. 팀이 없으면 None."""
    if not rows:
        return None
    out = []
    for r in rows:
        budget = int(r["budget"])
        pct = _spend_pct(str(r["team"]), month)
        spend = budget * pct // 100 // SPEND_UNIT * SPEND_UNIT
        out.append({"team": r["team"], "budget": budget, "spend": spend, "rate_pct": pct})
    return {"note": SPEND_NOTE, "rows": out, "total": sum(x["spend"] for x in out)}
