import { ApiError } from "./apiError";
import { DASHBOARD_API_URL } from "./env";
import type { Envelope } from "./finance";

// 고객지원 AI 화면(/support)의 자료. dashboard-api의 읽기 API 6개(ax-backend #388)를 그대로 읽는다.
// 위험 단계·보상 후보·진단은 support가 코드로 센 값이다. 화면에서는 이름만 쉬운 말로 바꾸고 값은 만들지 않는다.

export const UNKNOWN = "확인할 수 없음";

// ---------------------------------------------------------------- 응답 모양 (support 출력 그대로)
export type RiskLevel = "low" | "medium" | "high";
export interface RiskReason { code: string; count: number; points: number }
export interface CustomerSignals {
  buyer_id: string;
  level: RiskLevel;
  score: number;
  reasons: RiskReason[];
  counts: Record<string, number>;
  last_contact_at: string | null;
  recommended_action: string;
  claim_pattern_review: boolean;
}

export interface CompensationSuggestion {
  eligible: boolean;
  reason_code: string | null;
  message: string;
  unit_value: number;
  count: number;
  total_value: number;
  facts: { order_id?: string; promised_delivery_at?: string; shipped_at?: string; delivered_at?: string; days_late?: number } | null;
  request: { inquiry_id: number; buyer_id: string; reason_code: string; count: number } | null;
  notes: string[];
}

export interface PaymentAttempt {
  payment_id: string;
  amount: number;
  method_code: string;
  status: string;
  failure_code: string | null;
  created_at: string | null;
}
export interface PaymentDiagnosis {
  situation: string;
  next_action: string;
  message: string;
  likely_cause: string | null;
  duplicate_amount: number;
  facts: {
    order_id: string;
    order_status: string;
    total_amount: number;
    attempts: PaymentAttempt[];
    succeeded_count: number;
    failed_count: number;
    last_failure_code: string | null;
  } | null;
  notes: string[];
}

export interface Surge { topic_code: string; count: number; baseline_per_window: number; ratio: number | null; sample_inquiry_ids: number[] }
export interface InquirySurge {
  window_minutes: number;
  baseline_hours: number;
  baseline_method: string;
  evaluated_at: string | null;
  total_in_window: number;
  surges: Surge[];
}

/** 문의 급증에서 코드가 만든 기획 변경 요청안. 만들기만 했고 아무것도 보내지 않았다. */
export interface ChangeProposal {
  topic_code: string;
  title: string;
  evidence: string;
  inquiry_id: number; // 대표 문의 (급증 샘플 중 번호가 가장 작은 문의)
  evidence_ids: string[]; // 나머지 샘플 문의 (inq_<번호>)
  count: number;
  ratio: number | null;
  already_requested: boolean;
  declined: boolean; // 상담원이 "보내지 않기"를 골랐다
  decline_reason: DeclineReason | null;
}
export type DeclineReason = "already_known" | "not_a_real_problem" | "other";
export const DECLINE_REASON_KO: Record<DeclineReason, string> = {
  already_known: "이미 알고 있는 문제예요",
  not_a_real_problem: "실제 문제가 아니에요",
  other: "그 밖의 이유",
};
/** 제안을 기획에 보낸 결과 (`request_change` 출력). linked_duplicate면 같은 요청이 이미 있어 거기에 묶인 것이다 */
export interface ChangeProposalSent {
  outcome: "created" | "linked_duplicate";
  request_id: string;
  context_id: string;
  duplicate_of: string | null;
}
export interface ChangeProposals {
  window_minutes: number;
  baseline_hours: number;
  evaluated_at: string | null;
  proposals: ChangeProposal[];
}

export type ChangeStatus = "requested" | "completed" | "declined";
export interface ChangeRequest {
  tenant_id: string;
  context_id: string;
  inquiry_id: number | null;
  source_ref: string | null;
  status: ChangeStatus | string;
  request_id: string | null;
  duplicate_of: string | null;
  result: unknown;
  notice_id: string | null;
  seq: number;
  created_at: string | null;
  updated_at: string | null;
}

