// node --test (npm test). 보고서 한눈에 읽기 계산 (lib/trendReportRead.ts): 섹션 한 줄 요약 · 기본 펼침 · 핵심 숫자 · 재료 · 검색 표
import assert from "node:assert/strict";
import { test } from "node:test";
import type { TrendMaterialKind, TrendReport } from "./data.ts";
import { TREND_REPORT_EXAMPLE } from "./trendReportExample.ts";
import { TREND_REPORT_TR3F0D as R } from "./trendReportExampleRead.ts";
import {
  briefLine, defaultOpenSections, isOpen, keyNumber, keyNumbers, searchTable, sectionOfEvidence, sectionSummary,
} from "./trendReportRead.ts";
import { MATERIAL_LABEL, aiPickMode, keyEvidence, recommendationItems } from "./trendReportView.ts";

test("sectionSummary 검색: 상위 2개 키워드 변화와 'N개는 값 없음'", () => {
  assert.equal(sectionSummary("search", R), "소설 +361.3% · 다꾸 +6.9% · 3개는 값 없음");
});

test("sectionSummary 해시태그: 1위 태그와 계정 수 (새로 뜸이면 붙임)", () => {
  assert.equal(sectionSummary("hashtags", R), "#북토크 새로 뜸 · 2곳 사용");
});

test("sectionSummary 콘텐츠: 게시물 수와 최고 배수", () => {
  assert.equal(sectionSummary("content", R), "갑자기 뜬 게시물 6개 · 최고 ×14.8");
});

test("sectionSummary 경쟁사: 곳 수 · 최고 게시물이 릴스인 곳 수 · 릴스 조회 중앙값 최고 계정", () => {
  assert.equal(sectionSummary("competitors", R), "6곳 · 4곳이 최고 게시물로 릴스 · 릴스 조회 중앙값 최고 80,006 (민음사)");
});

test("sectionSummary 한계: 주의 개수와 호출 수 (limit_groups 없으면 limits 수)", () => {
  assert.equal(sectionSummary("limits", R), "주의 8개 · 호출 인스타 31 · 네이버 8 · AI 3");
  const old = { ...R, limit_groups: undefined, limits: ["a", "b"] };
  assert.equal(sectionSummary("limits", old), "주의 2개 · 호출 인스타 31 · 네이버 8 · AI 3");
});

test("sectionSummary: 섹션이 known이 아니면 상태 글자, 빈 섹션은 없다는 말 (지어낸 숫자 없음)", () => {
  assert.equal(sectionSummary("hashtags", TREND_REPORT_EXAMPLE), "자료 없음");
  const empty: TrendReport = { ...R, sections: { ...R.sections, content: { ...R.sections.content, posts: [] } } };
  assert.equal(sectionSummary("content", empty), "갑자기 뜬 게시물 없음");
  assert.equal(sectionSummary("theme", R), null); // 주제 후보 섹션이 없는 보고서
});

test("기본 펼침: 콘티 근거가 가리키는 섹션만 (검색·해시태그·콘텐츠), 경쟁사·한계는 접힘", () => {
  const open = defaultOpenSections(R.sections.recommendation);
  assert.deepEqual([...open].sort(), ["content", "hashtags", "search"]);
  // 예시 보고서: search:1 · content:1 · competitor:1 (근거 없는 칸은 무시)
  assert.deepEqual([...defaultOpenSections(TREND_REPORT_EXAMPLE.sections.recommendation)].sort(), ["competitors", "content", "search"]);
  // 근거 id가 없는 옛 저장본: 모두 접힘
  assert.equal(defaultOpenSections({ beats: [{ slot: "0-3", text: "a", evidence: ["b"] }] }).size, 0);
});

test("기본 펼침: 재료(materials)가 있으면 재료 근거가 가리키는 섹션을 쓴다", () => {
  const rec = {
    ...R.sections.recommendation,
    materials: [{ kind: "format" as const, text: "릴스", evidence: ["민음사"], evidence_items: [{ id: "competitor:1", text: "민음사", short: "민음사 ×3.7" }] }],
  };
  assert.deepEqual([...defaultOpenSections(rec)], ["competitors"]);
});

test("펼침 상태: 누른 값이 기본보다 먼저", () => {
  const d = new Set(["search"] as const);
  assert.equal(isOpen("search", d, {}), true);
  assert.equal(isOpen("search", d, { search: false }), false);
  assert.equal(isOpen("competitors", d, { competitors: true }), true);
  assert.equal(isOpen("limits", d, {}), false);
});

test("근거 id → 섹션", () => {
  assert.equal(sectionOfEvidence("search:2"), "search");
  assert.equal(sectionOfEvidence("shopping:1"), "search");
  assert.equal(sectionOfEvidence("hashtag:1"), "hashtags");
  assert.equal(sectionOfEvidence("meme:1"), "content");
  assert.equal(sectionOfEvidence("competitor:3"), "competitors");
  assert.equal(sectionOfEvidence("theme:1"), "theme");
  assert.equal(sectionOfEvidence("unknown:1"), null);
});

