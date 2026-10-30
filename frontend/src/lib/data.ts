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

/** 원인 추정(root_cause_hypotheses). 사실은 코드가 모은 것, 후보는 그 사실 id만 근거로 한다 */
export type CauseConfidence = "high" | "mid" | "low";
export interface CauseFact { fact_id: string; kind: string; at?: string | null; source: string; ref?: string | null; summary: string }
export interface CauseHypothesis { explanation: string; fact_ids: string[]; confidence: CauseConfidence | string }
export interface RootCauseOutput { hypotheses?: CauseHypothesis[] | null; needs_confirmation?: boolean | null; facts?: CauseFact[] | null; summary?: string | null }

export const CONFIDENCE_KO: Record<string, { label: string; tone: "ok" | "warn" | "muted" }> = {
  high: { label: "확신 높음", tone: "ok" },
  mid: { label: "확신 중간", tone: "warn" },
  low: { label: "확신 낮음", tone: "muted" },
};
export const FACT_KIND_KO: Record<string, string> = {
  anomaly: "이상 구간",
  deploy: "배포·PR",
  notice: "공지",
  inquiry_surge: "문의 급증",
  past_incident: "지난 사건",
  unavailable: UNKNOWN,
};
/** 사실 출처(skill) → 화면 이름 */
export const FACT_SOURCE_KO: Record<string, string> = {
  input: "총괄이 준 이상 구간",
  "planning.find_related_prs": "기획개발 AI · 관련 PR",
  "marketing.list_active_notices": "마케팅 AI · 게시 중 공지",
  "support.detect_inquiry_surge": "고객지원 AI · 문의 급증",
  "memory.card_search": "회사 기억 · 지난 사건",
};

export const stepOf = (inc: OrchIncident | null | undefined, stepId: string): OrchStep | undefined => inc?.steps.find((s) => s.step_id === stepId);
export const rootCauseOf = (inc: OrchIncident | null | undefined) => (stepOf(inc, "root_cause")?.output ?? null) as RootCauseOutput | null;
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

/** 쇼핑몰 카테고리 코드 → 화면 이름. 표에 없는 코드는 받은 그대로 보여 준다 (실서버 시드 코드: book, stationery) */
export const CATEGORY_KO: Record<string, string> = { book: "도서", books: "도서", stationery: "문구", ebook: "전자책", ebooks: "전자책", goods: "굿즈" };

export const STEP_STATUS_KO: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "info" | "muted" }> = {
  completed: { label: "끝남", tone: "ok" },
  running: { label: "진행 중", tone: "info" },
  pending: { label: "기다리는 중", tone: "muted" },
  held: { label: "사람 승인 기다림", tone: "warn" },
  waiting_human: { label: "담당 부서 처리 기다림", tone: "info" },
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
  /** 운영 중 사람 반응 (공통 로그로 셈). 로그가 없으면 null */
  human_feedback?: HumanFeedback | null;
}
export interface HumanFeedback {
  handoffs_closed: number; handoffs_rejected: number; handoff_reject_rate: number | null;
  reviews_decided: number; reviews_rejected: number; review_reject_rate: number | null;
  results_total: number; results_unknown: number; unknown_rate: number | null;
}

/** 정확도 평가 결과 (accuracy_report). source none이면 평가 결과가 없다 */
export interface AccuracyMiss { case_id: string; area: string; what: string }
export interface AccuracyReportOutput {
  source: "latest" | "baseline" | "none";
  generated_at: string | null;
  case_count: number | null;
  metrics: Record<string, number | null>;
  misses: AccuracyMiss[];
  summary: string;
}
/** 화면에 보여 줄 평가 지표: 이름, 단위(rate=비율 %, min=분, won=원, pct=%, flag=예/아니오) */
export const ACCURACY_METRICS: { key: string; label: string; unit: "rate" | "min" | "won" | "pct" | "flag"; better: "high" | "low" | null }[] = [
  { key: "anomaly.detection_rate", label: "이상 탐지 탐지율", unit: "rate", better: "high" },
  { key: "anomaly.false_positive_rate", label: "이상 탐지 오탐률", unit: "rate", better: "low" },
  { key: "anomaly.payment_mean_delay_minutes", label: "결제 장애 탐지 지연 (평균)", unit: "min", better: "low" },
  { key: "anomaly.drop_mean_delay_minutes", label: "주문 급감 탐지 지연 (평균)", unit: "min", better: "low" },
  { key: "anomaly.unknown_handled_rate", label: "빈 구간을 '확인할 수 없음'으로 답함", unit: "rate", better: "high" },
  { key: "impact.match_rate", label: "영향 분석 일치율", unit: "rate", better: "high" },
  { key: "impact.mean_abs_amount_error", label: "영향 분석 금액 오차 (평균)", unit: "won", better: "low" },
  { key: "fraud.skill_available", label: "사기 점수 skill 응답", unit: "flag", better: "high" },
  { key: "fraud.precision", label: "사기 점수 정밀도", unit: "rate", better: "high" },
  { key: "fraud.recall", label: "사기 점수 재현율", unit: "rate", better: "high" },
  { key: "llm.fabricated_number_rate", label: "설명에 지어낸 숫자", unit: "rate", better: "low" },
  { key: "llm.fallback_rate", label: "설명 템플릿 대체", unit: "rate", better: null },
  { key: "root_cause.top1_rate", label: "원인 추정 1순위 적중", unit: "rate", better: "high" },
  { key: "root_cause.ungrounded_survived_rate", label: "근거 없는 원인 후보가 남음", unit: "rate", better: "low" },
  { key: "root_cause.needs_confirmation_rate", label: "원인 추정 '확인 필요'", unit: "rate", better: null },
];
export function formatMetric(value: number | null | undefined, unit: (typeof ACCURACY_METRICS)[number]["unit"]): string {
  const v = num(value);
  if (v == null) return UNKNOWN;
  if (unit === "rate") return `${Math.round(v * 1000) / 10}%`;
  if (unit === "pct") return `${v}%`;
  if (unit === "min") return `${v}분`;
  if (unit === "won") return `${Math.round(v).toLocaleString("ko-KR")}원`;
  return v >= 1 ? "예" : "아니오 (미구현)";
}
/** 비율을 % 글자로. 분모가 없으면(null) 확인할 수 없음 */
export const percent = (v: number | null | undefined) => (num(v) == null ? UNKNOWN : `${Math.round((v as number) * 1000) / 10}%`);
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

