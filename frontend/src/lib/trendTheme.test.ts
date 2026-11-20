// node --test로 돈다 (npm test). #1540 주제 후보: 보고서 섹션 보이는 조건 · 출처 글자 · 진행 칸 수 · 카드·폼이 주제를 넘기는지.
// 픽스처 모양은 백엔드 tests/test_trend_report_theme.py(#1540)의 결과 그대로 (sections.theme · brief.theme·theme_mode).
import assert from "node:assert/strict";
import { test } from "node:test";
import type { TrendReport, TrendThemeSection } from "./data.ts";
import { REQUEST_CARD_EXAMPLE } from "./chatCardsExample.ts";
import { cardInput, formFromCard, progressPct, requestCardRows, sectionCount } from "./chatCards.ts";
import { TREND_REPORT_EXAMPLE } from "./trendReportExample.ts";
import {
  EMPTY_FORM, SECTION_STATUS, SOURCE_LABEL, buildInput, countedSections, formFromBrief, hasInput, headerRows, showThemeSection, sourceLabel,
  sourceTag, themeInput, themeRowView,
} from "./trendReportView.ts";

const THEME: TrendThemeSection = {
  status: "known", reason: null, theme: "아이돌", target_word: "여성 10대",
  rows: [
    { name: "에스파", level: 100, change_pct: null, seen_in: ["llm"], products: [], basis: "네이버 검색어 트렌드 · 여성 10대 · 최근 28일 (기준 '아이돌')" },
    { name: "뉴진스", level: 90, change_pct: 41.2, seen_in: ["radar"], products: ["뉴진스 포토북"], basis: "네이버 검색어 트렌드 · 여성 10대 · 최근 28일 (기준 '아이돌')" },
    { name: "아이브", level: 60, change_pct: -3, seen_in: ["youtube", "radar"], products: [], basis: "" },
  ],
  dropped: [{ name: "없는그룹", reason: "검색량이 적어 데이터랩 값이 없음" }],
  method: "네이버 검색어 트렌드(타깃 성별·나이 필터)로 후보마다 최근 28일 평균을 보고…",
};

/** find 의뢰 보고서 (#1540 결과 모양) */
function findReport(over: Partial<TrendReport["brief"]> = {}, theme: TrendThemeSection | undefined = THEME): TrendReport {
  const base = TREND_REPORT_EXAMPLE;
  return {
    ...base,
    brief: {
      ...base.brief, theme: "아이돌", theme_mode: "find", keywords: ["에스파", "뉴진스", "아이돌", "소설"],
      sources: { ...base.brief.sources, keywords: "theme", theme: "request_text" }, ...over,
    },
    sections: { ...base.sections, theme },
  };
}

test("출처 글자: company는 '회사 정보로 채움', theme은 '주제 후보에서' (출처 모름이 아님)", () => {
  assert.equal(SOURCE_LABEL.company, "회사 정보로 채움");
  assert.equal(SOURCE_LABEL.theme, "주제 후보에서");
  assert.equal(sourceLabel("company"), "회사 정보로 채움");
  assert.equal(sourceTag("company"), "회사 정보로 채움");
  assert.equal(sourceTag("theme"), "주제 후보에서");
  assert.deepEqual(SECTION_STATUS.skipped, { label: "해당 없음", tone: "muted" });
});

test("주제 후보 섹션은 find일 때만 보인다 (use·skipped·주제 없음·예전 저장본은 숨김)", () => {
  assert.equal(showThemeSection(findReport()), true);
  assert.equal(showThemeSection(findReport({ theme_mode: "use" })), false);
  assert.equal(showThemeSection(findReport({}, { ...THEME, status: "skipped" })), false);
  assert.equal(showThemeSection(findReport({ theme: null, theme_mode: null })), false);
  // 예전 저장본: sections.theme 자체가 없거나, 기본값(waiting · theme null)이고 brief에 주제 없음
  assert.equal(showThemeSection(TREND_REPORT_EXAMPLE), false);
  const old = { ...TREND_REPORT_EXAMPLE, sections: { ...TREND_REPORT_EXAMPLE.sections, theme: { ...THEME, status: "waiting" as const, theme: null, rows: [] } } };
  assert.equal(showThemeSection(old), false);
  // unknown·failed여도 find면 섹션은 보이고 서버 이유를 보여 준다
  assert.equal(showThemeSection(findReport({}, { ...THEME, status: "unknown", reason: "데이터랩에서 값이 나온 후보가 없음", rows: [] })), true);
});

test("진행 칸 수: 보통 5, 주제 후보가 보이면 6 (하드코딩 5 없음)", () => {
  assert.equal(sectionCount(TREND_REPORT_EXAMPLE), 5);
  assert.equal(sectionCount(findReport()), 6);
  assert.deepEqual(countedSections(findReport())[0], "theme");
  assert.equal(sectionCount(findReport({ theme_mode: "use" })), 5);
  const running = (r: TrendReport): TrendReport => ({ ...r, status: "running" });
  const fresh = findReport();
  const sections = { ...fresh.sections, recommendation: { ...fresh.sections.recommendation, status: "running" as const } };
  // 6칸 중 5칸 끝남 → 83%, 5칸 보고서에서 4칸 끝남 → 80%
  assert.equal(progressPct(running({ ...fresh, sections })), 83);
  const plain = { ...TREND_REPORT_EXAMPLE.sections, recommendation: { ...TREND_REPORT_EXAMPLE.sections.recommendation, status: "running" as const } };
  assert.equal(progressPct(running({ ...TREND_REPORT_EXAMPLE, sections: plain })), 80);
});

