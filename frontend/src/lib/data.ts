import { ApiError } from "./apiError";
import { DATA_A2A_PLACEHOLDER, DATA_A2A_TOKEN, DATA_A2A_URL } from "./env";
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

/** 데이터 AI가 총괄 요청을 받아 하는 일 4칸. 장애 감지는 쇼핑몰 모니터·Zabbix가 하고 데이터 AI 일이 아니다 */
export const FLOW: { key: string; label: string; stepId: string }[] = [
  { key: "impact", label: "영향 분석", stepId: "impact" },
  { key: "root_cause", label: "원인 후보", stepId: "root_cause" },
  { key: "recovery", label: "복구 판정 지원", stepId: "recovery" },
  { key: "kpi", label: "처리 시간 요약", stepId: "kpi" },
];

/** 사건을 처음 연 신호의 출처(총괄 detected_by.source) → 화면 이름. 표에 없으면 받은 값 그대로 */
export const DETECTOR_KO: Record<string, string> = {
  "shop-api": "쇼핑몰 모니터", // shop-api monitor.py가 보내는 shop.degraded
  infra: "Zabbix", // infra-dejafix dejafix_watch.py가 DejaFix(Zabbix) 장애를 옮긴 infra.anomaly_detected
  dejafix: "Zabbix",
  data: "데이터 AI 지표 감시", // data monitor.py의 metric.anomaly (심각도 높음 이상만 사건을 연다)
  support: "고객지원 AI",
};
export const detectorName = (source: string) => DETECTOR_KO[source] ?? (/^(infra|dejafix)/.test(source) ? "Zabbix" : source);

/** 데이터 단계를 건너뛴 이유(총괄 reason_code) → 사람 말 한 줄 */
export const SKIP_REASON_KO: Record<string, string> = {
  no_fix_applied: "되돌리기 같은 조치가 끝나지 않아 복구 판정을 부르지 않았어요",
  metric_not_supported: "이 지표는 데이터 AI가 아직 분석하지 못해 부르지 않았어요",
  service_disabled: "데이터 AI 서비스가 꺼져 있어 부르지 않았어요",
};
/** 사유 코드 없이 메모만 남은 기록 (총괄 plans/scenario_a.py의 note 문구) */
const SKIP_NOTE_KO: [RegExp, string][] = [
  [/^선행 단계 미완료/, SKIP_REASON_KO.no_fix_applied],
  [/지표는 아직 분석하지 못해/, SKIP_REASON_KO.metric_not_supported],
  [/서비스가 꺼져 있음/, SKIP_REASON_KO.service_disabled],
];
export const SKIP_DEFAULT = "이번 사건에서는 실행하지 않음";
export function skipReason(step: OrchStep): string {
  if (step.reason_code && SKIP_REASON_KO[step.reason_code]) return SKIP_REASON_KO[step.reason_code];
  return SKIP_NOTE_KO.find(([re]) => re.test(step.note ?? ""))?.[1] ?? SKIP_DEFAULT;
}

/** 시:분:초 (브라우저 시간대) */
export function clock(at: string | null | undefined): string | null {
  if (!at) return null;
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

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
  "dev.find_related_prs": "개발 AI · 관련 PR",
  "planning.find_related_prs": "개발 AI · 관련 PR", // 개발이 기획에서 나오기 전 기록
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

/** kpi_summary 근거 줄의 내부 이름 → 화면 말 (data metrics/kpi.py·skills/kpi_summary.py의 문구) */
export const KPI_NOTE_TERMS: [RegExp, string][] = [
  [/^grade_counts:\s*/, "등급별 처리: "],
  [/^blocked_counts:\s*/, "차단 사유별: "],
  [/^escalation_count:\s*/, "사람에게 넘긴 건수: "],
  [/^auto_handled_rate:\s*/, "AI 단독 처리율: "],
  // 문장에 이미 "최근 기록만 반영됨"이 있으면 꼬리표만 지우고, 없으면 한국어로 바꾼다
  [/(최근 기록만 반영됨)\s*\(truncated\)/, "$1"],
  [/\s*\(truncated\)/, " (최근 기록만 반영)"],
  [/incident\.resolved 발행·verify_recovery 완료 로그 없음/, "사건 해결 알림·복구 확인 기록 없음"],
  [/\(verify 완료 기준\)/, "(복구 확인 기록 기준)"],
  [/publish_notice 완료/, "공지 올리기"],
  [/\s*\((execute_decided|rejected|escalated)\)/, ""],
  [/해당 종류 로그 0건/, "기록 0건"],
  [/MTTR/, "복구 시간"],
];
/** 근거 줄 하나를 화면 말로: 사건 번호는 humanId 모양(장애 #…), 내부 이름은 한국어 */
export function kpiNote(line: string, humanId: (id: string) => string): string {
  let out = line.replace(/ctx_inc_[0-9a-z]+/g, (id) => humanId(id));
  for (const [re, ko] of KPI_NOTE_TERMS) out = out.replace(re, ko);
  return out;
}

/** 복구 시간 막대 단위: 전부 60초 미만이면 초, 아니면 분(소수 첫째 자리) */
export function mttrUnit(seconds: number[]): { unit: "초" | "분"; value: (s: number) => number } {
  if (seconds.every((s) => s < 60)) return { unit: "초", value: (s) => Math.round(s * 10) / 10 };
  return { unit: "분", value: (s) => Math.round(s / 6) / 10 };
}

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
/** AI를 쓰지 않는(켜지 않은) 부서로 넘긴 일: 부서별 건수와 업무 상위 3개 (예측 문구·숫자 없음) */
export interface UnusedHandoff { department: string; label: string; count: number; top_skills: SkillCount[] }
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
  /** 일간 저장을 쓴 값인지 (#data-report-snapshots). 예전 응답은 없다 */
  snapshot_at?: string | null;
  stored_days?: number | null;
  live_days?: number | null;
  snapshot_note?: string | null;
  /** 센 범위: 켠(AI를 쓰는) 서비스 id. 백엔드 AX_ENABLED_SERVICES 기준 */
  enabled_services?: string[] | null;
  unused_handoffs?: UnusedHandoff[];
}

// ---------------------------------------------------------------- 검색어 급상승 (trend_scan)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py TrendScanOutput. 판정·숫자는 모두 서버 값이다.
// 값은 검색 건수가 아니라 "그 요청 안 최댓값 = 100"인 상대값이다(그룹마다 따로 요청).
export type TrendStatus = "rising" | "normal" | "unknown";
export interface TrendEvidence {
  latest_period: string | null;
  latest_value: number | null;
  /** 기준선 = 판정 구간 전 56일 값의 중앙값 */
  baseline_value: number | null;
  baseline_points: number;
  spread: number | null;
  /** 평소 흔들림의 몇 배 위인지 (강건 z) */
  score: number | null;
  /** 평소 범위 띠 (예전 응답에는 없다) */
  band_low?: number | null;
  band_high?: number | null;
}
export interface TrendPoint { day: string; value: number }
export interface TrendVerdict {
  group_name: string;
  status: TrendStatus;
  ratio: number | null;
  evidence: TrendEvidence;
  unknown_reason: string | null;
  /** 받은 날짜별 값. 네이버가 주지 않은 날은 빠져 있다(0 아님) */
  series?: TrendPoint[];
  /** 판정 구간 시작일 (그 앞은 기준선) */
  window_from?: string | null;
  /** 이 그룹 설명 문장 (급상승만, 숫자는 서버가 검증) */
  summary?: string | null;
}
export interface TrendScanOutput {
  start_date: string;
  end_date: string;
  time_unit: "date" | "week" | "month";
  source: string;
  value_kind: "relative";
  results: TrendVerdict[];
}
// ---------------------------------------------------------------- YouTube 후보 (trend_candidates)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py TrendCandidatesOutput (#335).
// 후보는 최신 YouTube 인기 영상 스냅샷의 태그에서 코드 규칙으로 뽑고, 네이버 판정은 trend_scan과 같은 함수 결과다.
export interface YouTubeTagEvidence {
  /** 이 태그가 붙은 인기 영상 수 */
  video_count: number;
  /** 스냅샷 수집 시각 */
  snapshot_at: string;
  /** 대표 영상 id (인기 순위 순) */
  video_ids: string[];
  /** 이 후보로 센 태그 (스냅샷에 실제로 나온 것, #339). 예전 응답은 없다 */
  matched_tags?: string[];
  /** 이 후보로 센 제목 구 (YouTube 제목 2~3어절). 예전 응답은 없다 */
  matched_phrases?: string[];
}
/** 쌓인 스냅샷 전체에서 늘 붙는 말로 보고 자동으로 뺀 이름 (서버 값) */
export interface AutoExcludedTerm {
  name: string;
  source: "youtube_tag" | "youtube_title";
  snapshot_videos: number;
  videos: number;
  total_videos: number;
  doc_ratio: number;
  categories: number;
  days: number;
  total_days: number;
  /** 코드가 숫자로 만든 한 줄 */
  reason: string;
}
export interface TrendCandidate {
  keyword: string;
  /** 대표 이름에 묶인 별칭 (회사 별칭 표, #339). 네이버 그룹에 함께 넣었다. 없으면 빈 목록, 예전 응답은 없다 */
  aliases?: string[];
  youtube: YouTubeTagEvidence;
  /** 네이버 판정. 네이버를 못 읽으면 이 후보만 unknown + 이유. 이름 하나가 올라 급상승이 됐으면(naver_from = name) group_name은 그 이름 */
  naver: TrendVerdict;
  /** 이름마다 따로 판정한 결과 (별칭이 있을 때만). 예전 응답은 없다 */
  name_verdicts?: TrendVerdict[];
  /** 따로 판정해 급상승인 이름. 예전 응답은 없다 */
  rising_names?: string[];
  /** naver 판정이 어디서 왔는지: group = 묶음 그룹, name = 묶음은 평범인데 이름 하나가 급상승. 예전 응답은 없다 */
  naver_from?: "group" | "name";
  /** 후보가 나온 곳. 예전 응답은 없다 */
  origins?: CandidateOrigin[];
  /** 인기 검색어에도 있으면 그 근거. 예전 응답은 없다 */
  trending?: TrendingSearchEvidence | null;
  /** 데이터로 자동 묶은 다른 이름 (aliases에도 들어 있다). 예전 응답은 없다 */
  auto_grouped?: string[];
}
/** 후보 이름 두 개를 같은 대상으로 볼지의 근거 (서버 값) */
export interface NamePairEvidence {
  names: string[];
  /** 두 이름 네이버 일별 값(로그)의 같은 날 상관 */
  correlation: number;
  days: number;
  /** 쌓인 인기 영상 중 두 이름이 함께 나온 영상 수 */
  shared_videos: number;
  /** true = 자동으로 묶음, false = 제안만 */
  applied: boolean;
}
/** 후보가 나온 곳 (서버 값). 화면 이름: CANDIDATE_ORIGIN_LABEL */
export type CandidateOrigin = "youtube_tag" | "youtube_title" | "trending_search";
export const CANDIDATE_ORIGIN_LABEL: Record<CandidateOrigin | "keyword_group", string> = {
  keyword_group: "회사 기본 키워드",
  youtube_tag: "YouTube 태그",
  youtube_title: "YouTube 제목",
  trending_search: "인기 검색어",
};
/** 인기 검색어 근거 (Google Trends 공개 RSS 스냅샷, 피드 값 그대로) */
export interface TrendingSearchEvidence {
  /** 피드 안 순서 */
  rank: number;
  /** 피드의 트래픽 표시값 (예: "500+", 검색 건수가 아님) */
  traffic: string | null;
  snapshot_at: string;
  source?: string;
  /** 피드에 나온 검색어 그대로 */
  keyword?: string | null;
}
/** 인기 검색어에서만 나온 후보 (YouTube 근거 없음) */
export interface TrendingCandidate {
  keyword: string;
  trending: TrendingSearchEvidence;
  naver: TrendVerdict;
  aliases?: string[];
  name_verdicts?: TrendVerdict[];
  rising_names?: string[];
  naver_from?: "group" | "name";
  origins?: CandidateOrigin[];
}
export interface TrendCandidatesOutput {
  /** unknown = 스냅샷이 없거나 오래됨 (후보를 뽑지 않음) */
  status: "known" | "unknown";
  unknown_reason: string | null;
  snapshot_at: string | null;
  snapshot_videos: number;
  start_date: string | null;
  end_date: string | null;
  /** 나온 영상 수 많은 순 */
  candidates: TrendCandidate[];
  naver_calls: number;
  /** 코드가 숫자로 만든 한 문장 */
  summary: string;
  /** 인기 검색어에서만 나온 후보 (피드 순위 순). 예전 응답은 없다 */
  trending_candidates?: TrendingCandidate[];
  trending_status?: "known" | "unknown";
  trending_reason?: string | null;
  trending_snapshot_at?: string | null;
  /** 자동 제외된 늘 붙는 말과 근거. 예전 응답은 없다 */
  auto_excluded?: AutoExcludedTerm[];
  /** 자동 제외를 무엇으로 쟀는지 한 줄 (예: 쌓인 인기 영상 275개, 기록이 있는 날 1일) */
  exclusion_basis?: string | null;
  /** 자동으로 묶은 이름 쌍. 예전 응답은 없다 */
  auto_groups?: NamePairEvidence[];
  /** 기준에 못 미쳐 묶음 제안만 하는 쌍 (자동 적용 안 함). 예전 응답은 없다 */
  group_suggestions?: NamePairEvidence[];
}

