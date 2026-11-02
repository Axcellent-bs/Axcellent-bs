"""팀 예산 (인사 ↔ 재무, 2026-11-02 #2-재무 합의, 멘토링 "1명당 월 10만원 → 팀 계산").

- 팀 예산 = 그달 1일 팀 인원(인사 AI `team_headcount`) × 인당 월 단가
  (`[team_budget]`, 회사 폴더 tenant.toml이 덮음)
- 발령은 인사가 센다: 발령일이 1일이면 그 달부터, 아니면 다음 달 1일부터. 파견은 세지 않는다
- 재무는 지난달과 비교해 팀별로 늘고 주는 금액을 보여 주고, 예산 확정은 **재무 담당(사람)**이 한다.
  재무 AI가 혼자 예산을 바꾸지 않는다. 확정한 뒤 인원·단가가 바뀌면 "다시 확인 필요"로 돌아간다
- 인원을 못 받으면 0원으로 지어내지 않고 `unavailable`로 둔다
- 인사가 가상 명부(예시) 인원을 주면(`example`) 계산·증감은 보여 주되 확정하지 않는다
- 예산 대비 실제 지출은 팀별 지출 자료가 없어 아직 계산하지 않는다(`actual_spend: None`)

숫자는 이 파일의 코드만 계산한다. LLM은 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def month_of(d: date) -> str:
    return d.isoformat()[:7]


def shift_month(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def parse_month(raw: str | None, today: date) -> str:
    """`YYYY-MM`. 비우면 이번 달. 틀리면 ValueError."""
    if not raw:
        return month_of(today)
    if not MONTH_RE.match(raw):
        raise ValueError("month는 YYYY-MM")
    return raw


def counts(rows: list[tuple[str, int]] | None) -> dict[str, int] | None:
    if rows is None:
        return None
    out: dict[str, int] = {}
    for team, n in rows:
        out[team] = out.get(team, 0) + n
    return out


def signature(month: str, per_head: int, heads: dict[str, int]) -> str:
    """확정할 때 보던 숫자와 지금 숫자가 같은지 가리는 지문 (달·단가·팀별 인원)."""
    body = json.dumps([month, per_head, sorted(heads.items())], ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def report(
    month: str,
    heads: dict[str, int] | None,
    prev_heads: dict[str, int] | None,
    per_head: int,
    approved: dict[str, Any] | None,
    basis: str | None = None,
    example: bool = False,
) -> dict[str, Any]:
    """그 달 팀 예산과 지난달 대비 증감, 확정 상태."""
    base: dict[str, Any] = {
        "month": month,
        "prev_month": shift_month(month, -1),
        "per_head_monthly": per_head,
        "basis": basis,
        "example": example,  # 가상 명부(예시) 인원이면 True: 확정하지 않는다
        "approved": approved,
        "actual_spend": None,  # 팀별 지출 자료가 아직 없다 (화면은 "준비 중")
    }
    if heads is None:
        return base | {
            "status": "unavailable",
            "rows": [],
            "total": None,
            "prev_total": None,
            "change_total": None,
            "headcount_total": None,
            "signature": None,
        }
    teams = sorted(set(heads) | set(prev_heads or {}))
    rows = []
    for team in teams:
        n = heads.get(team, 0)
        prev = None if prev_heads is None else prev_heads.get(team, 0)
        budget = n * per_head
        prev_budget = None if prev is None else prev * per_head
        rows.append(
            {
                "team": team,
                "headcount": n,
                "budget": budget,
                "prev_headcount": prev,
                "prev_budget": prev_budget,
                "change": None if prev_budget is None else budget - prev_budget,
            }
        )
    total = sum(r["budget"] for r in rows)
    prev_total = None if prev_heads is None else sum(prev_heads.values()) * per_head
    sig = signature(month, per_head, heads)
    if approved is None:
        status = "pending"
    elif approved.get("signature") == sig:
        status = "approved"
    else:
        status = "changed"  # 확정한 뒤 인원·단가가 바뀌었다
    return base | {
        "status": status,
        "rows": rows,
        "total": total,
        "prev_total": prev_total,
        "change_total": None if prev_total is None else total - prev_total,
        "headcount_total": sum(heads.values()),
        "signature": sig,
    }


def approval(data: dict[str, Any], person: str, at: str, note: str | None) -> dict[str, Any]:
    """재무 담당이 확정한 그 달 팀 예산 (확정 당시 숫자를 그대로 남긴다)."""
    return {
        "month": data["month"],
        "per_head_monthly": data["per_head_monthly"],
        "rows": [
            {"team": r["team"], "headcount": r["headcount"], "budget": r["budget"]}
            for r in data["rows"]
        ],
        "total": data["total"],
        "signature": data["signature"],
        "by": person,
        "at": at,
        "note": note,
    }
