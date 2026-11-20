// node --test로 돈다 (npm test). 기획 전달 탭 사슬·요약 숫자·출처 칩, 보고서 인사이트 카드의 전달 상태·막힘, 인사이트 필터, 인쇄 머리글.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { TrendReportListItem } from "./data.ts";
import {
  HANDOFF_GATED_TEXT, activeCount, chainOf, dataProposals, deliveryOf, handoffChip, handoffGated, handoffSummary, insightFilterFromHash,
  insightFilterHash, insightReports, proposalFor, reportIdOfInsight, reportInsightId, sourceChip,
} from "./handoffView.ts";
import type { Proposal } from "./proposals.ts";
import { printHeaderLine } from "./trendReportView.ts";

const prop = (id: string, over: Partial<Proposal> = {}): Proposal => ({
  id, title: `제목 ${id}`, from: "data", skill: "trend_insight_brief", insight: "x", facts: [], products: [], idea: "", why: "",
  writtenBy: "rule", warnings: [], status: "확인 대기", created: "11-19 11:12", options: [], ...over,
});

const SAMPLE: Proposal[] = [
  prop("PROP-31", { insightId: "trend_report:tr_4913aa", skill: "trend_report_insight", status: "기획안", created: "11-19 11:12" }),
  prop("PROP-29", { status: "마케팅 전달", spec: "SPEC-3", handoff: { ok: true, campaign: "CMP-12" }, created: "11-18 09:00" }),
  prop("PROP-22", { skill: "promo_concepts", status: "성과 확인", handoff: { ok: true, campaign: "CMP-9" }, created: "11-12 10:00",
    result: { direction: "늘었음", summary: "", at: "" } }),
  prop("PROP-25", { status: "폐기", note: "작년과 같은 안", created: "11-15 08:00" }),
  prop("PROP-40", { from: "marketing", created: "11-20 08:00" }),
];

test("데이터에서 온 제안만 최근 순, 요약 숫자 4개, 탭 배지 = 진행 중", () => {
  const list = dataProposals(SAMPLE);
  assert.deepEqual(list.map((p) => p.id), ["PROP-31", "PROP-29", "PROP-25", "PROP-22"]);
  assert.deepEqual(handoffSummary(list), { received: 4, active: 2, marketing: 2, measured: 1 });
  assert.equal(activeCount(list), 2);
  assert.equal(activeCount([]), 0);
});

test("사슬: 지금 칸 = 1 + proposalStage, 지나간 칸 done, 폐기는 PROP 칸에서 멈추고 이유", () => {
  const states = (p: Proposal) => chainOf(p).cells.map((c) => c.state);
  const [p31, p29, p22, p25] = ["PROP-31", "PROP-29", "PROP-22", "PROP-25"].map((id) => SAMPLE.find((p) => p.id === id)!);
  // 기획안이지만 기획서(spec) 전 → proposalStage 0 → PROP 칸
  assert.deepEqual(states(p31), ["done", "current", "future", "future", "future"]);
  assert.deepEqual(states({ ...p31, spec: "SPEC-1" }), ["done", "done", "current", "future", "future"]);
  assert.deepEqual(states(p29), ["done", "done", "done", "current", "future"]);
  assert.equal(chainOf(p29).cells[3].label, "마케팅 CMP-12");
  assert.equal(chainOf(p29).cells[2].label, "확정");
  assert.deepEqual(states(p22), ["done", "done", "done", "done", "current"]);
  assert.equal(chainOf(p22).cells[4].label, "성과 · 늘었음");
  const rej = chainOf(p25);
  assert.equal(rej.rejected, true);
  assert.equal(rej.note, "작년과 같은 안");
  assert.equal(rej.cells.length, 2);
  assert.equal(chainOf(prop("PROP-1")).cells[1].label, "기획 PROP-1 · 확인 대기");
  assert.equal(chainOf(prop("PROP-2", { status: "기획서로", request: "REQ-4" })).cells[3].label, "개발 요청 REQ-4");
  assert.equal(chainOf(prop("PROP-3", { status: "마케팅 전달", handoff: { ok: false, reason: "응답 없음" } })).warn, "응답 없음");
});