/** 한 번에 판정할 수 있는 그룹 수 (화면 입력 한도) */
export const TREND_MAX_GROUPS = 5;

// ---------------------------------------------------------------- 월별 흐름 (trend_history)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py TrendHistoryOutput.
// 판정하지 않는다: 달마다 값(그 그룹 요청 안 상대값, 최댓값 100)과 작년 같은 달 대비 배수만 서버가 계산한다.
export interface TrendMonthPoint {
  /** 그 달 1일 */
  month: string;
  value: number;
  last_year_value: number | null;
  /** 작년 같은 달 대비 배수. 작년 값이 없거나 0이면 null */
  vs_last_year: number | null;
}
export interface TrendHistoryGroup {
  group_name: string;
  keywords: string[];
  status: "known" | "unknown";
  unknown_reason: string | null;
  /** 달 순서. 네이버가 주지 않은 달은 빠져 있다(0 아님) */
  points: TrendMonthPoint[];
  latest_month: string | null;
  latest_vs_last_year: number | null;
}
export interface TrendHistoryOutput {
  start_month: string;
  end_month: string;
  months: number;
  time_unit: "month";
  source: string;
  value_kind: "relative";
  groups: TrendHistoryGroup[];
  naver_calls: number;
  summary: string;
}

// ---------------------------------------------------------------- YouTube 지금 속도 (youtube_velocity)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py YouTubeVelocityOutput. 판정하지 않는다.
// 지금 속도 = 최근 두 스냅샷 사이 시간당 조회 증가. 숫자 칸이 빠진 영상은 서버가 비워 둔다(0 아님).
export interface YouTubeVideoVelocity {
  video_id: string;
  channel_id: string;
  published_at: string;
  popular_rank: number | null;
  tags: string[];
  views: number | null;
  likes: number | null;
  comments: number | null;
  views_per_hour: number | null;
  prev_views_per_hour: number | null;
  speed_change: number | null;
  /** 그 전 구간보다 빨라졌는지 (그 전 구간이 없으면 null) */
  faster: boolean | null;
  /** 영상 제목·채널 이름·길이(초) (#431, 예전 응답은 없다) */
  title?: string | null;
  channel_title?: string | null;
  duration_seconds?: number | null;
  /** 영상 주소와 썸네일 (서버가 저장된 YouTube 응답 값만, #620·#621). 예전 응답은 없다 */
  video_url?: string | null;
  thumbnail_url?: string | null;
}
export interface YouTubeVelocityOutput {
  status: "known" | "unknown";
  unknown_reason: string | null;
  latest_at: string | null;
  previous_at: string | null;
  earlier_at: string | null;
  compared_videos: number;
  missing_views: number;
  /** 지금 속도 큰 순, 상위 N개 */
  videos: YouTubeVideoVelocity[];
  summary: string;
}

// ---------------------------------------------------------------- 영상 소재 추천 (content_recommend)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py ContentRecommendOutput (#356, #370).
// 급상승만 코드 점수로 순위를 매기고(두 소스 가산), 소재마다 아이디어 문장·형식은 LLM(숫자 검증, 실패하면 기본 문장).
export type ContentSource = "naver" | "youtube";
export interface ContentScore {
  score: number;
  /** 변화 정도 (trend_scan 판정의 강건 z) */
  z: number;
  /** 기준선 대비 배수 */
  ratio: number;
  /** 검색 급상승 + YouTube 인기 태그 근거가 둘 다 있음 */
  two_sources: boolean;
  /** YouTube 인기 영상 수 (두 소스일 때) */
  youtube_videos: number | null;
}
export interface ContentRecommendation {
  rank: number;
  keyword: string;
  /** 같이 본 이름 (네이버 그룹의 다른 키워드) */
  aliases: string[];
  sources: ContentSource[];
  score: ContentScore;
  /** 회사 키워드와 겹치는지 (표시만, 순위에서 빼지 않음) */
  company_relevance: "related" | "low";
  company_match: string[];
  /** 영상 아이디어 1~2문장 (LLM, 숫자 검증. 실패하면 서버 기본 문장) */
  idea: string;
  format: "숏폼" | "롱폼";
  /** 아이디어 문장을 서버 기본 문장으로 바꿨는지 */
  fallback: boolean;
  fallback_reason: string | null;
  /** 소재가 나온 곳 (예전 응답은 없다) */
  origins?: (CandidateOrigin | "keyword_group")[];
  /** 연결할 회사 실제 상품 (회사 상품 목록 값 그대로). 예전 응답은 없다 */
  products?: LinkedProduct[];
  /** 회사 관련성 근거: 상품 연결·수동 회사 키워드·둘 다·없음. 예전 응답은 없다 */
  relevance_from?: "products" | "manual_keywords" | "both" | "none" | null;
  /** 밈 형식 (대표 영상 제목으로 서버 LLM이 목록 안에서만 고름). unknown = 분류하지 못함. 예전 응답은 없다 */
  meme_format?: "챌린지" | "패러디·밈" | "리액션" | "하이라이트" | "정보·리뷰" | "기타" | "unknown" | null;
  meme_reason?: string | null;
  /** 분류에 쓴 대표 영상 제목 수 */
  meme_titles?: number;
  /** 주제 분류 (서버 LLM이 목록 안에서만 고름). unknown = 분류하지 못함. 예전 응답은 없다 */
  topic_category?: TopicCategory | null;
  topic_category_reason?: string | null;
  /** 묶음: product_linked = 회사 상품과 연결된 소재, general = 일반 화제. 예전 응답은 없다 */
  group?: "product_linked" | "general" | null;
}
// ---------------------------------------------------------------- 지금 인기 (trend_popular)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py TrendPopularOutput (#411).
// 급상승 판정과 별개인 "지금 인기" 주제 순위. 출처 하나를 못 읽으면 그 몫만 None + 이유(0으로 채우지 않음)
export interface PopularScore {
  score: number;
  video_share: number | null;
  velocity_share: number | null;
  trending_share: number | null;
}
export interface PopularTopic {
  rank: number;
  keyword: string;
  /** 같이 센 이름 (태그·별칭·자동 묶음) */
  aliases: string[];
  origins: CandidateOrigin[];
  score: PopularScore;
  /** 이 주제가 나온 인기 영상 수 (YouTube를 못 읽었으면 null) */
  youtube_videos: number | null;
  video_ids: string[];
  /** 그 영상들의 지금 시간당 조회 증가 합 (못 쟀으면 null) */
  views_per_hour: number | null;
  velocity_videos: number;
  velocity_reason: string | null;
  /** 이번에 본 인기 검색어 중 순위 */
  trending_rank: number | null;
  /** 피드의 트래픽 표시값 (검색 건수 아님) */
  trending_traffic: string | null;
  topic_category: TopicCategory | null;
  topic_category_reason: string | null;
  brand_safe: boolean | null;
  /** 오늘 낸 네이버 판정이 급상승이면 true, 평범이면 false, 판정이 없으면 null */
  rising_now: boolean | null;
  rising_ratio: number | null;
  /** 실존 인물·팀·리그·상표가 주제에 들어 있는지와 그 이름 (#422, 분류하지 못했으면 null) */
  real_entity?: boolean | null;
  entity_names?: string[];
  /** 화면 카테고리 안 순위 1~5 (#431) */
  category_rank?: number | null;
  /** video_ids 순서의 영상 (제목·채널·조회수는 저장된 값, #512). 예전 응답은 없다 */
  videos?: PopularVideo[];
  /** 제목을 못 붙인 영상이 있으면 이유 */
  videos_reason?: string | null;
  /** "왜 뜨나" 근거 (#515, 예전 응답은 없다). 숫자는 서버 코드 값 */
  news?: TrendingNewsLink[];
  news_reason?: string | null;
  /** 네이버 판정 구간에서 평소 범위를 처음 넘은 날 */
  rising_started?: string | null;
  rising_reason?: string | null;
  /** 근거 숫자만 쓴 한 줄. llm = 숫자 검증 통과, fixed = 고정 문장, unknown = 근거 없음 */
  why_line?: string | null;
  why_status?: "llm" | "fixed" | "unknown" | null;
  why_reason?: string | null;
}
/** 화면용 카테고리 하나의 지금 인기 1~N위 (#431, 회사 설정 [popular] categories) */
export interface PopularCategory {
  category: TopicCategory;
  /** 점수 순, 최대 per_category개 */
  items: PopularTopic[];
  /** N개를 못 채웠으면 이유 */
  shortage_reason: string | null;
  /** 주제가 모자랄 때 그 카테고리의 최근 조회수 높은 영상 (지금 인기 아님, #470). 예전 응답은 없다 */
  top_videos?: PopularVideo[];
  /** 고른 기준, 또는 받지 못한 이유 */
  top_videos_reason?: string | null;
}
/** 카테고리의 최근 조회수 높은 영상 하나 (#470) */
/** 인기 검색어 RSS 관련 뉴스 하나 (피드 값 그대로) */
export interface TrendingNewsLink { title: string; source: string | null; url: string | null }
export interface PopularVideo {
  video_id: string;
  title: string | null;
  channel_title: string | null;
  views: number | null;
  published_at: string | null;
  duration_seconds: number | null;
  /** 칸을 정한 분류 (공유 분류기, #477). 예전 응답은 없다 */
  topic_category?: TopicCategory | null;
  /** YouTube가 준 카테고리 번호 (참고) */
  youtube_category_id?: string | null;
  /** 분류기 칸과 YouTube 카테고리가 다르면 true (분류기를 따름) */
  category_mismatch?: boolean | null;
  /** chart = 한국 인기 목록, search = 카테고리 검색 보충 */
  source?: "chart" | "search" | null;
  /** 영상 근거 (#515, 서버 코드 값, 모르면 null + evidence_reason) */
  hours_since_published?: number | null;
  views_per_hour?: number | null;
  channel_subscribers?: number | null;
  views_per_subscriber?: number | null;
  like_rate?: number | null;
  comment_rate?: number | null;
  is_short?: boolean | null;
  shorts_format?: string | null;
  evidence_reason?: string | null;
  /** 영상 주소와 썸네일 (서버가 저장된 YouTube 응답 값만, #620·#621). 예전 응답은 없다 */
  video_url?: string | null;
  thumbnail_url?: string | null;
  /** 광고 재료로 쓸 수 있는지·확인할 것 (#678·#679). 확인할 것이 없고 분류를 알 때만 true, 있으면 false, 모르면 null + caution_reason */
  ad_usable?: boolean | null;
  caution_types?: CautionType[];
  caution_reason?: string | null;
}
export interface TrendPopularOutput {
  /** 점수 순 */
  items: PopularTopic[];
  empty_reason: string | null;
  youtube_status: "known" | "unknown";
  youtube_reason: string | null;
  snapshot_at: string | null;
  velocity_status: "known" | "unknown";
  velocity_reason: string | null;
  velocity_from: string | null;
  velocity_to: string | null;
  trending_status: "known" | "unknown";
  trending_reason: string | null;
  trending_at: string | null;
  weights: Record<string, number>;
  excluded_categories: string[];
  summary: string;
  /** 카테고리별 보기 (#431, 예전 응답은 없다). 서버가 준 순서 그대로 */
  by_category?: PopularCategory[];
  /** 회사 설정의 화면 카테고리 순서 */
  display_categories?: string[];
  /** 점수 방식 (예: rank = 출처별 순위 몫) */
  score_method?: string | null;
}

