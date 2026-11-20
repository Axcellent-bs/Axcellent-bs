// skill·액션 코드 이름 → 사람이 읽는 이름 (대화방 말풍선·AI 오피스 말풍선·장애 대응 화면). node --test (skillNames.test.ts)
// 매핑이 없는 id만 원문(밑줄은 띄어 씀)으로 둔다
// 화면에는 이 이름만 보이고 코드 이름은 마우스를 올리면(툴팁) 보인다
export const ACTION_KO: Record<string, string> = {
  // 총괄 시나리오 A
  diagnose_incident: "원인 진단", anomaly_detection: "이상 탐지", impact_analysis: "영향 분석",
  rollback_deployment: "배포 되돌리기", verify_recovery: "복구 확인", restart_service: "서비스 다시 켜기",
  find_related_prs: "관련 코드 변경 찾기", link_incident: "이슈에 코드 변경 연결", verify_rollback_target: "되돌릴 배포 확인",
  draft_notice: "공지 초안 작성", publish_notice: "공지 게시", get_notice: "공지 조회",
  loss_aggregation: "손실 리포트", record_support_cost: "처리 결과 장부에 기록", record_case: "이슈 기록", check_plan: "예전 결정과 비교",
  // 돈이 나가는 일·안전 관문
  issue_coupon: "쿠폰 지급", refund_request: "환불", confirm_settlement: "판매자 정산 확정",
  verify_amount: "금액 다시 계산", review_hold: "보류 건 1차 판단", review_threshold_change: "기준 변경 판단",
  // 고객지원·기획·개발·인사
  send_reply: "고객 답변 보내기", request_compensation: "보상 요청", create_claim: "클레임 접수", decide_claim: "클레임 결정",
  get_claim: "클레임 조회", receive_return: "반품 입고", inspect_return: "반품 검수", create_ticket: "변경 요청 접수",
  generate_pr: "코드 변경 만들기", merge_pr: "코드 변경 합치기", confirm_spec: "기획서 확정",
  start_handover: "인수인계 시작", answer_handover_question: "인수인계 질문 답변", decide_candidate_card: "후보 기록 결정",
  request_incident_compensation: "피해 고객 한꺼번에 보상", suggest_compensation: "보상안 제안", diagnose_payment: "결제 기록 확인",
  kpi_summary: "핵심 지표 정리", "memory.card_write": "회사 기억에 남기기", "memory.card_link": "회사 기억 연결",
  find_active_incident: "진행 중인 이슈 찾기", create_inquiry: "고객 문의 접수", list_inquiries: "문의 목록 조회",
  get_inquiry: "문의 조회", inquiry_overview: "문의 현황 조회", list_claims: "클레임 목록 조회", claim_stats: "클레임 통계",
  claim_queue: "보상 대기열 조회", process_claim_queue: "보상 대기열 처리", customer_signals: "고객 이상 신호 조회",
  release_fault_injection: "쇼핑몰 장애 해제", receive_change_result: "변경 결과 받기",
  ledger_record: "장부에 기록", closing_report: "기간 손익 정리", record_incident_finance: "장애 비용 장부 기록",
  // 고객 의뢰(홍보 영상) 흐름 (11/20 대화방에 영어 id가 그대로 나왔다)
  generate_media: "영상 제작", prepare_video_draft_from_plan: "영상 초안 준비", trend_report_create: "트렌드 분석 보고서",
  trend_report_insight: "기획에 넘길 인사이트", receive_insight: "기획 제안 받기",
};
export const actionKo = (a: string) => ACTION_KO[a] ?? a;
/** 코드 이름 → 사람이 읽는 이름. 모르는 이름은 밑줄을 띄어 쓴다 */
export const skillKo = (a: string | null | undefined) => (a ? ACTION_KO[a] ?? a.replace(/_/g, " ") : "");

// 문장 속 skill id → 이름 (총괄·부서가 보낸 문장에 "generate_media" 같은 id가 그대로 들어올 때). 매핑이 있는 id만 바꾼다
const escapeRe = (k: string) => k.replace(/[.*+?^$()|[\]\\{}]/g, "\\$&");
const ID_ALT = Object.keys(ACTION_KO).sort((x, y) => y.length - x.length).map(escapeRe).join("|");
const ID_RE = new RegExp("(?<![\\w.])(" + ID_ALT + ")(?![\\w.])", "g");
export const humanizeSkills = (text: string | null | undefined): string => (text ? text.replace(ID_RE, (id) => ACTION_KO[id] ?? id) : "");
