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
  atRisk: (minLevel: "medium" | "high" = "medium", limit = 20) =>
    get<CustomerSignals[]>(`${BASE}/customers/at-risk${qs({ min_level: minLevel, limit })}`),
  signals: (buyerId: string) => get<CustomerSignals>(`${BASE}/customers/${encodeURIComponent(buyerId)}/signals`),
  compensation: (inquiryId: number) => get<CompensationSuggestion>(`${BASE}/conversations/${inquiryId}/compensation-suggestion`),
  paymentDiagnosis: (inquiryId: number) => get<PaymentDiagnosis>(`${BASE}/conversations/${inquiryId}/payment-diagnosis`),
  inquirySurge: () => get<InquirySurge>(`${BASE}/inquiry-surge`),
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
  payment: "결제", delivery: "배송", refund: "환불·반품", product: "상품", account: "계정", other: "기타",
};

export const CHANGE_STATUS_KO: Record<string, { label: string; tone: Tone }> = {
  requested: { label: "요청됨", tone: "info" },
  completed: { label: "처리 완료", tone: "ok" },
  declined: { label: "반려", tone: "warn" },
};

/** 코드 → 쉬운 말. 모르는 코드는 그대로 */
export const ko = (map: Record<string, string>, code: string | null | undefined) => (code ? map[code] ?? code : UNKNOWN);
