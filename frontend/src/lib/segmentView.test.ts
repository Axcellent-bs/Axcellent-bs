// node --test로 돈다: node --test src/lib/segmentView.test.ts
// 고객·시장 분석 화면에 나오는 글자만 시험한다. 픽스처는 계약(#1116 + segment_options) 예시 JSON
import assert from "node:assert/strict";
import { test } from "node:test";
import type {
  BasketPairsOutput, EventProposalOutput, MarketCoverageOutput, SalesBasis, SalesQuality, SegmentFavoritesOutput,
  SegmentOptionsOutput, SegmentProfileOutput,
} from "./data.ts";
import {
  basketNoun, basketView, coverageSource, coverageView, dataNote, indexTone, favoritesView, fakeBadge, filterLabel, isHigh, nextMonth, optionsView, parseTarget,
  pct, profileView, proposalsView, questionView, readableReason, retentionView, sectionState, sendResultText, times,
  cohortDone,
} from "./segmentView.ts";
import type { RetentionOutput } from "./data.ts";

const QUALITY: SalesQuality = {
  rows_in_period: 1234, empty_days: [], dropped_rows: 2, dropped_reasons: { "가격 없음": 2, missing_qty: 1 },
  last_synced_at: "2026-11-16T02:00:00Z", unknown_category_codes: [], unknown_genres: ["g_x"], unmapped_attribute_values: { job: ["xx"] },
};
const BASIS: SalesBasis = {
  period_from: "2026-08-18T15:00:00Z", period_to: "2026-11-16T03:00:00Z", previous_from: "2026-08-18T15:00:00Z",
  previous_to: "2026-08-18T15:00:00Z", rows_read: 1300, last_synced_at: null, catalog_id: "catbooks", catalog_version: "1", data_source: null,
};
const FAKE: SalesBasis = { ...BASIS, data_source: "가짜 데이터(데모용)" };

const OPTIONS: SegmentOptionsOutput = {
  attributes: [
    { key: "age_band", label: "연령대", values: [{ code: "20s", label: "20대" }, { code: "30s", label: "30대" }, { code: "no_answer", label: "답하지 않음" }] },
    { key: "gender", label: "성별", values: [{ code: "f", label: "여성" }, { code: "m", label: "남성" }] },
    { key: "job", label: "직업군", values: [{ code: "office", label: "직장인" }] },
  ],
  genres: [{ code: "g_ai", label: "AI 활용" }],
  categories: [{ code: "book", label: "도서" }],
  quick_questions: [
    { label: "20대 여성이 좋아하는 책은?", filters: { age_band: "20s", gender: "f" } },
    { label: "없는 속성 질문", filters: { region: "seoul" } },
  ],
  event_kinds: [{ code: "coupon", label: "쿠폰" }],
  periods: ["1d", "7d", "30d", "90d", "365d"],
  population_keys: [["age_band", "gender"], ["region"]],
  min_group_size: 5, data_source: "가짜 데이터(데모용)", catalog_id: "catbooks", catalog_version: "1",
};

const FAVORITES: SegmentFavoritesOutput = {
  status: "ok", insufficient_reasons: [], filters: { age_band: "20s", gender: "f" }, filter_label: "20대 · 여성", period: "90d",
  segment_buyers: 412, segment_suppressed: false,
  internal: [
    { kind: "genre", code: "g_novel", label: "소설", share: 0.31, lift: 1.43, buyers: 128, suppressed: false },
    { kind: "genre", code: "g_poem", label: "시", share: null, lift: null, buyers: null, suppressed: true },
  ],
  external: [
    { source_id: "data4library", label: "도서관 정보나루 인기대출", status: "ok", reason: "", period_label: "최근 3개월", condition_label: "20대 · 여성",
      items: [{ name: "책 하나", value: 1240, unit: "회", rank: 1 }] },
    { source_id: "naver_shopping", label: "네이버 쇼핑인사이트", status: "key_missing", reason: "NAVER_CLIENT_ID missing", period_label: "", condition_label: "", items: [] },
    { source_id: "kosis", label: "통계청", status: "limit", reason: "하루 한도 1000회를 넘었어요", period_label: "", condition_label: "", items: [] },
  ],
  quality: QUALITY, basis: BASIS, summary: "20대 여성은 소설 비중이 높아요.",
};

const PROFILE: SegmentProfileOutput = {
  status: "ok", insufficient_reasons: [], target_kind: "genre", target_code: "g_ai", target_label: "AI 활용", period: "90d",
  target_buyers: 300, target_suppressed: false, overall_buyers: 5000,
  attributes: [{
    key: "job", label: "직업군", unknown_buyers: 12,
    values: [
      { value_code: "office", label: "직장인", target_share: 0.58, overall_share: 0.39, lift: 1.487, buyers: 174, suppressed: false },
      { value_code: "self", label: "자영업", target_share: 0.09, overall_share: 0.11, lift: 0.818, buyers: 27, suppressed: false },
      { value_code: "farm", label: "농업", target_share: null, overall_share: null, lift: null, buyers: null, suppressed: true },
    ],
  }],
  quality: QUALITY, basis: FAKE, summary: "",
};

