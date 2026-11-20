import { tokenFree } from "./apiDirect";
import { SAFETY_API_URL } from "./env";
import { sessionAuth } from "./humanToken";
import type { ExecutionRecord, Review } from "./finance";

// 안전 처리 로그(safety, 8001) 읽기 전용 클라이언트. 모든 AI의 상태 변경(돈·게시·배포)은 execute()로
// 이 관문을 지나고, 여기서 등급(A·B·C)을 매겨 바로 실행·보류(사람 결정)·거절한다.
// 화면은 기록만 읽는다. 보류 결정은 그 일을 맡은 화면(고객지원·마케팅·재무)에서 사람이 한다.

export interface SafetyRule {
  key: string;
  action: string;
  metric: string;
  op: string;
  value: number | null;
  field: string | null;
  when: Record<string, unknown>;
}
export interface SafetyThresholds {
  version: number;
  verify_timeout_seconds: number;
  review_timeout_seconds: number;
  review_timeout_by_action: Record<string, number>;
  rules: SafetyRule[];
}
export interface RegisteredAction {
  action: string;
  owner: string;
  base_grade: "A" | "B" | "C";
  verifier: string | null;
}
export interface ConnectionHold {
  connection_id: string;
  hold: boolean;
  product: string;
  version: string;
  kev_cves: string[];
  judged_at: string;
  reason: string;
}

export class SafetyApiError extends Error {}

async function get<T>(path: string): Promise<T> {
  let res: Response;
  try {
    // 로그인 세션이 있으면 세션 토큰을 싣는다(없으면 지금처럼 헤더 없이). 토큰을 안 보는 읽기는 싣지 않는다
    res = await fetch(`${SAFETY_API_URL}${path}`, { cache: "no-store", headers: tokenFree("safety", path) ? {} : sessionAuth() });
  } catch {
    throw new SafetyApiError("안전 처리 로그 서버에 연결하지 못했습니다. 서버가 켜져 있는지 확인하세요.");
  }
  const body = (await res.json().catch(() => null)) as { data?: T; error?: { message?: string } } | null;
  if (!res.ok || !body || body.data === undefined) {
    throw new SafetyApiError(body?.error?.message ?? `안전 처리 로그 서버가 답하지 못했습니다 (${res.status}).`);
  }
  return body.data;
}

export const safety = {
  executions: () => get<ExecutionRecord[]>("/api/safety/executions"),
  reviews: () => get<Review[]>("/api/safety/reviews"),
  thresholds: () => get<SafetyThresholds>("/api/safety/thresholds"),
  registry: async () => (await get<{ actions: RegisteredAction[] }>("/api/safety/registry")).actions,
  connectionHolds: () => get<ConnectionHold[]>("/api/safety/connection-holds"),
};

/** 기준 이름 (SafetyQueue RULE_KO에 없는 것). 화면에만 쓴다 */
export const SAFETY_RULE_KO: Record<string, string> = {
  pr_changed_lines: "바꾼 줄 수", pr_changed_files: "바꾼 파일 수", pr_allowed_paths: "고쳐도 되는 폴더 밖을 바꿈", pr_protected_paths: "보호 파일을 바꿈",
  spec_open_question: "답 안 된 질문", spec_rejected_warning: "무시한 경고", spec_no_basis: "근거 수", irreversible: "되돌릴 수 없는 일",
  handover_completeness: "인수인계 채움 정도(%)",
  merge_changed_lines: "바꾼 줄 수", merge_changed_files: "바꾼 파일 수", merge_allowed_paths: "고쳐도 되는 폴더 밖을 바꿈", merge_protected_paths: "보호 파일을 바꿈",
  pr_review_mismatch: "리뷰와 다른 곳", pr_linked_incident: "장애와 이어진 변경", merge_cross_active_incident: "지금 장애와 겹침",
  merge_cross_data_verdict: "데이터 AI가 위험하다고 봄", merge_cross_recent_incident: "최근 장애와 겹침", merge_cross_evidence_missing: "확인 근거 빠짐", merge_irreversible: "되돌릴 수 없는 일",
  auto_policy_shrink_only: "자동 처리 범위를 넓힘", auto_policy_to_level: "자동 처리 단계",
  revert_changed_lines: "바꾼 줄 수", revert_changed_files: "바꾼 파일 수", revert_allowed_paths: "고쳐도 되는 폴더 밖을 바꿈", revert_protected_paths: "보호 파일을 바꿈", revert_irreversible: "되돌릴 수 없는 일",
  rollback_multiple_suspects: "의심 배포 수", rollback_low_confidence: "확신이 낮음", rollback_irreversible: "되돌릴 수 없는 일", rollback_repeat: "이미 되돌린 적 있음",
  audio_cost_single: "소리 한 건 비용", campaign_budget_daily: "오늘 캠페인 예산 합계", campaign_budget_zero: "캠페인 예산",
  purge_inquiries_batch: "한 번에 지우는 문의 수", purge_claims_batch: "한 번에 지우는 클레임 수",
};