// ---------------------------------------------------------------- 부서별 AI 업무 리포트 (activity_report, #147)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py ActivityReportOutput. 숫자는 모두 서버가 센다.
export type ActivityPeriod = "day" | "week" | "month";
/** 비율. 분모 3 미만이면 rate가 null이고 note가 "표본 부족" */
export interface ActivityRate { count: number; total: number; rate: number | null; note?: string | null }
/** 회사 숫자 하나. 받지 못하면 value가 null */
export interface CompanyFigure { value: number | null; previous_period: number | null; source: string; note?: string | null }
export interface ActivityCompany {
  gmv: CompanyFigure;
  commission_revenue: CompanyFigure;
  order_count: CompanyFigure;
  payment_success_rate: CompanyFigure;
  incident_count: CompanyFigure;
}
export interface SkillCount { skill: string; label: string; count: number }
export interface ActivityOutcomes {
  auto_completed: ActivityRate;
  human_approved: ActivityRate;
  held: ActivityRate;
  rejected: ActivityRate;
  unknown: ActivityRate;
  failed: ActivityRate;
}
export interface DepartmentActivity {
  requests: { total: number; top_skills: SkillCount[] };
  outcomes: ActivityOutcomes;
  handoffs: { handed: number; done: number; rejected: number; avg_close_minutes: number | null };
  quality: { fallback: ActivityRate; fallback_reasons: Record<string, number>; avg_response_ms: number | null; response_samples: number };
  incidents: { count: number; skills: SkillCount[]; mttr_median_minutes: number | null };
}
export interface DepartmentReport extends DepartmentActivity {
  department: string;
  label: string;
  owners: string[];
  services: string[];
  enabled: boolean;
  previous_period: DepartmentActivity;
}
export interface CollaborationEdge { from_department: string; from_label: string; to_department: string; to_label: string; count: number }
export interface ActivityHighlight { key: string; label: string; current: number; previous: number; change_percent: number }
export interface UnknownSection { section: string; reason: string }
export interface ActivityReportOutput {
  period: ActivityPeriod;
  start: string;
  end: string;
  window_from: string;
  window_to: string;
  previous_from: string;
  previous_to: string;
  company: ActivityCompany;
  departments: DepartmentReport[];
  collaboration: CollaborationEdge[];
  highlights: ActivityHighlight[];
  summary: string;
  summary_source: "llm" | "template" | "none";
  unknown_sections: UnknownSection[];
}

/** 서버가 꺼졌을 때 무엇이 꺼졌는지 보여 주는 이름 */
export const DATA_SERVER = "데이터 AI 서버(8106)";
export const ORCH_SERVER = "총괄 서버(8000)";
export const kpiLive = DATA_A2A_TOKEN !== "";

export const dataAi = {
  health: async () => {
    const res = await fetch(`${DATA_A2A_URL}/health`, { cache: "no-store" });
    if (!res.ok) throw new ApiError(`${DATA_SERVER} 상태 확인 실패 (${res.status})`, res.status);
    return true;
  },
  kpiSummary: () => askData<KpiSummaryOutput>("kpi_summary", "KPI"),
  accuracyReport: () => askData<AccuracyReportOutput>("accuracy_report", "정확도 평가 결과"),
  /** start: 한국 시간 날짜(YYYY-MM-DD). 비우면 서버가 직전 기간을 고른다. services: 비우면 전체 */
  activityReport: (period: ActivityPeriod, start: string | null, services: string[] = []) =>
    askData<ActivityReportOutput>("activity_report", "업무 리포트", { period, ...(start ? { start } : {}), services }),
};

async function askData<T>(skill: string, what: string, input: Record<string, unknown> = {}): Promise<A2AResult<T>> {
  const body = {
    meta: { context_id: "ctx_req_dashboard", token: DATA_A2A_TOKEN, path: [], deadline: new Date(Date.now() + 30_000).toISOString() },
    skill,
    input,
  };
  const res = await fetch(`${DATA_A2A_URL}/a2a`, { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const json = (await res.json().catch(() => null)) as A2AResult<T> | null;
  if (!res.ok || !json) throw new ApiError(`${DATA_SERVER}에서 ${what}를 받지 못했습니다 (${res.status})`, res.status);
  if (json.status !== "completed") throw new ApiError(`데이터 AI가 요청을 처리하지 않았습니다 (${json.outcome ?? json.status})`, res.status);
  return json;
}
