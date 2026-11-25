// node --test로 돈다 (npm test). 보고서 "의뢰 주제로 찾은 근거" (11/26): 숨김 조건 · 참고 행 뒤로 + 칩 · 뺀 근거 제목.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { TrendReport, TrendTopicEvidenceRow } from "./data.ts";
import { EXCLUDED_REASON, topicView } from "./trendTopicView.ts";
import { cardInput } from "./chatCards.ts";

type Part = Pick<TrendReport, "brief" | "related_terms" | "topic_evidence" | "excluded">;
const brief = (theme: string | null) => ({ theme } as unknown as TrendReport["brief"]);
const row = (label: string, over: Partial<TrendTopicEvidenceRow> = {}): TrendTopicEvidenceRow => ({
  kind: "scan", label, value: "평소의 3.2배", source: "감지 탭", evidence_id: "scan:1", reference: false, peak_end: null, ...over,
});

test("숨김: 주제 없음 · 예전 저장본(칸 없음) · 모두 빈 목록", () => {
  assert.equal(topicView({ brief: brief(null), related_terms: ["x"], topic_evidence: [], excluded: [] } as Part), null);
  assert.equal(topicView({ brief: brief("시험 기간") } as Part), null);
  assert.equal(topicView({ brief: brief("시험 기간"), related_terms: [], topic_evidence: [], excluded: [] } as Part), null);
});

test("근거 줄: 결론 근거 먼저, 끝난 급상승 참고 행은 뒤로 + 칩", () => {
  const v = topicView({
    brief: brief("시험 기간"), related_terms: ["기말고사", "벼락치기"],
    topic_evidence: [row("수능시간표 검색", { reference: true, evidence_id: null, value: "110.8배", peak_end: "2026-11-19" }), row("기말고사 검색")],
    excluded: [],
  } as Part)!;
  assert.deepEqual(v.terms, ["기말고사", "벼락치기"]);
  assert.deepEqual(v.rows.map((r) => r.row.label), ["기말고사 검색", "수능시간표 검색"]);
  assert.equal(v.rows[1].chip, "지난 급상승 · 11/19 정점");
  assert.equal(v.rows[0].chip, null);
  assert.equal(v.excludedTitle, null);
});

test("뺀 근거: 개수와 이유 종류 (같은 이유는 한 번)", () => {
  const v = topicView({
    brief: brief("시험 기간"), related_terms: ["기말고사"], topic_evidence: [],
    excluded: [
      { label: "소설 검색", value: "+275%", reason: "off_topic", evidence_id: "search:1" },
      { label: "다꾸", value: "+14%", reason: "off_topic", evidence_id: "search:3" },
      { label: "현대카드 게시물", value: "197배", reason: "notice_ad", evidence_id: "content:1" },
    ],
  } as Part)!;
  assert.equal(v.excludedTitle, "뺀 근거 3개 (주제와 상관없음 · 공지·광고)");
  assert.equal(EXCLUDED_REASON.faded, "지난 급상승(정점이 지나고 최근값은 평소 수준)");
});

test("대화 카드 접수: 주제가 있으면 확장어도 넘긴다 (예전 카드는 칸 없음)", () => {
  const card = {
    kind: "trend_request", request_text: "시험 기간", preset: null, cost_note: "", brief_reason: null,
    brief: { product: null, target_gender: null, target_ages: [], format: null, competitors: [], keywords: [], competitor_names: {},
      sources: { product: "request_text", target: "request_text", format: "request_text", competitors: "request_text", keywords: "request_text" },
      theme: "시험 기간", theme_mode: "use", related_terms: ["기말고사", "벼락치기"] },
  } as Parameters<typeof cardInput>[0];
  assert.deepEqual(cardInput(card, null).related_terms, ["기말고사", "벼락치기"]);
  const old = { ...card, brief: { ...card.brief, related_terms: undefined } };
  assert.equal("related_terms" in cardInput(old, null), false);
});
