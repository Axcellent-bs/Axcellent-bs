"""data Agent Card 검증 (이슈 #42)."""

import json
from pathlib import Path

import pytest

from ax_contracts.schemas.agent_card import AgentCard

CARD_PATH = Path(__file__).resolve().parents[1] / "agent-card.json"


@pytest.fixture(scope="module")
def card() -> AgentCard:
    return AgentCard.model_validate(json.loads(CARD_PATH.read_text(encoding="utf-8")))


def test_card_validates(card: AgentCard) -> None:
    assert card.name == "data"
    assert card.url == "http://data:8106/a2a"
    assert {s.id for s in card.skills} == {
        "anomaly_detection",
        "impact_analysis",
        "fraud_score",
        "kpi_summary",
        "root_cause_hypotheses",
        "accuracy_report",
        "activity_report",
    }


def test_all_skills_are_read(card: AgentCard) -> None:
    assert all(s.grade == "read" for s in card.skills)


def test_fraud_score_requesters(card: AgentCard) -> None:
    """재무에서 마켓운영이 빠져 지금 부르는 부서가 없다. 총괄만 남긴다.

    마켓운영 담당이 정해지면 그 부서를 연결한다.
    """
    skill = next(s for s in card.skills if s.id == "fraud_score")
    assert skill.allowed_requesters == ["orchestrator"]


def test_impact_analysis_requesters(card: AgentCard) -> None:
    skill = next(s for s in card.skills if s.id == "impact_analysis")
    assert set(skill.allowed_requesters) == {
        "orchestrator",
        "infra",
        "support",
        "finance",
        "planning",
    }
    assert skill.priority == "required"


def test_root_cause_is_a_lookup_for_orchestrator(card: AgentCard) -> None:
    skill = next(s for s in card.skills if s.id == "root_cause_hypotheses")
    assert set(skill.allowed_requesters) == {"orchestrator", "dashboard-api"}
    assert skill.handoff is False  # 꺼져 있으면 사람에게 넘기지 않고 건너뛴다


def test_activity_report_is_a_read_lookup_for_dashboard(card: AgentCard) -> None:
    skill = next(s for s in card.skills if s.id == "activity_report")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"dashboard-api", "orchestrator"}
    assert skill.handoff is False
