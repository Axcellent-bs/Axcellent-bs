import { ApiError } from "./apiError";
import { ORCHESTRATOR_URL, RELAY_TOKEN, SAFETY_HUMAN_TOKEN } from "./env";
import type { AgentDiagnosisRow, DiagnosisDelivery } from "./agentDiagnosis";
import type { DemoRequest, OrchIncident } from "./agentChat";

// 총괄(orchestrator) API. 응답은 ax-backend 규약 {data, meta}. 형식은 orchestrator/src/ax_orchestrator/main.py 기준.

async function callRaw<T, M = Record<string, unknown>>(path: string, init?: RequestInit): Promise<{ data: T; meta: M }> {
  const res = await fetch(`${ORCHESTRATOR_URL}${path}`, { cache: "no-store", ...init });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    // FastAPI HTTPException은 {detail}, 직접 만든 오류는 {error: {message}}
    const message = body?.error?.message ?? (typeof body?.detail === "string" ? body.detail : null);
    throw new ApiError(message ?? `총괄 AI 요청 실패 (${res.status})`, res.status);
  }
  return { data: body.data as T, meta: (body.meta ?? {}) as M };
}

const call = async <T>(path: string, init?: RequestInit) => (await callRaw<T>(path, init)).data;

/** GET /api/incidents의 meta. `mock`이 참일 때만 대화방에서 총괄 보류에 승인·거절 버튼을 보인다 */
export interface IncidentsMeta { mock?: boolean; published?: { type: string; context_id: string }[] }

// 총괄 쓰기는 토큰이 필요하다: 데모 이슈 생성은 관리자(admin) 토큰, 보류 결정은 사람 토큰 (ax_orchestrator/auth.py)
const post = <T>(path: string, payload: unknown, token: string) =>
  call<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });

/** 재무가 총괄에 보낸 의견 한 줄 (GET /api/finance-opinions, 최근 받은 것부터). 형식은 계약 FinanceOpinion */
export interface FinanceOpinionRow {
  context_id: string;
  received_at?: string;
  opinion: {
    opinion_id: string; kind: "campaign_result" | "budget_decision" | "insight";
    campaign_id: string | null; campaign_name: string | null; channel: string | null;
    spend_krw: number | null; revenue_krw: number | null; profit_krw: number | null; roi: number | null;
    verdict: string | null; reasons: string[]; recommendation: string; submitted_at: string;
  };
}

export const orchestrator = {
  health: () => call<{ status: string }>("/health"),
  /** 이슈 목록 (열린 순서). 각 항목에 steps·events·pending_holds가 모두 들어 있다 */
  incidents: () => callRaw<OrchIncident[], IncidentsMeta>("/api/incidents"),
  /** 재무가 보낸 의견 (캠페인 손익·예산 의견·인사이트). 총괄은 보관만 한다 */
  financeOpinions: () => call<FinanceOpinionRow[]>("/api/finance-opinions"),
  incident: (contextId: string) => call<OrchIncident>(`/api/incidents/${encodeURIComponent(contextId)}`),
  /** 에이전트 평가에서 같은 단계의 확인 필요가 반복돼 인사 AI가 보낸 진단 요청과 진단·전달 상태 (최근 받은 것부터) */
  agentDiagnoses: () => call<AgentDiagnosisRow[]>("/api/agent-diagnoses"),
  /** 사람이 진단의 개선 노트 전달을 철회한다. 에이전트가 그 노트를 쓰지 않게 거둔다. 사람 토큰을 보낸다 */
  revokeDiagnosis: (requestId: string) =>
    post<DiagnosisDelivery>(`/api/agent-diagnoses/${encodeURIComponent(requestId)}/revoke`, undefined, SAFETY_HUMAN_TOKEN),
  /** 목 모드 전용: 판매 사이트 결제 장애를 주입한다. 관리자(admin) 토큰을 보낸다 */
  startDemo: (req: DemoRequest = {}) => post<{ context_id: string }>("/api/demo/incidents", req, RELAY_TOKEN),
  /** C 등급 보류에 대한 사람 결정을 전달한다. 총괄이 승인하는 것이 아니다.
   *  사람 토큰을 보낸다. 총괄은 이 경로를 목 모드에서만 연다. 실제 C 보류는 안전 관문 보류 큐에서 결정(환불 /support, 쿠폰 /marketing, 나머지 /finance) */
  decideHold: (contextId: string, stepId: string, approved: boolean) =>
    post<{ ok: boolean }>(`/api/incidents/${encodeURIComponent(contextId)}/holds/${encodeURIComponent(stepId)}`, { approved }, SAFETY_HUMAN_TOKEN),
};