const SOURCE = "행정안전부 주민등록인구통계(KOSIS) · 2026년 10월 말";
const COVERAGE: MarketCoverageOutput = {
  status: "ok", insufficient_reasons: [], attribute_keys: ["age_band", "gender"], period: "365d",
  rows: [
    { values: { age_band: "30s", gender: "m" }, label: "30대 남성", buyers: 300, population: 1_000_000, population_source: SOURCE, base_year: 2025, coverage: 0.0003, suppressed: false, status: "ok", reason: "",
      population_note: "30~34세 + 35~39세", buyer_share: 0.2, population_share: 0.25, index: 80 },
    { values: { age_band: "20s", gender: "f" }, label: "20대 여성", buyers: 1180, population: 3_000_000, population_source: SOURCE, base_year: 2025, coverage: 0.000393, suppressed: false, status: "ok", reason: "",
      population_note: "20~24세 + 25~29세", buyer_share: 0.5, population_share: 0.3, index: 166.7 },
    { values: { age_band: "60s", gender: "f" }, label: "60대 여성", buyers: null, population: 2_000_000, population_source: SOURCE, base_year: 2025, coverage: null, suppressed: true, status: "ok", reason: "",
      population_note: "60세 이상 합산", buyer_share: null, population_share: null, index: null },
    { values: { age_band: "no_answer", gender: "f" }, label: "답하지 않음 · 여성", buyers: 40, population: null, population_source: SOURCE, base_year: null, coverage: null, suppressed: false, status: "population_missing", reason: "인구 통계에 없는 값이라 비율을 내지 않음" },
  ],
  quality: QUALITY, basis: BASIS, summary: "",
};

const PROPOSALS: EventProposalOutput = {
  status: "ok", insufficient_reasons: [], month: "2026-12", quality: QUALITY, basis: BASIS, summary: "다음 달 제안 1개",
  proposals: [{
    proposal_id: "evp_ab12cd", target_filters: { job: "office" }, target_label: "직장인", genre_code: "g_ai", category_code: null,
    item_label: "AI 활용서", event_kind: "coupon", event_label: "15% 쿠폰",
    timing_basis: { month: "2026-12", keywords: ["AI 업무"], last_year_multiple: 1.3, source: "네이버 검색어 트렌드" },
    reasons: [
      { kind: "segment", text: "AI 활용 책 구매자 중 직장인 1.5배", value: 1.5, unit: "배", source: "캣북스 주문 기록 최근 90일" },
      { kind: "season", text: "\"AI 업무\" 작년 12월이 평균의 1.3배", value: 1.3, unit: "배", source: "네이버 검색어 트렌드" },
      { kind: "conversion", text: "쿠폰 사용률 6~9% (가정)", value: 0.06, unit: "%", source: "가정값" },
    ],
    estimate: {
      target_customers: 2000, conversion_low: 0.06, conversion_high: 0.09, conversion_source: "assumption", assumption: true, aov: 22700,
      orders_low: 120, orders_high: 180, revenue_low: 2_724_000, revenue_high: 4_086_000, discount_cost_low: 408_600, discount_cost_high: 612_900,
      formula: "대상 2,000명 × 전환율 6~9% × 객단가 22,700원",
    },
    limits: ["직업은 회원이 고른 값"], score: 0.8, title: null, summary: null,
    sent: { sent_at: "2026-11-16T03:00:00Z", outcome: "duplicate", proposal_ref: "PROP-12" },
    past_results: [{ proposal_id: "evp_old", proposal_ref: "PROP-3", campaign_id: "cmp_1", expected_low: 1_000_000, expected_high: 2_000_000, actual: 1_500_000, period_from: null, period_to: null }],
  }],
};

/** 화면 글자에 영문 코드(키·code·source_id·proposal_id)가 새지 않았는지 */
function noCodes(v: unknown, codes: string[]) {
  const texts: string[] = [];
  const walk = (x: unknown) => {
    if (typeof x === "string") texts.push(x);
    else if (Array.isArray(x)) x.forEach(walk);
    else if (x && typeof x === "object") Object.values(x).forEach(walk);
  };
  walk(v);
  for (const c of codes) assert.equal(texts.some((t) => t.includes(c)), false, `code leaked: ${c}`);
}

test("숫자 글자: 퍼센트·배수", () => {
  assert.equal(pct(0.31), "31%");
  assert.equal(pct(0.125), "12.5%");
  assert.equal(pct(0.000393), "0.04%");
  assert.equal(times(1.487), "1.5배");
  assert.equal(times(1), "1.0배");
  assert.equal(times(2), "2.0배");
  assert.equal(isHigh(1.2), true);
  assert.equal(isHigh(1.19), false);
  assert.equal(isHigh(null), false);
});

