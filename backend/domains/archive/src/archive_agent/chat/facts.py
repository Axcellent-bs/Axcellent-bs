"""대화에 넣는 사실. 회사 기억의 카드를 읽기만 한다.

사람 이름은 넣지 않는다(카드 제목·종류·날짜·짧은 본문만). 후보 카드는 확정 기록이 아니라 따로 센다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from archive_agent.services import Services

RECENT_INCIDENTS = 8
RECENT_DECISIONS = 8
TEXT_CHARS = 160


def _short(text: Any) -> str:
    return " ".join(str(text or "").split())[:TEXT_CHARS]


def _day(card: Any) -> str:
    return card.created_at.strftime("%Y-%m-%d")


async def collect(svc: Services) -> dict[str, Any]:
    out: dict[str, Any] = {"today": svc.clock().strftime("%Y-%m-%d")}
    try:
        cards = await svc.memory.list_cards(states=["active"])
    except Exception:  # noqa: BLE001 - 기억 서비스가 안 닿으면 카드 없이 답한다
        out["cards"] = "기록을 읽지 못했다"
        return out
    confirmed = [c for c in cards if not c.candidate]
    out["confirmed_by_kind"] = dict(Counter(c.kind for c in confirmed))
    out["waiting_candidates"] = sum(1 for c in cards if c.candidate)
    newest = sorted(confirmed, key=lambda c: c.created_at, reverse=True)
    out["recent_incidents"] = [
        {"title": _short(c.title), "date": _day(c), "summary": _short(c.body)}
        for c in newest
        if c.kind == "incident"
    ][:RECENT_INCIDENTS]
    out["recent_decisions"] = [
        {
            "title": _short(c.title),
            "date": _day(c),
            "decision": _short(c.extra.get("decision") or c.body),
        }
        for c in newest
        if c.kind == "decision"
    ][:RECENT_DECISIONS]
    return out