/** 일 이름 (재무 ACTION_KO에 없는 것). 모르는 일은 코드 그대로 둔다 */
export const SAFETY_ACTION_KO: Record<string, string> = {
  end_incident_notice: "장애 끝 공지", receive_campaign_brief: "캠페인 기획안 받기", approve_data_deletion: "데이터 삭제 승인",
  create_inquiry: "문의 접수", add_customer_message: "고객 메시지 추가", send_reply: "고객에게 답장", reject_draft: "답장 초안 반려",
  set_inquiry_status: "문의 상태 바꾸기", set_triage: "문의 분류", record_claim_activity: "클레임 기록", submit_feedback: "의견 보내기",
  submit_idea: "아이디어 보내기", send_idea_digest: "아이디어 모음 보내기", set_feedback_status: "의견 상태 바꾸기", add_resolution_note: "해결 메모",
  purge_expired_claims: "기한 지난 클레임 지우기", purge_expired_inquiries: "기한 지난 문의 지우기", request_change: "변경 요청",
  decline_change_proposal: "변경 제안 거절", propose_incident_compensation: "장애 보상 제안", decide_incident_compensation: "장애 보상 결정",
  send_planning_answer: "기획 질문에 답", reveal_original: "원문 보기", create_claim: "클레임 접수", decide_claim: "클레임 결정",
  receive_return: "반품 받기", inspect_return: "반품 검수", record_case: "사례 기록", record_org_change: "발령 기록", decide_org_change: "발령 확정",
  confirm_team_budget: "팀 예산 확정", set_org_roster_complete: "전 직원 등록 완료", record_leave: "휴가 기록", cancel_leave: "휴가 취소",
  start_handover: "인수인계 시작", answer_handover_question: "인수인계 질문 답", decide_candidate_card: "후보 카드 결정",
  confirm_handover: "인수인계 확정", confirm_handover_incomplete: "덜 된 인수인계 확정", release_fault: "장애 풀기",
  finance_request_change: "재무 변경 요청", finance_withdraw_change: "재무 요청 철회", finance_answer_planning: "재무가 기획 질문에 답",
  "memory.card_write": "회사 기억에 남기기", "memory.card_link": "회사 기억 잇기", "memory.card_review": "회사 기억 검토",
  send_campaign_update: "캠페인 진행 알림", receive_change_result: "변경 결과 받기", create_ticket: "요청 접수", withdraw_ticket: "요청 철회",
  start_dev: "개발 시작", receive_dev_result: "개발 결과 받기", unlink_duplicate: "중복 요청 풀기", reject_spec: "기획서 반려",
  request_release_check: "출시 확인 요청", receive_request_pause: "요청 멈춤 받기", request_deploy: "배포 요청", approve_deploy: "배포 승인",
  receive_release_order: "출시 지시 받기", send_release_order: "출시 지시", receive_planning_answer: "기획 답 받기", receive_insight: "분석 결과 받기",
  receive_campaign_update: "캠페인 알림 받기", approve_repo: "레포 연결 승인", reject_repo: "레포 연결 거절", write_spec: "기획서 쓰기",
  split_tasks: "작업 나누기", review_pr: "코드 리뷰", send_message: "메시지 보내기", link_incident: "이슈에 코드 변경 연결",
  auto_policy_update: "자동 처리 기준 바꾸기", confirm_spec: "기획서 확정", merge_pr: "코드 변경 합치기", repo_revert_pr: "코드 변경 되돌리기",
  repo_close_pr: "코드 변경 닫기", repo_reopen_pr: "코드 변경 다시 열기", repo_comment: "코드 변경에 댓글", repo_delete_branch: "브랜치 지우기",
  code_write: "코드 쓰기", dev_chat: "개발 대화",
  retry_stuck_intake: "멈춘 접수 다시 시도",
};
