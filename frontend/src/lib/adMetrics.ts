// 캠페인별 광고 숫자(사람이 보고서를 보고 적는 값)와 추적 링크의 순수 함수.
// 설계: docs/marketing-ad-metrics-design.md C. 계약: backend ax_contracts/schemas/ad_metrics.py
// - 빈칸 = 모름(null), 0 = 0. 둘을 섞지 않는다
// - 클릭률 같은 비율은 만들지 않는다(데이터분석이 계산한다)
// - source·medium 조합표는 서버 응답(`combos`)에서 받는다. 여기에 따로 복사해 두지 않는다
// 서버 호출은 adMetricsApi.ts(이 파일은 node --test로 돈다).

export interface AdMetricsRow {
  campaign_id: string;
  date: string;
  source: string;
  medium: string;
  impressions: number | null;
  clicks: number | null;
  spend_krw: number | null;
  report_ref: string | null;
  entered_by: string;
  entered_at: string;
  updated_at: string;
  revisions: number;
}

export interface AdMetricsTotals {
  impressions: number | null;
  clicks: number | null;
  spend_krw: number | null;
  missing_dates: Record<string, string[]>;
}

export interface AdMetricsView {
  status: "ok" | "unknown";
  reason: string | null;
  rows: AdMetricsRow[];
  totals: AdMetricsTotals | null;
  combos: Record<string, string[]>;
  default_base_url: string | null;
}

export type AdMetricsRejectReason =
  | "unknown_campaign" | "invalid_date" | "future_date" | "invalid_source_medium" | "clicks_over_impressions" | "storage_error";

export interface RecordAdMetricsAnswer {
  outcome: "recorded" | "updated" | "unchanged" | "rejected";
  reason_code: AdMetricsRejectReason | null;
  row: AdMetricsRow | null;
}

export interface CampaignLink { url: string; utm_campaign: string; utm_source: string; utm_medium: string }

export interface AdMetricsBody {
  date: string;
  source: string;
  medium: string;
  impressions: number | null;
  clicks: number | null;
  spend_krw: number | null;
  report_ref: string | null;
}

/** 입력 칸 글자 그대로(숫자만 남긴 값). 빈 글자는 모름 */
export interface AdMetricsForm {
  date: string;
  source: string;
  medium: string;
  impressions: string;
  clicks: string;
  spend: string;
  reportRef: string;
}

export const SOURCE_KO: Record<string, string> = { banner: "쇼핑몰 배너", notice: "쇼핑몰 공지", instagram: "인스타그램", google: "구글", youtube: "유튜브" };
export const MEDIUM_KO: Record<string, string> = { onsite: "쇼핑몰 안", social: "일반 게시", cpc: "클릭당 광고" };
export const sourceKo = (s: string) => SOURCE_KO[s] ?? s;
export const mediumKo = (m: string) => MEDIUM_KO[m] ?? m;

const REJECT_KO: Record<AdMetricsRejectReason, string> = {
  unknown_campaign: "받은 기획안에 없는 캠페인이라 적지 않았습니다.",
  invalid_date: "날짜가 올바르지 않습니다.",
  future_date: "오늘(한국 날짜)보다 뒤의 날짜는 적을 수 없습니다.",
  invalid_source_medium: "이 source와 medium은 함께 쓸 수 없습니다.",
  clicks_over_impressions: "클릭이 노출보다 많을 수 없습니다.",
  storage_error: "마케팅 저장소에 쓰지 못했습니다. 잠시 뒤 다시 저장하세요.",
};
export const rejectKo = (code: AdMetricsRejectReason | null) => (code ? REJECT_KO[code] ?? "적지 못했습니다." : "적지 못했습니다.");

const OUTCOME_KO: Record<Exclude<RecordAdMetricsAnswer["outcome"], "rejected">, string> = {
  recorded: "새 줄을 저장했습니다.",
  updated: "같은 날짜·source·medium 줄을 고쳐 썼습니다.",
  unchanged: "같은 값이 이미 저장돼 있어 바꾸지 않았습니다.",
};
export const recordOutcomeKo = (a: RecordAdMetricsAnswer) => (a.outcome === "rejected" ? rejectKo(a.reason_code) : OUTCOME_KO[a.outcome]);

/** 숫자만 남긴다(붙여 넣은 쉼표·"원" 등 제거) */
export const digitsOnly = (s: string) => s.replace(/[^0-9]/g, "");

