"""대화에 넣는 사실. 인사 데이터를 읽기만 한다.

이름·직원 번호는 넣지 않는다(팀 이름·인원·종류·날짜만).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from hr_agent.org import changes, ledger
from hr_agent.org.roster import org_teams
from hr_agent.services import Services

RECENT_CHANGES = 12
RECENT_CARDS = 8
TEXT_CHARS = 160


def _next_month(month: str) -> str:
    y, m = (int(p) for p in month.split("-"))
    return f"{y + 1:04d}-01" if m == 12 else f"{y:04d}-{m + 1:02d}"


def _headcount(records: list[ledger.Change], month: str, teams: list[str]) -> dict[str, Any]:
    first = ledger.earliest_month(records)
    if first is not None and month < first:
        return {
            "month": month,
            "known": False,
            "reason": f"{first}부터 기록돼 있어 이 달 인원은 알 수 없다",
        }
    try:
        members = list(ledger.roster_at(records, month).values())
    except ledger.LedgerError:
        return {"month": month, "known": False, "reason": "발령 기록이 서로 맞지 않아 셀 수 없다"}
    counts = Counter(members)
    return {
        "month": month,
        "known": True,
        "teams": {t: counts.get(t, 0) for t in (teams or sorted(counts))},
        "total": sum(counts.values()),
    }


def _short(text: Any) -> str:
    return " ".join(str(text or "").split())[:TEXT_CHARS]


async def collect(svc: Services) -> dict[str, Any]:
    teams = org_teams()
    records = changes.confirmed(svc)
    month = svc.clock().strftime("%Y-%m")
    out: dict[str, Any] = {
        "today": svc.clock().strftime("%Y-%m-%d"),
        "confirmed_records": len(records),
        "roster_complete": changes.roster_complete(svc),
        "registered_members": changes.registered_members(svc),
        "draft_changes": (
            svc.store.one("SELECT COUNT(*) AS n FROM org_changes WHERE status = 'draft'")
            or {"n": 0}
        )["n"],
    }
    if records:  # 조직도를 못 읽어도 확정 기록에 나온 팀으로 센다
        out["headcount"] = [_headcount(records, m, teams) for m in (month, _next_month(month))]
    else:
        out["headcount"] = "확정된 발령 기록이 없어 팀 인원을 알 수 없다"
    out["recent_changes"] = [
        {
            "kind": r.kind,
            "from_team": r.from_team,
            "to_team": r.to_team,
            "counts_from_month": r.counts_from_month,
        }
        for r in records[-RECENT_CHANGES:]
    ]
    try:
        cards = await svc.memory.list_cards(states=["active"])
    except Exception:  # noqa: BLE001 - 기억 서비스가 안 닿으면 카드 없이 답한다
        out["recent_cards"] = "결정 기록을 읽지 못했다"
    else:
        cards = [c for c in cards if not c.candidate]  # 후보는 판정 근거가 아니다
        cards = sorted(cards, key=lambda c: c.created_at, reverse=True)[:RECENT_CARDS]
        out["recent_cards"] = [
            {"title": _short(c.title), "decision": _short(c.extra.get("decision"))} for c in cards
        ]
    return out
