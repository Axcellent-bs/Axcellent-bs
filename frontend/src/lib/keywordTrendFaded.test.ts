// node --test로 돈다 (npm test). 끝난 급상승(faded, 11/26): 칩 글자 · 같은 칸 뒤로 · 대표로 안 고름.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { RisingKeyword } from "./data.ts";
import { fadedChip, fadedLast, splitRising, topRising } from "./keywordTrendView.ts";

const row = (keyword: string, over: Partial<RisingKeyword> = {}): RisingKeyword => ({
  keyword, rank: 1, status: "rising", ratio: 2, score: null, reason: null, category: null, volume: null, monthly_volume: null,
  volume_reason: null, estimate: null, increase: null, increase_reason: null, news_count: null, news_source: null, news: [], spread: null,
  products: null, product_names: [], products_reason: null, level: "rising", ...over,
});

test("칩: faded면 '지난 급상승 · M/D 정점', 정점 모르면 '지난 급상승', 아니면 null", () => {
  assert.equal(fadedChip({ faded: true, peak_end: "2026-11-19" }), "지난 급상승 · 11/19 정점");
  assert.equal(fadedChip({ faded: true, peak_end: null }), "지난 급상승");
  assert.equal(fadedChip({ faded: false, peak_end: "2026-11-19" }), null);
  assert.equal(fadedChip({}), null); // 예전 응답
  assert.equal(fadedChip(null), null);
});

test("정렬: 같은 급상승 칸 안에서 faded 아닌 것 먼저 (나머지 순서 그대로)", () => {
  const rows = [row("수능시간표", { ratio: 110.8, faded: true, peak_end: "2026-11-19" }), row("기말고사", { ratio: 3 }), row("벼락치기", { ratio: 2.5 })];
  assert.deepEqual(splitRising(rows).rising.map((r) => r.keyword), ["기말고사", "벼락치기", "수능시간표"]);
  assert.deepEqual(fadedLast([{ faded: true }, {}, { faded: false }]).map((r) => !!r.faded), [false, false, true]);
});

test("대표 급상승: faded는 고르지 않고, 모두 faded면 없음", () => {
  const out = (rising: RisingKeyword[]) => ({ rising, stale_reason: null, judged_on: null });
  assert.equal(topRising(out([row("수능시간표", { faded: true }), row("기말고사")]))?.row.keyword, "기말고사");
  assert.equal(topRising(out([row("수능시간표", { faded: true })])), null);
});
