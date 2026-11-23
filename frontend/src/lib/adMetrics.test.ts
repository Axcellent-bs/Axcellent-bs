// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  countText, defaultSpendFor, formProblem, krwText, mediumsFor, missingNotes, parseCount, periodSpend, recordOutcomeKo,
  toBody, withMedium, withSource, type AdMetricsForm, type AdMetricsRow,
} from "./adMetrics.ts";

// 서버 응답 combos 모양(마케팅 links.py SOURCE_MEDIUMS)
const COMBOS = { banner: ["onsite"], notice: ["onsite"], instagram: ["social"], google: ["cpc"], youtube: ["social", "cpc"] };
const EMPTY: AdMetricsForm = { date: "2026-11-20", source: "", medium: "", impressions: "", clicks: "", spend: "", reportRef: "" };

const row = (date: string, spend: number | null): AdMetricsRow => ({
  campaign_id: "cmp_p1", date, source: "google", medium: "cpc", impressions: null, clicks: null, spend_krw: spend,
  report_ref: null, entered_by: "human", entered_at: "", updated_at: "", revisions: 0,
});

test("빈칸은 모름(null), 0은 0으로 읽는다", () => {
  assert.equal(parseCount(""), null);
  assert.equal(parseCount("  "), null);
  assert.equal(parseCount("0"), 0);
  assert.equal(parseCount("1,200원"), 1200);
});

test("저장된 값에서 모름은 –, 0은 0원으로 다르게 보인다", () => {
  assert.equal(krwText(null), "–");
  assert.equal(krwText(0), "0원");
  assert.equal(krwText(15000), "15,000원");
  assert.equal(countText(null), "–");
  assert.equal(countText(0), "0");
});

test("source를 고르면 조합표에 맞는 medium만 남는다", () => {
  assert.deepEqual(mediumsFor(COMBOS, "youtube"), ["social", "cpc"]);
  assert.deepEqual(mediumsFor(COMBOS, "unknown"), []);
  assert.equal(withSource(EMPTY, COMBOS, "google").medium, "cpc");
  assert.equal(withSource(EMPTY, COMBOS, "youtube").medium, "");
  assert.equal(withSource({ ...EMPTY, source: "youtube", medium: "cpc" }, COMBOS, "instagram").medium, "social");
});

test("onsite 줄은 광고비 기본값 0, 다른 줄은 빈칸(모름)", () => {
  assert.equal(defaultSpendFor("onsite"), "0");
  assert.equal(defaultSpendFor("cpc"), "");
  assert.equal(withSource(EMPTY, COMBOS, "banner").spend, "0");
  // 기본값 그대로면 medium이 바뀔 때 새 기본값으로 바뀐다
  assert.equal(withSource(withSource(EMPTY, COMBOS, "banner"), COMBOS, "google").spend, "");
  // 사람이 적은 값은 덮어쓰지 않는다
  assert.equal(withMedium({ ...EMPTY, source: "banner", medium: "onsite", spend: "5000" }, "cpc").spend, "5000");
});

test("보낼 수 없는 입력은 이유를 알려 준다", () => {
  const ok = { ...EMPTY, source: "google", medium: "cpc", impressions: "100", clicks: "10" };
  assert.equal(formProblem(ok, COMBOS, "2026-11-23"), null);
  assert.match(formProblem({ ...ok, date: "2026-11-24" }, COMBOS, "2026-11-23") ?? "", /오늘/);
  assert.match(formProblem({ ...ok, medium: "social" }, COMBOS, "2026-11-23") ?? "", /medium/);
  assert.match(formProblem({ ...ok, clicks: "101" }, COMBOS, "2026-11-23") ?? "", /클릭/);
  // 한쪽만 알면 비교하지 않는다
  assert.equal(formProblem({ ...ok, impressions: "" }, COMBOS, "2026-11-23"), null);
});

test("보내는 본문: 빈칸은 null, 0은 0, 근거는 앞뒤 공백을 지운다", () => {
  assert.deepEqual(toBody({ ...EMPTY, source: "banner", medium: "onsite", spend: "0", clicks: "", reportRef: "  " }), {
    date: "2026-11-20", source: "banner", medium: "onsite", impressions: null, clicks: null, spend_krw: 0, report_ref: null,
  });
});

test("결과 저장 기간 안 광고비 합계: 0은 더하고, 모름이 섞이면 합계 없음", () => {
  const rows = [row("2026-11-18", 1000), row("2026-11-19", 0), row("2026-11-21", null), row("2026-11-25", 9999)];
  assert.deepEqual(periodSpend(rows, "2026-11-18", "2026-11-20"), { total: 1000, rows: 2, missingDates: [] });
  assert.deepEqual(periodSpend(rows, "2026-11-18", "2026-11-21"), { total: null, rows: 3, missingDates: ["2026-11-21"] });
  assert.deepEqual(periodSpend(rows, "2026-12-01", "2026-12-02"), { total: null, rows: 0, missingDates: [] });
  assert.deepEqual(periodSpend([row("2026-11-19", 0)], "2026-11-19", "2026-11-19"), { total: 0, rows: 1, missingDates: [] });
});

test("모르는 날짜 안내와 저장 결과 문구", () => {
  assert.deepEqual(missingNotes({ impressions: 10, clicks: null, spend_krw: null, missing_dates: { clicks: ["2026-11-20"], spend_krw: ["2026-11-21", "2026-11-20"] } }),
    ["클릭: 2026-11-20 모름", "광고비: 2026-11-20, 2026-11-21 모름"]);
  assert.deepEqual(missingNotes(null), []);
  assert.match(recordOutcomeKo({ outcome: "rejected", reason_code: "unknown_campaign", row: null }), /기획안/);
  assert.match(recordOutcomeKo({ outcome: "updated", reason_code: null, row: null }), /고쳐/);
});
