"""서비스 13개와 에이전트의 대응표.

AXcellent는 부서가 아니라 서비스 단위로 판다(고객사가 필요한 서비스만 고른다).
그래서 에이전트도 서비스마다 하나씩 띄운다. 업무 로직은 원래 부서 패키지
(`backend/domains/<부서>`)를 그대로 쓰고, 서비스는 그중 자기 skill만 카드에 올려 따로 띄운다.

- 다른 에이전트는 예전처럼 부서 이름(`support`, `hr` 등)으로 불러도 된다. `route_target()`이
  skill을 보고 그 skill을 맡은 서비스로 보낸다.
- 받는 쪽 `allowed_requesters`에는 부서 이름이 적혀 있다. 서비스 이름으로 온 요청은
  `department_of()`로 부서 이름까지 같이 본다
  (`claims`가 보낸 요청은 `support`가 보낸 것으로도 인정).
- 서비스 id는 화면 `frontend/src/demo/services.ts`의 `SERVICES` id와 같다.
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
    Service("orchestrator", "orchestrator", 8000, ("find_active_incident",), "총괄·사건"),
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
            "record_claim_activity",
            "submit_feedback",
            "set_feedback_status",
            "list_feedback",
            "feedback_overview",
            "inquiry_overview",
            "detect_inquiry_surge",
            "request_change",
            "list_change_requests",
            "receive_change_result",
            "diagnose_payment",
            "prepare_handoff",
            "assess_deletion_request",
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
            "inspect_return",
            "process_claim_queue",
            "suggest_compensation",
            "request_incident_compensation",
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
        ("draft_notice", "publish_notice", "get_notice", "list_active_notices", "issue_coupon"),
        "공지·쿠폰",
    ),
    Service(
        "finance",
        "finance",
        8105,
        (
            "closing_report",
            "audit_report",
            "record_support_cost",
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
        ),
        "데이터 분석",
    ),
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
            "list_candidate_cards",
            "list_cards",
            "get_audit_timeline",
            "decide_candidate_card",
        ),
        "결정 기록",
    ),
    Service("planning", "planning", 8101, ("create_ticket", "receive_dev_result"), "요청·기획서"),
    Service(
        "dev",
        "planning",
        8111,
        (
            "find_related_prs",
            "link_incident",
            "verify_rollback_target",
            "list_in_progress",
            "verify_handover",
            "start_dev",
        ),
        "개발·배포",
    ),
    Service(
        "incident",
        "infra",
        8107,
        ("diagnose_incident", "rollback_deployment", "verify_recovery"),
        "장애 대응",
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


def department_of(name: str) -> str:
    """서비스 id면 원래 부서 이름, 아니면 그대로."""
    service = BY_ID.get(name)
    return service.department if service else name


def route_target(target: str, skill: str) -> str:
    """부서 이름으로 부른 요청을 그 skill을 맡은 서비스로 보낸다. 대응이 없으면 그대로 둔다."""
    return _SKILL_HOME.get((target, skill), target)
