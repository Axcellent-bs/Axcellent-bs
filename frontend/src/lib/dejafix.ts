import { DEJAFIX_API_URL, SAFETY_HUMAN_TOKEN } from "./env";
import { SESSION_HEADER, sessionReadAuth } from "./humanToken";
import { parseDejafixStatus, type DejafixStatus } from "./toolForms";
import { parseZabbixSettings, type ZabbixSettings } from "./zabbixSettings";

export { parseZabbixSettings, zabbixSummary, type ZabbixSettings } from "./zabbixSettings";

// 장애 대응 탭: DejaFix 화면을 띄우지 않고 DejaFix API에서 데이터만 받는다.
// 형식은 DejaFix/DejaFix contracts/api.md #10 `GET /api/incidents`, contracts/incident.schema.json 기준. 화면 없는 함수만 둔다.

export type Severity = { level: number; label: string };
export type AnalysisStatus = "pending" | "running" | "done" | "fallback" | "failed" | "skipped";

export interface Incident {
  id: number;
  source: string;
  zabbix: { eventid: string | null; triggerid: string | null; hostid: string | null };
  /** 호스트 보강 전에는 둘 다 null일 수 있다 (계약 #10) */
  host: { id: string | null; name: string | null };
  title: string;
  severity: Severity;
  status: "problem" | "resolved";
  started_at: string;
  resolved_at: string | null;
  tags: { tag: string; value: string }[];
  analysis: { status: AnalysisStatus | string; report_id: number | null; plan_id: number | null };
}

/** 목록의 meta. data_as_of·stale은 계약에 있지만 DejaFix 목 응답에는 없어서 선택으로 둔다 */
export interface IncidentMeta {
  next_cursor: string | null;
  data_as_of?: string | null;
  stale?: boolean;
}

/** 계약 공통 형식: 값 하나와 단위·수집 시각. 확인할 수 없으면 value=null + unknown=true (0으로 바꾸지 않는다) */
export interface Measured {
  value: number | null;
  unit: string;
  collected_at: string;
  unknown: boolean;
  unknown_reason?: string;
}

/** 계약 #5 GET /api/dashboard/summary */
export interface DashboardSummary {
  active_incidents: Measured;
  high_incidents: Measured;
  hosts: { ok: Measured; total: Measured; unknown: Measured };
  reports_today: Measured;
  avg_analysis_sec: Measured;
}

/** DejaFix가 연결되지 않음: 프록시(dejafix-proxy)가 503 DEJAFIX_NOT_CONNECTED를 돌려줬다. 화면은 연결 안내를 띄운다 */
export class DejafixNotConnectedError extends Error {
  constructor() {
    super("DejaFix가 연결되지 않아 장애 목록을 받지 못해요.");
  }
}

/** dejafix-proxy가 부르는 쪽 토큰을 확인한다. 다른 백엔드 호출처럼 사람 토큰(로그인 세션 우선, env.ts)을 붙인다 */
// 같은 출처 nginx가 읽기의 Authorization을 공용 토큰으로 바꿔 붙여도 회사를 알 수 있게 세션은 X-AX-Session으로도 싣는다(11/25)
const authHeaders = (): Record<string, string> => {
  const session = sessionReadAuth()[SESSION_HEADER];
  return { ...(SAFETY_HUMAN_TOKEN ? { Authorization: `Bearer ${SAFETY_HUMAN_TOKEN}` } : {}), ...(session ? { [SESSION_HEADER]: session } : {}) };
};

/** dejafix-proxy가 화면 토큰을 막았을 때의 코드 (DejaFix 계정 문제의 401과 나눠 알린다) */
const PROXY_AUTH_CODES = new Set(["AUTH_REQUIRED", "TOKEN_INVALID", "SESSION_EXPIRED", "SESSION_REVOKED", "SESSION_ENDED"]);

/** 404면 null로 돌려준다(DejaFix 옛 버전에 없는 경로). 그 밖은 `get`과 같다 */
async function getOrNull<T>(path: string): Promise<T | null> {
  try {
    return await get<T>(path, true);
  } catch (e) {
    if (e instanceof NotFound) return null;
    throw e;
  }
}

class NotFound extends Error {}