test("머리 줄: 주제 줄(값 + 찾아서 순위 + 출처), 주제 후보에서 온 키워드 앞 2개만 ★", () => {
  const rows = headerRows(findReport().brief, THEME);
  const theme = rows.find((r) => r.key === "theme");
  assert.ok(theme);
  assert.deepEqual(theme.chips.map((c) => c.text), ["아이돌", "찾아서 순위"]);
  assert.equal(theme.sourceTag, "의뢰문에서 추정");
  assert.equal(rows[1].key, "theme"); // 제품 다음
  const kw = rows.find((r) => r.key === "keywords");
  assert.deepEqual(kw?.chips.map((c) => [c.text, !!c.star]), [["에스파", true], ["뉴진스", true], ["아이돌", false], ["소설", false]]);
  assert.equal(kw?.sourceTag, "주제 후보에서");
  const use = headerRows({ ...findReport().brief, theme: "캠핑", theme_mode: "use", sources: { ...findReport().brief.sources, keywords: "request_text" } });
  assert.deepEqual(use.find((r) => r.key === "theme")?.chips.map((c) => c.text), ["캠핑", "그대로 씀"]);
  assert.ok(!headerRows(TREND_REPORT_EXAMPLE.brief).some((r) => r.key === "theme")); // 주제 없으면 줄 없음
});

test("표 한 줄: 변화 없으면 –, 함께 본 곳 칩 이름, 상품 없으면 –", () => {
  assert.deepEqual(themeRowView(THEME.rows[0]), { name: "에스파", level: 100, change: "–", seenIn: ["AI 제안"], products: "–" });
  assert.deepEqual(themeRowView(THEME.rows[1]), { name: "뉴진스", level: 90, change: "+41.2%", seenIn: ["인스타 레이더"], products: "뉴진스 포토북" });
  assert.deepEqual(themeRowView(THEME.rows[2]).seenIn, ["YouTube 인기", "인스타 레이더"]);
  assert.equal(themeRowView(THEME.rows[2]).change, "-3%");
});

test("대화 카드: 주제 줄 '아이돌 · 찾아서 순위', 접수·고치기가 theme·mode를 넘긴다", () => {
  const card = { ...REQUEST_CARD_EXAMPLE, brief: { ...REQUEST_CARD_EXAMPLE.brief, theme: "아이돌", theme_mode: "find" as const } };
  const rows = requestCardRows(card);
  assert.deepEqual(rows[1], { key: "theme", label: "주제", value: "아이돌 · 찾아서 순위", preset: false });
  const input = cardInput(card, "th_1");
  assert.equal(input.theme, "아이돌");
  assert.equal(input.theme_mode, "find");
  const form = formFromCard(card);
  assert.equal(form.theme, "아이돌");
  assert.equal(form.themeFind, true);
  assert.equal(buildInput(form).theme_mode, "find"); // 고쳐서 다시 보내도 주제가 남는다
  // 주제 없는 예전 카드는 줄도 칸도 없다
  assert.ok(!requestCardRows(REQUEST_CARD_EXAMPLE).some((r) => r.key === "theme"));
  assert.ok(!("theme" in cardInput(REQUEST_CARD_EXAMPLE, null)));
});

test("폼 주제: 체크 → find, 안 하면 use, 주제가 비면 둘 다 null", () => {
  assert.deepEqual(themeInput("아이돌", true), { theme: "아이돌", theme_mode: "find" });
  assert.deepEqual(themeInput(" 캠핑 ", false), { theme: "캠핑", theme_mode: "use" });
  assert.deepEqual(themeInput("  ", true), { theme: null, theme_mode: null });
  const f = { ...EMPTY_FORM, product: "책", theme: "아이돌", themeFind: true };
  assert.deepEqual([buildInput(f).theme, buildInput(f).theme_mode], ["아이돌", "find"]);
  assert.deepEqual([buildInput({ ...f, themeFind: false }).theme_mode], ["use"]);
  assert.ok(!("theme" in buildInput({ ...f, theme: "" })));
  assert.equal(hasInput({ ...EMPTY_FORM, theme: "아이돌" }), true); // 주제만 있어도 만들 수 있다
  // 보고서 손보기(formFromBrief)도 주제·쓰임을 채운다
  const back = formFromBrief(findReport().brief);
  assert.deepEqual([back.theme, back.themeFind], ["아이돌", true]);
  assert.deepEqual([formFromBrief(TREND_REPORT_EXAMPLE.brief).theme, formFromBrief(TREND_REPORT_EXAMPLE.brief).themeFind], ["", false]);
});
