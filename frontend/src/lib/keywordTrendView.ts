// 감지 → 검색 "급상승 검색어" 패널(components/data/KeywordTrend.tsx)의 순수 계산. node --test (keywordTrendView.test.ts).
// 숫자·판정은 서버 keyword_trend 값 그대로. 예전 응답(level 없음)은 status로 칸을 정한다 (warming은 예전 응답에 없음)
import type { KeywordCandidateSource, KeywordTrendOutput, RisingKeyword, RisingLevel } from "./data.ts";
import { formFromBrief, type RequestForm } from "./trendReportView.ts";

/** 줄 하나의 칸: level이 있으면 그것, 없으면(예전 응답) status rising·normal·unknown */
export function levelOf(r: Pick<RisingKeyword, "level" | "status">): RisingLevel {
  if (r.level) return r.level;
  return r.status === "rising" ? "rising" : r.status === "normal" ? "normal" : "unknown";
}

export interface RisingSplit {
  rising: RisingKeyword[];
  warming: RisingKeyword[];
  /** 평범·판정 못 함 (접힘 표) */
  rest: RisingKeyword[];
  /** 전부 unknown(판정 하나도 없음) → 구름·순위 대신 "오늘 판정 못 함" + 월 검색 큰 순 표 */
  noJudgment: boolean;
}

const volume = (r: RisingKeyword) => r.monthly_volume ?? -1;

/** 칸 나누기 (서버 순서 유지). 판정이 하나도 없으면 rest를 월 검색 큰 순으로 */
export function splitRising(rows: RisingKeyword[]): RisingSplit {
  const rising = rows.filter((r) => levelOf(r) === "rising");
  const warming = rows.filter((r) => levelOf(r) === "warming");
  const rest = rows.filter((r) => { const l = levelOf(r); return l === "normal" || l === "unknown"; });
  const noJudgment = rows.length > 0 && rows.every((r) => levelOf(r) === "unknown");
  return { rising, warming, rest: noJudgment ? [...rest].sort((a, b) => volume(b) - volume(a)) : rest, noJudgment };
}

/** 고른 단어의 줄을 맨 앞으로 (나머지 순서는 그대로) */
export function pickFirst<T extends { keyword: string }>(list: T[], keyword: string | null): T[] {
  if (!keyword) return list;
  const hit = list.find((r) => r.keyword === keyword);
  return hit ? [hit, ...list.filter((r) => r !== hit)] : list;
}

// ---- 후보 출처

const SOURCE_ORDER: KeywordCandidateSource[] = ["new", "related_up", "youtube", "google", "company", "pool"];
/** 패널 머리 출처 칩 이름 */
export const CANDIDATE_LABEL: Record<KeywordCandidateSource, string> = {
  new: "새로 나타난 말", related_up: "연관어 검색량 증가", youtube: "YouTube 인기", google: "Google 인기", company: "회사 기본 키워드", pool: "검색량 상위",
};
/** 카드·표의 짧은 출처 칩 이름 */
export const CANDIDATE_SHORT: Record<KeywordCandidateSource, string> = {
  new: "새로 나타남", related_up: "연관어 증가", youtube: "YouTube 인기", google: "Google 인기", company: "회사 키워드", pool: "검색량 상위",
};

/** candidate_counts → 칩 (정해진 순서, 0·모르는 출처는 숨김) */
export function candidateChips(counts: Record<string, number> | null | undefined): { key: KeywordCandidateSource; label: string; count: number }[] {
  if (!counts) return [];
  return SOURCE_ORDER.filter((k) => (counts[k] ?? 0) > 0).map((k) => ({ key: k, label: CANDIDATE_LABEL[k], count: counts[k] }));
}

export function sourceShort(sources: KeywordCandidateSource[] | null | undefined): string[] {
  return (sources ?? []).filter((s) => s in CANDIDATE_SHORT).map((s) => CANDIDATE_SHORT[s]);
}

// ---- 머리 줄 · 요약

const KST = 9 * 3600_000;
function kst(iso: string | null | undefined): { md: string; hm: string } | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  const d = new Date(t + KST);
  const p = (n: number) => String(n).padStart(2, "0");
  return { md: `${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`, hm: `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}` };
}

/** "11-19 07:10 판정 · 데이터랩 6번". 판정한 날(judged_on)이 계산 시각과 다르면(직전 판정 재사용) 날짜만 */
export function judgedLine(out: Pick<KeywordTrendOutput, "judged_on" | "generated_at" | "naver_calls">): string | null {
  const at = kst(out.generated_at);
  const day = out.judged_on ? out.judged_on.slice(5) : at?.md ?? null;
  if (!day) return null;
  const time = at && at.md === day ? ` ${at.hm}` : "";
  return `${day}${time} 판정 · 데이터랩 ${out.naver_calls.toLocaleString("ko-KR")}번`;
}

/** 종합 칩 "검색 급상승 1위": level rising 첫 줄. 직전 판정 재사용이면 "(MM-DD 판정)" */
export function topRising(out: Pick<KeywordTrendOutput, "rising" | "stale_reason" | "judged_on">): { row: RisingKeyword; stale: string | null } | null {
  const row = out.rising.find((r) => levelOf(r) === "rising");
  if (!row) return null;
  return { row, stale: out.stale_reason && out.judged_on ? `(${out.judged_on.slice(5)} 판정)` : null };
}

export const RISING_BASIS =
  "어제 값 ÷ 그 전 56일 중앙값(네이버 데이터랩 상대값) · 2배 이상 급상승 · 1.5배 이상 오르는 중 · 하루 한 번 판정, 화면은 저장한 결과를 읽어요";

// ---- 작은 그래프

/** points → SVG polyline 점과 평소(점선) y. 값이 2개 미만이면 null */
export function sparkline(points: number[] | null | undefined, baseline: number | null | undefined, w: number, h: number):
  { line: string; area: string; baseY: number | null } | null {
  const vals = (points ?? []).filter((v) => Number.isFinite(v));
  if (vals.length < 2) return null;
  const all = typeof baseline === "number" && Number.isFinite(baseline) ? [...vals, baseline] : vals;
  const lo = Math.min(0, ...all);
  const hi = Math.max(...all);
  const span = hi - lo || 1;
  const pad = 2;
  const y = (v: number) => +(h - pad - ((v - lo) / span) * (h - pad * 2)).toFixed(1);
  const x = (i: number) => +((i / (vals.length - 1)) * w).toFixed(1);
  const pts = vals.map((v, i) => `${x(i)},${y(v)}`);
  return {
    line: pts.join(" "),
    area: `M0 ${h} L${pts.join(" L")} L${w} ${h}Z`,
    baseY: typeof baseline === "number" && Number.isFinite(baseline) ? y(baseline) : null,
  };
}

// ---- 분석 의뢰로 보내기

/** "이 키워드로 분석 의뢰": 분석 의뢰 탭 "직접 정하기"에 키워드만 넣은 폼 (보고서 손보기의 formFromBrief 재사용) */
export function requestFormForKeyword(keyword: string): RequestForm {
  return formFromBrief({
    product: null, target_gender: null, target_ages: [], format: null, competitors: [], keywords: [keyword], preset: null,
    sources: { product: "input", target: "input", format: "input", competitors: "input", keywords: "input" },
  });
}