test("선택지는 segment_options에서만 읽고, 없는 속성을 가리키는 칩·조합은 뺀다", () => {
  const o = optionsView(OPTIONS);
  assert.deepEqual(o.attributes.map((a) => a.label), ["연령대", "성별", "직업군"]);
  assert.deepEqual(o.attributes[0].values.map((v) => v.label), ["20대", "30대", "답하지 않음"]);
  assert.deepEqual(o.chips.map((c) => c.label), ["20대 여성이 좋아하는 책은?"]);
  assert.deepEqual(o.targets.map((t) => [t.group, t.label]), [["장르", "AI 활용"], ["분류", "도서"]]);
  assert.deepEqual(o.periods.map((p) => p.label), ["최근 1일", "최근 7일", "최근 30일", "최근 3개월", "최근 1년"]);
  assert.deepEqual(o.coverage, [{ value: "age_band,gender", label: "연령대 × 성별" }]);
  assert.equal(o.badge, "가짜 데이터로 본 결과");
  assert.deepEqual(parseTarget(o.targets[0].value), { kind: "genre", code: "g_ai" });
  assert.equal(parseTarget("product:p1"), null);
  assert.equal(filterLabel({ age_band: "20s", gender: "f", region: "x" }, o.attributes), "20대 · 여성");
});

test("물어보기: 우리 쇼핑몰 순위와 바깥 시장(키 필요·한도) 칸", () => {
  const v = favoritesView(FAVORITES, "책");
  assert.equal(v.kind, "ok");
  if (v.kind !== "ok") return;
  assert.equal(v.header, "우리 쇼핑몰 · 20대 · 여성 구매자 412명 (최근 3개월)");
  assert.equal(v.genres.title, "특히 많이 사는 장르");
  assert.deepEqual(v.genres.rows[0], { label: "소설", detail: "구매자의 31% · 전체 대비 1.4배 · 128명", suppressed: false, high: true });
  assert.deepEqual(v.genres.rows[1], { label: "시", detail: "표본 부족", suppressed: true, high: false });
  assert.equal(v.products.title, "많이 산 책");
  assert.deepEqual(v.external, [{ title: "도서관 정보나루 인기대출", meta: "20대 · 여성 · 최근 3개월", state: "ok", note: null, rows: [{ label: "1. 책 하나", detail: "1,240회" }] }]);
  // 실패한 출처는 한 줄로: 영어 이유는 내보내지 않는다
  assert.equal(v.externalNote, "네이버 쇼핑인사이트 키 등록 필요 · 통계청 오늘 조회 한도에 닿음 · 하루 한도 1000회를 넘었어요 · 확인한 출처 1/3");
  assert.equal(v.badge, null);
  noCodes(v, ["g_novel", "g_poem", "data4library", "naver_shopping", "kosis", "age_band"]);
});

test("물어보기: 장르는 배수 높은 순 5개, 품목은 product만 5개, 분류는 뺀다, 요약은 배수 1위 → 구매 비율 1위", () => {
  const g = (code: string, share: number, lift: number) => ({ kind: "genre" as const, code, label: `장르${code}`, share, lift, buyers: 10, suppressed: false });
  const p = (code: string) => ({ kind: "product" as const, code, label: `품목${code}`, share: 0.1, lift: 1, buyers: 10, suppressed: false });
  const v = favoritesView({
    ...FAVORITES, external: [],
    internal: [
      g("1", 0.5, 1.1), g("2", 0.3, 2.0), g("3", 0.2, 1.2), g("4", 0.2, 1.19), g("5", 0.1, 0.9), g("6", 0.1, 3.0),
      { kind: "category", code: "c", label: "큰 분류", share: 0.9, lift: 5, buyers: 99, suppressed: false },
      ...["a", "b", "c", "d", "e", "f"].map(p),
    ],
  }, "게임");
  assert.ok(v.kind === "ok");
  if (v.kind !== "ok") return;
  assert.deepEqual(v.genres.rows.map((r) => r.label), ["장르6", "장르2", "장르3", "장르4", "장르1"]);
  assert.deepEqual(v.genres.rows.map((r) => r.high), [true, true, true, false, false]); // 1.2배부터 강조
  assert.equal(v.products.title, "많이 산 게임");
  assert.deepEqual(v.products.rows.map((r) => r.label), ["품목a", "품목b", "품목c", "품목d", "품목e"]);
  assert.equal(JSON.stringify(v).includes("큰 분류"), false);
  assert.equal(v.summary, "특히 많이 사는 장르: 장르6 (전체 대비 3.0배). 가장 많이 산 장르: 장르1 (구매자의 50%).");
  assert.equal(v.externalNote, null);
});

test("물어보기: 같은 이유로 실패한 출처는 한 번만, 확인한 출처 n/m", () => {
  const miss = (label: string) => ({ source_id: "lib", label, status: "key_missing" as const, reason: "UNAUTHORIZED: key", period_label: "", condition_label: "", items: [] });
  const v = favoritesView({ ...FAVORITES, external: [miss("공공 도서관"), miss("공공 도서관 · 장르1"), miss("공공 도서관 · 장르2")] });
  assert.ok(v.kind === "ok" && v.external.length === 0);
  if (v.kind !== "ok") return;
  assert.equal(v.externalNote, "공공 도서관 키 등록 필요 · 확인한 출처 0/1");
});

test("물어보기: 집단 전체가 표본 부족이면 순위를 비우고 표본 부족만", () => {
  const v = favoritesView({ ...FAVORITES, segment_buyers: null, segment_suppressed: true, internal: [] });
  assert.ok(v.kind === "ok" && v.header.includes("표본 부족") && v.genres.rows.length === 0 && v.products.rows.length === 0);
  assert.ok(v.kind === "ok" && v.summary === "");
});