/** 안전 관문 실행 기록 한 줄 (dashboard-api GET /executions). 요청 원문·금액 계산은 빼고 판정만 온다 */
export interface ExecutionRow {
  execution_id: string;
  created_at: string;
  request: { action: string; requester: string; context_id: string };
  decision: { grade: "A" | "B" | "C"; status: "executed" | "verifying" | "held" | "rejected" | "failed"; hold_reason?: string | null };
}

/** 사건 하나에서 보상 쿠폰을 받은 고객 (dashboard-api GET /incident-compensation) */
export interface CompensatedBuyer {
  buyer_id: string;
  status: "issued" | "linked_duplicate";
  reason_code: string;
  coupon_id: string | null;
  count: number;
  unit_value: number;
  total_value: number;
  marketing_outcome: string | null;
}
export interface IncidentCompensation {
  context_id: string;
  buyers: CompensatedBuyer[];
  issued_buyers: number;
  issued_total: number;
  missing_buyer_ids: string[] | null;
  outside_buyer_ids: string[] | null;
}
export interface IncidentCompensationList { incidents: IncidentCompensation[]; notes: string[] }

/** 장애 보상 대기 → 사람 승인 (dashboard-api GET /incident-compensation-queue). 승인하기 전에는 쿠폰이 나가지 않는다 */
export type QueueStatus = "pending" | "approved" | "sent" | "held" | "rejected";
export interface QueueRow {
  buyer_id: string;
  status: QueueStatus;
  reason_code: string;
  count: number;
  unit_value: number;
  total_value: number;
  impact_ref: string | null;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  coupon_id: string | null;
  sent_at: string | null;
}
export interface IncidentQueue { context_id: string; pending_buyers: number; pending_total: number; rows: QueueRow[] }
export interface IncidentQueueList { incidents: IncidentQueue[]; notes: string[] }
/** 기획이 되물었는데 기록으로 답하지 못해 사람이 답해야 하는 질문 (고객지원이 받아 둔 것) */
export interface PlanningQuestion {
  question_id: string; request_id: string; context_id: string; tenant_id: string;
  spec_id: string | null; about: string | null; question: string; options: string[];
  status: "open" | "answered"; received_at: string; answered_at: string | null; answered_by: string | null;
}
// 고객 아이디어(고객의 소리). 문의·불만과 따로 쌓이고, 묶음 현황과 숫자는 고객지원이 코드로 센 값이다
export type IdeaKind = "event" | "feature" | "improvement" | "product" | "other";
export type IdeaArea = "shipping" | "payment" | "product" | "benefit" | "usability" | "other";
export interface IdeaItem {
  idea_id: string; kind: IdeaKind; area: IdeaArea; kind_label: string; area_label: string;
  text: string; status: "new" | "sent"; created_at: string;
  sent_at: string | null; request_id: string | null; sent_by: string | null;
}
export interface IdeaGroup {
  kind: IdeaKind; area: IdeaArea; kind_label: string; area_label: string;
  idea_count: number; customer_count: number; first_at: string; last_at: string;
  top_words: { word: string; count: number }[]; idea_ids: string[]; ready: boolean;
}
export interface IdeaList { items: IdeaItem[]; total: number; new_count: number; groups: IdeaGroup[] }
export interface IdeaDigestRow {
  kind: IdeaKind; area: IdeaArea; idea_count: number; customer_count: number;
  request_id?: string; outcome?: "created" | "linked_duplicate"; duplicate_of?: string | null; error?: string;
}
export interface IdeaDigestResult { sent: IdeaDigestRow[]; failed?: number; message?: string }
export interface PlanningQuestionList { items: PlanningQuestion[]; open_count: number }
export interface PlanningAnswerResult {
  question_id: string; request_id: string;
  planning_outcome: "applied" | "duplicate" | "unknown_question"; status: "open" | "answered"; spec_id: string | null;
}
export interface IncidentCompensationDecision {
  incident_context_id: string;
  results: { buyer_id: string; status: "sent" | "held" | "approved" | "rejected" | "not_found" | "not_pending" | "failed"; coupon_id: string | null; total_value: number }[];
  approved: number;
  rejected: number;
  sent: number;
  stopped_early: boolean;
  notes: string[];
}

