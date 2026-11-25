// 보고서 "의뢰 주제로 찾은 근거" 칸의 순수 계산 (components/data/TrendTopicEvidence.tsx). node --test (trendTopicView.test.ts).
// 값·이유는 서버 그대로. 주제가 없는 의뢰·예전 저장본(칸 없음)은 칸을 숨긴다
import type { ExcludedReason, TrendExcludedEvidence, TrendReport, TrendTopicEvidenceRow } from "./data.ts";
import { fadedChip } from "./keywordTrendView.ts";

export const EXCLUDED_REASON: Record<ExcludedReason, string> = {
  off_topic: "의뢰 주제와 연결 낱말 없음",
  notice_ad: "브랜드 공지·광고 게시물",
  faded: "지난 급상승(정점이 지나고 최근값은 평소 수준)",
};
const REASON_SHORT: Record<ExcludedReason, string> = { off_topic: "주제와 상관없음", notice_ad: "공지·광고", faded: "지난 급상승" };

export interface TopicRow { row: TrendTopicEvidenceRow; chip: string | null }
export interface TopicView {
  /** 주제 없는 의뢰면 null (그때는 뺀 근거(공지·광고)만 보인다) */
  theme: string | null;
  terms: string[];
  rows: TopicRow[];
  excluded: TrendExcludedEvidence[];
  /** "뺀 근거 5개 (주제와 상관없음 · 공지·광고)" — 없으면 null */
  excludedTitle: string | null;
}

/** 주제가 있고 확장어·근거·뺀 근거 중 하나라도 있으면 칸을 보인다. 주제가 없어도 뺀 근거(공지·광고)가 있으면 그것만 */
export function topicView(report: Pick<TrendReport, "brief" | "related_terms" | "topic_evidence" | "excluded">): TopicView | null {
  const theme = report.brief.theme?.trim() || null;
  const terms = theme ? report.related_terms ?? [] : [];
  const evidence = theme ? report.topic_evidence ?? [] : [];
  const excluded = report.excluded ?? [];
  if (!terms.length && !evidence.length && !excluded.length) return null;
  // 결론 근거로 쓴 줄 먼저, 참고 행(끝난 급상승)은 뒤로 (서버 순서는 그대로)
  const rows = [...evidence.filter((r) => !r.reference), ...evidence.filter((r) => r.reference)]
    .map((row) => ({ row, chip: row.reference ? fadedChip({ faded: true, peak_end: row.peak_end }) : null }));
  const reasons = [...new Set(excluded.map((e) => REASON_SHORT[e.reason] ?? e.reason))];
  return {
    theme, terms, rows, excluded,
    excludedTitle: excluded.length ? `뺀 근거 ${excluded.length}개 (${reasons.join(" · ")})` : null,
  };
}
