import { ApiError } from "./apiError";
import type { AdminReview, Claim, ClaimDetail, ClaimStats, CommLine, CommMeta, Conversation, ConversationListItem, Dashboard, Health, Kpi, Me, QueueRun, QueueView, Summary, Validation } from "./types";

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
  const b = (body ?? {}) as { error?: { code?: string; message?: string }; detail?: unknown };
  if (b.error?.message) return new ApiError(b.error.message, status);
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

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/api/dashboard/health"),
  // 새 문의 만들기에서 가상 고객 목록을 고른다
  users: () => request<Me[]>("/api/dashboard/users"),
  /** 부서 간 A2A 소통 (공통 로그). 공통 로그 읽기가 꽂히기 전에는 503 (목 모드 제외, ax-backend #249) */
  communications: (q: { dept?: string; context_id?: string } = {}) => {
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
    create: (b: { user_id: number; subject: string; message: string; order_id?: number | null }) => post<Conversation>("/api/dashboard/conversations", b),
    customerMessage: (id: number, text: string) => post<Conversation>(`/api/dashboard/conversations/${id}/customer-messages`, { text }),
    check: (id: number, text: string) => post<Validation>(`/api/dashboard/conversations/${id}/check`, { text }),
    send: (id: number, b: { text: string; draft_style?: string | null; draft_text?: string | null }) =>
      post<Conversation>(`/api/dashboard/conversations/${id}/agent-messages`, b),
    rejectDraft: (id: number, draft_style: string) => post<void>(`/api/dashboard/conversations/${id}/reject-draft`, { draft_style }),
    summary: (id: number) => post<Summary>(`/api/dashboard/conversations/${id}/summary`),
    setStatus: (id: number, status: string) => post<ConversationListItem>(`/api/dashboard/conversations/${id}/status`, { status }),
    orders: (user_id: number) => request<{ id: number; order_no: string; status: string; status_ko: string; title: string }[]>(`/api/dashboard/orders?user_id=${user_id}`),
    claims: (status?: string) => request<Claim[]>(`/api/dashboard/claims${status ? `?status=${status}` : ""}`),
    claimStats: () => request<ClaimStats>("/api/dashboard/claims/stats"),
    claim: (id: number) => request<ClaimDetail>(`/api/dashboard/claims/${id}`),
    approve: (id: number, note: string) => post<ClaimDetail>(`/api/dashboard/claims/${id}/approve`, { note }),
    reject: (id: number, note: string) => post<ClaimDetail>(`/api/dashboard/claims/${id}/reject`, { note }),
    receive: (id: number, inspection: string, note?: string) => post<ClaimDetail>(`/api/dashboard/claims/${id}/receive`, { inspection, note }),
    inspect: (id: number, inspection: string, note?: string) => post<ClaimDetail>(`/api/dashboard/claims/${id}/inspection`, { inspection, note }),
    queue: () => request<QueueView>("/api/dashboard/queue-entries"),
    processQueue: (b: { limit?: number; claim_ids?: number[] } = {}) => post<QueueRun>("/api/dashboard/queue-entries/process", b),
    reviews: () => request<AdminReview[]>("/api/dashboard/reviews"),
    setReview: (id: number, status: "visible" | "hidden") => post<AdminReview>(`/api/dashboard/reviews/${id}/status`, { status }),
  },
};
