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
        "trend_scan",
        "trend_candidates",
        "trend_history",
        "youtube_velocity",
        "content_recommend",
        "trend_popular",
        "promo_concepts",
        "shorts_stats",
        "answer_planning_question",
        "campaign_effect",
        "video_performance",
        "campaign_result",
        "receive_idea_report",
        "list_idea_reports",
        "trend_age",
        "trend_insight_brief",
        "domain_profile",
        "data_sources",
        "domain_subtopics",
        "source_plan",
        "video_explorer",
        "data_chat_send",
        "data_chat_list",
        "data_chat_get",
        "data_chat_close",
        "data_chat_remove",
    }


def test_data_chat_is_a_read_only_for_the_screen(card: AgentCard) -> None:
    """직접 대화: grade read, 화면(dashboard-api)만, execute() 안 씀 (registry 항목 필요 없음)."""
    for sid in ("data_chat_send", "data_chat_list", "data_chat_get", "data_chat_close"):
        skill = next(s for s in card.skills if s.id == sid)
        assert skill.grade == "read" and skill.allowed_requesters == ["dashboard-api"]
        assert "#DataChat" in skill.input_schema and "#DataChat" in skill.output_schema
    remove = next(s for s in card.skills if s.id == "data_chat_remove")
    assert remove.grade == "read" and remove.allowed_requesters == ["dashboard-api"]
    assert remove.input_schema.endswith("#DataChatRef")
    assert remove.output_schema.endswith("chat.py#ChatRemoveOutput")


def test_video_explorer_is_a_read_for_screen_marketing_planning_and_orchestrator(
    card: AgentCard,
) -> None:
    skill = next(s for s in card.skills if s.id == "video_explorer")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"dashboard-api", "marketing", "planning",
                                             "orchestrator"}  # fmt: skip
    assert skill.input_schema.endswith("#VideoExplorerRequest")
    assert skill.output_schema.endswith("#VideoExplorerOutput")


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


def test_anomaly_detection_is_a_read_for_orchestrator_infra_and_dev(card: AgentCard) -> None:
    """개발 교차검증·배포 신호가 최근 이상을 묻는다 (dev)."""
    skill = next(s for s in card.skills if s.id == "anomaly_detection")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"orchestrator", "infra", "dev"}


def test_service_card_has_the_same_requesters_as_the_domain_card() -> None:
    """서비스 카드(services/data)는 도메인 카드의 skill을 올려 띄운다. 요청자 목록이 같아야 한다."""
    service_path = CARD_PATH.parents[2] / "services" / "data" / "agent-card.json"
    domain = json.loads(CARD_PATH.read_text(encoding="utf-8"))
    service = json.loads(service_path.read_text(encoding="utf-8"))
    by_domain = {s["id"]: s["allowed_requesters"] for s in domain["skills"]}
    by_service = {s["id"]: s["allowed_requesters"] for s in service["skills"]}
    assert by_service == by_domain


def test_root_cause_is_a_lookup_for_orchestrator(card: AgentCard) -> None:
    skill = next(s for s in card.skills if s.id == "root_cause_hypotheses")
    assert set(skill.allowed_requesters) == {"orchestrator", "dashboard-api"}
    assert skill.handoff is False  # 꺼져 있으면 사람에게 넘기지 않고 건너뛴다


def test_activity_report_is_a_read_lookup_for_dashboard(card: AgentCard) -> None:
    skill = next(s for s in card.skills if s.id == "activity_report")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"dashboard-api", "orchestrator"}
    assert skill.handoff is False


def test_trend_scan_is_a_read_for_orchestrator_marketing_and_dashboard(card: AgentCard) -> None:
    skill = next(s for s in card.skills if s.id == "trend_scan")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"orchestrator", "marketing", "dashboard-api"}
    assert skill.input_schema.endswith("#TrendScanRequest")
    assert skill.output_schema.endswith("#TrendScanOutput")


