import { ApiError, failureError, isNginxBlocked } from "./apiError";
import type { DeadlineReport, FinanceMeta, Settlements } from "./finance";
import type { ChangeProposalSent, DeclineReason, IdeaArea, IdeaDigestResult, IdeaKind, IncidentCompensationDecision, PlanningAnswerResult } from "./support";
import type { AdminReview, Claim, ClaimDetail, ClaimIntake, ClaimSuggestion, ClaimStats, CommLine, CommMeta, Conversation, ConversationListItem, Dashboard, FeedbackItem, FeedbackOverview, FeedbackStatus, Health, Kpi, Me, PurgeReport, QueueRun, QueueView, ReceiptAnalysis, ReceiptImageInput, Summary, Validation } from "./types";

import { DASHBOARD_API_URL, SAFETY_HUMAN_TOKEN } from "./env";

// 상담원 화면 → dashboard-api. 경로 목록은 README의 API 표 참고.
const BASE = DASHBOARD_API_URL;

export { ApiError };

/** 응답 본문 해석: ax-backend 규약 `{data, meta}`이면 data를, 아니면(catbooks 백엔드) 본문 그대로 쓴다. */
function unwrap<T>(body: unknown): T {
  if (body && typeof body === "object" && !Array.isArray(body) && "data" in body && "meta" in body) return (body as { data: T }).data;
  return body as T;
}

/** 실패 본문 해석: ax-backend `{error: {code, message}}`와 FastAPI `{detail}` 둘 다 받는다. */
function toError(body: unknown, status: number): ApiError {
  if (isNginxBlocked(status, body)) return failureError(body, status);
  const b = (body ?? {}) as { error?: { code?: string; message?: string }; detail?: unknown };
  if (b.error?.message) return new ApiError(b.error.message, status, undefined, b.error.code);
  const detail = b.detail;
  if (detail && typeof detail === "object" && "validation" in detail) {
    const d = detail as { message: string; validation: Validation };
    return new ApiError(d.message, status, d.validation);
  }
  return new ApiError(typeof detail === "string" ? detail : `요청 실패 (${status})`, status);
}

/**
 * 쓰기(GET 외) 요청에는 사람 토큰을 붙인다 (ax-backend #295). 토큰이 없으면 서버가 401로 거절한다.
 * `/plan-checks`는 저장하지 않는 조회라 POST여도 붙이지 않는다. 인사 화면(`hr.ts`)도 이 함수를 쓴다.
 */
export const writeAuth = (method?: string, path = ""): Record<string, string> =>
  method && method !== "GET" && !path.includes("/plan-checks") && SAFETY_HUMAN_TOKEN
    ? { Authorization: `Bearer ${SAFETY_HUMAN_TOKEN}` }
    : {};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...writeAuth(init?.method, path), ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      /* body was not JSON */
    }
    throw toError(body, res.status);
  }
  return res.status === 204 ? (undefined as T) : unwrap<T>(await res.json());
}

/** `{data, meta}` 그대로 받는다 (meta가 필요한 목록용). 실패 처리는 request와 같다 */
async function requestEnvelope<T, M>(path: string): Promise<{ data: T; meta: M }> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  const body: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw toError(body, res.status);
  return body as { data: T; meta: M };
}

/** 쓰기인데 `{data, meta}`를 그대로 받는다 (판매자 정산처럼 meta에 수집 시각이 있는 응답) */
async function postEnvelope<T, M>(path: string, body: unknown): Promise<{ data: T; meta: M }> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...writeAuth("POST", path) },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const json: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw toError(json, res.status);
  return json as { data: T; meta: M };
}