test("글로 묻기: 읽은 조건은 선택지와 맞추고, 못 읽으면 고르라고 안내", () => {
  const o = optionsView(OPTIONS);
  const ok = questionView({ status: "ok", filters: { age_band: "20s", gender: "f", region: "x" }, genre_code: null, period: "30d",
    understood_label: "20대 · 여성 · 직업군 전체 · 최근 30일", reason: "" }, o);
  assert.deepEqual(ok, { kind: "ok", text: "이렇게 읽었어요: 20대 · 여성 · 직업군 전체 · 최근 30일", filters: { age_band: "20s", gender: "f" }, period: "30d" });
  const unparsed = questionView({ status: "unparsed", filters: {}, genre_code: null, period: null, understood_label: "", reason: "질문에서 고객 묶음(속성 값)을 찾지 못함" }, o);
  assert.deepEqual(unparsed, { kind: "unparsed", text: "어느 집단인지 골라 주세요", reason: "질문에서 고객 묶음(속성 값)을 찾지 못함" });
  // 선택지에 없는 값만 왔으면 unparsed, 영어 이유는 숨긴다
  const odd = questionView({ status: "ok", filters: { age_band: "99s" }, genre_code: null, period: "7d" as never, understood_label: "", reason: "bad" }, o);
  assert.deepEqual(odd, { kind: "unparsed", text: "어느 집단인지 골라 주세요", reason: null });
});

test("시장 커버: 모든 줄에 인구가 없으면 안내 한 줄 + 연령대(줄) × 성별(칸) 구매자 표", () => {
  const o = optionsView(OPTIONS);
  const missing = (age: string, g: string, buyers: number | null, suppressed = false) => ({
    values: { age_band: age, gender: g }, label: "", buyers, population: null, population_source: "", base_year: null, coverage: null,
    suppressed, status: "population_missing" as const, reason: "인구 통계표 설정 필요",
  });
  const v = coverageView({ ...COVERAGE, rows: [missing("20s", "f", 120), missing("20s", "m", 80), missing("30s", "f", null, true), missing("30s", "m", 40)] }, o.attributes);
  assert.equal(v.kind, "buyers");
  if (v.kind !== "buyers") return;
  assert.equal(v.note, "인구 통계를 아직 받지 못해 구성 지수 대신 우리 구매자 수(최근 1년)만 보여요 · 인구 통계표 설정 필요");
  assert.equal(v.corner, "연령대 \\ 성별");
  assert.deepEqual(v.columns, ["여성", "남성"]);
  assert.deepEqual(v.rows, [
    { label: "20대", cells: ["120명", "80명"] },
    { label: "30대", cells: ["표본 부족", "40명"] },
    { label: "답하지 않음", cells: ["-", "-"] },
  ]);
  noCodes(v, ["age_band", "gender", "20s"]);
  // 인구가 있는 줄이 하나라도 있으면 지금 표 그대로
  assert.equal(coverageView(COVERAGE, o.attributes).kind, "ok");
});

// 232에서 KOSIS 키가 없을 때 서버가 모든 줄에 같은 이유로 주는 값
const KOSIS_ERROR = "KOSIS를 읽지 못함 (UNAUTHORIZED: KOSIS_API_KEY 키가 없어 KOSIS를 부를 수 없다 (키 등록 후 가능))";
const row = (age: string, g: string, status: "ok" | "population_missing" | "error", reason: string, buyers: number | null = 10, suppressed = false) => ({
  values: { age_band: age, gender: g }, label: "", buyers, population: status === "ok" ? 1_000_000 : null,
  population_source: status === "ok" ? "통계청 주민등록인구" : "", base_year: null, coverage: status === "ok" && buyers ? buyers / 1_000_000 : null,
  suppressed, status, reason,
});

test("시장 커버: 모든 줄이 같은 이유로 오류면 한 줄 + 구매자 표 (오류 코드·설정 이름은 빼고)", () => {
  const o = optionsView(OPTIONS);
  const v = coverageView({ ...COVERAGE, rows: [
    row("20s", "f", "error", KOSIS_ERROR, 120), row("20s", "m", "error", KOSIS_ERROR, 80),
    row("30s", "f", "error", KOSIS_ERROR, null, true), row("30s", "m", "error", KOSIS_ERROR, 40),
  ] }, o.attributes);
  assert.equal(v.kind, "buyers");
  if (v.kind !== "buyers") return;
  assert.equal(v.note, "인구 통계를 읽지 못해 구성 지수 대신 우리 구매자 수(최근 1년)만 보여요 · KOSIS를 읽지 못함 (키가 없어 KOSIS를 부를 수 없다 (키 등록 후 가능))");
  assert.deepEqual(v.rows.slice(0, 2), [
    { label: "20대", cells: ["120명", "80명"] },
    { label: "30대", cells: ["표본 부족", "40명"] }, // 숨긴 칸은 표본 부족
  ]);
  assert.equal(/UNAUTHORIZED|KOSIS_API_KEY/.test(JSON.stringify(v)), false);
  // 영어로만 된 이유는 붙이지 않는다
  const english = coverageView({ ...COVERAGE, rows: [row("20s", "f", "error", "UNAVAILABLE: timeout"), row("20s", "m", "error", "UNAVAILABLE: timeout")] }, o.attributes);
  assert.ok(english.kind === "buyers" && english.note === "인구 통계를 읽지 못해 구성 지수 대신 우리 구매자 수(최근 1년)만 보여요");
  assert.equal(readableReason("UNAUTHORIZED: key"), null);
});