/** 입력 칸 → 값. 빈칸은 모름(null), "0"은 0 */
export function parseCount(s: string): number | null {
  const d = digitsOnly(s);
  return d === "" ? null : Number(d);
}

/** source에 맞는 medium(서버 조합표 기준). 모르는 source면 빈 목록 */
export const mediumsFor = (combos: Record<string, string[]>, source: string): string[] => combos[source] ?? [];

/** 쇼핑몰 안(onsite) 줄은 광고비 기본값을 0으로 채운다(사람이 바꿀 수 있다). 그 밖은 빈칸(모름) */
export const defaultSpendFor = (medium: string) => (medium === "onsite" ? "0" : "");

/** source를 바꿀 때의 새 입력. medium이 조합표에 하나뿐이면 그것을 고르고, 광고비 기본값을 맞춘다 */
export function withSource(form: AdMetricsForm, combos: Record<string, string[]>, source: string): AdMetricsForm {
  const mediums = mediumsFor(combos, source);
  const medium = mediums.includes(form.medium) ? form.medium : mediums.length === 1 ? mediums[0] : "";
  return withMedium({ ...form, source }, medium);
}

/** medium을 바꿀 때의 새 입력. 광고비 칸이 비었거나 이전 기본값 그대로면 새 기본값으로 바꾼다 */
export function withMedium(form: AdMetricsForm, medium: string): AdMetricsForm {
  const untouched = form.spend === "" || form.spend === defaultSpendFor(form.medium);
  return { ...form, medium, spend: untouched ? defaultSpendFor(medium) : form.spend };
}

/** 보낼 수 없는 이유(한국어). 없으면 null. 최종 판단은 서버가 한다 */
export function formProblem(form: AdMetricsForm, combos: Record<string, string[]>, today: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.date)) return "날짜를 고르세요.";
  if (form.date > today) return "오늘(한국 날짜)보다 뒤의 날짜는 적을 수 없습니다.";
  if (!form.source) return "source를 고르세요.";
  if (!mediumsFor(combos, form.source).includes(form.medium)) return "medium을 고르세요.";
  const imp = parseCount(form.impressions);
  const clk = parseCount(form.clicks);
  if (imp !== null && clk !== null && clk > imp) return "클릭이 노출보다 많을 수 없습니다.";
  return null;
}

export function toBody(form: AdMetricsForm): AdMetricsBody {
  const ref = form.reportRef.trim();
  return {
    date: form.date,
    source: form.source,
    medium: form.medium,
    impressions: parseCount(form.impressions),
    clicks: parseCount(form.clicks),
    spend_krw: parseCount(form.spend),
    report_ref: ref === "" ? null : ref,
  };
}

/** 저장된 값 보이기: 모름은 "–", 0은 "0" */
export const countText = (v: number | null) => (v === null ? "–" : v.toLocaleString("ko-KR"));
/** 저장된 광고비 보이기: 모름은 "–", 0은 "0원" */
export const krwText = (v: number | null) => (v === null ? "–" : `${v.toLocaleString("ko-KR")}원`);

/** 합계 칸 이름 → 모르는 날짜 안내. 모르는 칸이 없으면 빈 목록 */
export function missingNotes(totals: AdMetricsTotals | null): string[] {
  if (!totals) return [];
  const names: Record<string, string> = { impressions: "노출", clicks: "클릭", spend_krw: "광고비" };
  return Object.entries(totals.missing_dates)
    .filter(([, dates]) => dates.length > 0)
    .map(([key, dates]) => `${names[key] ?? key}: ${[...new Set(dates)].sort().join(", ")} 모름`);
}

export interface PeriodSpend {
  /** 기간 안 줄의 광고비 합계. 줄이 없거나 모르는 줄이 섞이면 null */
  total: number | null;
  rows: number;
  missingDates: string[];
}

/** 결과 저장 기간(양 끝 포함) 안 줄의 광고비 합계. 모르는 줄이 하나라도 있으면 합계는 null */
export function periodSpend(rows: AdMetricsRow[], from: string, to: string): PeriodSpend {
  const inside = rows.filter((r) => r.date >= from && r.date <= to);
  const missing = [...new Set(inside.filter((r) => r.spend_krw === null).map((r) => r.date))].sort();
  if (inside.length === 0 || missing.length > 0) return { total: null, rows: inside.length, missingDates: missing };
  return { total: inside.reduce((s, r) => s + (r.spend_krw ?? 0), 0), rows: inside.length, missingDates: [] };
}
