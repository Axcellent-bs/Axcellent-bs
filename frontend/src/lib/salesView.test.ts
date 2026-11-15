// node --test로 돈다 (npm test). 매출 분석 화면에 나오는 글자만 시험한다.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { SalesAnalysisOutput } from "./data.ts";
import { changeText, koreaTime, rate, salesView, won, type SalesOkView } from "./salesView.ts";

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