test("시장 커버: 오류 이유가 섞이거나 일부만 ok면 지금 표 그대로", () => {
  const o = optionsView(OPTIONS);
  const mixed = coverageView({ ...COVERAGE, rows: [
    row("20s", "f", "error", KOSIS_ERROR), row("20s", "m", "error", "KOSIS를 읽지 못함 (RATE_LIMITED: 한도)"),
  ] }, o.attributes);
  assert.equal(mixed.kind, "ok");
  const partly = coverageView({ ...COVERAGE, rows: [
    row("20s", "f", "ok", ""), row("20s", "m", "error", KOSIS_ERROR), row("30s", "f", "error", KOSIS_ERROR),
  ] }, o.attributes);
  assert.equal(partly.kind, "ok");
  if (partly.kind !== "ok") return;
  assert.equal(partly.rows.length, 3);
  assert.equal(partly.rows[0].buyers, "10명"); // 인구가 있는 줄도 지수가 없으면 구매자 수와 이유만
  assert.equal(partly.rows[0].note, "지수를 내지 않았어요");
});

test("이벤트 제안: 기준 미달 후보는 '제외한 후보 n건'으로 접는다", () => {
  const v = proposalsView({ ...PROPOSALS, excluded: [
    { target_label: "20대 · 여성", item_label: "시·에세이", reason: "작년 같은 달 검색 배수 0.52배 (기준 1.00배 미만)" },
    { target_label: "10대", item_label: "만화", reason: "too few" },
  ] });
  assert.ok(v.kind === "ok");
  if (v.kind !== "ok") return;
  assert.deepEqual(v.excluded, { title: "제외한 후보 2건", rows: ["20대 · 여성 · 시·에세이 — 작년 같은 달 검색 배수 0.52배 (기준 1.00배 미만)", "10대 · 만화"] });
  const none = proposalsView(PROPOSALS); // 예전 서버(excluded 없음)
  assert.ok(none.kind === "ok" && none.excluded === null);
});

test("다른 회사(가짜 게임사) 설정이면 화면 글자에 '책'이 나오지 않는다", () => {
  const game: SegmentOptionsOutput = {
    attributes: [{ key: "age_band", label: "연령대", values: [{ code: "20s", label: "20대" }] }, { key: "gender", label: "성별", values: [{ code: "male", label: "남성" }] }],
    genres: [{ code: "rpg", label: "RPG" }], categories: [{ code: "item", label: "게임 아이템" }],
    quick_questions: [{ label: "20대 남성", filters: { age_band: "20s", gender: "male" } }], event_kinds: [{ code: "pack", label: "아이템 패키지" }],
    periods: ["30d", "90d", "365d"], population_keys: [["age_band", "gender"]], min_group_size: 3, data_source: null,
    catalog_id: "pixelforge", catalog_version: "game-1", item_noun: "게임",
  };
  const o = optionsView(game);
  assert.equal(o.itemNoun, "게임");
  const fav = favoritesView({
    ...FAVORITES, filter_label: "20대 · 남성", summary: "",
    internal: [
      { kind: "genre", code: "rpg", label: "RPG", share: 0.8, lift: 2.4, buyers: 8, suppressed: false },
      { kind: "product", code: "rpg_pack", label: "rpg pack", share: 0.8, lift: 2.4, buyers: 8, suppressed: false },
    ],
    external: [{ source_id: "store", label: "앱 마켓 인기 순위", status: "key_missing", reason: "", period_label: "", condition_label: "", items: [] }],
  }, o.itemNoun);
  const q = questionView({ status: "ok", filters: { age_band: "20s", gender: "male" }, genre_code: "rpg", period: null,
    understood_label: "20대 · 남성 · RPG · 최근 90일", reason: "" }, o);
  const shown = JSON.stringify([o.attributes, o.chips, o.targets, o.periods, o.coverage, o.itemNoun, fav, q]);
  assert.equal(shown.includes("책"), false);
  assert.equal(shown.includes("대출"), false);
  assert.ok(fav.kind === "ok" && fav.products.title === "많이 산 게임");
  // 품목 이름이 없는 예전 서버면 일반 낱말
  assert.equal(optionsView(OPTIONS).itemNoun, "상품");
});

test("누가 사나: 대상 vs 전체 막대와 배수, 표본 부족·모름, 가짜 데이터 배지", () => {
  const v = profileView(PROFILE);
  assert.equal(v.kind, "ok");
  if (v.kind !== "ok") return;
  assert.equal(v.title, "AI 활용은(는) 누가 사나");
  assert.equal(v.header, "AI 활용 구매자 300명 · 전체 5,000명 (최근 3개월)");
  const [t] = v.tables;
  assert.equal(t.label, "직업군");
  assert.deepEqual(t.rows[0], { label: "직장인", target: "58%", targetPct: 58, overall: "39%", lift: "1.5배", high: true, suppressed: false });
  assert.equal(t.rows[1].high, false);
  assert.equal(t.rows[2].target, "표본 부족");
  assert.equal(t.unknown, "직업군 모름 12명");
  assert.equal(v.badge, "가짜 데이터로 본 결과");
  noCodes(v, ["office", "self", "farm", "job", "g_ai"]);
});

