import { ApiError } from "./apiError";
import { tokenFree } from "./apiDirect";
import { ORCHESTRATOR_URL, RELAY_TOKEN, SAFETY_HUMAN_TOKEN } from "./env";
import { sessionAuth } from "./humanToken";
import type { AgentDiagnosisRow, DiagnosisDelivery } from "./agentDiagnosis";
import type { DemoRequest, OrchIncident } from "./agentChat";
import type { WorkRequestRow, WorkRequestTask } from "./workRequests";
import type { Department, DepartmentsMeta } from "./adminRelay";

// 총괄(orchestrator) API. 응답은 ax-backend 규약 {data, meta}. 형식은 orchestrator/src/ax_orchestrator/main.py 기준.

async function callRaw<T, M = Record<string, unknown>>(path: string, init?: RequestInit): Promise<{ data: T; meta: M }> {
  // 머리 없는 읽기(GET)는 로그인 세션이 있으면 세션 토큰을 싣는다(없으면 지금처럼 헤더 없이). 토큰을 안 보는 읽기는 싣지 않는다
  const read = (init?.method ?? "GET").toUpperCase() === "GET" && !new Headers(init?.headers).has("authorization") && !tokenFree("orch", path);
  const res = await fetch(`${ORCHESTRATOR_URL}${path}`, { cache: "no-store", ...init, ...(read ? { headers: { ...Object.fromEntries(new Headers(init?.headers)), ...sessionAuth() } } : {}) });
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
  /** 기획이 알아야 하는 의견(재배분 등)을 총괄이 기획 receive_insight로 넘긴 결과 (11/18). 없으면 넘기지 않는 의견 */
  relay?: { to: "planning"; status: "pending" | "delivered" | "invalid"; attempts: number; outcome?: string | null; proposal_id?: string | null; problem?: string | null; at?: string };
}

/** 사람 토큰으로 읽는 GET (고객 글이 들어 있다) */
const getAuthed = <T, M = Record<string, unknown>>(path: string, token: string) =>
  callRaw<T, M>(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });

export const orchestrator = {
  health: () => call<{ status: string }>("/health"),
  /** 이슈 목록 (열린 순서). 각 항목에 steps·events·pending_holds가 모두 들어 있다 */
  incidents: () => callRaw<OrchIncident[], IncidentsMeta>("/api/incidents"),
  /** 부서 카드·사용량(총괄이 부른 호출을 센 실측). 총괄을 직접 읽는다(캣북스를 거치지 않는다) */
  departments: () => callRaw<Department[], DepartmentsMeta>("/api/departments"),
  /** 접수된 고객 의뢰 (최근 것부터, 최대 100건)와 고를 수 있는 일 목록. 사람 토큰이 필요하다 */
  workRequests: () =>
    getAuthed<WorkRequestRow[], { tasks: WorkRequestTask[] }>("/api/requests", SAFETY_HUMAN_TOKEN),
  /** 의뢰 하나 (목록 최근 100건에 없을 때 대화방 진행 카드가 한 번 부른다). 사람 토큰이 필요하다 */
  workRequest: (requestId: string) =>
    getAuthed<WorkRequestRow>(`/api/requests/${encodeURIComponent(requestId)}`, SAFETY_HUMAN_TOKEN).then((r) => r.data),
  /** 홍보 영상 의뢰에 쓸 회사 대표 캐릭터(제작 기록 CR-…, null이면 캐릭터 없이). 영상 준비 전까지만(뒤면 409) (11/23) */
  setBrandCharacter: (requestId: string, recordId: string | null) =>
    post<{ ok: boolean; record_id: string | null; plan_outdated?: boolean }>(`/api/requests/${encodeURIComponent(requestId)}/brand-character`, { record_id: recordId }, SAFETY_HUMAN_TOKEN),
  /** 사람 확인·승인 대기 중인 의뢰 단계를 승인하거나 거절한다. 승인하면 다음 단계로 이어진다 */
  decideRequestStep: (requestId: string, stepId: string, approved: boolean) =>
    post<{ ok: boolean }>(`/api/requests/${encodeURIComponent(requestId)}/steps/${encodeURIComponent(stepId)}/decision`, { approved }, SAFETY_HUMAN_TOKEN),
  /** 잘못 골랐으면 일을 바꿔 다시 돌린다. 이전 선택은 기록에 남는다 */
  rerunRequest: (requestId: string, task: string) =>
    post<{ ok: boolean }>(`/api/requests/${encodeURIComponent(requestId)}/rerun`, { task }, SAFETY_HUMAN_TOKEN),
  /** 의뢰 삭제 = 멈추고 숨김 (11/23). 끝나지 않았으면 남은 단계를 멈추고, 기록은 남고 되돌릴 수 있다 */
  hideRequest: (requestId: string) =>
    post<{ ok: boolean }>(`/api/requests/${encodeURIComponent(requestId)}/hide`, undefined, SAFETY_HUMAN_TOKEN),
  /** 숨긴 의뢰를 되돌린다 (멈춘 단계는 그대로) */
  unhideRequest: (requestId: string) =>
    post<{ ok: boolean }>(`/api/requests/${encodeURIComponent(requestId)}/unhide`, undefined, SAFETY_HUMAN_TOKEN),
  /** 의뢰문 수정 = 고쳐서 다시 접수. 새 의뢰 번호를 돌려준다 (새 접수에 실패하면 지금 의뢰는 그대로) */
  editRequest: (requestId: string, text: string) =>
    post<{ ok: boolean; request_id: string; replaces: string }>(`/api/requests/${encodeURIComponent(requestId)}/edit`, { text }, SAFETY_HUMAN_TOKEN),
  /** 숨긴(삭제한) 의뢰만 (최근 것부터, 최대 100건). 사람 토큰이 필요하다 */
  hiddenRequests: () =>
    getAuthed<WorkRequestRow[], { tasks: WorkRequestTask[] }>("/api/requests?hidden=1", SAFETY_HUMAN_TOKEN),
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