test("출처 칩: 보고서 인사이트면 '분석 의뢰 · TR-xxxx', 아니면 skill 이름", () => {
  assert.equal(sourceChip({ skill: "trend_report_insight", insightId: "trend_report:tr_4913aa" }), "분석 의뢰 · TR-4913");
  assert.equal(sourceChip({ skill: "promo_concepts" }), "숏츠 아이디어");
  assert.equal(sourceChip({ skill: "새skill" }), "새skill");
  assert.equal(reportInsightId("tr_ab"), "trend_report:tr_ab");
  assert.equal(reportIdOfInsight("trend_report:tr_ab"), "tr_ab");
  assert.equal(reportIdOfInsight("ins_1"), null);
});

test("보고서 인사이트 카드: 완료·부분 + has_insight가 false 아님, 전달 상태는 기획 기록 → 보낸 결과 → 아직 안 감", () => {
  const item = (report_id: string, over: Partial<TrendReportListItem> = {}): TrendReportListItem =>
    ({ report_id, status: "done", created_at: "", caller: "human", product: null, summary_line: null, ...over });
  assert.deepEqual(insightReports([item("a"), item("b", { status: "partial" }), item("c", { status: "running" }), item("d", { has_insight: false }),
    item("e", { has_insight: null })]).map((i) => i.report_id), ["a", "b", "e"]);
  const p = proposalFor(SAMPLE, "trend_report:tr_4913aa");
  assert.equal(p?.id, "PROP-31");
  assert.equal(proposalFor(SAMPLE, "trend_report:없음"), null);
  assert.deepEqual(deliveryOf(p, null), { text: "기획 전달됨 · PROP-31", tone: "ok", sent: true });
  assert.deepEqual(deliveryOf(null, { outcome: "duplicate", proposal_id: "PROP-7", reason: null }), { text: "기획 전달됨 · PROP-7", tone: "ok", sent: true });
  assert.deepEqual(deliveryOf(null, null), { text: "아직 기획에 안 감", tone: "warn", sent: false });
  assert.equal(handoffChip({ outcome: "rejected", proposal_id: null, reason: null }).text, "기획이 최근 지운 제안과 같아요");
  assert.deepEqual(handoffChip({ outcome: "not_sent", proposal_id: null, reason: "근거가 없음" }), { text: "근거가 없음", tone: "muted", sent: false });
  assert.equal(deliveryOf({ id: "PROP-25", status: "폐기" }, null).tone, "bad");
});

test("기획에 보내기 막힘(게이트웨이 403·404·거부)이면 준비 중", () => {
  assert.equal(handoffGated({ status: 403, message: "x" }), true);
  assert.equal(handoffGated({ status: 404 }), true);
  assert.equal(handoffGated({ status: 200, message: "데이터 AI가 요청을 처리하지 않았습니다 (rejected) · skill 없음" }), true);
  assert.equal(handoffGated({ status: 500, message: "서버 오류 (500)" }), false);
  assert.equal(handoffGated(null), false);
  assert.ok(HANDOFF_GATED_TEXT.includes("허용 목록 추가 대기"));
});

test("인사이트 필터 주소 #recommend?from=report|brief", () => {
  assert.equal(insightFilterFromHash("#recommend?from=report"), "report");
  assert.equal(insightFilterFromHash("#recommend?from=brief"), "brief");
  assert.equal(insightFilterFromHash("#recommend"), "all");
  assert.equal(insightFilterFromHash("#recommend?from=x"), "all");
  assert.equal(insightFilterFromHash("#detect?from=report"), "all");
  assert.equal(insightFilterHash("report"), "#recommend?from=report");
  assert.equal(insightFilterHash("all"), "#recommend");
});

test("인쇄 머리글: TR-xxxx · 회사 · 만든 시각(한국) · 상태", () => {
  assert.equal(printHeaderLine({ report_id: "tr_4913aa", created_at: "2026-11-19T02:11:00Z", status: "done" }, "캣북스"), "TR-4913 · 캣북스 · 2026-11-19 11:11 · 완료");
  assert.equal(printHeaderLine({ report_id: "tr_4913aa", created_at: null, status: "partial" }, null), "TR-4913 · 일부 완료");
});