// ---------------------------------------------------------------- 홍보 영상 기획안 (promo_concepts)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py PromoConceptsOutput (#415).
// 재료(상품·안전한 지금 인기·시즌·이벤트)는 코드가 모으고 LLM은 기획안 문장만. 재료 밖 상품·트렌드·숫자는 코드가 버린다(dropped)
export type PromoFormat = "15초 숏폼" | "30초 숏폼" | "롱폼";
export type PromoTrendKind = "popular" | "season" | "event";
export interface PromoProduct {
  product_id: string;
  name: string;
  category_name: string | null;
  /** 판매가 (원) */
  price: number | null;
}
export interface PromoTrend {
  name: string;
  kind: PromoTrendKind;
  topic_category: TopicCategory | null;
  /** 실존 인물·팀·리그·상표가 든 트렌드 */
  ip_risk: boolean;
  entity_names: string[];
  /** 코드가 만든 한 줄 */
  detail: string;
}
export interface PromoConcept {
  concept_id: string;
  title: string;
  concept: string;
  /** 빌려 온 트렌드 (재료 트렌드 이름 그대로) */
  borrowed_trend: string;
  trend_kind: PromoTrendKind | null;
  products: PromoProduct[];
  format: PromoFormat | null;
  /** 콘티 3컷 */
  storyboard: string[];
  /** 주의사항 (LLM) */
  cautions: string[];
  /** 코드가 단 경고 (상표·초상권 확인 필요 등) */
  warnings: string[];
  /** 숏츠 길이(초). 예전 응답은 없다 */
  duration_seconds?: number | null;
  /** 빌려 온 인기 숏츠 형식과 그 평균 조회수 (서버 값) */
  shorts_format?: string | null;
  shorts_format_avg_views?: number | null;
  /** 그 형식의 중앙값 조회수(대표값)와 숏츠 통계 기준 시각 (#547) */
  shorts_format_median_views?: number | null;
  shorts_format_at?: string | null;
  /** 이 트렌드·형식이 이 상품과 맞는 이유 한 줄 */
  fit_reason?: string | null;
  /** 타깃 연령대 (trend_age, #524). 없으면 target_age_reason이 미정 이유 */
  target_age?: string | null;
  target_age_ratio?: number | null;
  target_age_status?: TrendStatus | null;
  target_age_reason?: string | null;
}
/** 기획안 재료로 쓴 인기 숏츠 형식 (shorts_stats 값) */
export interface PromoShortsFormat {
  name: string;
  /** 보조 칸 (평균) */
  avg_views: number;
  count: number;
  /** 대표값 = 중앙값, 숏츠 통계 기준 시각 (#547, 예전 응답은 없다) */
  median_views?: number | null;
  snapshot_at?: string | null;
}
/** 시즌을 확인한 키워드 하나 (카탈로그 카테고리 이름 또는 상품 핵심어) */
export interface PromoSeasonCheck {
  keyword: string;
  source: "category" | "product";
  status: "known" | "unknown";
  reason: string | null;
  same_month: string | null;
  same_month_ratio: number | null;
  next_month: string | null;
  next_month_ratio: number | null;
}
export interface DroppedConcept {
  title: string;
  reason: string;
}
export interface PromoConceptsOutput {
  concepts: PromoConcept[];
  dropped: DroppedConcept[];
  empty_reason: string | null;
  trends: PromoTrend[];
  /** 재료 상품 수 */
  products: number;
  catalog_status: "known" | "unknown";
  catalog_reason: string | null;
  popular_status: "known" | "unknown";
  popular_reason: string | null;
  season_status: "known" | "unknown";
  season_reason: string | null;
  naver_calls: number;
  /** 오늘 만든 기획안을 그대로 줬는지 */
  cached: boolean;
  run_id: string | null;
  generated_at: string | null;
  summary: string;
  /** 재료 숏츠 형식·시즌 확인 키워드 (예전 응답은 없다) */
  shorts_formats?: PromoShortsFormat[];
  shorts_status?: "known" | "unknown";
  shorts_reason?: string | null;
  season_checks?: PromoSeasonCheck[];
  /** 재료 기준·사람 이름으로 뺀 지금 인기 주제와 이유 */
  skipped_trends?: { name: string; reason: string }[];
  /** 지금 인기 트렌드를 쓴 초안이 없을 때 이유 */
  popular_trend_note?: string | null;
}

/** 근거 영상 하나의 주소와 썸네일 (계약 VideoLink). 서버가 저장된 YouTube 응답 값만 준다 */
export interface VideoLink {
  video_id: string;
  video_url: string | null;
  thumbnail_url: string | null;
  /** 광고 재료로 쓸 수 있는지·확인할 것 (#678·#679). 확인할 것이 없고 분류를 알 때만 true, 있으면 false, 모르면 null + caution_reason */
  ad_usable?: boolean | null;
  caution_types?: CautionType[];
  caution_reason?: string | null;
  /** 저장된 응답의 제목·조회수 (#706, 없으면 null) */
  title?: string | null;
  views?: number | null;
}

// ---------------------------------------------------------------- 영상 탐색 (video_explorer)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py VideoExplorerOutput (#709).
// 카테고리 × 기간(올린 시각 1일·3일·7일) × 길이(숏 = 서버 기준 이하, 롱 = 초과). 수집기가 미리 계산하고 요청은 읽기·정렬만
export type ExplorerPeriod = "1d" | "3d" | "7d";
export type ExplorerLength = "short" | "long";
export type ExplorerSort = "views" | "views_per_subscriber" | "likes";
export interface ExplorerVideo {
  video_id: string;
  title: string | null;
  channel_title: string | null;
  channel_id: string | null;
  thumbnail_url: string | null;
  video_url: string | null;
  duration_seconds: number | null;
  /** 올린 시각 */
  published_at: string | null;
  views: number | null;
  views_reason: string | null;
  likes: number | null;
  /** 좋아요 숨김 등 */
  likes_reason: string | null;
  channel_subscribers: number | null;
  /** 구독자 대비 배수 */
  views_per_subscriber: number | null;
  subscriber_reason: string | null;
  category: TopicCategory | null;
  ad_usable: boolean | null;
  caution_types: CautionType[];
  caution_reason: string | null;
  source: string | null;
  /** 조회수·좋아요를 읽은 시각 */
  at: string | null;
  /** 칸이 모자라 7일 이전 숏츠 표본으로 채운 영상 (#730, 예전 응답은 없다) */
  before_period?: boolean;
}
export interface ExplorerCell {
  category: string;
  period: ExplorerPeriod;
  length: ExplorerLength;
  count: number;
  /** 최소 수보다 적으면 이유 */
  shortage_reason: string | null;
  videos: ExplorerVideo[];
}
export interface VideoExplorerOutput {
  status: "known" | "unknown";
  reason: string | null;
  category: TopicCategory | null;
  period: ExplorerPeriod;
  length: ExplorerLength;
  sort: ExplorerSort;
  /** 설정의 카테고리 (화면 순서) */
  categories: string[];
  min_per_cell: number;
  cells: ExplorerCell[];
  computed_at: string | null;
  searches_today: number;
  search_cap: number;
  quota_used_today: number;
  limits: string[];
  summary: string;
}

