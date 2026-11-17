"""`book_issue_stats`의 서비스 카드(`claims`)와 도메인 카드(`support`)가 같은 요청자를 허용한다.

두 카드가 어긋나면 부르는 쪽이 `FORBIDDEN_SKILL`을 받는다(#1045). 재무가 예산 의견에 고객 반응을
붙이려고 부르므로 `finance`가 둘 다에 있어야 한다.
"""

import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
DOMAIN_CARD = BACKEND / "domains" / "support" / "agent-card.json"
SERVICE_CARD = BACKEND / "services" / "claims" / "agent-card.json"


def _skill(card: Path, skill_id: str) -> dict:
    data = json.loads(card.read_text(encoding="utf-8"))
    (found,) = [s for s in data["skills"] if s["id"] == skill_id]
    return found


def test_both_cards_allow_the_same_requesters_for_book_issue_stats() -> None:
    domain = _skill(DOMAIN_CARD, "book_issue_stats")["allowed_requesters"]
    service = _skill(SERVICE_CARD, "book_issue_stats")["allowed_requesters"]
    assert sorted(domain) == sorted(service)


def test_finance_may_read_book_issue_stats_and_it_stays_a_read_skill() -> None:
    for card in (DOMAIN_CARD, SERVICE_CARD):
        skill = _skill(card, "book_issue_stats")
        assert "finance" in skill["allowed_requesters"], card.name
        assert {"data", "orchestrator", "dashboard-api"} <= set(skill["allowed_requesters"])
        assert skill["grade"] == "read"  # 읽기만: 재무가 고객지원 상태를 바꾸지 않는다
