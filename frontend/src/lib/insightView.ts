// 트렌드&콘텐츠 인사이트 탭 보고서 카드 = "기획에 넘긴 것" (11/26 시안). 순수 계산만. node --test (insightView.test.ts).
// 화면: components/data/ReportInsights.tsx. 값은 카드가 이미 받는 trend_report_insight 출력·기획 기록만 쓴다(새 API 없음).
// - 방향 칩: facts 중 label "방향"의 value를 " · "로 나눈 것 (근거 목록에서는 뺀다)
// - 주제 후보: label이 "주제 '"로 시작하는 근거의 후보 이름 (최대 3)
// - 출처 글자: 데이터 AI가 낸 값(source trend_report_create)은 label 앞머리로 섹션 이름 + " · TR-xxxx"
// - 약한 근거: 변화율이 음수(줄어드는 중) · 해시태그 N곳에서 N < 5(표본 작음). 데이터 AI pick_facts(is_weak)와 같은 기준, 화면은 표시·순서만
import { sourceLabel } from "./planningLabels.ts";
import type { Proposal } from "./proposals.ts";
import { reportLabel } from "./trendReportView.ts";

export interface FactLike { label: string; value: string; source: string; link?: string | null }

export const DIRECTION_LABEL = "방향";
/** 데이터 AI 보고서 근거의 source (계약 주석: "값을 낸 skill") */
export const REPORT_SOURCE = "trend_report_create";
/** 해시태그를 이만큼 미만 계정이 쓰면 작은 표본 (데이터 AI HASHTAG_MIN_ACCOUNTS와 같은 값) */
export const HASHTAG_MIN_ACCOUNTS = 5;
export const FIRST_ROWS = 3;
export const THEME_MAX = 3;

/** 방향 칩: "재미 먼저 · 브랜드는 끝 카드에" → ["재미 먼저", "브랜드는 끝 카드에"] */
export function directionChips(facts: readonly FactLike[]): string[] {
  return facts.filter((f) => f.label === DIRECTION_LABEL)
    .flatMap((f) => f.value.split(" · ").map((x) => x.trim()).filter(Boolean));
}

/** 근거 목록 (방향 줄 뺌) */
export const evidenceFacts = <F extends FactLike>(facts: readonly F[]): F[] => facts.filter((f) => f.label !== DIRECTION_LABEL);

/** 주제 후보 이름: "주제 '공연' 후보 뮤지컬 (20대 여성) 네이버 검색 관심" → "뮤지컬". 최대 3, 같은 이름은 한 번 */
export function themeCandidates(facts: readonly FactLike[]): string[] {
  const names = facts.filter((f) => f.label.startsWith("주제 '"))
    .map((f) => /^주제 '[^']*' 후보 (.+?)(?: \(|$)/.exec(f.label)?.[1]?.trim())
    .filter((x): x is string => !!x);
  return [...new Set(names)].slice(0, THEME_MAX);
}

/** 근거 label 앞머리 → 보고서 섹션 이름 (데이터 AI row_facts의 label 규칙) */
export function reportSection(label: string): string | null {
  if (label.startsWith("주제 '")) return "주제 후보";
  if (label.startsWith("네이버 검색")) return "네이버 검색";
  if (label.startsWith("네이버 쇼핑")) return "네이버 쇼핑";
  if (label.startsWith("인스타 #") || label.startsWith("인스타 ")) return "인스타 해시태그";
  if (label.startsWith("경쟁사 ")) return "경쟁사";
  if (/^(릴스|영상|유튜브 쇼츠|쇼츠 ').*조회/.test(label) || / 터짐$/.test(label) || label.startsWith("같이 뜬 밈")) return "콘텐츠";
  return null;
}

/** 근거 표 출처 글자. 보고서 근거면 "네이버 검색 · TR-BB81"(섹션을 못 고르면 "트렌드 보고서 · TR-BB81"), 그 밖은 지금 출처 이름 */
export function insightSourceLabel(fact: Pick<FactLike, "label" | "source">, reportId: string): string {
  if (fact.source !== REPORT_SOURCE) return sourceLabel(fact.source);
  return `${reportSection(fact.label) ?? "트렌드 보고서"} · ${reportLabel(reportId)}`;
}

export interface FactFlags { falling: boolean; smallSample: boolean }
/** 값 표시: "-54.9%"·"37/100 · 최근 7일 -3.0%" → 줄어드는 중, "2곳 (직전 7일 1곳)" → 표본 작음 */
export function factFlags(value: string): FactFlags {
  const v = value.trim();
  const falling = /^-\d[\d.,]*%$/.test(v) || /최근 7일 -\d/.test(v);
  const m = /^(\d+)곳 \(/.exec(v);
  return { falling, smallSample: !!m && Number(m[1]) < HASHTAG_MIN_ACCOUNTS };
}
export const isWeakFact = (f: Pick<FactLike, "value">) => { const x = factFlags(f.value); return x.falling || x.smallSample; };

/** 약하지 않은 근거 먼저 (같은 무리 안 순서 그대로). 데이터 AI가 고치기 전 보고서도 화면에서 같은 순서 */
export function orderFacts<F extends FactLike>(facts: readonly F[]): F[] {
  return [...facts.filter((f) => !isWeakFact(f)), ...facts.filter(isWeakFact)];
}

/** 처음 3줄이 모두 약하면 카드 위 경고 */
export function weakWarning(ordered: readonly FactLike[]): string | null {
  const first = ordered.slice(0, FIRST_ROWS);
  return first.length > 0 && first.every(isWeakFact) ? "기획이 기댈 근거가 약해요" : null;
}

/** 카드 머리: "TR-BB81 → 기획 PROP-31" / "TR-BB81 → 아직 기획에 안 감" */
export const cardHead = (reportId: string, proposal: Pick<Proposal, "id"> | null) =>
  `${reportLabel(reportId)} → ${proposal ? `기획 ${proposal.id}` : "아직 기획에 안 감"}`;

/** 보고서 상태 칩 */
export const reportStatusLabel = (status: string | null | undefined): string | null =>
  status === "done" ? "완료" : status === "partial" ? "일부 완료" : null;

export const WRITTEN_BY: Record<Proposal["writtenBy"], string> = { llm: "AI 제작안", rule: "규칙 초안", human: "사람이 씀" };

/** 기획 결과 한 줄: "AI 제작안 · {제목}" + 지금 상태 */
export function planResult(p: Pick<Proposal, "writtenBy" | "title" | "status"> | null): { who: string; title: string; status: string; tone: "ok" | "warn" | "bad" } | null {
  if (!p) return null;
  const tone = p.status === "폐기" ? "bad" : p.status === "확인 대기" ? "warn" : "ok";
  return { who: WRITTEN_BY[p.writtenBy] ?? p.writtenBy, title: p.title, status: p.status, tone };
}