// ---------------------------------------------------------------- 인사이트 브리프 (trend_insight_brief)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py TrendInsightBriefOutput. 서버 하루 캐시.
// 기획팀이 제안에 insight_id를 적는다. 예시 아이디어(example_ideas)는 참고용
export interface InsightEvidence {
  youtube_videos: number | null;
  youtube_views_per_hour: number | null;
  youtube_views_per_subscriber: number | null;
  youtube_at: string | null;
  google_rank: number | null;
  google_news: TrendingNewsLink[];
  google_at: string | null;
  naver_ratio: number | null;
  naver_status: TrendStatus | null;
  naver_rising_started: string | null;
  naver_until: string | null;
  links: string[];
  /** 없는 출처와 이유 */
  missing: string[];
  /** 시즌 근거 (작년 같은 달·다음 달 ÷ 기간 평균 중 큰 값, #543). 예전 응답은 없다 */
  season_keyword?: string | null;
  season_ratio?: number | null;
  season_month?: string | null;
  /** 근거 영상의 주소·썸네일 (links의 영상과 같은 것). 예전 응답은 없다 */
  video_links?: VideoLink[];
}
export interface InsightProduct {
  product_id: string;
  name: string;
  category_name: string | null;
  similarity: number | null;
  /** 코드가 만든 연결 근거 한 줄 */
  reason: string;
  season: string | null;
}
/** 근거 요약 하나 (계약 EvidenceHighlight, #710). 서버 코드가 facts 중 센 것을 최대 3개 고른다(LLM 안 씀).
 *  평소 값이 없는 근거(Google 순위·숏츠 조회수 등)는 baseline_value·ratio가 null이고 값만 있다 */
export type EvidenceHighlightSource = "naver_search" | "naver_shopping" | "youtube" | "google";
export interface EvidenceHighlight {
  source: EvidenceHighlightSource;
  label: string;
  /** 지금 값 */
  value: number;
  unit: string;
  /** 평소 값 (기준선) */
  baseline_value: number | null;
  /** 지금 ÷ 평소 */
  ratio: number | null;
  period: string | null;
  sample: number | null;
  /** 템플릿 문장 하나 */
  sentence: string;
  fact_id: string | null;
  /** 표본이 기준보다 적은 근거 (#735): 화면은 막대 없이 회색 "참고". 예전 응답은 없다 */
  low_sample?: boolean;
  /** 표시 문구 (예: "표본 6개 · 참고") */
  sample_note?: string | null;
}

/** 형식 빌리기 인사이트의 빌릴 숏츠 형식 (#737, 숏츠 통계 값 그대로) */
export interface BorrowFormat {
  name: string;
  median_views: number | null;
  samples: number | null;
  source: "topic" | "category" | "all" | null;
}

/**
 * 인사이트의 지난번 캠페인 결과 하나 (계약 InsightPastResult, campaign_result 값 그대로 + 띠 문장).
 * 띠 색·둘째 줄·셋째 줄은 서버가 정한다. 화면은 계산하지 않는다
 */
export interface InsightPastResult {
  campaign_id: string;
  campaign_name?: string | null;
  window_from?: string | null;
  window_to?: string | null;
  views_verdict: "achieved" | "missed" | "measuring" | "none";
  /** "조회 달성" · "조회 미달" · "측정 중" · "조회 판정 없음" */
  views_label: string;
  sales_verdict?: "more" | "less" | "no_effect" | null;
  /** "더 팔림" · "덜 팔림" · "효과 확인 안 됨(표본 적음)" · "측정 불가: …" */
  sales_label: string;
  actions: string[];
  alerts: { kind: "loss" | "data_gap"; message: string; note?: string | null }[];
  band: "green" | "red" | "gray";
  /** "조회 목표의 N배 (영상 M개 중 K개 달성) · 매출 +N원 · 주문 +N건" */
  summary_line: string;
  /** "지난번 개선 방향: …" 또는 조회·매출 반대 문구 */
  note_line?: string | null;
}
export interface TrendInsight {
  insight_id: string;
  rank: number;
  topic: string;
  aliases: string[];
  /** 한 줄 인사이트 (검증 실패면 고정 문장) */
  line: string;
  line_status: "llm" | "fixed";
  line_reason: string | null;
  category: TopicCategory | null;
  ad_material: boolean | null;
  ad_material_reason: string | null;
  reference_only: boolean;
  real_entity: boolean | null;
  entity_names: string[];
  evidence: InsightEvidence;
  target_age: string | null;
  target_age_ratio: number | null;
  target_age_status: TrendStatus | null;
  target_age_reason: string | null;
  products: InsightProduct[];
  products_reason: string | null;
  shorts_format: string | null;
  shorts_format_samples: number | null;
  shorts_format_reason: string | null;
  /** 형식을 고른 단계: topic = 이 주제 근거 숏츠, category = 이 주제 카테고리 숏츠 통계, all = 전체 숏츠 통계 (#556). 예전 응답은 없다
   *  count = 이 형식인 숏츠 수, total = 형식을 분류한 숏츠 수 */
  shorts_format_source?: "topic" | "category" | "all" | null;
  shorts_format_source_count?: number | null;
  shorts_format_source_total?: number | null;
  /** 연령대별 평소 대비 배수 전체 (trend_age 결과 그대로, 설정 순서, #556). 보지 않은 주제는 빈 목록 + target_age_reason */
  age_bands?: AgeBandTrend[];
  /** 숏츠 통계와 같은 시점의 그 형식 중앙값 조회수와 기준 시각 (#547). 예전 응답은 없다 */
  shorts_format_median_views?: number | null;
  shorts_format_at?: string | null;
  limits: string[];
  example_ideas: string[];
  score: number;
  /** 분류기가 아직 분류하지 못한 주제 (순위에 넣지 않고 참고용, #543). 예전 응답은 없다 */
  classification_pending?: boolean;
  /** 회사 관련 인사이트 칸 (시즌 키워드·상품 핵심어 중 오른 것, #543). 예전 응답은 없다 */
  company_related?: boolean;
  /** 왜 회사 관련으로 골랐는지 (코드 문장) */
  company_reason?: string | null;
  /** 날짜 없이 바뀐 insight_id 이전의 id와 이 인사이트의 숫자 (#614). 예전 응답은 없다 */
  legacy_insight_id?: string | null;
  fact_ids?: string[];
  /** 회사와의 연결 (#628): direct = 연결 상품, domain = 도메인 프로필·회사 키워드, none = 둘 다 아님(참고용). 예전 응답은 없다 */
  company_relevance?: "direct" | "domain" | "none" | "format" | null;
  company_relevance_reason?: string | null;
  /** 형식 빌리기(company_relevance = format, #737): 빌릴 형식·이어 붙일 책·주의. 예전 응답은 없다 */
  borrow_format?: BorrowFormat | null;
  borrow_products?: InsightProduct[];
  cautions?: CautionType[];
  caution_note?: string | null;
  /** 브리프 v4: 같은 insight_id로 한 캠페인의 지난번 결과 (최근 3개, 최신순). 없으면 []. 예전 응답은 없다 */
  past_results?: InsightPastResult[];
  /** 숏츠 적합도·목표 참고값 (#678). 숏츠 표본이 모자라면 null + shorts_fit_status unknown + shorts_fit_reason. 예전 응답은 없다 */
  shorts_fit?: ShortsFit | null;
  shorts_fit_status?: "known" | "unknown" | null;
  shorts_fit_reason?: string | null;
  target_views_reference?: number | null;
  target_reference_basis?: string | null;
  /** 평소 대비 가장 크게 오른 연령대 (#678, age_bands 중 known만). 모르면 null + top_age_band_reason. 예전 응답은 없다 */
  top_age_band?: string | null;
  top_age_band_ratio?: number | null;
  top_age_band_reason?: string | null;
  /** 근거 요약 최대 3개·근거 숏츠 최대 3개 (#710, 예전 응답은 없다) */
  evidence_highlights?: EvidenceHighlight[];
  evidence_shorts?: VideoLink[];
  /**
   * 브리프 v6 (예전 응답은 없다). trend = 트렌드 주제, promo = 실제로 오르는 숏츠 형식만 빌린 캣북스 홍보 영상 아이디어
   * (topic = 형식 이름). promo 근거: 숏츠 전체 대비 배수(이 형식 1시간 조회 중앙값 ÷ 전체 숏츠 1시간 조회 중앙값),
   * 최근 N일 안에 게시된 같은 형식 수, 게시 시각을 몰라 세지 않은 수
   */
  kind?: "trend" | "promo";
  promo_format?: string | null;
  promo_idea?: string | null;
  /** 아이디어가 LLM 대신 고정 틀 문장이면 true */
  promo_idea_fallback?: boolean;
  promo_views_per_hour_ratio?: number | null;
  promo_recent_count?: number | null;
  promo_recent_days?: number | null;
  promo_recent_unknown?: number | null;
  /** 분자·분모에서 뺀 하위 주제 숏츠 수 */
  promo_subtopic_excluded?: number | null;
  /** 기획에 넘기는 기준을 통과한 근거 한 줄 */
  gate_reason?: string | null;
}
/** 근거가 모자라 기획에 넘기지 않은 주제·형식과 이유 (브리프 v6) */
export interface HeldBack { topic: string; reason: string }
/** 이 판에 쓴 기획에 넘기는 기준 값 (서버 설정 그대로) */
export interface BriefGate {
  vs_all_ratio: number;
  naver_ratio: number;
  naver_min_format_ratio: number;
  recent_min: number;
  max_ratio: number;
}
export interface TrendInsightBriefOutput {
  status: "known" | "unknown";
  reason: string | null;
  day: string | null;
  rules_version: string;
  insights: TrendInsight[];
  cached: boolean;
  generated_at: string | null;
  sources: string[];
  limits: string[];
  summary: string;
  /** 기회 가설 카드 (#579). 예전 응답은 없다. 비었으면 opportunities_reason */
  opportunities?: DomainOpportunity[];
  opportunities_reason?: string | null;
  /** 쓴 수집 계획 id */
  plan_id?: string | null;
  /** 응답에 나오는 숫자 목록 (#614). 인사이트·카드는 fact_ids로 가리킨다. 예전 응답은 없다 */
  facts?: DataFact[];
  schema_version?: string;
  snapshot_id?: string | null;
  /** 지금 data LLM 상태 (오류 원인·중단). 저장된 브리프를 줄 때도 응답 시점 값. 예전 응답은 없다 */
  llm_status?: LlmStatus | null;
  // 아래는 v5 (같은 날 판·다시 추천, 계약 trend_insight_brief.v5). 예전 응답은 없다. 문장은 서버 값
  version?: number | null;
  latest_version?: number | null;
  /** "00:27 자동" · "11:16 다시 추천" */
  version_label?: string | null;
  created_at?: string | null;
  trigger?: BriefTrigger | null;
  requested_by?: string | null;
  detect_as_of?: string | null;
  versions?: BriefVersionInfo[];
  regen_used?: number;
  regen_limit?: number;
  regen_blocked?: BriefRegenBlocked | null;
  regen_job?: BriefRegenJob | null;
  building?: BriefBuilding | null;
  diff_from_previous?: BriefDiff | null;
  estimated_llm_pct?: number | null;
  estimated_llm_basis?: string | null;
  estimated?: boolean;
  llm_pct_now?: number | null;
  detect_latest_at?: string | null;
  /** v6: 기준(트렌드 근거)을 넘지 못해 기획에 넘기지 않은 것 (예전 응답은 없다) */
  held_back?: HeldBack[];
  gate?: BriefGate | null;
}
export type BriefTrigger = "auto" | "manual";
export interface BriefVersionInfo { version: number; label: string; created_at: string; trigger: BriefTrigger; requested_by: string | null; detect_as_of: string | null }
export interface BriefRegenBlocked { code: "daily_limit" | "cooldown" | "llm_budget" | "in_progress" | "no_new_data"; text: string }
export interface BriefRegenJob { job_id: string; state: "running" | "done" | "failed"; requested_by: string | null; started_at: string; finished_at: string | null; version: number | null; tokens: number | null; reason: string | null }
export interface BriefBuilding { who: string; trigger: BriefTrigger; started_at: string; text: string }
export interface BriefDiff { from_version: number; from_label: string; new_topics: string[]; gone_topics: string[]; new_count: number; gone_count: number }
/**
 * data LLM 오류 원인과 중단 상태 (계약 LlmStatus). 문구는 서버 문장 그대로.
 * key_error 빨강(401·403, 그날 중단) · provider_limit 노랑(429) · our_cap 파랑(우리 하루 상한).
 * 키 값·주소 전체는 오지 않는다 (키 이름·모델·호스트만).
 */
