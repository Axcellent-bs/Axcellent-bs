// node --test로 돈다 (npm test). 매출 분석 화면에 나오는 글자만 시험한다.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChannelValueOutput, SalesAnalysisOutput } from "./data.ts";
import { STALE_RELOAD_MS, calcLine, calcOfResult, changeText, channelValueView, koreaTime, rate, salesView, won, type SalesOkView } from "./salesView.ts";

test("출처 줄: 계산 시각(한국)과 판매 기록 마지막 수집", () => {
  assert.equal(calcLine("2026-11-18T03:05:00Z", "2026-11-18T03:00:00Z"), "계산 12:05 (판매 기록 12:00까지)");
  assert.equal(calcLine("2026-11-18T03:05:00Z", null), "계산 12:05");
  assert.equal(calcLine(null, "2026-11-18T03:00:00Z"), null);
  assert.equal(calcLine("엉터리", null), null);
});

test("예전 결과를 먼저 받으면 계산 시각 옆에 갱신 중, basis 계산 시각이 근거보다 먼저", () => {
  assert.equal(calcLine("2026-11-18T03:05:00Z", "2026-11-18T03:00:00Z", true), "계산 12:05 · 갱신 중 (판매 기록 12:00까지)");
  const r = {
    evidence: { collected_at: "2026-11-18T03:09:00Z" },
    output: { basis: { computed_at: "2026-11-18T03:05:00Z", last_synced_at: "2026-11-18T03:08:00Z", stale: true } },
  };
  assert.deepEqual(calcOfResult(r), { line: "계산 12:05 · 갱신 중 (판매 기록 12:08까지)", stale: true, stamp: "2026-11-18T03:05:00Z" });
  const plain = calcOfResult({ evidence: { collected_at: "2026-11-18T03:09:00Z" }, output: { basis: null } });
  assert.deepEqual(plain, { line: "계산 12:09", stale: false, stamp: "2026-11-18T03:09:00Z" });
  assert.deepEqual(calcOfResult(null), { line: null, stale: false, stamp: null });
  assert.equal(STALE_RELOAD_MS, 30_000);
});

function sample(over: Partial<SalesAnalysisOutput> = {}): SalesAnalysisOutput {
  return {
    period: "7d", as_of: "2026-11-16T03:00:00Z", status: "ok", insufficient_reasons: [], definition: "환불을 뺀 결제 금액 기준입니다.",
    kpis: [
      { key: "revenue", label: "매출", unit: "won", value: 18420000, previous: 17344000, change: 0.062, change_kind: "ratio" },
      { key: "order_count", label: "주문 수", unit: "count", value: 1234, previous: 1300, change: -0.0507, change_kind: "ratio" },
      { key: "aov", label: "평균 주문 금액", unit: "won", value: 14928, previous: null, change: null, change_kind: "ratio" },
      { key: "repeat_rate", label: "재구매율", unit: "rate", value: 0.2345, previous: 0.2165, change: 0.018, change_kind: "points" },
    ],
    categories: [{ code: "book", label: "도서", revenue: 9000000, share: 0.489, previous_revenue: 8000000, change: 0.125 }],
    products_up: [{ product_id: "prd_001", label: "고양이 집사 일기", category_label: "도서", revenue: 500000, previous_revenue: 100000, change: 4, orders: 50, previous_orders: 10 }],
    products_down: [], products_low_sample: 3, min_product_orders: 5,
    customers: { buyers: 100, repeat_buyers: 20, new_revenue: 1, repeat_revenue: 1, new_share: 0.7, repeat_share: 0.3, coupon_orders: 10, coupon_order_rate: 0.123 },
    channels: [{ source: "naver", medium: "cpc", label: "네이버 검색광고", revenue: 3000000, orders: 200, share: 0.163 }],
    quality: { rows_in_period: 1234, empty_days: ["2026-11-10"], dropped_rows: 2, dropped_reasons: { missing_price: 2 }, last_synced_at: "2026-11-16T02:00:00Z", unknown_category_codes: ["x1", "x2"] },
    basis: { period_from: "2026-11-09T03:00:00Z", period_to: "2026-11-16T03:00:00Z", previous_from: "2026-11-02T03:00:00Z", previous_to: "2026-11-09T03:00:00Z", rows_read: 1300, last_synced_at: null, catalog_id: "catbooks", catalog_version: "1" },
    summary: "지난 7일 매출이 늘었어요.",
    ...over,
  };
}

test("부족 상태는 숫자 없이 이유와 품질 사실만 보인다", () => {
  const v = salesView(sample({ status: "insufficient", insufficient_reasons: ["데이터 부족: 기간 안 주문이 없습니다"] }));
  assert.equal(v.kind, "gate");
  if (v.kind !== "gate") return;
  assert.deepEqual(v.reasons, ["데이터 부족: 기간 안 주문이 없습니다"]);
  assert.ok(v.facts.some((f) => f.startsWith("마지막 수집 11.16 11:00")));
  assert.ok(v.facts.includes("기간 안 주문 행 1234개"));
  assert.ok(v.facts.includes("주문 없는 날 1일"));
  assert.equal("kpis" in v, false);
  assert.equal(JSON.stringify(v).includes("18,420,000"), false);
});

