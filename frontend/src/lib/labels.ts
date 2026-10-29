import type { CommOutcome } from "./types";

export const CATEGORY_KO: Record<string, string> = {
  refund: "환불·반품", cancel: "주문 취소", delivery: "배송", defect: "불량·파손·오배송", payment: "결제",
  product: "상품", review: "후기", account: "적립금·계정", other: "기타",
};
export const SENTIMENT_KO: Record<string, string> = { calm: "차분", frustrated: "답답함", angry: "화남" };
export const URGENCY_KO: Record<string, string> = { low: "낮음", medium: "보통", high: "높음" };
export const STYLE_KO: Record<string, string> = { empathetic: "공감형", concise: "간결형", step_by_step: "단계형" };
export const STATUS_KO: Record<string, string> = { open: "진행 중", resolved: "해결", escalated: "이관" };
export const URGENCY_DOT: Record<string, string> = { high: "bg-red-500", medium: "bg-amber-400", low: "bg-emerald-400" };

export const CLAIM_TYPE_KO: Record<string, string> = { cancel: "주문 취소", return: "반품", exchange: "교환", compensation: "보상" };
export const CLAIM_REASON_KO: Record<string, string> = {
  change_of_mind: "단순변심", defect: "상품 불량", wrong_item: "오배송", damaged: "배송 중 파손",
  late_delivery: "배송 지연", missing_item: "배송 완료인데 받지 못함", other: "기타",
};
export const CLAIM_STATUS_KO: Record<string, string> = {
  needs_info: "사진 필요", needs_review: "상담원 확인 중", awaiting_return: "반품 입고 대기", return_received: "입고 완료·처리 대기",
  completed: "처리 완료", rejected: "접수 불가", withdrawn: "철회",
};
export const CLAIM_STATUS_TONE: Record<string, string> = {
  needs_info: "bg-amber-50 text-amber-700", needs_review: "bg-violet-50 text-violet-700", awaiting_return: "bg-brand-50 text-brand-700",
  return_received: "bg-brand-50 text-brand-700", completed: "bg-emerald-50 text-emerald-700", rejected: "bg-red-50 text-red-700",
  withdrawn: "bg-ink-100 text-ink-500",
};
export const INSPECTION_KO: Record<string, string> = { pending: "검수 대기", ok: "정상", mismatch: "사유와 불일치" };
export const PAYMENT_KO: Record<string, string> = { card: "신용·체크카드", transfer: "계좌이체", easy_pay: "간편결제" };

/** Rule codes in plain Korean, for the agent's rule tables. */
export const RULE_KO: Record<string, string> = {
  INVALID_REASON: "사유·종류 불일치", OWNER_MISMATCH: "주문자 불일치", ITEM_NOT_IN_ORDER: "영수증에 없는 상품", ALREADY_CLAIMED: "중복 신청", QTY_ADJUSTED: "수량 보정",
  CANCEL_TOO_LATE: "출고 후 취소", ALREADY_CANCELLED: "이미 취소됨", NOT_DELIVERED_YET: "배송 완료 전", CANCEL_INSTEAD: "취소로 신청해야 함",
  WINDOW_MIND_EXPIRED: "단순변심 기간 경과", WINDOW_DEFECT_EXPIRED: "불량 신청 기간 경과", NON_RETURNABLE: "환불 불가 상품", UNSEALED: "개봉·사용",
  EBOOK_DEFECT: "전자책 오류", NEED_EVIDENCE: "증빙 사진 필요", NOT_LATE: "지연 기준 미달", LATE_COMPENSATION: "지연 보상",
  ALREADY_COMPENSATED: "이미 보상됨", MISSING_ITEM_INVESTIGATE: "미수령 조사", RETURN_FEE: "반품 배송비", SHIPPING_FEE_LOST: "무료배송 상실",
  AMOUNT_ADJUSTED: "금액 보정", AMOUNT_EXCEEDS_RECEIPT: "영수증 금액 초과", DEFECT_COMPENSATION: "불량 보상", OVER_AUTO_LIMIT: "자동 한도 초과",
  ABUSE_SUSPECTED: "점검 대상", OTHER_REASON: "기타 사유", EXCHANGE_STOCK_LOW: "교환 재고 부족",
};
export const BLOCKER_KO: Record<string, string> = {
  NOT_INSPECTED: "검수 대기", ON_HOLD: "상담원 확인", ABUSE_HOLD: "점검 대상", OVER_LIMIT: "한도 초과", OUT_OF_STOCK: "재고 부족", LIMIT: "개수 제한",
};
export const AUDIT_KO: Record<string, string> = {
  submitted: "신청 접수", decided: "자동 판정", evidence_added: "사진 추가", withdrawn: "철회", agent_override: "상담원 승인",
  agent_confirmed: "상담원 거절", received: "반품 입고", inspected: "검수 결과 변경", paid: "지급 완료",
};
/** 영수증 대조 결과. 같음·다름·확인할 수 없음만 말하고 환불 판정은 하지 않는다. */
export const RECEIPT_VERDICT_KO: Record<string, string> = { consistent: "주문과 같음", mismatch: "주문과 다름", insufficient: "확인할 수 없음" };
export const RECEIPT_VERDICT_TONE: Record<string, string> = {
  consistent: "bg-emerald-50 text-emerald-700", mismatch: "bg-red-50 text-red-700", insufficient: "bg-amber-50 text-amber-700",
};
export const RECEIPT_MISMATCH_KO: Record<string, string> = {
  RECEIPT_AMOUNT_MISMATCH: "총 결제 금액이 주문과 다름", RECEIPT_DATE_MISMATCH: "결제일이 주문과 다름",
  RECEIPT_ORDER_NO_MISMATCH: "주문번호가 주문과 다름", RECEIPT_SHIPPING_MISMATCH: "배송비가 주문과 다름",
  RECEIPT_ITEM_AMOUNT_MISMATCH: "품목 금액이 주문과 다름", RECEIPT_ITEM_UNKNOWN: "주문에 없는 품목이 영수증에 있음",
};
export const RECEIPT_FIELD_KO: Record<string, string> = { total: "총 결제 금액", date: "결제일", order_no: "주문번호", shipping_fee: "배송비", items: "품목" };
export const RECEIPT_IMAGES_KO: Record<string, string> = {
  none: "사진 없음", read: "사진을 읽음", unavailable: "이미지 분석 미연결", failed: "사진 읽기 실패", skipped: "사진을 읽지 않음(개수·크기·형식)",
};
export const OUTCOME_KO: Record<string, string> = {
  auto_rejected: "자동 거절", needs_info: "자료 요청", needs_review: "상담원 확인", approved_pending_return: "승인(입고 후 지급)", auto_approved: "자동 승인·지급",
};

