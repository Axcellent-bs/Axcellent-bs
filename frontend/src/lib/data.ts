import { ApiError } from "./apiError";
import { DATA_A2A_TOKEN, DATA_A2A_URL } from "./env";
import type { OrchIncident, OrchStep } from "./agentChat";

// 데이터분석 AI 화면(/data)의 자료.
// 1) 장애 사건: 총괄(orchestrator) 사건의 데이터 단계(impact·recovery·kpi) 결과를 그대로 읽는다.
// 2) 학습 효과·KPI: 데이터 AI(8106)의 kpi_summary를 직접 부른다 (개발 전용, 운영은 dashboard-api 중계 예정).
// 숫자는 서버가 계산한 값만 보여 준다. 화면에서는 단위만 바꾼다(초 → 분, 원 콤마).

export const UNKNOWN = "확인할 수 없음";

// ---------------------------------------------------------------- 총괄 사건의 데이터 단계
export interface CategoryImpact { category: string; failed_order_count: number | null; damage_amount: number | null }
export interface ImpactOutput {
  failed_order_count?: number | null;
  affected_customer_count?: number | null;
  affected_customer_ids?: string[] | null;
  damage_amount?: number | null;
  by_category?: CategoryImpact[] | null;
  summary?: string | null;
}
export interface RecoveryOutput { recovered?: boolean | null; infra_metrics_ok?: boolean | null; business_metrics_ok?: boolean | null }

/** 처리 흐름 4칸. 이상 감지는 따로 단계가 없어 복구 확인(recovery) 안에서 함께 실행된다 */
export const FLOW: { key: string; label: string; stepId: string; note?: string }[] = [
  { key: "anomaly", label: "이상 감지", stepId: "recovery", note: "복구 확인 중 함께 실행" },
  { key: "impact", label: "영향 분석", stepId: "impact" },
  { key: "recovery", label: "복구 확인", stepId: "recovery" },
  { key: "kpi", label: "처리 시간 요약", stepId: "kpi" },
];

export const stepOf = (inc: OrchIncident | null | undefined, stepId: string): OrchStep | undefined => inc?.steps.find((s) => s.step_id === stepId);
export const impactOf = (inc: OrchIncident | null | undefined) => (stepOf(inc, "impact")?.output ?? null) as ImpactOutput | null;
export const recoveryOf = (inc: OrchIncident | null | undefined) => (stepOf(inc, "recovery")?.output ?? null) as RecoveryOutput | null;

/** 숫자만 통과. 없거나 숫자가 아니면 null */
export const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);

export const count = (n: unknown, unit: string) => {
  const v = num(n);
  return v == null ? UNKNOWN : `${v.toLocaleString("ko-KR")}${unit}`;
};

/** 걸린 시간: 4.78 → "4.8초", 540 → "9분", 570 → "9분 30초" */
export function duration(sec: unknown): string {
  const s = num(sec);
  if (s == null) return UNKNOWN;
  if (s < 60) return `${Math.round(s * 10) / 10}초`;
  const m = Math.floor(s / 60), r = Math.round(s - m * 60);
  return r ? `${m}분 ${r}초` : `${m}분`;
}
/** 막대용 분 단위(소수 첫째 자리) */
export const minutes = (sec: number) => Math.round(sec / 6) / 10;

export const CATEGORY_KO: Record<string, string> = { books: "도서", stationery: "문구", ebooks: "전자책", goods: "굿즈" };

export const STEP_STATUS_KO: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "info" | "muted" }> = {
  completed: { label: "끝남", tone: "ok" },
  running: { label: "진행 중", tone: "info" },
  pending: { label: "기다리는 중", tone: "muted" },
  held: { label: "사람 승인 기다림", tone: "warn" },
  failed: { label: "실패", tone: "bad" },
  rejected_by_human: { label: "사람이 거절", tone: "warn" },
  skipped: { label: "건너뜀", tone: "muted" },
};
export const INCIDENT_STATUS_KO: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "info" | "muted" }> = {
  open: { label: "처리 중", tone: "warn" },
  resolved: { label: "해결됨", tone: "ok" },
  degraded: { label: "일부 실패", tone: "bad" },
};

/** 최근 사건이 먼저 */
export const sortRecent = (list: OrchIncident[]) =>
  [...list].sort((a, b) => (b.opened_at ?? "").localeCompare(a.opened_at ?? "") || b.context_id.localeCompare(a.context_id));

// ---------------------------------------------------------------- 데이터 AI 직접 조회 (kpi_summary)
export interface KpiIncident {
  context_id: string;
  detected_at: string | null;
  recovered_at: string | null;
  resolved_notice_at: string | null;
  mttr_seconds: number | null;
  used_memory: boolean | null;
}
export interface KpiSummaryOutput {
  incidents: KpiIncident[];
  auto_handled_rate: number | null;
  grade_counts: Record<string, number>;
  blocked_counts: Record<string, number>;
  escalation_count: number | null;
}
export interface Evidence { data_summary: string; queries: string[]; ref_ids: string[]; collected_at?: string | null }
export interface A2AResult<T> { status: string; outcome?: string | null; output: T; evidence: Evidence; fallback: boolean }

export const GRADE_KO: Record<string, string> = { A: "A · 바로 처리", B: "B · 처리 후 보고", C: "C · 사람 승인" };
export const BLOCK_KO: Record<string, string> = {
  UNAUTHORIZED: "권한 없음", FORBIDDEN_SKILL: "허용되지 않은 작업", INJECTION_SUSPECTED: "지시 끼워 넣기 의심",
};

/** 근거 요약: 첫 줄은 설명 한 줄, 나머지 줄은 "확인할 수 없음" 사유 */
export function evidenceLines(summary: string | null | undefined): { headline: string | null; notes: string[] } {
  const lines = (summary ?? "").split("\n").map((l) => l.trim()).filter(Boolean);
  return { headline: lines[0] ?? null, notes: lines.slice(1) };
}

export const DATA_SERVER = "데이터 AI 서버(8106)";
export const kpiLive = DATA_A2A_TOKEN !== "";

export const dataAi = {
  health: async () => {
    const res = await fetch(`${DATA_A2A_URL}/health`, { cache: "no-store" });
    if (!res.ok) throw new ApiError(`${DATA_SERVER} 상태 확인 실패 (${res.status})`, res.status);
    return true;
  },
  kpiSummary: async (): Promise<A2AResult<KpiSummaryOutput>> => {
    const body = {
      meta: { context_id: "ctx_req_dashboard", token: DATA_A2A_TOKEN, path: [], deadline: new Date(Date.now() + 30_000).toISOString() },
      skill: "kpi_summary",
      input: {},
    };
    const res = await fetch(`${DATA_A2A_URL}/a2a`, { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const json = (await res.json().catch(() => null)) as A2AResult<KpiSummaryOutput> | null;
    if (!res.ok || !json) throw new ApiError(`${DATA_SERVER}에서 KPI를 받지 못했습니다 (${res.status})`, res.status);
    if (json.status !== "completed") throw new ApiError(`데이터 AI가 요청을 처리하지 않았습니다 (${json.outcome ?? json.status})`, res.status);
    return json;
  },
};