test("정상 상태는 원·건·비율과 변화를 글자로 만든다", () => {
  const v = salesView(sample()) as SalesOkView;
  assert.equal(v.kind, "ok");
  assert.deepEqual(v.kpis.map((k) => k.value), ["18,420,000원", "1,234건", "14,928원", "23.5%"]);
  assert.deepEqual(v.kpis.map((k) => k.change), ["▲ 6.2%", "▼ 5.1%", "비교 없음", "▲ 1.8%p"]);
  assert.deepEqual(v.kpis.map((k) => k.tone), ["up", "down", "flat", "up"]);
  assert.equal(v.categories[0].share, "48.9%");
  assert.equal(v.categories[0].change, "▲ 12.5%");
  assert.equal(v.asOf, "기준 11.16 12:00");
  assert.equal(v.compare, "비교 기간 11.02 12:00 ~ 11.09 12:00");
  assert.equal(v.lowSample, "표본 부족 3개 (두 기간 중 한쪽이라도 주문 5건 미만이라 비교에서 뺐어요)");
  assert.deepEqual(v.customers?.map((c) => c.value), ["70%", "30%", "12.3%"]);
});

test("작은 헬퍼: 원 쉼표, 비율 소수 한 자리, 변화 없음, 한국 시간", () => {
  assert.equal(won(1000), "1,000원");
  assert.equal(rate(0.4), "40%");
  assert.equal(changeText(0, "ratio").text, "― 0%");
  assert.equal(changeText(null, "points").text, "비교 없음");
  assert.equal(koreaTime("2026-11-16T15:30:00Z"), "11.17 00:30");
  assert.equal(koreaTime("nope"), null);
});

test("유입 경로 이름은 그대로, 비어 있으면 빈 목록과 빈 상태 값", () => {
  const v = salesView(sample()) as SalesOkView;
  assert.equal(v.channels[0].label, "네이버 검색광고");
  assert.equal(v.channels[0].revenue, "3,000,000원");
  assert.equal(v.channels[0].share, "16.3%");
  const e = salesView(sample({ channels: [], categories: [], customers: null, products_up: [], products_low_sample: 0 })) as SalesOkView;
  assert.deepEqual(e.channels, []);
  assert.deepEqual(e.categories, []);
  assert.equal(e.customers, null);
  assert.equal(e.lowSample, null);
});

test("품질 줄: 이름 없는 분류는 개수만, 코드는 내보내지 않는다", () => {
  const v = salesView(sample()) as SalesOkView;
  assert.ok(v.quality.includes("카탈로그에 이름이 없는 분류 2개"));
  assert.ok(v.quality.includes("버린 행 2개"));
  assert.ok(v.quality.includes("주문 없는 날 1일"));
});

test("화면 글자에 코드값(book · naver · prd_ · missing_price · cpc)이 없다", () => {
  const text = JSON.stringify(salesView(sample()));
  for (const raw of ["book", "naver", "prd_", "missing_price", "cpc", "x1"]) assert.equal(text.includes(raw), false, raw);
});

const CHANNEL_VALUE: ChannelValueOutput = {
  status: "ok", insufficient_reasons: [], period: "365d", horizon_days: 90, cost_note: "재무 연결 대기",
  basis: null, quality: { rows_in_period: 0, empty_days: [], dropped_rows: 0, dropped_reasons: {}, last_synced_at: null, unknown_category_codes: [] },
  summary: "최근 365일 첫 구매 고객 기준, 유튜브로 들어온 고객이 90일 동안 1인 41,200원으로 가장 높음.",
  rows: [
    { source: "youtube", label: "유튜브", new_customers: 820, revenue_per_customer: 41200, repeat_rate: 0.36, orders_per_customer: 1.8, cost_krw: null, suppressed: false },
    { source: "naver", label: "네이버 검색", new_customers: 1140, revenue_per_customer: 33900, repeat_rate: 0.29, orders_per_customer: 1.46, cost_krw: null, suppressed: false },
    { source: null, label: "기타 경로", new_customers: null, revenue_per_customer: null, repeat_rate: null, orders_per_customer: null, cost_krw: null, suppressed: true },
  ],
};

test("유입 경로별 고객 가치: 1인 매출·재구매율·1인 주문, 비용은 재무 연결 대기, 숨긴 경로는 표본 부족", () => {
  const v = channelValueView(CHANNEL_VALUE);
  assert.equal(v.kind, "ok");
  if (v.kind !== "ok") return;
  assert.deepEqual(v.columns, ["첫 구매 경로", "새 고객", "90일 1인 매출", "재구매율", "1인 주문", "광고·쿠폰 비용"]);
  assert.deepEqual(v.rows[0], { label: "유튜브", customers: "820명", revenue: "41,200원", repeat: "36%", orders: "1.8건", cost: "재무 연결 대기", best: true, suppressed: false });
  assert.equal(v.rows[1].orders, "1.5건");
  assert.equal(v.rows[1].best, false);
  assert.deepEqual(v.rows[2], { label: "기타 경로", customers: "표본 부족", revenue: "-", repeat: "-", orders: "-", cost: "재무 연결 대기", best: false, suppressed: true });
  assert.ok(v.hint.includes("90일 동안"));
  const text = JSON.stringify(v);
  for (const raw of ["youtube", "naver"]) assert.equal(text.includes(raw), false, raw);
  const empty = channelValueView({ ...CHANNEL_VALUE, rows: [] });
  assert.ok(empty.kind === "ok" && empty.empty === "최근 1년 안에 첫 구매하고 90일이 지난 고객이 아직 없어요");
  const gated = channelValueView({ ...CHANNEL_VALUE, status: "insufficient", insufficient_reasons: ["데이터 부족: 저장된 고객 기록이 없음"] });
  assert.deepEqual(gated, { kind: "gate", reasons: ["데이터 부족: 저장된 고객 기록이 없음"] });
});
