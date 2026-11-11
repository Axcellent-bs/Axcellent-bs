"""서비스 14개와 에이전트의 대응표.

AXcellent는 부서가 아니라 서비스 단위로 판다(고객사가 필요한 서비스만 고른다).
그래서 에이전트도 서비스마다 하나씩 띄운다. 업무 로직은 원래 부서 패키지
(`backend/domains/<부서>`)를 그대로 쓰고, 서비스는 그중 자기 skill만 카드에 올려 따로 띄운다.

- 다른 에이전트는 예전처럼 부서 이름(`support`, `hr` 등)으로 불러도 된다. `route_target()`이
  skill을 보고 그 skill을 맡은 서비스로 보낸다.
- 받는 쪽 `allowed_requesters`에는 부서 이름이 적혀 있다. 서비스 이름으로 온 요청은
  `department_of()`로 부서 이름까지 같이 본다
  (`claims`가 보낸 요청은 `support`가 보낸 것으로도 인정).
- 서비스 id는 화면 `frontend/src/demo/services.ts`의 `SERVICES` id와 같다.
  다만 인수인계(`handover`)는 화면에서 접어 `services.ts`에 없다(정의는 되살릴 수 있게 남겼다).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Service:
    id: str
    department: str
    port: int
    skills: tuple[str, ...]
    name: str


SERVICES: tuple[Service, ...] = (
    Service(
        "orchestrator",
        "orchestrator",
        8000,
        (
            "find_active_incident",
            "answer_planning_question",
            "receive_finance_opinion",
            "orchestrator_chat_send",
            "orchestrator_chat_close",
            "orchestrator_chat_list",
            "orchestrator_chat_get",
        ),
        "총괄·사건",
    ),
    Service(
        "inquiry",
        "support",
        8102,
        (
            "list_inquiries",
            "get_inquiry",
            "check_reply",
            "summarize_inquiry",
            "create_inquiry",
            "add_customer_message",
            "send_reply",
            "reject_draft",
            "set_inquiry_status",
            "set_triage",
            "record_claim_activity",
            "submit_feedback",
            "inquiry_pull_status",
            "list_stuck_intake",
            "retry_stuck_intake",
            "submit_idea",
            "list_ideas",
            "send_idea_digest",
            "send_idea_report",
            "list_idea_trends",
            "set_feedback_status",
            "add_resolution_note",
            "preview_data_purge",
            "purge_customer_data",
            "purge_expired_inquiries",
            "reveal_original",
            "list_feedback",
            "feedback_overview",
            "inquiry_overview",
            "detect_inquiry_surge",
            "propose_change_requests",
            "decline_change_proposal",
            "answer_planning_question",
            "list_planning_questions",
            "send_planning_answer",
            "request_change",
            "list_change_requests",
            "receive_change_result",
            "diagnose_payment",
            "prepare_handoff",
            "assess_deletion_request",
            "knowledge_gap_report",
            "qa_findings",
            "get_privacy_audit",
            "support_chat_send",
            "support_chat_close",
            "support_chat_list",
            "support_chat_get",
            # 문의 저장소를 읽는 skill은 문의를 가진 서비스에 둔다 (저장소가 프로세스마다 따로라서)
            "analyze_receipt",
            "analyze_claim_request",
        ),
        "고객 문의",
    ),
    Service(
        "claims",
        "support",
        8112,
        (
            "request_compensation",
            "list_claims",
            "get_claim",
            "claim_stats",
            "claim_queue",
            "create_claim",
            "decide_claim",
            "receive_return",
            "claim_pull_status",
            "inspect_return",
            "process_claim_queue",
            "suggest_compensation",
            "request_incident_compensation",
            "list_incident_compensation",
            "propose_incident_compensation",
            "decide_incident_compensation",
            "list_incident_queue",
            "purge_expired_claims",
        ),
        "클레임·보상",
    ),
    Service(
        "reviews", "support", 8122, ("customer_signals", "list_at_risk_customers"), "리뷰·지식"
    ),
    Service(
        "coupon",
        "marketing",
        8103,
        (
            "draft_notice",
            "publish_notice",
            "end_incident_notice",
            "get_notice",
            "list_active_notices",
            "receive_campaign_brief",
            "list_campaign_briefs",
            "get_campaign_brief",
            "build_campaign_link",
            "marketing_chat_send",
            "marketing_chat_list",
            "marketing_chat_get",
            "marketing_chat_close",
            "issue_coupon",
            "start_campaign",
            "record_campaign_result",
            "draft_sns_post",
            "check_sns_post",
            "publish_sns",
            "generate_media",
            "cancel_campaign",
        ),
        "공지·쿠폰",
    ),
    Service(
        "finance",
        "finance",
        8105,
        (
            "closing_report",
            "record_support_cost",
            "answer_planning_question",
            "record_campaign_result",
            "review_campaign_budget",
            "verify_campaign_budget",
            "finance_insights",
            "cancel_campaign",
            "record_incident_finance",
            "finance_chat_send",
            "finance_chat_close",
            "finance_chat_list",
            "finance_chat_get",
        ),
        "재무 관리",
    ),
    Service(
        "data",
        "data",
        8106,
        (
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
        ),
        "데이터 분석",
    ),
    # 화면에서 접었다(인수인계). 정의·skill·계약은 되살릴 수 있게 그대로 두고,
    # run_all.sh가 띄우지 않고 켠 서비스에서도 뺀다
    Service(
        "handover",
        "hr",
        8104,
        (
            "get_roster",
            "start_handover",
            "answer_handover_question",
            "confirm_handover",
            "list_handovers",
            "list_open_questions",
        ),
        "인수인계",
    ),
    Service(
        "decisions",
        "hr",
        8114,
        (
            "record_case",
            "check_plan",
            "find_similar_fraud_cards",
            "find_similar_cases",
            "record_resolution",
            "record_event_times",
            "list_duplicate_groups",
            "decide_duplicate_group",
            "restore_merged_card",
            "list_candidate_cards",
            "list_cards",
            "get_audit_timeline",
            "evaluate_agents",
            "decide_candidate_card",
            "answer_planning_question",
        ),
        "결정 기록",
    ),
    # 조직 변동(발령) 기록과 팀 인원: 인사 AI의 독립 메뉴. 재무 팀 예산이 team_headcount를 부른다
    Service(
        "org",
        "hr",
        8124,
        (
            "team_headcount",
            "record_org_change",
            "decide_org_change",
            "list_org_changes",
            "set_org_roster_complete",
            "record_leave",
            "cancel_leave",
            "get_presence",
            "find_org_change_basis",
            "parse_org_change_text",
            "describe_org_change",
        ),
        "조직 변동",
    ),
    Service(
        "planning",
        "planning",
        8101,
        (
            "create_ticket",
            "receive_dev_result",
            "receive_planning_answer",
            "request_release_check",
            # 마케팅 게시(published)·데이터분석 성과(result)를 제안에 기록
            "receive_campaign_update",
            # 데이터분석·마케팅 인사이트를 기획 제안으로 받기
            "receive_insight",
            # 사람과 직접 대화 (dashboard-api /api/dashboard/planning-chat)
            "planning_chat_send",
            "planning_chat_list",
            "planning_chat_get",
            "planning_chat_close",
        ),
        "요청·기획서",
    ),
    # 개발은 기획과 다른 부서다. 기획 이름을 빌리지 않아 기획 권한도 같이 갖지 않는다
    Service(
        "dev",
        "dev",
        8111,
        (
            "find_related_prs",
            "link_incident",
            "verify_rollback_target",
            "list_in_progress",
            "list_dev_outcomes",
            "verify_handover",
            "start_dev",
            "receive_release_order",
            "receive_request_pause",
            # 사람과 직접 대화 (dashboard-api /api/dashboard/dev-chat)
            "dev_chat_send",
            "dev_chat_list",
            "dev_chat_get",
            "dev_chat_close",
        ),
        "개발·배포",
    ),
    Service(
        "incident",
        "infra",
        8107,
        (
            "diagnose_incident",
            "rollback_deployment",
            "verify_recovery",
            # 같은 유형 과거 장애의 처리 방식(장애 상태 해제) 다시 하기
            "release_fault",
            # 사람과 직접 대화 (dashboard-api /api/dashboard/infra-chat)
            "infra_chat_send",
            "infra_chat_list",
            "infra_chat_get",
            "infra_chat_close",
        ),
        "인프라 AI",
    ),
    # 사내 PC·서버 연결: 아직 백엔드 skill이 없다 (backend/services/hosts/README.md)
    Service("hosts", "infra", 8117, (), "PC·서버 연결"),
)

BY_ID: dict[str, Service] = {s.id: s for s in SERVICES}
SERVICE_PORTS: dict[str, int] = {s.id: s.port for s in SERVICES}

# (부서, skill) → 서비스 id
_SKILL_HOME: dict[tuple[str, str], str] = {
    (s.department, skill): s.id for s in SERVICES for skill in s.skills
}
# 예전에 다른 부서 이름으로 불리던 서비스 (서비스 id → 예전 부서 이름).
# 그 이름 + 이 서비스 skill로 온 호출은 주소만 맞춰 이 서비스로 보낸다. 권한(`department_of`)은
# 넘겨주지 않는다. 개발은 기획 부서 안에 있다가 따로 나왔다
_FORMER_DEPARTMENT: dict[str, str] = {"dev": "planning"}
for _sid, _old in _FORMER_DEPARTMENT.items():
    for _skill in BY_ID[_sid].skills:
        _SKILL_HOME.setdefault((_old, _skill), _sid)


def department_of(name: str) -> str:
    """서비스 id면 원래 부서 이름, 아니면 그대로."""
    service = BY_ID.get(name)
    return service.department if service else name


def route_target(target: str, skill: str) -> str:
    """부서 이름으로 부른 요청을 그 skill을 맡은 서비스로 보낸다. 대응이 없으면 그대로 둔다."""
    return _SKILL_HOME.get((target, skill), target)