/** 판매자 정산 묶음 요청 결과. 같은 주를 이미 요청했으면 `requested: false` */
export interface SettlementRequestResult { settlement: Settlements; requested: boolean }

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/api/dashboard/health"),
  // 새 문의 만들기에서 가상 고객 목록을 고른다
  users: () => request<Me[]>("/api/dashboard/users"),
  /** 부서 간 A2A 소통 (공통 로그). 공통 로그 읽기가 꽂히기 전에는 503 (ax-backend #249).
   *  `from_agent`·`to_agent`·`message_type`(request|reply)은 읽기 전용 거르개 (재무 "총괄이 재무에 맡긴 일") */
  communications: (q: { dept?: string; context_id?: string; from_agent?: string; to_agent?: string; message_type?: "request" | "reply" } = {}) => {
    const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => !!v) as [string, string][]).toString();
    return requestEnvelope<CommLine[], CommMeta>(`/api/dashboard/communications${qs ? `?${qs}` : ""}`);
  },

  // 상담원 화면. 읽기(GET)는 dashboard-api #17에 있다. 쓰기(POST)는 아직 서버에 없어 경로만 맞춰 둔 것이다.
  admin: {
    dashboard: (days = 14) => request<Dashboard>(`/api/dashboard/metrics?days=${days}`),
    kpi: () => request<Kpi>("/api/dashboard/kpis"),
    kb: () => request<{ id: string; category: string; title: string; section: string; text: string }[]>("/api/dashboard/kb-chunks"),
    list: () => request<ConversationListItem[]>("/api/dashboard/conversations"),
    get: (id: number) => request<Conversation>(`/api/dashboard/conversations/${id}`),
    create: (b: { user_id: number; subject: string; message: string; order_id?: string | null }) => post<Conversation>("/api/dashboard/conversations", b),
    customerMessage: (id: number, text: string) => post<Conversation>(`/api/dashboard/conversations/${id}/customer-messages`, { text }),
    check: (id: number, text: string) => post<Validation>(`/api/dashboard/conversations/${id}/check`, { text }),
    send: (id: number, b: { text: string; draft_style?: string | null; draft_text?: string | null }) =>
      post<Conversation>(`/api/dashboard/conversations/${id}/agent-messages`, b),
    rejectDraft: (id: number, draft_style: string) => post<void>(`/api/dashboard/conversations/${id}/reject-draft`, { draft_style }),
    /** 고객 요청 개인정보 삭제 미리보기(읽기): 이 문의를 낸 구매자의 문의 중 지워질 것과 보존 의무로 남을 것 */
    purgePreview: (id: number) => request<PurgeReport>(`/api/dashboard/privacy/purge-preview?mode=customer_request&inquiry_id=${id}`),
    /** 고객 요청 삭제. 안전 관문이 사람에게 보류해 처음엔 409(승인 대기), 승인한 뒤 다시 부르면 집행한다 */
    purgeCustomerData: (id: number) => post<PurgeReport>(`/api/dashboard/conversations/${id}/customer-data-purge`),
    receiptAnalysis: (id: number, b: { images?: ReceiptImageInput[]; receipt_text?: string }) =>
      post<ReceiptAnalysis>(`/api/dashboard/conversations/${id}/receipt-analysis`, b),
    feedback: (q: { status?: FeedbackStatus; max_rating?: number } = {}) =>
      request<FeedbackItem[]>(`/api/dashboard/feedback${Object.keys(q).length ? `?${new URLSearchParams(Object.entries(q).map(([k, v]) => [k, String(v)]))}` : ""}`),
    feedbackStats: () => request<FeedbackOverview | null>("/api/dashboard/feedback/stats"),
    setFeedbackStatus: (inquiryId: number, status: "visible" | "hidden") =>
      post<FeedbackItem>(`/api/dashboard/feedback/${inquiryId}/status`, { status }),
    /** 실패로 빼 둔 문의·클레임 접수를 다시 읽는 차례로 돌린다 (사람 토큰) */
    retryStuckIntake: (kind: "inquiry" | "claim", seq: number) =>
      post<{ kind: string; seq: number; requeued: boolean; error?: string }>(`/api/dashboard/inquiry-pull/stuck/${kind}/${seq}/retry`),
    submitFeedback: (id: number, b: { rating: number; resolved: boolean; comment: string }) =>
      post<Conversation>(`/api/dashboard/conversations/${id}/feedback`, b),
    addResolutionNote: (id: number, b: { root_cause: string; action_taken: string; result: string }) =>
      post<Conversation>(`/api/dashboard/conversations/${id}/resolution-notes`, b),
    // 문의 급증에서 만든 변경 요청 제안을 상담원이 고른 대로 기획에 보내거나 보내지 않는다 (사람 토큰 필요)
    sendChangeProposal: (b: { inquiry_id: number; title: string; evidence: string; evidence_ids: string[] }) =>
      post<ChangeProposalSent>(`/api/dashboard/change-proposals/send`, b),
    declineChangeProposal: (b: { topic_code: string; inquiry_id: number; reason: DeclineReason; note?: string }) =>
      post<{ topic_code: string; inquiry_id: number; reason: DeclineReason; decided_by: string; created: boolean }>(
        `/api/dashboard/change-proposals/decline`, b),
    // 장애 보상 대기를 사람이 고객별로 승인·거절한다 (사람 토큰 필요). 승인된 고객만 마케팅 쿠폰 요청으로 넘어간다
    decideIncidentCompensation: (b: { incident_id: string; approve_buyer_ids: string[]; reject_buyer_ids: string[] }) =>
      post<IncidentCompensationDecision>(`/api/dashboard/incident-compensation-queue/decide`, b),
    // 기획이 되물은 질문에 사람이 답한다 (사람 토큰 필요). 고객지원이 안전 관문을 거쳐 기획으로 보낸다
    answerPlanningQuestion: (b: { question_id: string; answer: string; evidence_ids?: string[] }) =>
      post<PlanningAnswerResult>(`/api/dashboard/planning-questions/answer`, b),
    // 쌓인 고객 아이디어를 묶음별로 기획에 넘긴다 (사람 토큰 필요). 종류·영역을 비우면 기준을 넘은 묶음 전부
    sendIdeaDigest: (b: { kind?: IdeaKind; area?: IdeaArea; min_ideas?: number }) =>
      post<IdeaDigestResult>(`/api/dashboard/ideas/digest`, b),
    revealOriginal: (id: number, messageId: number) =>
      post<{ message_id: number; text: string }>(`/api/dashboard/conversations/${id}/messages/${messageId}/original`),
    claimSuggestion: (id: number) => post<ClaimSuggestion>(`/api/dashboard/conversations/${id}/claim-suggestion`),
    createClaim: (id: number, b: ClaimIntake) => post<ClaimDetail>(`/api/dashboard/conversations/${id}/claims`, b),
    summary: (id: number) => post<Summary>(`/api/dashboard/conversations/${id}/summary`),
    setStatus: (id: number, status: string) => post<ConversationListItem>(`/api/dashboard/conversations/${id}/status`, { status }),
    /** 분류 필요: send=올리기, decide=분류 이름 정하고 마치기, clear=표시 지우기 */
    setTriage: (id: number, b: { action: "send" | "decide" | "clear"; label?: string }) => post<ConversationListItem>(`/api/dashboard/conversations/${id}/triage`, b),
    /** `since`(ISO 8601)를 주면 그 시각 이후 주문만 읽는다. 구매자의 주문이 수천 건이면 전부 읽기에 느리다. 최신순 */
    orders: (user_id: number, since?: string) =>
      request<{ id: number; order_no: string; status: string; status_ko: string; title: string }[]>(
        `/api/dashboard/orders?user_id=${user_id}${since ? `&since=${encodeURIComponent(since)}` : ""}`),
    claims: (status?: string) => request<Claim[]>(`/api/dashboard/claims${status ? `?status=${status}` : ""}`),
    claimStats: () => request<ClaimStats>("/api/dashboard/claims/stats"),
    claim: (id: number) => request<ClaimDetail>(`/api/dashboard/claims/${id}`),
    approve: (id: number, note: string) => post<ClaimDetail>(`/api/dashboard/claims/${id}/approve`, { note }),
    reject: (id: number, note: string) => post<ClaimDetail>(`/api/dashboard/claims/${id}/reject`, { note }),
    /** 만료(review_timeout)로 닫힌 환불을 다시 요청한다. 사람이 거절한 건은 409 */
    retryRefund: (id: number, note: string) => post<ClaimDetail>(`/api/dashboard/claims/${id}/retry-refund`, { note }),
    receive: (id: number, inspection: string, note?: string) => post<ClaimDetail>(`/api/dashboard/claims/${id}/receive`, { inspection, note }),
    inspect: (id: number, inspection: string, note?: string) => post<ClaimDetail>(`/api/dashboard/claims/${id}/inspection`, { inspection, note }),
    queue: () => request<QueueView>("/api/dashboard/queue-entries"),
    processQueue: (b: { limit?: number; claim_ids?: number[] } = {}) => post<QueueRun>("/api/dashboard/queue-entries/process", b),
    reviews: () => request<AdminReview[]>("/api/dashboard/reviews"),
    // 판매자 정산은 고객지원(claims 서비스)이 한다. 재무는 결과를 받아 보여 주기만 한다
    settlements: (period?: string | null) =>
      requestEnvelope<Settlements, FinanceMeta>(`/api/dashboard/settlements${period ? `?period=${encodeURIComponent(period)}` : ""}`),
    settlementDeadlines: () => requestEnvelope<DeadlineReport, FinanceMeta>("/api/dashboard/settlements/deadlines"),
    /** 보류 없는 판매자를 묶어 지급 승인 요청 → 안전 장치 보류 큐(항상 사람). 사람 토큰 필요, 돈은 움직이지 않는다 */
    requestSettlementBatch: (period: string) =>
      postEnvelope<SettlementRequestResult, FinanceMeta>("/api/dashboard/settlements/batch", { period }),
    setReview: (id: number, status: "visible" | "hidden") => post<AdminReview>(`/api/dashboard/reviews/${id}/status`, { status }),
  },
};