/** 공통 로그 소통 결과 5가지 (dashboard-api communications.py CommunicationOutcome).
 *  소통 탭과 AI 대화방이 이 표 하나만 쓴다 — 두 화면의 말이 어긋나지 않게. */
export const COMM_OUTCOME_KO: Record<CommOutcome, string> = {
  completed: "완료", rejected: "거절·실패", held: "보류(안전 관문)", needs_human: "사람 판단 필요", in_progress: "진행 중",
};
export const COMM_OUTCOME_TONE: Record<CommOutcome, string> = {
  completed: "bg-emerald-50 text-emerald-700", rejected: "bg-red-50 text-red-700", held: "bg-amber-50 text-amber-700",
  needs_human: "bg-amber-50 text-amber-700", in_progress: "bg-ink-100 text-ink-500",
};

/** 에이전트 업무(skill) 이름을 사람이 읽는 말로. 할 일함이 쓴다. skill 목록은 backend agent-template services.py.
 *  여기 없는 skill은 skillLabel()이 원래 이름을 그대로 보여 준다(지어내지 않는다). */
export const SKILL_KO: Record<string, string> = {
  find_active_incident: "진행 중인 사건 찾기",
  list_inquiries: "문의 목록 보기", get_inquiry: "문의 보기", check_reply: "답변 점검", summarize_inquiry: "문의 요약",
  create_inquiry: "문의 만들기", add_customer_message: "고객 메시지 추가", send_reply: "답변 보내기", reject_draft: "답변 초안 버리기",
  set_inquiry_status: "문의 상태 바꾸기", inquiry_overview: "문의 현황 보기", detect_inquiry_surge: "문의 급증 감지",
  request_change: "변경 요청", list_change_requests: "변경 요청 목록 보기", receive_change_result: "변경 결과 받기",
  diagnose_payment: "결제 문제 진단", prepare_handoff: "인계 준비", assess_deletion_request: "삭제 요청 검토",
  request_compensation: "보상 요청", list_claims: "클레임 목록 보기", get_claim: "클레임 보기", claim_stats: "클레임 통계 보기",
  claim_queue: "보상 큐 보기", create_claim: "클레임 접수", decide_claim: "클레임 판정", receive_return: "반품 입고 처리",
  inspect_return: "반품 검수", process_claim_queue: "보상 큐 처리", suggest_compensation: "보상안 제안",
  request_incident_compensation: "장애 보상 요청", analyze_receipt: "영수증 분석", analyze_claim_request: "클레임 신청 분석",
  customer_signals: "고객 신호 보기", list_at_risk_customers: "이탈 위험 고객 보기",
  draft_notice: "공지 초안 쓰기", publish_notice: "공지 올리기", get_notice: "공지 보기", list_active_notices: "게시 중인 공지 보기",
  issue_coupon: "쿠폰 발급",
  seller_review: "판매자 점검", settlement_hold: "정산 보류", closing_report: "결산 보고서", audit_report: "감사 보고서",
  anomaly_detection: "이상 징후 찾기", impact_analysis: "영향 분석", fraud_score: "부정 거래 점수", kpi_summary: "지표 요약",
  get_roster: "담당 명부 보기", start_handover: "인수인계 시작", answer_handover_question: "인수인계 질문 답하기",
  confirm_handover: "인수인계 확인", list_handovers: "인수인계 목록 보기", list_open_questions: "남은 질문 보기",
  record_case: "결정 기록", check_plan: "계획 점검", list_candidate_cards: "후보 카드 보기", get_audit_timeline: "감사 기록 보기",
  decide_candidate_card: "후보 카드 결정",
  create_ticket: "요청 접수",
  find_related_prs: "관련 PR 찾기", link_incident: "사건 연결", verify_rollback_target: "롤백 대상 확인",
  list_in_progress: "진행 중인 작업 보기", verify_handover: "인계 확인",
  diagnose_incident: "장애 진단", rollback_deployment: "배포 롤백", verify_recovery: "복구 확인",
};

export const skillLabel = (skill: string) => SKILL_KO[skill] ?? skill;
