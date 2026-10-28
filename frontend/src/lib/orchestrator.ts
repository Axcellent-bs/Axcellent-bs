import { ApiError } from "./apiError";
import { ORCHESTRATOR_URL } from "./env";
import type { DemoRequest, OrchIncident } from "./agentChat";

// 총괄(orchestrator) API. 응답은 ax-backend 규약 {data, meta}. 형식은 orchestrator/src/ax_orchestrator/main.py 기준.

async function callRaw<T, M = Record<string, unknown>>(path: string, init?: RequestInit): Promise<{ data: T; meta: M }> {
  const res = await fetch(`${ORCHESTRATOR_URL}${path}`, { cache: "no-store", ...init });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    // FastAPI HTTPException은 {detail}, 직접 만든 오류는 {error: {message}}
    const message = body?.error?.message ?? (typeof body?.detail === "string" ? body.detail : null);
    throw new ApiError(message ?? `총괄 요청 실패 (${res.status})`, res.status);
  }
  return { data: body.data as T, meta: (body.meta ?? {}) as M };
}

const call = async <T>(path: string, init?: RequestInit) => (await callRaw<T>(path, init)).data;

/** GET /api/incidents의 meta. `mock`이 참일 때만 대화방에서 총괄 보류에 승인·거절 버튼을 보인다 */
export interface IncidentsMeta { mock?: boolean; published?: { type: string; context_id: string }[] }

const post = <T>(path: string, payload?: unknown) =>
  call<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: payload === undefined ? undefined : JSON.stringify(payload) });

export const orchestrator = {
  health: () => call<{ status: string }>("/health"),
  /** 사건 목록 (열린 순서). 각 항목에 steps·events·pending_holds가 모두 들어 있다 */
  incidents: () => callRaw<OrchIncident[], IncidentsMeta>("/api/incidents"),
  incident: (contextId: string) => call<OrchIncident>(`/api/incidents/${encodeURIComponent(contextId)}`),
  /** 목 모드 전용: 판매 사이트 결제 장애를 주입한다 */
  startDemo: (req: DemoRequest = {}) => post<{ context_id: string }>("/api/demo/incidents", req),
  /** C 등급 보류에 대한 사람 결정을 전달한다. 총괄이 승인하는 것이 아니다.
   *  이 경로는 사람 토큰을 확인하지 않는다(DSH-03) — 목 모드 데모에서만 부른다. 실제 C 보류는 안전 관문 보류 큐(/finance)에서 결정 */
  decideHold: (contextId: string, stepId: string, approved: boolean) =>
    post<{ ok: boolean }>(`/api/incidents/${encodeURIComponent(contextId)}/holds/${encodeURIComponent(stepId)}`, { approved }),
};
