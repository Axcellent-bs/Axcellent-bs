// 새 회사·시장 근거 0 보고서 (11/27 가비). node --test src/lib/requestBasedReport.test.ts
// 설정·자료가 없어 한 칸도 못 본 일부 완료는 실패가 아니다: 의뢰 카드 2단계는 초록 완료 + "의뢰문 기반" 한 줄
import assert from "node:assert/strict";
import { test } from "node:test";
import { REQUEST_BASED_KO, doneView, requestBasedReport } from "./chatCards.ts";
import { DONE_CARD_EXAMPLE, DONE_CARD_FAILED } from "./chatCardsExample.ts";
import type { TrendReport } from "./data.ts";
import { TREND_REPORT_EXAMPLE } from "./trendReportExample.ts";

const NAMES = ["search", "hashtags", "content", "competitors", "recommendation"] as const;

function withSections(status: string, states: Record<string, string>): TrendReport {
  const r = structuredClone(TREND_REPORT_EXAMPLE) as TrendReport;
  r.status = status as TrendReport["status"];
  for (const name of NAMES) (r.sections as unknown as Record<string, { status: string }>)[name].status = states[name] ?? "unknown";
  return r;
}

test("한 칸도 못 본 일부 완료만 의뢰문 기반", () => {
  assert.equal(requestBasedReport(withSections("partial", {})), true);
  assert.equal(requestBasedReport(withSections("partial", { content: "failed" })), true); // 칸 하나 예외 + 나머지 자료 없음
  assert.equal(requestBasedReport(withSections("partial", { search: "known" })), false); // 찾은 사실이 있으면 지금처럼 일부만 완료
  assert.equal(requestBasedReport(withSections("partial", { hashtags: "empty" })), false);
  assert.equal(requestBasedReport(withSections("failed", {})), false); // 진짜 실패는 그대로
});

test("완료 카드: 초록 완료 + 의뢰문 기반 한 줄 (설정 안내 없음), 진짜 실패는 지금처럼 빨강", () => {
  const v = doneView({ ...DONE_CARD_EXAMPLE, status: "partial", request_based: true });
  assert.equal(v.tone, "ok");
  assert.match(v.title, /^보고서 완료 · TR-/);
  assert.equal(v.note, REQUEST_BASED_KO);
  assert.doesNotMatch(`${v.title} ${v.note}`, /설정|채워|연결/);
  // 표시가 없으면 지금 그대로
  assert.equal(doneView({ ...DONE_CARD_EXAMPLE, status: "partial" }).tone, "warn");
  assert.equal(doneView({ ...DONE_CARD_FAILED, request_based: true }).tone, "bad");
  assert.equal(doneView(DONE_CARD_EXAMPLE).note, undefined);
});