test("insufficient는 숫자 없이 이유와 품질 사실(한글만)", () => {
  const v = profileView({ ...PROFILE, status: "insufficient", insufficient_reasons: ["데이터 부족: 마지막 수집이 2시간 전"], attributes: [], target_buyers: null });
  assert.equal(v.kind, "gate");
  if (v.kind !== "gate") return;
  assert.deepEqual(v.reasons, ["데이터 부족: 마지막 수집이 2시간 전"]);
  assert.deepEqual(v.facts, ["기간 안 주문 행 1,234개", "버린 행 2개 (가격 없음 2개)", "이름이 없는 장르 1개", "설정에 없는 고객 속성 값 1개"]);
  const empty = coverageView({ ...COVERAGE, status: "insufficient", insufficient_reasons: [], rows: [] });
  assert.ok(empty.kind === "gate" && empty.reasons[0] === "데이터가 부족해 계산하지 않았어요");
  assert.equal(favoritesView({ ...FAVORITES, status: "insufficient" }).kind, "gate");
  assert.equal(proposalsView({ ...PROPOSALS, status: "insufficient" }).kind, "gate");
});

test("구성 지수: 지수 높은 묶음이 위, 표본 부족·응답 안 함은 아래, 칸·막대·색", () => {
  const v = coverageView(COVERAGE, optionsView(OPTIONS).attributes);
  assert.equal(v.kind, "ok");
  if (v.kind !== "ok") return;
  assert.deepEqual(v.rows.map((r) => r.label), ["20대 여성", "30대 남성", "60대 여성", "답하지 않음 · 여성"]);
  assert.deepEqual(v.rows[0], {
    label: "20대 여성", bands: "20~24세 + 25~29세", buyers: "1,180명", buyerShare: "50%", populationShare: "30%",
    buyerPct: 50, populationPct: 30, index: "167", tone: "high", note: null,
  });
  assert.equal(v.rows[1].tone, "normal"); // 지수 80은 빨강이 아니다 (80 미만만)
  assert.deepEqual([v.rows[2].buyers, v.rows[2].index, v.rows[2].buyerShare], ["표본 부족", "-", "-"]);
  assert.equal(v.rows[3].note, "인구 통계에 없는 값이라 비율을 내지 않음");
  assert.equal(v.rows[3].index, "-");
  assert.equal(JSON.stringify(v).includes("커버"), false); // 커버 비율 칸은 화면에서 뺐다
  noCodes(v, ["age_band", "gender", "no_answer", "20s"]);
});

test("구성 지수: 120 이상 초록, 80 미만 빨강, 그 사이 기본색", () => {
  assert.deepEqual([indexTone(120), indexTone(119.9), indexTone(80), indexTone(79.9), indexTone(null)],
    ["high", "normal", "normal", "low", "normal"]);
});

test("구성 지수 출처 한 줄: 기준 달은 서버 문자열 하나만 ('기준'·'년'이 겹치지 않음)", () => {
  const v = coverageView(COVERAGE, optionsView(OPTIONS).attributes);
  assert.ok(v.kind === "ok");
  if (v.kind !== "ok") return;
  assert.equal(v.source,
    "출처: 행정안전부 주민등록인구통계(KOSIS) · 2026년 10월 말 · 20세 이상 · 각 묶음은 5세별 구간 합산 · "
    + "비중의 분모는 연령대·성별을 답한 구매자와 20세 이상 인구 · '답하지 않음'은 인구에 없어 지수를 내지 않음");
  assert.equal(/기준/.test(v.source!), false);
  assert.equal(v.source!.match(/년/g)?.length, 1); // base_year(2025)를 따로 붙이지 않는다
  // 인구를 하나도 못 붙였으면 출처 한 줄도 없다
  assert.equal(coverageSource({ ...COVERAGE, rows: COVERAGE.rows.map((r) => ({ ...r, population_source: "" })) }), null);
});

test("이벤트 제안: 근거 3개·효과 범위·가정·이미 넘김·지난 예상 vs 실제", () => {
  const v = proposalsView(PROPOSALS);
  assert.equal(v.kind, "ok");
  if (v.kind !== "ok") return;
  assert.equal(v.month, "12월");
  const [p] = v.items;
  assert.equal(p.title, "12월 · 직장인 · AI 활용서 · 15% 쿠폰");
  assert.deepEqual(p.reasons.map((r) => r.title), ["누구", "언제", "무엇"]);
  assert.equal(p.effect, "주문 +120건~180건 · 매출 +2,724,000원~4,086,000원");
  assert.equal(p.assumption, true);
  assert.equal(p.basis, "전환율 6%~9% · 가정값");
  assert.equal(p.cost, "할인 비용 408,600원~612,900원");
  assert.equal(p.sent, "이미 넘김 · PROP-12");
  assert.deepEqual(p.past, ["PROP-3 · 예상 1,000,000원~2,000,000원 · 실제 1,500,000원"]);
  const shown = { ...p, id: "" };
  noCodes(shown, ["evp_ab12cd", "coupon", "g_ai", "office", "assumption", "past_campaign"]);
  const none = proposalsView({ ...PROPOSALS, proposals: [] });
  assert.ok(none.kind === "ok" && none.empty === "이번 달에 낼 만한 제안이 없어요");
});