test("핵심 숫자: 짧은 근거에서 숫자를 크게, 나머지는 설명 한 줄", () => {
  assert.deepEqual(keyNumber({ id: "search:1", text: "t", short: "소설 검색 +361%" }), { id: "search:1", big: "+361%", label: "소설 검색", text: "t" });
  assert.deepEqual(keyNumber({ id: "content:3", text: "t", short: "민음사 릴스 좋아요 ×3.7" }).big, "×3.7");
  const tag = keyNumber({ id: "hashtag:1", text: "t", short: "#북토크 2곳 사용" });
  assert.equal(tag.big, "2곳");
  assert.equal(tag.label, "#북토크 사용");
  // 숫자가 없으면 통째로
  assert.deepEqual(keyNumber({ id: "meme:1", text: "t", short: "밈 책 더미" }), { id: "meme:1", big: "밈 책 더미", label: "", text: "t" });
});

test("재료 종류 7가지(#1597 계약 TrendMaterialKind) 모두 한국어 이름 (영문 그대로 나오는 종류 없음)", () => {
  const KINDS: TrendMaterialKind[] = ["hashtag", "format", "topic", "meme", "writing", "timing", "other"];
  assert.deepEqual(Object.keys(MATERIAL_LABEL).sort(), [...KINDS].sort());
  for (const k of KINDS) {
    const label = MATERIAL_LABEL[k];
    assert.ok(label && label.trim(), k);
    assert.match(label, /^[가-힣 ]+$/, `${k} → ${label}`);
  }
});

test("결론 카드: 재료가 있으면 재료만(콘티와 두 번 나오지 않음), 재료 없는 옛 보고서(beats만)는 콘티, 핵심 숫자는 그 칸들 근거", () => {
  // 옛 보고서: materials 없음·빈 목록 → 콘티
  assert.equal(aiPickMode(R.sections.recommendation), "beats");
  assert.equal(aiPickMode({ ...R.sections.recommendation, materials: [] }), "beats");
  assert.deepEqual(keyNumbers(keyEvidence(recommendationItems(R.sections.recommendation))).map((k) => k.big), ["+361%", "2곳", "×3.7"]);
  // 새 보고서: 재료(beats 비움) → 재료 한 곳, 핵심 숫자도 재료 근거에서
  const rec = {
    ...R.sections.recommendation, beats: [],
    materials: [{ kind: "format" as const, text: "릴스", evidence: ["민음사"], evidence_items: [{ id: "competitor:1", text: "민음사", short: "민음사 ×3.7" }] }],
  };
  assert.equal(aiPickMode(rec), "materials");
  assert.deepEqual(keyNumbers(keyEvidence(recommendationItems(rec))).map((k) => k.big), ["×3.7"]);
  assert.equal(aiPickMode({ ...rec, materials: [] }), "none");
});

test("검색 표: 키워드 한 줄씩, 값 없는 키워드는 맨 아래 한 줄로 합침", () => {
  const rows = searchTable(R.sections.search.rows);
  assert.deepEqual(rows.map((r) => r.keyword), ["소설", "다꾸", "트렌드 밈 · 시·에세이 · 경제경영"]);
  assert.deepEqual(rows[0], { key: "소설", no: 1, keyword: "소설", search: "+361.3%", up: true, shopping: "-12.7%", memo: "" });
  assert.equal(rows[1].shopping, "–");
  assert.equal(rows[1].memo, "쇼핑: 쇼핑 분야에 없음");
  assert.deepEqual({ no: rows[2].no, search: rows[2].search, memo: rows[2].memo }, { no: null, search: "–", memo: "검색량이 적어 네이버가 값을 안 줌" });
  // 쇼핑 값만 있는 키워드는 합치지 않는다 · 기준값 작음은 표시
  const ex = searchTable(TREND_REPORT_EXAMPLE.sections.search.rows);
  assert.ok(ex.some((r) => r.keyword === "예시 키워드2" && r.shopping === "0%" && r.search === "–"));
  assert.ok(ex.some((r) => r.keyword === "예시 키워드3" && r.search.startsWith("급증(기준값 작음)")));
  assert.ok(ex.some((r) => r.no === null && r.keyword === "예시 키워드5" && r.memo === "월 검색 10 미만"));
});

test("의뢰 한 줄 요약: 목표 · 누구에게 · 무엇을 · 주제 · 경쟁사 N곳 (빈 칸은 뺌)", () => {
  assert.deepEqual(briefLine(R.brief).map((x) => `${x.label} ${x.value}`), ["목표 브랜드 인지도", "누구에게 20·30대", "무엇을 광고 영상", "주제 트렌드 밈", "경쟁사 6곳"]);
  assert.deepEqual(briefLine(TREND_REPORT_EXAMPLE.brief).map((x) => x.value), ["브랜드 인지도", "여성 20·30대", "인스타 릴스 · 30초", "2곳"]);
  const bare = { ...R.brief, goal: null, target_ages: [], format: null, format_parts: [], theme: null, competitors: [] };
  assert.deepEqual(briefLine(bare), [{ label: "누구에게", value: "전체" }]);
});