// ---------------------------------------------------------------- 주문 내역 (읽기 전용, 판매 서버 주문을 그대로)
// 판매 서버가 주지 않은 값은 null이다. 0이나 빈 문자열로 채우지 않고 화면에서 "—"로 보여 준다.
export interface OrderRow {
  id: number;
  order_no: string;
  status: string;
  status_ko: string;
  title: string;
  created_at: string | null;
  /** 실제 결제(현금) 금액 */
  total_amount: number | null;
  refunded_amount: number | null;
  item_count: number | null;
}
export interface OrderLine { title: string | null; qty: number | null; unit_price: number | null; paid_amount: number | null }
export interface OrderPayment { amount: number | null; method_code: string | null; status: string | null; failure_code: string | null; created_at: string | null }
export interface OrderDetail {
  order_no: string;
  status: string;
  status_ko: string;
  created_at: string | null;
  total_amount: number | null;
  refunded_amount: number | null;
  shipping_fee: number | null;
  points_used: number | null;
  coupon_discount: number | null;
  promised_delivery_at: string | null;
  shipped_at: string | null;
  delivered_at: string | null;
  items: OrderLine[];
  payments: OrderPayment[];
  /** 환불 기록, 시각 오름차순. 판매 서버가 환불 목록을 주지 않으면 null(또는 없음) */
  refunds?: OrderRefund[] | null;
}
export interface OrderRefund { amount: number | null; created_at: string | null }

// ---------------------------------------------------------------- 호출
async function get<T>(path: string): Promise<Envelope<T> & { meta: { total?: number } }> {
  let res: Response;
  try {
    res = await fetch(`${DASHBOARD_API_URL}${path}`, { cache: "no-store" });
  } catch {
    throw new ApiError("업무 서버(dashboard-api)에 연결하지 못했습니다. 서버가 켜져 있는지 확인하세요.", 0);
  }
  const body = (await res.json().catch(() => null)) as { data?: T; meta?: Envelope<T>["meta"]; error?: { message?: string } } | null;
  if (!res.ok) {
    const msg = body?.error?.message ?? (res.status === 404 ? "찾을 수 없습니다 (404)." : `요청에 실패했습니다 (${res.status}).`);
    throw new ApiError(msg, res.status);
  }
  if (!body || body.data === undefined) throw new ApiError("서버 응답이 비어 있습니다.", res.status);
  return { data: body.data, meta: { mock: false, ...(body.meta ?? {}) } };
}

const qs = (p: Record<string, string | number | undefined>) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
};

const BASE = "/api/dashboard";
export const support = {
  /** 구매자의 주문, 최신순. since 이상·until 미만(주문 시각, ISO 8601) */
  orders: (userId: number, since?: string, until?: string) =>
    get<OrderRow[]>(`${BASE}/orders${qs({ user_id: userId, since, until })}`),
  order: (orderNo: string) => get<OrderDetail>(`${BASE}/orders/${encodeURIComponent(orderNo)}`),
  atRisk: (minLevel: "medium" | "high" = "medium", limit = 20) =>
    get<CustomerSignals[]>(`${BASE}/customers/at-risk${qs({ min_level: minLevel, limit })}`),
  signals: (buyerId: string) => get<CustomerSignals>(`${BASE}/customers/${encodeURIComponent(buyerId)}/signals`),
  compensation: (inquiryId: number) => get<CompensationSuggestion>(`${BASE}/conversations/${inquiryId}/compensation-suggestion`),
  paymentDiagnosis: (inquiryId: number) => get<PaymentDiagnosis>(`${BASE}/conversations/${inquiryId}/payment-diagnosis`),
  inquirySurge: () => get<InquirySurge>(`${BASE}/inquiry-surge`),
  /** 사건별로 보상 쿠폰을 받은 고객. 사건을 주면 그 사건만 */
  incidentCompensation: (incidentId?: string) =>
    get<IncidentCompensationList>(`${BASE}/incident-compensation${qs({ incident_id: incidentId })}`),
  /** 장애 보상 대기·승인·보류 목록 (사건을 주면 그 사건만) */
  incidentQueue: (incidentId?: string) =>
    get<IncidentQueueList>(`${BASE}/incident-compensation-queue${qs({ incident_id: incidentId })}`),
  /** 사람이 답해야 하는 기획 질문 (기본은 아직 답하지 못한 것만) */
  planningQuestions: (status: "open" | "answered" = "open") =>
    get<PlanningQuestionList>(`${BASE}/planning-questions${qs({ status })}`),
  /** 고객이 낸 아이디어 목록과 기획에 넘길 묶음 현황 */
  ideas: (q: { status?: "new" | "sent"; minIdeas?: number } = {}) =>
    get<IdeaList>(`${BASE}/ideas${qs({ status: q.status, min_ideas: q.minIdeas })}`),
  changeProposals: () => get<ChangeProposals>(`${BASE}/change-proposals`),
  /** 한 사건 번호로 기록된 처리(안전 관문 실행 기록) */
  executions: (contextId: string) => get<ExecutionRow[]>(`${BASE}/executions${qs({ context_id: contextId })}`),
  changeRequests: (q: { inquiryId?: number; status?: ChangeStatus } = {}) =>
    get<ChangeRequest[]>(`${BASE}/change-requests${qs({ inquiry_id: q.inquiryId, status: q.status })}`),
};