export interface LlmStatus {
  /** request_error = 400 (같은 요청만 그날 중단, 빨강) */
  state: "ok" | "key_error" | "provider_limit" | "our_cap" | "request_error";
  tone: "none" | "red" | "yellow" | "blue";
  title?: string | null;
  detail?: string | null;
  key_source: string;
  model?: string | null;
  base_host?: string | null;
  halted_until?: string | null;
  strikes: number;
  /** request_error: 막힌 응답 형식 이름과 공급자 메시지 앞 200자 (서버가 키·토큰 모양을 가림) */
  request?: string | null;
  error_message?: string | null;
}
/** 응답에 나오는 숫자 하나 (출처·기간·표본, #614) */
export interface DataFact {
  fact_id: string;
  label: string;
  value: number;
  unit: string;
  source: string;
  /** 근거 주소 (영상·뉴스 등) */
  link: string | null;
  period: string | null;
  sample: number | null;
  /** 어느 항목의 숫자인지 (insight_id·opportunity_id) */
  about: string | null;
}

// ---------------------------------------------------------------- 연령대별 상승 (trend_age)
// 계약: TrendAgeOutput. 네이버 값은 요청마다 최댓값이 100인 상대값이라 연령대끼리 비교하지 않는다(note)
export interface AgeBandTrend {
  band: string;
  ages: string[];
  status: TrendStatus;
  ratio: number | null;
  latest_value: number | null;
  baseline_value: number | null;
  reason: string | null;
}
export interface AgeTopic {
  keyword: string;
  keywords: string[];
  sources: ("popular" | "rising" | "season" | "request")[];
  status: "known" | "unknown";
  reason: string | null;
  bands: AgeBandTrend[];
  top_band: string | null;
  top_ratio: number | null;
}
export interface TrendAgeOutput {
  status: "known" | "unknown";
  reason: string | null;
  start_date: string | null;
  end_date: string | null;
  bands: string[];
  items: AgeTopic[];
  calls_used: number;
  call_cap: number;
  cached: boolean;
  note: string;
  generated_at: string | null;
  summary: string;
}

// 출처가 주는 데이터 종류 (#571): 검색 관심 · 실제 소비 · 시청 · 조사
export type SourceDataKind = "search_interest" | "consumption" | "viewing" | "survey";

// ---------------------------------------------------------------- 도메인 프로필 (domain_profile, #573)
export interface ProductGroup { name: string; category_codes: string[]; product_count: number }
export interface SeedKeyword {
  keyword: string;
  /** 근거 문장 (소개·상품 이름·카테고리·설명 원문 일부) */
  evidence: string;
  evidence_from: "about" | "product" | "category" | "description" | "override";
  product_ids: string[];
}
/** 프로필 칸 하나의 근거 문장 (소개·상품 이름·카테고리 이름·설명 원문 일부, 코드가 대조) */
export interface ProfileEvidence {
  field: "industry" | "product_group" | "customer_axis" | "question";
  /** 그 칸의 값 (업종 이름·상품군 이름·고객 축·질문) */
  value: string;
  evidence: string;
  evidence_from: "about" | "product" | "category" | "description";
}
export interface DomainProfileOutput {
  status: "known" | "unknown";
  reason: string | null;
  tenant: string | null;
  industry: string | null;
  industry_tags: string[];
  product_groups: ProductGroup[];
  customer_axes: string[];
  questions: string[];
  seed_keywords: SeedKeyword[];
  dropped_keywords: { keyword: string; reason: string }[];
  /** 업종·상품군·고객 축·분석 질문의 근거 문장 (원문 대조를 통과한 것만). 예전 응답은 없다 */
  evidence?: ProfileEvidence[];
  /** 사람이 덮어쓴 칸 이름 */
  overridden: string[];
  catalog_hash: string;
  products_seen: number;
  cached: boolean;
  generated_at: string | null;
  limits: string[];
}

// ---------------------------------------------------------------- 데이터 출처 목록 (data_sources, #576)
export type SourceStatus = "available" | "key_required";
export interface DataSource {
  source_id: string;
  name: string;
  kinds: SourceDataKind[];
  /** 맞는 업종 태그 ("all" = 전체) */
  industries: string[];
  age: boolean;
  gender: boolean;
  categories: "api" | "fixed" | "none";
  period: string;
  quota_daily: number | null;
  quota_unit: string;
  quota_note: string;
  /** 필요한 키 환경변수 이름 (값은 없다) */
  key_envs: string[];
  status: SourceStatus;
  status_reason: string | null;
  docs_url: string;
  approved: boolean;
}
/** 스냅샷 수집기 상태 (YouTube·인기 검색어). stalled면 warning에 "수집 멈춤: ..." */
export interface CollectorStatus {
  collector_id: string;
  name: string;
  running: boolean;
  pid: number | null;
  started_at: string | null;
  last_collected_at: string | null;
  max_age_hours: number;
  stalled: boolean;
  warning: string | null;
  reason: string;
}
/** collectors는 예전 응답에 없다 */
/** data 부서 LLM 오늘(한국 날짜) 사용량과 하루 상한. reached면 오늘은 LLM을 부르지 않는다 */
export interface LlmDailyUsage {
  day: string;
  calls: number;
  call_cap: number;
  tokens: number;
  token_cap: number;
  reached: boolean;
  note: string | null;
}
/** 영상 탐색 카테고리 인기 차트를 받지 못한 YouTube 카테고리. 그 칸은 검색(최근 3일, 하루 상한 안)으로 대신 채운다 */
export interface YouTubeChartFailure {
  category_id: string;
  categories: string[];
  code: string;
  reason: string;
  failed_at: string;
  fallback: "searched" | "cap" | "failed" | "not_run";
  fallback_found: number | null;
  fallback_reason: string | null;
  fallback_day: string | null;
}
/** collectors·llm_usage·youtube_chart_failures는 예전 응답에 없다 */
export interface DataSourcesOutput {
  sources: DataSource[];
  checked_at: string | null;
  summary: string;
  collectors?: CollectorStatus[];
  llm_usage?: LlmDailyUsage | null;
  youtube_chart_failures?: YouTubeChartFailure[];
  /** 새 숏츠 추적 (같은 형식 비교 기준용). 예전 응답은 없다 */
  fresh_track?: FreshTrackStatus | null;
}
/** 새 숏츠 추적: 오늘 쓴 YouTube 단위 / 이 작업 하루 상한, 추적 중 수 (서버 값 그대로) */
export interface FreshTrackStatus {
  units_today: number;
  unit_cap: number;
  tracking: number;
  formats: string[];
  /** 상한에 닿아 오늘 찾기를 멈춤 (추적은 계속) */
  find_stopped: boolean;
  /** "새 숏츠 추적: 오늘 N유닛 / 2,500, 추적 중 N개" */
  line: string;
}