async function get<T>(path: string, quietNotFound = false): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${DEJAFIX_API_URL}${path}`, { cache: "no-store", headers: authHeaders() });
  } catch {
    throw new Error("DejaFix 서버에 연결할 수 없습니다.");
  }
  if (res.status === 503) {
    const body: unknown = await res.json().catch(() => undefined);
    if ((body as { error?: { code?: string } } | undefined)?.error?.code === "DEJAFIX_NOT_CONNECTED") throw new DejafixNotConnectedError();
  }
  if (res.status === 502) throw new Error("DejaFix 백엔드에 연결할 수 없습니다 (서버 담당이 설치 설정의 DejaFix 주소를 확인해 주세요).");
  if (res.status === 401) {
    const body: unknown = await res.json().catch(() => undefined);
    const code = (body as { error?: { code?: string } } | undefined)?.error?.code;
    if (code && PROXY_AUTH_CODES.has(code)) throw new Error("장애 목록을 볼 권한이 없거나 로그인이 끊겼습니다. 다시 로그인해 주세요.");
    throw new Error("DejaFix 로그인 정보가 없거나 만료됐습니다 (서버 담당이 설치 설정의 DejaFix 계정을 확인해 주세요).");
  }
  if (res.status === 404) throw quietNotFound ? new NotFound(path) : new Error("DejaFix에 없는 장애입니다.");
  if (!res.ok) throw new Error(`DejaFix가 오류를 돌려줬습니다 (HTTP ${res.status}).`);
  return (await res.json()) as T;
}

/** DejaFix 계약 #7 `GET /api/hosts`: Zabbix 감시 호스트와 에이전트 연결 상태 */
export interface DejafixHost {
  id: string;
  name: string;
  /** 에이전트 가용성: up 응답함 · down 응답 없음 · unknown 확인할 수 없음 (장애 여부와 따로) */
  state: "up" | "down" | "unknown";
  /** 열린 Problem 수 */
  problems: number;
}

/** 호스트 목록. DejaFix가 아직 이 API를 주지 않으면(옛 버전) null */
export async function hostList(): Promise<{ data: DejafixHost[]; meta: { collected_at?: string; truncated?: boolean } } | null> {
  return getOrNull("/api/hosts");
}

/** 프록시 `GET /status`(연결 출처·응답 여부). 회원·서버 관리 › DejaFix 연결 카드가 읽는다. 못 읽으면 null ("확인할 수 없음") */
export async function dejafixStatus(): Promise<DejafixStatus | null> {
  try {
    const res = await fetch(`${DEJAFIX_API_URL}/status`, { cache: "no-store", headers: authHeaders() });
    if (!res.ok) return null;
    return parseDejafixStatus(await res.json());
  } catch {
    return null;
  }
}

async function zabbixCall(method: "GET" | "POST", body?: unknown): Promise<ZabbixSettings> {
  let res: Response;
  try {
    res = await fetch(`${DEJAFIX_API_URL}/settings/zabbix`, {
      method,
      cache: "no-store",
      headers: { ...authHeaders(), ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("DejaFix 서버에 연결할 수 없습니다.");
  }
  const json: unknown = await res.json().catch(() => undefined);
  if (!res.ok) {
    const message = (json as { error?: { message?: string } } | undefined)?.error?.message;
    throw new Error(message || `DejaFix가 오류를 돌려줬습니다 (HTTP ${res.status}).`);
  }
  const parsed = parseZabbixSettings(json);
  if (!parsed) throw new Error("DejaFix 응답을 읽을 수 없습니다.");
  return parsed;
}

/** 지금 Zabbix 연결 (운영자·관리자 로그인만) */
export const zabbixSettings = () => zabbixCall("GET");

/** Zabbix 주소·읽기 전용 토큰 저장. DejaFix가 저장 전에 연결을 확인한다. 토큰을 비우면 저장된 토큰을 쓴다 */
export const saveZabbixSettings = (input: { url: string; token: string; verify_tls: boolean }) => zabbixCall("POST", input);

type List = { data?: Incident[]; meta?: IncidentMeta };
const list = (body: List) => ({ data: body.data ?? [], meta: body.meta ?? { next_cursor: null } });

/** 지금 진행 중인 장애만 (status=problem). DejaFix가 started_at 내림차순으로 준다 */
export const currentIncidents = async () => list(await get<List>("/api/incidents?status=problem&limit=100"));

/** 해결된 것까지 최근 장애 100건 (시간대·호스트 분포용) */
export const recentIncidents = async () => list(await get<List>("/api/incidents?limit=100"));

/** 현황 KPI 4종 */
export const dashboardSummary = async () => (await get<{ data: DashboardSummary }>("/api/dashboard/summary")).data;

// 계약 #11 GET /api/incidents/{id}. DejaFix가 저장한 값을 옮겨 줄 뿐이라 화면도 문장·원인·티어를 바꾸지 않고 그대로 보여 준다.
export type Confidence = "low" | "medium" | "high";
export type Tier = "A" | "B" | "C";

export interface Report {
  id: number;
  version: number;
  kind: "llm" | "fallback" | string;
  summary: string;
  impact: { hosts: string[]; services: string[]; user_impact: string | null; evidence_ids: string[] };
  causes: { rank: number; title: string; confidence: Confidence; reasoning: string; evidence_ids: string[]; verify: string | null }[];
  facts: { label: string; value: number | string | null; unit: string | null; unknown_reason: string | null; evidence_ids: string[]; collected_at: string | null }[];
  unknowns: { label: string; reason: string }[];
  next_checks: string[];
  created_at: string;
}

export interface Evidence {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  duration_ms: number | null;
  result_summary: string | null;
  data_clock: string | null;
  called_at: string;
}

export interface PlanStep {
  seq: number;
  action_id: string | null;
  title: string;
  target: string | null;
  command_preview: string | null;
  tier: Tier;
  rationale: string | null;
  precheck: string | null;
  expected_effect: string | null;
  risk: string | null;
  rollback: string | null;
  evidence_ids: string[];
}

export interface IncidentDetail {
  incident: Incident;
  report: Report | null;
  evidence: Evidence[];
  plan: { id: number; title: string; goal: string | null; mode: string; max_tier: Tier; assumptions: string[] } | null;
  steps: PlanStep[];
  mail_logs: { id: number; kind: string; recipient: string; status: string; attempts: number; error: string | null; created_at: string; sent_at: string | null }[];
  /** 구버전 DejaFix 응답에는 없을 수 있다. 없으면 빈 목록으로 본다 (계약 #11) */
  knowledge_hits?: KnowledgeHit[];
}

/** 계약 #11 knowledge_hits[]: DejaFix AI가 분석 때 찾은 비슷한 과거 사례 (최대 3건). 저장된 값을 옮긴 것이다 */
export interface KnowledgeHit {
  chunk_id: string;
  title: string;
  source_type: "incident_report" | "runbook" | "pack_action" | "resolution_note" | string;
  host_name: string | null;
  incident_id: number | null;
  excerpt: string;
}

/** 리포트 facts 중 비슷한 과거 장애 검색 결과를 담은 행의 이름 (DejaFix가 저장하는 값) */
export const SIMILAR_FACT_LABEL = "과거 유사 사례";

export const KNOWLEDGE_SOURCE: Record<string, string> = {
  incident_report: "장애 리포트",
  runbook: "런북",
  pack_action: "팩 동작",
  resolution_note: "해결 메모",
};

/** 장애 하나의 상세(리포트·근거·변경 계획·메일 기록) */
export const incidentDetail = async (id: number) => (await get<{ data: IncidentDetail }>(`/api/incidents/${id}`)).data;

/** 확인할 수 없는 이유 코드를 사람이 읽는 말로 */
export const UNKNOWN_REASON: Record<string, string> = {
  NOT_COLLECTED: "수집하지 않음",
  STALE: "값이 오래됨",
  TOOL_ERROR: "조회 실패",
  NO_DATA: "데이터 없음",
};

export const CONFIDENCE: Record<string, { label: string; tone: string }> = {
  high: { label: "확신도 높음", tone: "bg-emerald-50 text-emerald-700" },
  medium: { label: "확신도 보통", tone: "bg-amber-50 text-amber-700" },
  low: { label: "확신도 낮음", tone: "bg-ink-100 text-ink-700" },
};

/** 변경 단계 등급. 판정은 DejaFix가 규칙(actions.yaml)으로 한 값 그대로다 */
export const TIER: Record<string, { label: string; tone: string }> = {
  A: { label: "A · 조회 · 안전", tone: "bg-emerald-50 text-emerald-700" },
  B: { label: "B · 변경 · 승인 필요", tone: "bg-amber-50 text-amber-700" },
  C: { label: "C · 금지 · 사람이 판단", tone: "bg-red-50 text-red-700" },
};

/**
 * 목록용 장애 제목. Zabbix 트리거 이름을 사람이 읽기 쉽게 줄인다(원문은 title 속성으로 남긴다).
 * DejaFix frontend/src/components/incidentFormat.ts readableTitle (DejaFix PR #187)과 같은 규칙.
 * - 앞의 템플릿 접두사 제거: 'Windows: …' → '…'
 * - '"서비스ID" (표시 이름 (서비스ID))'·'"서비스ID" (표시 이름)' → '표시 이름'
 * 문장을 새로 만들거나 번역하지 않고 중복만 덜어 낸다.
 */
export function readableTitle(title: string): string {
  const out = title
    .replace(/^[A-Za-z][\w .-]{0,40}:\s+/, "")
    .replace(/"([^"]+)"\s*\(([^()]*?)(?:\s*\(\1\))?\)/g, (_, id: string, name: string) => name.trim() || id)
    .trim();
  return out || title;
}

/** Zabbix 심각도 0~5 → 화면 표시 */
export const SEVERITY: Record<number, { label: string; tone: string }> = {
  5: { label: "재해", tone: "bg-red-600 text-white" },
  4: { label: "높음", tone: "bg-red-50 text-red-700" },
  3: { label: "보통", tone: "bg-amber-50 text-amber-700" },
  2: { label: "경고", tone: "bg-amber-50 text-amber-700" },
  1: { label: "정보", tone: "bg-ink-100 text-ink-700" },
  0: { label: "분류 안 됨", tone: "bg-ink-100 text-ink-700" },
};

/** analysis.status 변환 규칙(계약 #10)을 사람이 읽는 말로 */
export const ANALYSIS: Record<string, { label: string; tone: string }> = {
  pending: { label: "분석 대기", tone: "bg-ink-100 text-ink-700" },
  running: { label: "AI 분석 중", tone: "bg-brand-50 text-brand-700" },
  done: { label: "AI 분석 완료", tone: "bg-emerald-50 text-emerald-700" },
  fallback: { label: "규칙 기반 리포트", tone: "bg-amber-50 text-amber-700" },
  failed: { label: "분석 실패", tone: "bg-red-50 text-red-700" },
  skipped: { label: "분석 안 함", tone: "bg-ink-100 text-ink-500" },
};
