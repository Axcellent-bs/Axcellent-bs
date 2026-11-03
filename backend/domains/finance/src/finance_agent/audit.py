"""⑦ 사후 감사: 안전 장치 기록의 보류·거절 추이.

재무는 쿠폰·환불을 끝난 금액만 장부에 적는다. 결정·확인·검증·감사는 재무 일이 아니라서 쿠폰·환불은
여기서 보지 않는다(`NOT_AUDITED`). 없앤 규칙과 경위는 `README.md`에 있다.
기간은 오늘까지 `days`일, 비교 기간은 그 바로 앞 같은 길이. 판정은 코드 규칙이다
(기준값은 `finance_policy.toml [audit]`).

- `gate_hold_trend`: 관문 보류·거절이 기간 안에 `gate_hold_min`건 이상이고 지난 기간의
  `gate_hold_jump_ratio`배 이상

`context_id`를 주면 그 사건의 실행 기록만 본다. 통계도 좁힌 자료 기준이다.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any

from finance_agent import finance_config as fc
from finance_agent.books import Books, in_range

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
# 쿠폰은 마케팅, 환불은 고객지원 일이다. 재무는 끝난 금액만 장부에 적는다
NOT_AUDITED = {"issue_coupon", "refund_request"}


def _finding(code: str, severity: str, title: str, detail: str, refs: list[str]) -> dict:
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "detail": detail,
        "refs": [r for r in dict.fromkeys(refs) if r],
    }


# ---------------------------------------------------------------- 규칙
def gate_trend(
    books: Books, start, end, prev_start, days: int
) -> tuple[list[dict], int | None, int | None]:
    cfg = fc.load_config().audit
    if books.executions is None:
        return [], None, None
    rows = [e for e in books.executions if e.get("action") not in NOT_AUDITED]
    cur = [e for e in rows if in_range(e["created_at"], start, end)]
    prev = [e for e in rows if in_range(e["created_at"], prev_start, start)]
    held = [e for e in cur if e.get("status") == "held"]
    rejected = [e for e in cur if e.get("status") == "rejected"]
    prev_count = sum(1 for e in prev if e.get("status") in ("held", "rejected"))
    count = len(held) + len(rejected)
    out = []
    if count >= cfg.gate_hold_min and (
        prev_count == 0 or count >= prev_count * cfg.gate_hold_jump_ratio
    ):
        out.append(
            _finding(
                "gate_hold_trend",
                "medium",
                "안전 장치 보류·거절 증가",
                f"최근 {days}일 안전 장치 보류 {len(held)}건·거절 {len(rejected)}건 "
                f"(지난 {days}일 {prev_count}건)",
                [str(e.get("execution_id")) for e in held + rejected][:10],
            )
        )
    return out, len(held), len(rejected)


def narrow(books: Books, context_id: str) -> Books:
    """한 사건의 실행 기록만 남긴 장부."""
    executions = (
        None
        if books.executions is None
        else [e for e in books.executions if e.get("context_id") == context_id]
    )
    return dataclasses.replace(books, executions=executions)


# ---------------------------------------------------------------- 리포트
async def audit_report(
    books: Books, days: int = 7, *, context_id: str | None = None
) -> tuple[dict[str, Any], list[str]]:
    if context_id:
        books = narrow(books, context_id)
    today = fc.today()
    start = fc.day_start(today - timedelta(days=days - 1))
    end = fc.day_start(today + timedelta(days=1))
    prev_start = start - timedelta(days=days)

    findings, held, rejected = gate_trend(books, start, end, prev_start, days)
    findings.sort(key=lambda f: SEVERITY_ORDER[f["severity"]])

    unavailable = []
    if books.executions is None:
        unavailable += ["stats.gate_held", "stats.gate_rejected"]
    data = {
        "from": fc.iso(start),
        "to": fc.iso(end),
        "stats": {"gate_held": held, "gate_rejected": rejected},
        "findings": findings,
    }
    return data, unavailable