// ---------------------------------------------------------------- 수집 계획 (source_plan, #578)
export interface PlanDropped { value: string; reason: string }
export interface SourcePlanItem {
  source_id: string;
  status: SourceStatus;
  approved: boolean;
  /** 이 출처를 고른 이유 (LLM 문장) */
  reason: string;
  categories: { category_id: string; name: string | null }[];
  category_check: "verified" | "after_key" | "not_applicable" | "unknown";
  category_check_reason: string | null;
  keywords: string[];
  use_age: boolean;
  calls: number;
  quota_units: number;
  dropped: PlanDropped[];
  /** 키 필요 출처: 이 키가 있으면 더 쓸 수 있는 근거 */
  unlocks: string | null;
}
export interface PlanQuota { source_id: string; units: number; cap: number; unit: string }
export interface SourcePlanOutput {
  status: "known" | "unknown";
  reason: string | null;
  plan_id: string;
  profile_hash: string;
  items: SourcePlanItem[];
  dropped_sources: PlanDropped[];
  quota: PlanQuota[];
  trimmed: string[];
  approval_note: string;
  cached: boolean;
  generated_at: string | null;
  summary: string;
}

// ---------------------------------------------------------------- 기회 가설 카드 (trend_insight_brief 선택 칸, #571·#579)
// 수집 계획(source_plan)대로 모은 근거(사용 가능·승인된 출처만). 숫자는 코드, 한 줄만 LLM
export type EvidenceStatus = "known" | "unknown" | "key_required" | "not_approved";
export interface OpportunityEvidence {
  source_id: string;
  kind: SourceDataKind;
  status: EvidenceStatus;
  /** 무엇을 잰 값인지 */
  label: string;
  value: number | null;
  unit: string | null;
  period_start: string | null;
  period_end: string | null;
  samples: number | null;
  /** 모르면 이유 (예: 키 등록 후 가능) */
  reason: string | null;
}
export interface ShortsFit {
  level: "high" | "uncertain" | "low";
  category: string | null;
  median_views: number | null;
  overall_median_views: number | null;
  ratio: number | null;
  samples: number;
  snapshot_at: string | null;
  /** 이 등급인 이유 (기준값 포함, 코드 문장) */
  reason: string;
}
export interface OpportunityFormat { name: string; count: number; median_views: number | null }
export interface DomainOpportunity {
  opportunity_id: string;
  subtopic: string;
  subtopic_evidence: string | null;
  line: string;
  line_status: "llm" | "fixed";
  line_reason: string | null;
  consumption: OpportunityEvidence[];
  search_interest: OpportunityEvidence[];
  age_bands: AgeBandTrend[];
  age_reason: string | null;
  shorts_fit: ShortsFit | null;
  formats: OpportunityFormat[];
  /** 목표 참고값: 이 분류 숏츠 조회 중앙값 (없으면 전체 중앙값) */
  target_views_reference: number | null;
  target_reference_basis: string | null;
  products: InsightProduct[];
  products_reason: string | null;
  limits: string[];
  /** 기획이 정할 것 (코드 고정 문장) */
  planning_decides: string[];
  score: number;
  /** 하위 주제 넓히기에서 온 카드의 축과 씨앗 키워드 (#587). 예전 응답은 없다 */
  axis?: string | null;
  seed_keyword?: string | null;
  /** 이 카드의 숫자 (브리프 facts의 fact_id, #614) */
  fact_ids?: string[];
  /** 근거 숏츠의 주소·썸네일. 예전 응답은 없다 */
  video_links?: VideoLink[];
  /** 평소 대비 가장 크게 오른 연령대 (#678, age_bands 중 known만). 모르면 null + top_age_band_reason. 예전 응답은 없다 */
  top_age_band?: string | null;
  top_age_band_ratio?: number | null;
  top_age_band_reason?: string | null;
  /** 근거 요약 최대 3개·근거 숏츠 최대 3개 (#710, 예전 응답은 없다) */
  evidence_highlights?: EvidenceHighlight[];
  evidence_shorts?: VideoLink[];
}

// ---------------------------------------------------------------- 숏츠 통계 (shorts_stats)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py ShortsStatsOutput (#437).
// 숏츠 = 길이 기준(max_seconds) 이하 영상. 숫자는 코드, 분류는 LLM(정해진 목록 안). 표본이 적은 묶음은 unknown + 이유
export interface ShortsVideo {
  video_id: string;
  title: string | null;
  channel_title: string | null;
  views: number | null;
  /** 시간당 조회 증가 (두 번 본 숏츠만) */
  views_per_hour?: number | null;
  /** known = 잼, measuring = 다시 본 간격이 기준 미만, pending = 다음 조회 대기 */
  velocity_status?: "known" | "measuring" | "pending" | null;
  /** 영상 주소와 썸네일 (서버가 저장된 YouTube 응답 값만, #620·#621). 예전 응답은 없다 */
  video_url?: string | null;
  thumbnail_url?: string | null;
  /** 화면 카드 칸 (#683, by_category[].videos만 채운다). 주제 분류 */
  category?: string | null;
  /** 채널 구독자 수·조회 ÷ 구독자 (channels.list). 숨김·못 받음이면 null + subscriber_reason */
  channel_subscribers?: number | null;
  views_per_subscriber?: number | null;
  subscriber_reason?: string | null;
  /** 광고 재료로 쓸 수 있는지·확인할 것 (VideoLink와 같은 규칙) */
  ad_usable?: boolean | null;
  caution_types?: CautionType[];
  caution_reason?: string | null;
}
/** 확인할 것: 실존 인물(초상권)·상표·음원 (계약 CautionType, #678) */
export type CautionType = "real_person" | "trademark" | "music";
export interface ShortsGroup {
  /** 주제 분류 값 또는 숏츠 형식 */
  name: string;
  status: "known" | "unknown";
  /** unknown이면 이유 (예: 표본 부족) */
  reason: string | null;
  count: number;
  avg_views: number | null;
  /** 시간당 조회 증가를 잰 숏츠만의 평균 */
  avg_views_per_hour: number | null;
  velocity_videos: number;
  /** 조회수 상위 N개 */
  top: ShortsVideo[];
  /** 대표값 = 중앙값 (#547). 평균(avg_*)은 보조 */
  median_views?: number | null;
  median_views_per_hour?: number | null;
  /** 다시 본 간격이 기준 미만이라 측정 중인 수 */
  velocity_measuring?: number;
  /** 카테고리 그룹만: 화면 카드 숏츠 (조회수 순, 설정 개수까지, 표본 부족 칸도). #683, 예전 응답은 없다 */
  videos?: ShortsVideo[];
}
export interface ShortsSearch {
  youtube_category_id: string;
  status: "known" | "unknown";
  reason: string | null;
  found: number;
  shorts: number;
}
export interface ShortsStatsOutput {
  status: "known" | "unknown";
  reason: string | null;
  snapshot_at: string | null;
  /** 숏츠 길이 기준(초) */
  max_seconds: number;
  /** 숏츠 표본 수 (인기 + 검색, 중복 없음) */
  shorts: number;
  popular_shorts: number;
  searched_shorts: number;
  /** 길이를 몰라 뺀 인기 영상 수 */
  unknown_length: number;
  by_category: ShortsGroup[];
  by_format: ShortsGroup[];
  search: ShortsSearch[];
  /** known = 이번에 검색함, cached = 오늘 이미 검색해 저장한 결과, unknown = 검색하지 않음(이유) */
  search_status: "known" | "unknown" | "cached";
  search_reason: string | null;
  quota_used: number;
  /** 평균 시간당 조회 증가를 잰 범위 */
  velocity_note: string | null;
  /** 표본 한계 (서버 문장) */
  limitation: string;
  summary: string;
}

export type TopicCategory =
  | "뉴스·사건" | "정치·사회" | "기부·재난" | "인물" | "게임" | "연예·스포츠" | "K-pop·음악" | "유머" | "드라마" | "교양" | "생활·취미" | "교육·책" | "IT" | "기타" | "unknown";
/** 급상승했지만 회사 설정의 "추천 안 함" 분류라 추천하지 않은 소재 (참고용, 서버 값) */
export interface ReferenceTopic {
  keyword: string;
  aliases: string[];
  origins: (CandidateOrigin | "keyword_group")[];
  ratio: number;
  z: number;
  two_sources: boolean;
  topic_category: TopicCategory;
  topic_category_reason: string | null;
  /** 추천하지 않은 이유 (서버가 만든 한 줄) */
  reason: string;
}
/** 소재와 연결한 회사 실제 상품 (서버 값) */
export interface LinkedProduct {
  product_id: string;
  name: string;
  category_code: string | null;
  category_name: string | null;
  /** name = 상품 이름 글자 일치, category = 카테고리 이름 일치, embedding = 임베딩 유사도 */
  match: "name" | "category" | "embedding";
  similarity: number | null;
  /** 서버가 만든 한 줄 (예: 이름 일치: 파이썬) */
  reason: string;
  /** 임베딩으로 이었을 때 1등 − 2등 유사도 (예전 응답·글자 일치는 없다) */
  margin?: number | null;
}
export interface ContentRecommendOutput {
  start_date: string | null;
  end_date: string | null;
  /** 순위 순 */
  items: ContentRecommendation[];
  /** 추천이 없을 때 서버 이유 (예: 이번 기간 급상승한 소재가 없음) */
  empty_reason: string | null;
  /** 이번 실행에서 네이버를 실제로 부른 수 (캐시 제외) */
  naver_calls: number;
  naver_failed: string[];
  youtube_status: "known" | "unknown";
  youtube_reason: string | null;
  snapshot_at: string | null;
  /** 코드가 숫자로 만든 한 문장 */
  summary: string;
  /** 회사 상품 목록을 읽었는지 (예전 응답은 없다) */
  catalog_status?: "known" | "unknown";
  catalog_reason?: string | null;
  catalog_products?: number;
  /** 임베딩 유사도를 쟀는지. unknown이면 글자 일치만 썼다 */
  similarity_status?: "known" | "unknown";
  similarity_reason?: string | null;
  /** 유사도를 재지 못한 까닭: 설정 없음·설정 오류(키 없음 등)·요청 실패 (예전 응답은 없다) */
  similarity_failure?: "not_configured" | "config_error" | "request_failed" | null;
  /** "추천 안 함" 분류라 뺀 소재 (참고용, 순위 순). 예전 응답은 없다 */
  reference_items?: ReferenceTopic[];
  /** 이번에 쓴 "추천 안 함" 분류 (회사 설정) */
  excluded_categories?: string[];
}

