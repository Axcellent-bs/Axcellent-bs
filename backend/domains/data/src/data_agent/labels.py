"""부서·서비스·skill의 한국어 표시명 (activity_report 문장·화면용).

- skill 이름은 화면 `frontend/src/lib/labels.ts`의 `SKILL_KO`와 같은 말을 쓴다
  (두 곳이 어긋나지 않게).
  화면 표에 없는 skill만 여기서 더한다.
  여기도 없으면 "기타 작업(원래 이름)"이다(이름을 지어내지 않는다).
- 서비스 이름은 `ax_agent_template.services`의 `Service.name`을 쓴다.
"""

from __future__ import annotations

from ax_agent_template.services import BY_ID

DEPARTMENT_KO: dict[str, str] = {
    "orchestrator": "총괄",
    "support": "고객지원",
    "marketing": "마케팅",
    "finance": "재무",
    "data": "데이터분석",
    "hr": "인사",
    "planning": "기획개발",
    "infra": "인프라",
}

SKILL_KO: dict[str, str] = {
    "find_active_incident": "진행 중인 사건 찾기",
    "list_inquiries": "문의 목록 보기",
    "get_inquiry": "문의 보기",
    "check_reply": "답변 점검",
    "summarize_inquiry": "문의 요약",
    "create_inquiry": "문의 만들기",
    "add_customer_message": "고객 메시지 추가",
    "send_reply": "답변 보내기",
    "reject_draft": "답변 초안 버리기",
    "set_inquiry_status": "문의 상태 바꾸기",
    "inquiry_overview": "문의 현황 보기",
    "detect_inquiry_surge": "문의 급증 감지",
    "request_change": "변경 요청",
    "list_change_requests": "변경 요청 목록 보기",
    "receive_change_result": "변경 결과 받기",
    "diagnose_payment": "결제 문제 진단",
    "prepare_handoff": "인계 준비",
    "assess_deletion_request": "삭제 요청 검토",
    "request_compensation": "보상 요청",
    "list_claims": "클레임 목록 보기",
    "get_claim": "클레임 보기",
    "claim_stats": "클레임 통계 보기",
    "claim_queue": "보상 큐 보기",
    "create_claim": "클레임 접수",
    "decide_claim": "클레임 판정",
    "receive_return": "반품 입고 처리",
    "inspect_return": "반품 검수",
    "process_claim_queue": "보상 큐 처리",
    "suggest_compensation": "보상안 제안",
    "request_incident_compensation": "장애 보상 요청",
    "analyze_receipt": "영수증 분석",
    "analyze_claim_request": "클레임 신청 분석",
    "customer_signals": "고객 신호 보기",
    "list_at_risk_customers": "이탈 위험 고객 보기",
    "draft_notice": "공지 초안 쓰기",
    "publish_notice": "공지 올리기",
    "get_notice": "공지 보기",
    "list_active_notices": "게시 중인 공지 보기",
    "issue_coupon": "쿠폰 발급",
    "seller_review": "판매자 점검",
    "settlement_hold": "정산 보류",
    "closing_report": "결산 보고서",
    "audit_report": "감사 보고서",
    "anomaly_detection": "이상 징후 찾기",
    "impact_analysis": "영향 분석",
    "fraud_score": "부정 거래 점수",
    "kpi_summary": "지표 요약",
    "get_roster": "담당 명부 보기",
    "start_handover": "인수인계 시작",
    "answer_handover_question": "인수인계 질문 답하기",
    "confirm_handover": "인수인계 확인",
    "list_handovers": "인수인계 목록 보기",
    "list_open_questions": "남은 질문 보기",
    "find_similar_cases": "비슷한 과거 사건 찾기",
    "record_resolution": "해결 내용 기록",
    "record_event_times": "이벤트 시각 기록",
    "record_case": "결정 기록",
    "check_plan": "계획 점검",
    "list_candidate_cards": "후보 카드 보기",
    "get_audit_timeline": "감사 기록 보기",
    "decide_candidate_card": "후보 카드 결정",
    "create_ticket": "요청 접수",
    "find_related_prs": "관련 PR 찾기",
    "link_incident": "사건 연결",
    "verify_rollback_target": "롤백 대상 확인",
    "list_in_progress": "진행 중인 작업 보기",
    "verify_handover": "인계 확인",
    "diagnose_incident": "장애 진단",
    "rollback_deployment": "배포 롤백",
    "verify_recovery": "복구 확인",
    # 화면 표에 아직 없는 skill
    "root_cause_hypotheses": "원인 후보 찾기",
    "accuracy_report": "정확도 보고서",
    "activity_report": "부서별 AI 업무 리포트",
    "receive_dev_result": "개발 결과 받기",
    "start_dev": "개발 시작",
}


def department_label(department: str) -> str:
    return DEPARTMENT_KO.get(department, department)


OTHER_WORK = "기타 작업"


def skill_label(skill: str) -> str:
    """한국어 이름. 표에 없으면 영어 이름만 내보이지 않고 "기타 작업(원래 이름)"이다."""
    return SKILL_KO.get(skill) or f"{OTHER_WORK}({skill})"


def service_label(service_id: str) -> str:
    service = BY_ID.get(service_id)
    return service.name if service else service_id