// ---------------------------------------------------------------- 표시 도우미 (쉬운 말)
type Tone = "ok" | "warn" | "bad" | "info" | "muted";

export const LEVEL_KO: Record<string, { label: string; tone: Tone }> = {
  high: { label: "위험 높음", tone: "bad" },
  medium: { label: "위험 보통", tone: "warn" },
  low: { label: "위험 낮음", tone: "muted" },
};

export const REASON_KO: Record<string, string> = {
  unanswered_over_24h: "24시간 넘게 답 못 받은 문의",
  angry_messages_30d: "화난 말투 (30일)",
  frustrated_messages_30d: "답답함 표현 (30일)",
  escalated_inquiries: "사람에게 넘어간 문의",
  claims_90d: "클레임 (90일)",
  rejected_claims_90d: "거절된 클레임 (90일)",
  open_inquiries: "열린 문의",
  inquiries_30d: "문의 (30일)",
};

export const ACTION_KO: Record<string, string> = {
  personal_followup: "담당자가 직접 연락",
  proactive_check: "먼저 상황 확인",
  none: "따로 할 일 없음",
};

export const COMP_REASON_KO: Record<string, string> = { late_delivery: "배송 지연" };

export const SITUATION_KO: Record<string, string> = {
  paid: "결제 완료",
  payment_failed: "결제 실패",
  duplicate_charge: "중복 결제",
  pending: "결제 대기",
};
export const NEXT_ACTION_KO: Record<string, string> = {
  none: "따로 할 일 없음",
  wait_and_retry: "잠시 뒤 다시 결제",
  refund_duplicate: "중복 금액 환불",
  escalate: "사람에게 넘김",
};
export const CAUSE_KO: Record<string, string> = { payment_service_fault: "결제 서버 오류" };
export const FAILURE_KO: Record<string, string> = { PG_UNAVAILABLE: "결제 대행사 응답 없음" };
export const METHOD_KO: Record<string, string> = { card: "카드", transfer: "계좌이체", easy: "간편결제" };
export const PAY_STATUS_KO: Record<string, string> = { succeeded: "성공", failed: "실패", pending: "대기", refunded: "환불" };

export const TOPIC_KO: Record<string, string> = {
  payment: "결제", shipping: "배송", refund: "환불·반품", cancel: "주문 취소", product: "상품", account: "계정",
  review: "후기", privacy_deletion: "개인정보 삭제 요청", general: "일반 문의",
  delivery: "배송", other: "기타", // 예전 코드
};

export const CHANGE_STATUS_KO: Record<string, { label: string; tone: Tone }> = {
  requested: { label: "요청됨", tone: "info" },
  completed: { label: "처리 완료", tone: "ok" },
  declined: { label: "반려", tone: "warn" },
};

/** 코드 → 쉬운 말. 모르는 코드는 그대로 */
export const ko = (map: Record<string, string>, code: string | null | undefined) => (code ? map[code] ?? code : UNKNOWN);