// ---------------------------------------------------------------- 캠페인 효과 (campaign_effect)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py CampaignEffectOutput. 계산·이유·설명 문장은 모두 서버 값이다.
// 항목마다 "앎(값)" 또는 "모름 + 이유". 모르면 서버가 0을 채우지 않는다.
export type CampaignDirection = "up" | "none" | "down";
export interface CampaignChange {
  status: "known" | "unknown";
  direction?: CampaignDirection | null;
  /** 캠페인 기간 합계 */
  actual?: number | null;
  /** 기준선 = 지난 7일 같은 시간대 합계의 평균 */
  expected?: number | null;
  /** 증분 = 실제 − 기준선 */
  excess?: number | null;
  ratio?: number | null;
  p_value?: number | null;
  /** 기준선에 쓴 날 수 */
  baseline_days: number;
  unknown_reason?: string | null;
  /** 기준선 정의 문장 (예: "지난 7일 같은 시간대", "시작 전 14일 같은 요일·시간대"). 예전 응답은 없다 */
  baseline_rule?: string | null;
  /** 기준선에서 뺀 지난 구간과 이유 (빈 구간·기록 공백). 뺀 게 없으면 없다 */
  baseline_note?: string | null;
}
export interface CampaignCoupons {
  status: "known" | "unknown";
  coupon_count?: number | null;
  issued_units?: number | null;
  used_units?: number | null;
  use_rate?: number | null;
  /** 서버의 고정 설명 문장 */
  note?: string | null;
  unknown_reason?: string | null;
}
export interface CampaignEffectOutput {
  campaign_id: string | null;
  window_from: string;
  window_to: string;
  orders: CampaignChange;
  revenue: CampaignChange;
  coupons: CampaignCoupons;
  summary: string;
  sales?: CampaignSales | null;
  incremental_orders?: number | null; // 모르면 null. 0으로 채우지 않는다
  schema_version?: string;
  generated_at?: string | null;
}
/** 캠페인 판매액 (증분). 기준선이 모자라면 unknown + 이유 */
export interface CampaignSales {
  status: "known" | "unknown";
  unknown_reason?: string | null;
  baseline_days: number;
  incremental_gross_krw?: number | null;
  incremental_low_krw?: number | null;
  incremental_high_krw?: number | null;
  direction?: "up" | "down" | "uncertain" | null;
  direction_reason?: string | null;
}
export const CAMPAIGN_DIRECTION_KO: Record<CampaignDirection, string> = { up: "늘었음", none: "같음", down: "줄었음" };

// ---------------------------------------------------------------- 영상 성과 (video_performance, list_all)
// 계약: backend/shared/contracts/src/ax_contracts/schemas/data.py VideoPerformanceOutput (v3).
// 판정·신호 등급·성과 요인·개선 방향 문장·예상치는 모두 서버 값이다 (규칙 기반). 화면은 자릿수와 색만 정한다.
// 같은 형식 비교는 시간 맞춤(peer_age_ratio)만 쓴다. 시간 안 맞춘 peer_ratio는 받아도 보여 주지 않는다.
export type VideoVerdict = "achieved" | "missed" | "measuring" | "excluded";
export type SignalLevel = "strong" | "normal" | "weak" | "unknown";
export type VideoCheckpointName = "1h" | "1d" | "3d" | "7d";
export interface VideoCheckpoint {
  checkpoint: VideoCheckpointName;
  status: "known" | "unknown";
  /** 측정 중 · 놓침 · 연결 대기 등 */
  reason?: string | null;
  due_at?: string | null;
  hours_after?: number | null;
  views?: number | null;
  likes?: number | null;
  comments?: number | null;
  /** 7일에만 (목표 참고값이 정의된 시점) */
  target_ratio?: number | null;
  /** 같은 형식 숏츠의 게시 후 같은 시간대 중앙값 대비 (시간 맞춤) */
  peer_age_ratio?: number | null;
  baseline_n?: number | null;
  /** "기준 없음(표본 n)" */
  baseline_reason?: string | null;
}
export interface VideoSignal {
  value?: number | null;
  /** 화면 값 ("49%", "6.0%", "0.80%", "+64%") */
  display?: string | null;
  level: SignalLevel;
  reason?: string | null;
  baseline_n: number;
  /** v5: 같은 형식 기준 표본 5개 미만 = 기준 모으는 중 (reason "기준 모으는 중 (n/5)") */
  collecting?: boolean;
}
export interface VideoSignals { first: VideoSignal; like: VideoSignal; cmt: VideoSignal; grow: VideoSignal }
/** 같은 형식 비교 기준 모으는 중: 형식·시점마다 새 숏츠 추적 표본 수 (need개면 신호 등급이 켜짐) */
export interface BaselineProgress {
  shorts_format: string;
  checkpoint: VideoCheckpointName;
  n: number;
  need: number;
}
export interface VideoProjection {
  views?: number | null;
  ratio?: number | null;
  /** 늘 true (예상치) */
  is_estimate: boolean;
  reason?: string | null;
}
export interface VideoPerformanceVideo {
  video_id?: string | null;
  video_url?: string | null;
  title?: string | null;
  test: boolean;
  campaign_id?: string | null;
  /** v4: 프로모션 링크·쿠폰이 없으면 false + "측정 불가: 프로모션 링크/쿠폰 없음" */
  sales_measurable?: boolean | null;
  sales_reason?: string | null;
  insight_id?: string | null;
  topic?: string | null;
  shorts_format?: string | null;
  target_views_reference?: number | null;
  verdict?: VideoVerdict | null;
  verdict_reason?: string | null;
  checkpoints: VideoCheckpoint[];
  signals?: VideoSignals | null;
  /** 성과 요인 문장 (숫자 포함, 서버 문장) */
  factors: string[];
  /** 개선 방향 문장 (서버 문장) */
  actions: string[];
  projection?: VideoProjection | null;
}
export interface VideoChip {
  video_id: string;
  name: string;
  verdict: VideoVerdict;
  target_ratio?: number | null;
  projected_ratio?: number | null;
}
export interface VideoInsightCard {
  insight_id: string;
  topic?: string | null;
  finished: number;
  achieved: number;
  missed: number;
  measuring: number;
  /** "끝난 N개 중 M개 달성 · K개 측정 중" */
  line: string;
  chips: VideoChip[];
  /** 끝난 영상 3개 이상일 때만 */
  median_target_ratio?: number | null;
  notes: string[];
}
export interface VideoVerdictTally { videos: number; achieved: number; missed: number; measuring: number; excluded: number }
// ---------------------------------------------------------------- 성과 결과 1건 (campaign_result)
// 계약: data.py CampaignResultOutput. 판정·네 칸·알림·문장은 모두 서버 값이다.
// 네 칸(귀속·상품·쿠폰·전체 매장)은 겹치는 매출이라 화면에서도 더하지 않는다. 마이너스는 그대로 보여 준다.
export type CampaignSalesVerdictName = "more" | "less" | "no_effect";
export interface CampaignSalesVerdict {
  status: "known" | "unknown";
  verdict?: CampaignSalesVerdictName | null;
  /** "더 팔림" · "덜 팔림" · "효과 확인 안 됨" · "효과 확인 안 됨(표본 적음)" · "측정 불가: …" */
  label: string;
  low_sample: boolean;
  low_sample_reason?: string | null;
  reason?: string | null;
}
export interface CampaignSalesCell {
  key: "attribution" | "products" | "coupons" | "store";
  label: string;
  status: "known" | "unknown";
  reason?: string | null;
  /** 서버 한 줄 (숫자 그대로) */
  line?: string | null;
}
export interface CampaignSalesEffect {
  status: "known" | "unknown";
  reason?: string | null;
  verdict?: CampaignSalesVerdict | null;
  /** "+1,200,000원 · 주문 +40건 (… 대비)" */
  headline?: string | null;
  cells: CampaignSalesCell[];
  videos_total: number;
  videos_measurable: number;
  /** "영상 N개 중 매출 측정 가능 M개" */
  measurable_line: string;
  notes: string[];
}
export interface CampaignAlert { kind: "loss" | "data_gap"; message: string; note?: string | null }
/** 기획에 결과 보내기 상태 (campaign_result v2 send_status). 칩 글자·한 줄은 서버 문장 그대로. */
export type CampaignSendState = "sent" | "pending" | "not_sent" | "failed" | "off";
export interface CampaignSendStatus {
  state: CampaignSendState;
  /** 기획에 보냄 · 기획에 보낼 예정 · 보내지 않음 · 보내기 실패 · 다시 시도 중 (n/3) · 자동 보내기 꺼짐 */
  chip: string;
  /** PROP-3 · "늘었음" · 11월 12일 14:05 / 7일 측정이 끝나는 … 이후 자동으로 보냅니다 / 이유 */
  line: string;
  proposal_id?: string | null;
  direction?: string | null;
  failures?: number;
  retry_limit?: number;
  reason?: string | null;
}
export interface CampaignResultOutput {
  status: "known" | "unknown";
  reason?: string | null;
  campaign_id: string;
  campaign_name?: string | null;
  test: boolean;
  window_from?: string | null;
  window_to?: string | null;
  sales?: CampaignSalesEffect | null;
  alerts: CampaignAlert[];
  summary: string;
  schema_version?: string;
  /** v2부터. 예전 응답에는 없다 (그러면 줄을 그리지 않는다) */
  send_status?: CampaignSendStatus | null;
}
export type CampaignResultView = "planning" | "marketing" | "finance";

export interface VideoPerformanceListOutput {
  status: "known" | "unknown";
  reason?: string | null;
  videos: VideoPerformanceVideo[];
  tally?: VideoVerdictTally | null;
  insight_cards: VideoInsightCard[];
  limits: string[];
  summary: string;
  schema_version?: string;
  /** v5: 같은 형식 비교 기준 모으는 중 (대상 형식 × 시점). 모두 need개면 baseline_ready */
  baseline_progress?: BaselineProgress[];
  baseline_ready?: boolean;
}

/** 서버가 꺼졌을 때 무엇이 꺼졌는지 보여 주는 이름 */
export const DATA_SERVER = "데이터 AI 서버(8106)";
export const ORCH_SERVER = "총괄 서버(8000)";
/**
 * 데이터 AI를 부를지. 실제 토큰(로컬)이거나 공유 서버의 자리표시자(DATA_A2A_PLACEHOLDER)면 부른다.
 * 자리표시자도 본문 meta.token에 그대로 보내고, 실제 토큰은 nginx가 Authorization 헤더로 넣는다. 빈 값이면 부르지 않는다.
 */
