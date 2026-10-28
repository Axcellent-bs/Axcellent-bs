import { DEJAFIX_API_URL } from "./env";

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

async function get<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${DEJAFIX_API_URL}${path}`, { cache: "no-store" });
  } catch {
    throw new Error("DejaFix 서버에 연결할 수 없습니다.");
  }
  if (res.status === 502) throw new Error("DejaFix 백엔드에 연결할 수 없습니다 (frontend/.env 의 DEJAFIX_PROXY_TARGET 확인).");
  if (res.status === 401) throw new Error("DejaFix 로그인 정보가 없거나 만료됐습니다 (frontend/.env 의 DEJAFIX_USERNAME·DEJAFIX_PASSWORD 확인).");
  if (res.status === 404) throw new Error("DejaFix에 없는 장애입니다.");
  if (!res.ok) throw new Error(`DejaFix가 오류를 돌려줬습니다 (HTTP ${res.status}).`);
  return (await res.json()) as T;
}

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
}

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