test("이벤트 제안: 묶음 할인 제안은 첫 근거 제목이 '함께 사는 것'", () => {
  const [base] = PROPOSALS.proposals;
  const bundle = {
    ...base, genre_code: null, item_label: "소설 + 에세이",
    bundle: { a_code: "novel", a_label: "소설", b_code: "essay", b_label: "에세이", lift: 1.44, together_buyers: 42 },
  };
  const v = proposalsView({ ...PROPOSALS, proposals: [bundle, base] });
  assert.ok(v.kind === "ok");
  if (v.kind !== "ok") return;
  assert.deepEqual(v.items[0].reasons.map((r) => r.title), ["함께 사는 것", "언제", "무엇"]);
  assert.deepEqual(v.items[1].reasons.map((r) => r.title), ["누구", "언제", "무엇"]); // 예전 응답(bundle 없음)
  noCodes({ ...v.items[0], id: "" }, ["novel", "essay"]);
});

const BASKET: BasketPairsOutput = {
  status: "ok", insufficient_reasons: [], level: "genre", period: "90d", filters: { age_band: "20s" },
  pairs: [
    { a_code: "g_novel", a_label: "소설", b_code: "g_essay", b_label: "에세이", together_buyers: 1180, a_buyers: 3200,
      b_buyers: 2100, confidence: 0.3688, lift: 1.44, suppressed: false },
    { a_code: "g_ai", a_label: "AI 활용", b_code: "g_essay", b_label: "에세이", together_buyers: 40, a_buyers: 900,
      b_buyers: 2100, confidence: 0.0444, lift: 1.1, suppressed: false },
    { a_code: "g_kids", a_label: "어린이", b_code: "g_ai", b_label: "AI 활용", together_buyers: null, a_buyers: null,
      b_buyers: null, confidence: null, lift: null, suppressed: true },
  ],
  basis: FAKE, quality: QUALITY, summary: "최근 90일 20대 구매자 …",
};

test("함께 사는 것: 쌍·둘 다 산 사람·비율·배수, 1.2배 이상만 초록, 숨김은 표본 부족", () => {
  const v = basketView(BASKET, optionsView(OPTIONS).attributes, "책");
  assert.equal(v.kind, "ok");
  if (v.kind !== "ok") return;
  assert.equal(v.header, "20대 · 함께 사는 장르 (최근 3개월)");
  assert.deepEqual(v.rows[0], { pair: "소설 + 에세이", together: "1,180명", confidence: "소설 구매자의 36.9%", lift: "1.4배", high: true, suppressed: false });
  assert.equal(v.rows[1].high, false);
  assert.deepEqual(v.rows[2], { pair: "어린이 + AI 활용", together: "표본 부족", confidence: "-", lift: "-", high: false, suppressed: true });
  assert.equal(v.badge, "가짜 데이터로 본 결과");
  noCodes(v, ["g_novel", "g_essay", "g_ai", "g_kids", "age_band", "20s"]);
  const product = basketView({ ...BASKET, level: "product", filters: {}, pairs: [] }, [], "게임");
  assert.ok(product.kind === "ok" && product.header === "전체 고객 · 함께 사는 게임 (최근 3개월)");
  assert.ok(product.kind === "ok" && product.empty === "전체 고객에서 함께 산 사람이 충분한 쌍이 없어요");
  assert.equal(basketNoun("product", "게임"), "게임");
  const gated = basketView({ ...BASKET, status: "insufficient", insufficient_reasons: ["데이터 부족: 저장된 고객 기록이 없음"] }, [], "책");
  assert.ok(gated.kind === "gate" && gated.reasons[0].startsWith("데이터 부족"));
});

test("기획에 넘기기 결과 글자", () => {
  assert.equal(sendResultText({ outcome: "created", proposal_ref: "PROP-20", reason: "" }), "기획에 넘김 · PROP-20");
  assert.equal(sendResultText({ outcome: "duplicate", proposal_ref: "PROP-20", reason: "" }), "이미 넘김 · PROP-20");
  assert.equal(sendResultText({ outcome: "rejected", proposal_ref: null, reason: "facts too many" }), "기획이 받지 않음");
});

test("skill이 없으면 준비 중, 다른 실패는 다시 시도 안내 (영문 코드 없이)", () => {
  assert.deepEqual(sectionState("데이터 AI가 요청을 처리하지 않았습니다 (FORBIDDEN_SKILL)"), { kind: "missing" });
  assert.deepEqual(sectionState("데이터 AI(8100)에서 분석 선택지를 받지 못했습니다 (403)"), { kind: "missing" });
  const failed = sectionState("Failed to fetch");
  assert.equal(failed.kind, "failed");
  assert.ok(failed.kind === "failed" && !/[A-Za-z]/.test(failed.message));
});