export const kpiLive = DATA_A2A_TOKEN !== "" || DATA_A2A_TOKEN === DATA_A2A_PLACEHOLDER;

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
    // 주간·월간은 재무 마감을 날마다 물어 30초를 넘는다. 마감이 지나면 재무 호출이 취소되므로 이 skill만 2분
    askData<ActivityReportOutput>("activity_report", "업무 리포트", { period, ...(start ? { start } : {}), services }, REPORT_DEADLINE_MS),
  /** 키워드 그룹별 검색어 급상승. groups를 비우면 서버가 회사 기본 키워드(trends.toml)를 본다. 그룹마다 네이버 1번 */
  trendScan: (groups: string[] = []) =>
    askData<TrendScanOutput>(
      "trend_scan",
      "검색어 급상승 판정",
      groups.length ? { keyword_groups: groups.map((g) => ({ group_name: g, keywords: [g] })) } : {},
      TREND_DEADLINE_MS,
    ),
  /** YouTube 인기 태그 후보 + 후보마다 네이버 판정. 입력을 비우면 회사 설정(trends.toml [candidates])과 어제 날짜. 후보마다 네이버 1번 */
  trendCandidates: () => askData<TrendCandidatesOutput>("trend_candidates", "YouTube 후보 판정", {}, TREND_DEADLINE_MS),
  /** 키워드 그룹별 월별 흐름(작년 같은 달 대비). 입력을 비우면 회사 기본 키워드·설정 개월 수(24)·지난달까지. 그룹마다 네이버 1번 */
  trendHistory: () => askData<TrendHistoryOutput>("trend_history", "월별 흐름", {}, TREND_DEADLINE_MS),
  /** YouTube 영상 지금 속도 (판정 없음). 스냅샷 DB만 읽고 외부 호출이 없다. 입력을 비우면 회사 설정 개수(10) */
  youtubeVelocity: () => askData<YouTubeVelocityOutput>("youtube_velocity", "YouTube 지금 속도", {}),
  /** 지금 인기 주제 순위 (판정과 별개). 네이버를 부르지 않는다. 입력을 비우면 회사 설정 [popular] 개수 */
  trendPopular: () => askData<TrendPopularOutput>("trend_popular", "지금 인기", {}),
  /** 영상 소재 추천 (콘텐츠 루프 2단계). 입력을 비우면 회사 설정(trends.toml [recommend])과 어제 날짜.
   *  네이버는 서버 하루 캐시를 같이 쓰고, 소재마다 아이디어 문장을 LLM이 쓴다 */
  contentRecommend: () => askData<ContentRecommendOutput>("content_recommend", "영상 소재 추천", {}, RECOMMEND_DEADLINE_MS),
  /** 숏츠 통계 (카테고리·형식별). YouTube 검색으로 표본을 넓힌다(할당량, 서버 하루 캐시) */
  shortsStats: () => askData<ShortsStatsOutput>("shorts_stats", "숏츠 통계", {}, TREND_DEADLINE_MS),
  /** 영상 탐색 (카테고리 × 기간 × 길이). 서버가 미리 계산한 결과를 자르고 정렬만 한다(외부 호출 없음). 카테고리를 비우면 설정의 카테고리 전부 */
  videoExplorer: (q: { period: ExplorerPeriod; length: ExplorerLength; sort: ExplorerSort }) =>
    askData<VideoExplorerOutput>("video_explorer", "영상 탐색", q, TREND_DEADLINE_MS),
  /** 인사이트 브리프 (2단계). 서버 하루 캐시. 브리프 안에서 trend_age·임베딩·한 줄 문장(LLM)을 부른다 */
  trendInsightBrief: () => askData<TrendInsightBriefOutput>("trend_insight_brief", "인사이트 브리프", {}, RECOMMEND_DEADLINE_MS),
  /** 오늘 특정 판 (version 없으면 최신 판). 저장된 판을 읽기만 한다 */
  trendInsightBriefVersion: (version?: number) =>
    askData<TrendInsightBriefOutput>("trend_insight_brief", "인사이트 브리프", version ? { version } : {}, RECOMMEND_DEADLINE_MS),
  /** 다시 추천 시작: 서버가 막는 조건을 보고 바로 started/blocked (만들기는 서버 백그라운드). requested_by는 표시·기록용 */
  trendInsightBriefRegenerate: (requestedBy: string) =>
    askData<TrendInsightBriefOutput>("trend_insight_brief", "인사이트 다시 추천", { action: "regenerate_start", requested_by: requestedBy }),
  /** 다시 추천 진행 상태 (본문 없이 판 목록·만드는 중·작업 상태만). 화면이 2초마다 */
  trendInsightBriefStatus: () =>
    askData<TrendInsightBriefOutput>("trend_insight_brief", "인사이트 다시 추천 상태", { action: "status" }),
  /** 회사 도메인 프로필 (업종·상품군·고객 축·질문·씨앗 키워드). 서버가 저장해 두고 카탈로그가 바뀌면 다시 만든다 */
  domainProfile: () => askData<DomainProfileOutput>("domain_profile", "회사 도메인 프로필", {}, RECOMMEND_DEADLINE_MS),
  /** 데이터 출처 목록 (능력·키 상태·승인). 키 값은 응답에 없다 */
  dataSources: () => askData<DataSourcesOutput>("data_sources", "데이터 출처 목록", {}),
  /** 수집 계획 (출처 목록 안에서 고르고 코드가 검증) */
  sourcePlan: () => askData<SourcePlanOutput>("source_plan", "수집 계획", {}, RECOMMEND_DEADLINE_MS),
  /** 홍보 영상 기획안. 서버 하루 캐시. regenerate = true면 오늘 만든 기획안이 있어도 새로 만든다(LLM·네이버를 다시 부름) */
  promoConcepts: (regenerate = false) =>
    askData<PromoConceptsOutput>("promo_concepts", "홍보 영상 기획안", regenerate ? { regenerate: true } : {}, RECOMMEND_DEADLINE_MS),
  /** 캠페인 효과. from·to는 ISO 시각(UTC). campaignId가 없으면 쿠폰은 서버가 "확인할 수 없음"으로 답한다 */
  campaignEffect: (from: string, to: string, campaignId: string | null) =>
    askData<CampaignEffectOutput>(
      "campaign_effect",
      "캠페인 효과",
      { window_from: from, window_to: to, ...(campaignId ? { campaign_id: campaignId } : {}) },
      CAMPAIGN_DEADLINE_MS,
    ),
  /** 게시 영상 성과 전부 (성과 탭). 저장분만 읽고 새로 재지 않는다 (수집기가 1시간마다 잰다) */
  videoPerformance: () =>
    askData<VideoPerformanceListOutput>("video_performance", "영상 성과", { list_all: true }, TREND_DEADLINE_MS),
  /** 캠페인 성과 결과 1건. view = 부서별 묶음 (재무 = 연결·매출·알림). 매출은 캠페인 효과 계산 그대로 + 판정 */
  campaignResult: (campaignId: string, view: CampaignResultView = "finance") =>
    askData<CampaignResultOutput>("campaign_result", "캠페인 성과 결과", { campaign_id: campaignId, view }, CAMPAIGN_DEADLINE_MS),
};

const DEADLINE_MS = 30_000;
const REPORT_DEADLINE_MS = 120_000;
const TREND_DEADLINE_MS = 60_000; // 그룹마다 네이버 호출 + 급상승마다 설명 문장
const CAMPAIGN_DEADLINE_MS = 60_000; // 지난 7일 같은 시간대 지표 + 쿠폰 + 설명 문장
const RECOMMEND_DEADLINE_MS = 120_000; // 기본 그룹·YouTube 후보 네이버 판정(캐시가 없으면) + 소재마다 아이디어 문장

/** 목적격 조사: 마지막 글자에 받침이 있으면 "을", 없거나 한글이 아니면 "를" ("급상승 판정을", "캠페인 효과를") */
function objectParticle(word: string): string {
  const code = word.charCodeAt(word.length - 1);
  return code >= 0xac00 && code <= 0xd7a3 && (code - 0xac00) % 28 !== 0 ? "을" : "를";
}

// 같은 skill·입력의 요청이 아직 오는 중이면 새로 보내지 않고 그 응답을 같이 쓴다
// (개발 모드 StrictMode의 effect 두 번, 같은 응답을 쓰는 칸이 동시에 부를 때). 끝나면 지운다(캐시 아님)
const inFlight = new Map<string, Promise<A2AResult<unknown>>>();

function askData<T>(skill: string, what: string, input: Record<string, unknown> = {}, deadlineMs = DEADLINE_MS): Promise<A2AResult<T>> {
  const key = `${skill} ${JSON.stringify(input)}`;
  const pending = inFlight.get(key) as Promise<A2AResult<T>> | undefined;
  if (pending) return pending;
  const p = sendData<T>(skill, what, input, deadlineMs).finally(() => inFlight.delete(key));
  inFlight.set(key, p as Promise<A2AResult<unknown>>);
  return p;
}

async function sendData<T>(skill: string, what: string, input: Record<string, unknown>, deadlineMs: number): Promise<A2AResult<T>> {
  const body = {
    meta: { context_id: "ctx_req_dashboard", token: DATA_A2A_TOKEN, path: [], deadline: new Date(Date.now() + deadlineMs).toISOString() },
    skill,
    input,
  };
  const res = await fetch(`${DATA_A2A_URL}/a2a`, { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const json = (await res.json().catch(() => null)) as A2AResult<T> | null;
  if (!res.ok || !json) throw new ApiError(`${DATA_SERVER}에서 ${what}${objectParticle(what)} 받지 못했습니다 (${res.status})`, res.status);
  if (json.status !== "completed") {
    // 서버가 이유를 주면 같이 보여 준다 (예: 네이버를 읽지 못함, 볼 키워드 없음)
    const message = (json.output as { message?: unknown } | null)?.message;
    const reason = typeof message === "string" && message ? ` · ${message}` : "";
    throw new ApiError(`데이터 AI가 요청을 처리하지 않았습니다 (${json.outcome ?? json.status})${reason}`, res.status);
  }
  return json;
}
