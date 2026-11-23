// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  AUTO_AD_METRICS_SOURCE, AUTO_BUDGET_SOURCE, campaignOptions, categoryViewsBreakdown, contentFields, contentPrefill,
  spendSourceBadge, viewSourcesText, type MarketingCampaign,
} from "./campaignLinks.ts";

const CMP: MarketingCampaign = {
  campaign_id: "cmp_1", campaign_name: "가을 세일", channel: "유튜브", budget_krw: 300000, started_on: "2026-11-16",
  ends_on: "2026-11-18", status: "active", content_category: "아재개그", format: "shorts", production_cost_krw: 0,
};

test("캠페인 고르기: 맨 앞은 고르지 않음, 목록에 없는 저장 값도 보인다", () => {
  const opts = campaignOptions([CMP], "cmp_old");
  assert.deepEqual(opts[0], { value: "", label: "고르지 않음" });
  assert.equal(opts[1].value, "cmp_1");
  assert.match(opts[1].label, /가을 세일 · 2026-11-16~2026-11-18 · 진행 중/);
  assert.deepEqual(opts[2], { value: "cmp_old", label: "cmp_old (목록에 없음)" });
  assert.equal(campaignOptions([CMP], "cmp_1").length, 2);
  assert.equal(campaignOptions([], null).length, 1);
});

test("결과 칸 미리 채우기: 대장(시작 때 값) → 재무 기록 순, 0원 제작비는 0", () => {
  assert.deepEqual(contentPrefill(CMP), { category: "아재개그", format: "shorts", production: "0" });
  assert.deepEqual(contentPrefill(null, { content_category: "몸개그", format: "reels", production_cost_krw: 5000 }), { category: "몸개그", format: "reels", production: "5000" });
  assert.deepEqual(contentPrefill(null, null), { category: "", format: "", production: "" });
});

test("결과 요청 칸: 빈 칸은 보내지 않고 0은 0으로 보낸다", () => {
  assert.deepEqual(contentFields({ category: " 아재개그 ", format: "shorts", production: "0" }), { content_category: "아재개그", format: "shorts", production_cost_krw: 0 });
  assert.deepEqual(contentFields({ category: "", format: "", production: "" }), {});
});

test("광고비 출처: 승인 예산은 잠정, 광고 숫자 합계는 따로, 사람이 저장한 값은 표시 없음", () => {
  assert.equal(spendSourceBadge(AUTO_BUDGET_SOURCE)?.label, "예산 기준(잠정)");
  assert.equal(spendSourceBadge(AUTO_BUDGET_SOURCE)?.provisional, true);
  assert.equal(spendSourceBadge(AUTO_AD_METRICS_SOURCE)?.label, "광고 숫자 합계");
  assert.equal(spendSourceBadge(AUTO_AD_METRICS_SOURCE)?.provisional, false);
  assert.equal(spendSourceBadge("marketing_screen_manual"), null);
});

test("조회수 출처: 영상 성과와 회사 유튜브를 나눠 적고 모르면 null", () => {
  assert.equal(viewSourcesText({ views: 1000, views_point: "7d", youtube_views: 500, youtube_views_at: "2026-11-23T01:02:03+00:00" }),
    "영상 성과(데이터 7d) 1,000회 + 회사 유튜브 500회(2026-11-23 01:02 읽음)");
  assert.equal(viewSourcesText({ youtube_views: 0 }), "회사 유튜브 0회");
  assert.equal(viewSourcesText({}), null);
  assert.equal(categoryViewsBreakdown({ performance_views: 100, youtube_views: 50 }), "영상 성과 7일 100 + 회사 유튜브 50");
  assert.equal(categoryViewsBreakdown({ performance_views: 100, youtube_views: 0 }), null);
});