test("다음 달은 한국 날짜 기준", () => {
  assert.equal(nextMonth(new Date("2026-11-16T03:00:00Z")), "2026-12");
  assert.equal(nextMonth(new Date("2026-12-31T16:00:00Z")), "2027-02"); // 한국은 1월 1일
  assert.equal(nextMonth(new Date("2026-12-15T00:00:00Z")), "2027-01");
});

test("가짜 데이터 배지는 data_source가 있을 때만", () => {
  assert.equal(fakeBadge({ basis: BASIS }), null);
  assert.equal(fakeBadge({ basis: null }), null);
  assert.equal(fakeBadge({ basis: FAKE }), "가짜 데이터로 본 결과");
  assert.equal(fakeBadge({ data_source: "가짜" }), "가짜 데이터로 본 결과");
});

test("맨 위 안내: 가짜 데이터일 때만 '시연용 가상 데이터', 실제 판매 기록이면 공공 데이터 안내만", () => {
  assert.equal(dataNote(optionsView(OPTIONS)), "고객 속성은 시연용 가상 데이터, 바깥 시장 숫자는 공공 데이터");
  const real = dataNote(optionsView({ ...OPTIONS, data_source: null }));
  assert.equal(real, "바깥 시장 숫자는 공공 데이터");
  assert.equal(real!.includes("가상"), false);
  assert.equal(dataNote(null), null); // 선택지를 못 받았으면 아무 말도 하지 않는다
});

// 재구매·이탈: 기준 시각 2026-11-16 12:00 (한국)
const RET_NOW = new Date("2026-11-16T03:00:00Z");
const RETENTION: RetentionOutput = {
  status: "ok", insufficient_reasons: [], filters: {}, churn_days: 60, basis: BASIS, quality: QUALITY,
  summary: "첫 구매 달 3개 중 2개 달 재구매율 계산.",
  cohorts: [
    { cohort_month: "2026-08", customers: 1240, r30: 0.18, r60: 0.27, r90: null, suppressed: false },
    { cohort_month: "2026-09", customers: 1410, r30: null, r60: null, r90: null, suppressed: false },
    { cohort_month: "2026-10", customers: null, r30: null, r60: null, r90: null, suppressed: true },
  ],
  at_risk: [
    { filters: { age_band: "40s", gender: "m" }, label: "40대 · 남성 · 직장인", regulars: 445, at_risk: 138, share: 0.31,
      top_item_label: "경제경영", suppressed: false },
    { filters: { age_band: "50s" }, label: "50대 이상 · 여성", regulars: 216, at_risk: 41, share: 0.19, top_item_label: null, suppressed: false },
  ],
};

test("다시 사나: 기간이 남은 칸은 '아직', 다 지났는데 비면 '표본 부족'", () => {
  assert.equal(cohortDone("2026-08", 90, RET_NOW), false); // 9/1 + 90일 = 11/30
  assert.equal(cohortDone("2026-08", 60, RET_NOW), true); // 9/1 + 60일 = 10/31
  assert.equal(cohortDone("2026-12", 30, new Date("2027-01-31T15:00:00Z")), true); // 해를 넘김 (1/1 + 30일 = 1/31 0시 한국)
  const v = retentionView(RETENTION, RET_NOW);
  assert.equal(v.kind, "ok");
  if (v.kind !== "ok") return;
  assert.deepEqual(v.columns, ["30일 안", "60일 안", "90일 안"]);
  assert.deepEqual(v.cohorts[0], { month: "8월", customers: "1,240명", cells: ["18%", "27%", "아직"], suppressed: false });
  assert.deepEqual(v.cohorts[1].cells, ["표본 부족", "아직", "아직"]); // 9월 30일 칸은 10/31에 다 지남 → 서버가 숨김
  assert.deepEqual(v.cohorts[2], { month: "10월", customers: "표본 부족", cells: ["-", "-", "-"], suppressed: true });
});

test("떠나나: 묶음 수와 비율·주로 사던 것, 기준 일수는 가정으로", () => {
  const v = retentionView(RETENTION, RET_NOW);
  assert.ok(v.kind === "ok");
  if (v.kind !== "ok") return;
  assert.equal(v.riskTitle, "떠날 것 같은 단골 (60일 넘게 안 삼)");
  assert.deepEqual(v.atRisk[0], { label: "40대 · 남성 · 직장인", count: "138명", detail: "단골의 31%가 60일 넘게 안 삼 · 주로 사던 것: 경제경영" });
  assert.equal(v.atRisk[1].detail, "단골의 19%가 60일 넘게 안 삼");
  assert.equal(v.riskEmpty, null);
  assert.ok(v.note.includes("60일은 회사 설정(가정)"));
  const none = retentionView({ ...RETENTION, at_risk: [] }, RET_NOW);
  assert.ok(none.kind === "ok" && none.riskEmpty === "보여 줄 만큼 큰 묶음이 없어요 (표본 부족)");
  noCodes(v, ["age_band", "40s", "50s"]);
  const gated = retentionView({ ...RETENTION, status: "insufficient", insufficient_reasons: ["데이터 부족: 저장된 고객 기록이 없음"] }, RET_NOW);
  assert.ok(gated.kind === "gate" && gated.reasons[0].includes("고객 기록"));
});
