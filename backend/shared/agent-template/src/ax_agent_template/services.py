"""서비스 15개와 에이전트의 대응표.

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
            "diagnose_agent_issue",
            "orchestrator_chat_send",
            "orchestrator_chat_close",
            "orchestrator_chat_remove",
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
            "inquiry_order_counts",
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
            "support_chat_remove",
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
            "list_buyer_orders",
            "claim_stats",
            "book_issue_stats",
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
            "receive_improvement_note",
        ),
        "클레임·보상",
    ),
    # 판매자 정산 (2026-11-12 재무에서 옮김). 처음에는 claims 안에 있었는데, 주문 30만 건 장부를
    # 읽고
    # 계산하는 동안 같은 프로세스의 클레임 처리가 느려져 따로 띄운다. 화면은 클레임·보상 메뉴의
    # `/settlements`이고, 켠 서비스 목록에서는 claims를 따른다(`ENABLED_WITH`)
    Service(
        "settlement",
        "support",
        8126,
        (
            "seller_settlement",
            "seller_settlement_weeks",
            "seller_settlement_batches",
            "seller_settlement_deadlines",
            "request_seller_settlement",
        ),
        "판매자 정산",
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
            "channel_followers",
            "character_sheet_prompt",
            "storyboard_prompt",
            "higgsfield_status",
            "creative_records_list",
            "creative_record_get",
            "creative_video_request",
            "creative_record_attach",
            "receive_campaign_brief",
            "list_campaign_briefs",
            "get_campaign_brief",
            "handoff_brief_to_sns",
            "build_campaign_link",
            "marketing_chat_send",
            "marketing_chat_list",
            "marketing_chat_get",
            "marketing_chat_close",
            "marketing_chat_remove",
            "issue_coupon",
            "start_campaign",
            "record_campaign_result",
            "draft_sns_post",
            "prepare_video_draft_from_plan",
            "draft_outage_sns_post",
            "list_outage_sns_drafts",
            "merge_outage_sns_drafts",
            "check_sns_post",
            "publish_sns",
            "get_publish_control",
            "set_publish_control",
            "get_manual_publish_package",
            "complete_manual_publish",
            "generate_media",
            "prepare_sns_post_from_media",
            "prepare_brief_media",
            "choose_brief_audio",
            "request_brief_publish",
            "prepare_outage_sns_media",
            "request_outage_sns_publish",
            "cancel_campaign",
            "receive_improvement_note",
            "list_video_providers",
            "select_video_provider",
            "save_custom_video_provider",
            "delete_custom_video_provider",
            "get_media_cost_limits",
            "lower_media_cost_limits",
            "raise_media_cost_limits",
            "set_cost_limit_option",
            "add_secret",
            "change_secret",
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
            # 인사가 확정한 팀 예산 기록 (11/13, 확정은 인사)
            "record_team_budget",
            "answer_planning_question",
            # 기획이 영상 기획안을 넘기기 전에 카테고리 제작비를 묻는다 (11/18)
            "category_budget_advice",
            "record_campaign_result",
            "review_campaign_budget",
            "verify_campaign_budget",
            "finance_insights",
            "cancel_campaign",
            "record_incident_finance",
            "finance_chat_send",
            "finance_chat_close",
            "finance_chat_remove",
            "finance_chat_list",
            "finance_chat_get",
            "receive_improvement_note",
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
            "sales_analysis",
            # 고객·시장 분석 (11/16, 표준 판매 저장소만, grade read)
            "segment_options",
            # 글 질문 → 고르는 칸 값 (LLM은 카탈로그 보기 안의 code만 고름, grade read)
            "segment_question",
            "segment_profile",
            "segment_favorites",
            "market_coverage",
            # 함께 사는 장르·상품 (11/17, 표준 판매 저장소만, grade read)
            "basket_pairs",
            # 재구매·이탈, 첫 구매 경로별 고객 가치 (11/17, grade read)
            "retention",
            "channel_value",
            # 이벤트 제안 (11/16). event_proposal_send는 grade A (execute 뒤 기획 receive_insight)
            "event_proposal",
            "event_proposal_send",
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
            # 요즘 유행·밈 (11/18, grade read)
            "trend_memes",
            # 검색 트렌드 · 도서 분야 (11/18, grade read)
            "keyword_trend",
            # 인스타그램 (11/18): 오디오·레이더·메모 목록은 read, 메모 추가는 A (safety 등록 필요)
            "instagram_audio",
            "instagram_radar",
            "instagram_memo_list",
            "instagram_memo_add",
            # 트렌드 분석 의뢰 보고서 (11/19, grade read, 끝나면 data.trend_report_ready)
            "trend_report_create",
            "trend_report_get",
            "trend_report_list",
            # 총괄 plan ②: 보고서 → 기획 receive_insight 입력 (11/20, grade read)
            "trend_report_insight",
            # 사람이 넣은 의뢰의 인사이트를 기획에 보내기 (11/20 v3, grade A, execute 먼저)
            "trend_report_handoff",
            "data_chat_send",
            "data_chat_list",
            "data_chat_get",
            "data_chat_close",
            "data_chat_remove",
            "receive_improvement_note",
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
        "archive",
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
            "find_similar_decisions",
            "decide_candidate_card",
            "retire_request_cards",
            "answer_planning_question",
            "receive_improvement_note",
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
            "confirm_team_budget",
            "list_org_changes",
            "set_org_roster_complete",
            "record_leave",
            "cancel_leave",
            "get_presence",
            "find_org_change_basis",
            "parse_org_change_text",
            "describe_org_change",
            "evaluate_agents",
            "judge_step_timings",
            "request_agent_diagnosis",
        ),
        "조직 변동",
    ),
    Service(
        "planning",
        "planning",
        8101,
        (
            "create_ticket",
            # 요청 부서가 자기 요청 거둬들이기 (재무 화면 "요청 빼기", 11/13)
            "withdraw_ticket",
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
            "planning_chat_remove",
            "receive_improvement_note",
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
            "revert_merged_pr",
            "verify_rollback_target",
            "list_in_progress",
            "list_dev_progress",
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
            "dev_chat_remove",
            "receive_improvement_note",
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
            "infra_chat_remove",
            "receive_improvement_note",
        ),
        "인프라 AI",
    ),
    # 사내 PC·서버 연결: 아직 백엔드 skill이 없다 (backend/services/hosts/README.md)
    Service("hosts", "infra", 8117, (), "PC·서버 연결"),
)

BY_ID: dict[str, Service] = {s.id: s for s in SERVICES}
# 화면 서비스 목록(`services.ts`)에 따로 없고 다른 서비스를 따라 켜지는 서비스 (서비스 id → 따르는
# id).
# 판매자 정산은 화면에서 클레임·보상 메뉴 안에 있다
ENABLED_WITH: dict[str, str] = {"settlement": "claims"}
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