def test_trend_candidates_is_a_read_for_orchestrator_marketing_and_dashboard(
    card: AgentCard,
) -> None:
    skill = next(s for s in card.skills if s.id == "trend_candidates")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"orchestrator", "marketing", "dashboard-api"}
    assert skill.input_schema.endswith("#TrendCandidatesRequest")
    assert skill.output_schema.endswith("#TrendCandidatesOutput")
    assert skill.handoff is False


def test_content_recommend_is_a_read_for_orchestrator_marketing_and_dashboard(
    card: AgentCard,
) -> None:
    skill = next(s for s in card.skills if s.id == "content_recommend")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"orchestrator", "marketing", "dashboard-api"}
    assert skill.input_schema.endswith("#ContentRecommendRequest")
    assert skill.output_schema.endswith("#ContentRecommendOutput")
    assert skill.handoff is False


def test_trend_history_is_a_read_for_orchestrator_marketing_and_dashboard(
    card: AgentCard,
) -> None:
    skill = next(s for s in card.skills if s.id == "trend_history")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"orchestrator", "marketing", "dashboard-api"}
    assert skill.input_schema.endswith("#TrendHistoryRequest")
    assert skill.output_schema.endswith("#TrendHistoryOutput")


def test_youtube_velocity_is_a_read_for_orchestrator_marketing_and_dashboard(
    card: AgentCard,
) -> None:
    skill = next(s for s in card.skills if s.id == "youtube_velocity")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"orchestrator", "marketing", "dashboard-api"}
    assert skill.input_schema.endswith("#YouTubeVelocityRequest")
    assert skill.output_schema.endswith("#YouTubeVelocityOutput")


def test_answer_planning_question_is_for_planning(card: AgentCard) -> None:
    """기획 되묻기 창구 (이슈 #266). 기획만 부르고, 상태를 바꾸지 않는다."""
    skill = next(s for s in card.skills if s.id == "answer_planning_question")
    assert skill.allowed_requesters == ["planning"]
    assert skill.grade == "read"
    assert skill.input_schema.endswith("planning.py#AnswerPlanningQuestionInput")
    assert skill.output_schema.endswith("planning.py#AnswerPlanningQuestionOutput")


def test_video_performance_is_a_read_for_planning_marketing_dashboard_orchestrator(
    card: AgentCard,
) -> None:
    skill = next(s for s in card.skills if s.id == "video_performance")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"planning", "marketing", "dashboard-api",
                                             "orchestrator"}  # fmt: skip


def test_campaign_result_is_a_read_for_planning_marketing_finance(card: AgentCard) -> None:
    """성과 결과 1건: 기획·마케팅·재무가 부서별 묶음(view)으로 부른다. 상태를 바꾸지 않는다."""
    skill = next(s for s in card.skills if s.id == "campaign_result")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"planning", "marketing", "finance", "dashboard-api",
                                             "orchestrator"}  # fmt: skip
    assert skill.input_schema.endswith("#CampaignResultRequest")
    assert skill.output_schema.endswith("#CampaignResultOutput")


def test_campaign_effect_is_a_read_for_marketing_dashboard_and_planning(card: AgentCard) -> None:
    """기획 measure_proposal이 성과를 묻는다 (planning)."""
    skill = next(s for s in card.skills if s.id == "campaign_effect")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {
        "orchestrator",
        "marketing",
        "dashboard-api",
        "planning",
    }
    assert skill.input_schema.endswith("#CampaignEffectInput")
    assert skill.output_schema.endswith("#CampaignEffectOutput")


def test_trend_age_is_a_read_for_orchestrator_marketing_planning_and_dashboard(
    card: AgentCard,
) -> None:
    skill = next(s for s in card.skills if s.id == "trend_age")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {
        "orchestrator",
        "marketing",
        "planning",
        "dashboard-api",
    }
    assert skill.output_schema.endswith("#TrendAgeOutput")


def test_trend_insight_brief_is_a_read_for_planning_orchestrator_and_dashboard(
    card: AgentCard,
) -> None:
    skill = next(s for s in card.skills if s.id == "trend_insight_brief")
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"planning", "orchestrator", "dashboard-api"}
    assert skill.output_schema.endswith("#TrendInsightBriefOutput")
