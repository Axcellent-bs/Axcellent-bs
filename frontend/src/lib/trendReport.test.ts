// node --test로 돈다 (npm test). 트렌드 분석 의뢰 탭 주소와 보고서 화면 글자만 시험한다.
import assert from "node:assert/strict";
import { test } from "node:test";
import { TREND_TABS, trendTabFromHash, trendTabHash } from "./contentLoop.ts";
import { sectionState } from "./segmentView.ts";
import { TREND_REPORT_EXAMPLE } from "./trendReportExample.ts";
import { TREND_REPORT_PRESETS } from "./trendReportPresets.ts";
import type { TrendReportMaterial } from "./data.ts";
import {
  EMPTY_FORM, MATERIAL_LABEL, MATERIALS_NOTE, aiPickMode, recommendationItems, barPct, beatEvidence, beatWidths, buildInput, competitorSummary, evidenceDomId, evidenceExpanded, evidenceListClass, formFromBrief,
  hasInput, hashtagMeta, headerRows, keepPolling, keyEvidence, limitGroups, pctText, reportHash, reportIdFromHash, searchKind, searchMain,
  searchNotes, shareText, sortBeats, sortCompetitors, sortSearchRows, sourceLabel, sourceTag, targetText, traitLines, traitSentence,
} from "./trendReportView.ts";

test("하위 탭 순서: 감지 · 분석 의뢰 · 인사이트 · 기획 전달 · 성과 · 데이터 출처(오른쪽 끝)", () => {
  assert.deepEqual(TREND_TABS.map((t) => t.title), ["감지", "분석 의뢰", "인사이트", "기획 전달", "성과", "데이터 출처"]);
  assert.deepEqual(TREND_TABS.map((t) => t.key), ["detect", "request", "recommend", "handoff", "result", "sources"]);
  const request = TREND_TABS.find((t) => t.key === "request")!;
  assert.deepEqual(request.stages, []); // 루프 단계와 별개
  assert.equal(trendTabHash(request), "#request");
  // 데이터 출처만 오른쪽 끝(side), 루프 단계와 별개
  assert.deepEqual(TREND_TABS.filter((t) => t.side).map((t) => t.key), ["sources"]);
  assert.deepEqual(TREND_TABS.find((t) => t.key === "sources")!.stages, []);
  // 넘김 → 기획 전달: 주소는 #handoff 그대로
  assert.equal(trendTabHash(TREND_TABS.find((t) => t.title === "기획 전달")!), "#handoff");
});

test("탭 주소 변환: #request · 보고서 id가 붙어도 분석 의뢰 탭, 다른 탭은 그대로", () => {
  assert.equal(trendTabFromHash("#request").key, "request");
  assert.equal(trendTabFromHash("#request?id=rpt_1").key, "request");
  assert.equal(trendTabFromHash("#sources").key, "sources");
  assert.equal(trendTabFromHash("#detect?view=instagram").key, "detect");
  assert.equal(trendTabFromHash("#trend/campaign").key, "result"); // 옛 주소
  assert.equal(trendTabFromHash("#trend/video").key, "handoff"); // 옛 주소
  for (const key of ["detect", "request", "recommend", "handoff", "result", "sources"]) assert.equal(trendTabFromHash(`#${key}`).key, key);
  assert.equal(trendTabFromHash("#recommend?from=report").key, "recommend"); // 인사이트 필터
  assert.equal(trendTabFromHash("#nope").key, "detect");
  assert.equal(reportHash("rpt 1/a"), "#request?id=rpt%201%2Fa");
  assert.equal(reportIdFromHash(reportHash("rpt 1/a")), "rpt 1/a");
  assert.equal(reportIdFromHash("#request"), null);
  assert.equal(reportIdFromHash("#request?id="), null);
  assert.equal(reportIdFromHash("#detect?id=rpt_1"), null);
});

test("진행 폴링: 대기·만드는 중이면 계속, 완료·부분·실패·중단이면 멈춘다", () => {
  assert.equal(keepPolling("queued"), true);
  assert.equal(keepPolling("running"), true);
  for (const s of ["done", "partial", "failed", "interrupted"] as const) assert.equal(keepPolling(s), false, s);
});

test("% 표시: 소수 한 자리·부호, null이면 서버 이유", () => {
  const [a, b] = TREND_REPORT_EXAMPLE.sections.search.rows;
  assert.deepEqual(pctText(a.search_change_pct, a.search_reason), { text: "+12.3%", known: true });
  assert.deepEqual(pctText(a.shopping_change_pct, a.shopping_reason), { text: "-4%", known: true });
  assert.deepEqual(pctText(b.search_change_pct, b.search_reason), { text: "검색량이 적어 비교하지 않음", known: false });
  assert.deepEqual(pctText(b.shopping_change_pct, b.shopping_reason), { text: "0%", known: true });
  assert.deepEqual(pctText(null, null), { text: "값 없음", known: false });
  assert.equal(barPct(-6, 12), 50);
  assert.equal(barPct(null, 12), 0);
  assert.equal(barPct(30, 12), 100);
  assert.equal(shareText(0.62), "62%");
  assert.equal(shareText(null), null);
});

test("콘티 slot 순서: 0-3 → 3-15 → 15-25 → 25-30", () => {
  const sorted = sortBeats(TREND_REPORT_EXAMPLE.sections.recommendation.beats);
  assert.deepEqual(sorted.map((b) => b.slot), ["0-3", "3-15", "15-25", "25-30"]);
  assert.deepEqual(sorted[1].evidence, ["근거 예시 2", "근거 예시 3"]);
});

test("출처 라벨과 타깃 글자", () => {
  const s = TREND_REPORT_EXAMPLE.brief.sources;
  assert.equal(sourceLabel(s.product), "프리셋");
  assert.equal(sourceLabel(s.target), "의뢰문에서 추정");
  assert.equal(sourceLabel(s.competitors), "직접 입력");
  assert.equal(sourceLabel(null), "출처 모름");
  assert.equal(targetText("f", ["20", "30"]), "여성 20·30대");
  assert.equal(targetText(null, []), "전체");
});

test("의뢰 입력: 의뢰문이 있으면 request_text만, 없으면 직접 정한 칸 (프리셋 값 그대로)", () => {
  const pasted = buildInput({ ...EMPTY_FORM, requestText: "  친환경 화장품 릴스 의뢰  ", product: "무시됨" });
  assert.deepEqual(pasted, { caller: "human", request_text: "친환경 화장품 릴스 의뢰" });
  const eco = TREND_REPORT_PRESETS.find((p) => p.id === "eco_beauty")!;
  const direct = buildInput({
    ...EMPTY_FORM, preset: eco.id, product: eco.product, gender: "f", ages: ["30", "20"], format: eco.format,
    competitors: eco.competitors.join(", "), keywords: "리필 화장품, 비건 선크림, 리필 화장품",
  }, true);
  assert.deepEqual(direct, {
    caller: "human", rebuild: true, preset: "eco_beauty", product: "친환경 화장품 브랜드 인지도", target_gender: "f", target_ages: ["20", "30"],
    format: "인스타 릴스 30초", competitors: eco.competitors, keywords: ["리필 화장품", "비건 선크림"],
  });
  assert.equal(eco.competitors.length, 7);
  assert.equal(hasInput(EMPTY_FORM), false);
  assert.equal(hasInput({ ...EMPTY_FORM, keywords: "클린뷰티" }), true);
  assert.equal(buildInput({ ...EMPTY_FORM, gender: "all" }).target_gender, null);
});

test("v2 헤더 칩: input은 꼬리표 없음, 이름 없으면 @id만, 결과물은 쪼개지 않는다", () => {
  const b = TREND_REPORT_EXAMPLE.brief;
  const rows = headerRows(b);
  const by = (k: string) => rows.find((r) => r.key === k)!;
  assert.equal(by("product").sourceTag, "프리셋에서 채움");
  assert.equal(by("target").sourceTag, "의뢰문에서 추정");
  assert.equal(by("competitors").sourceTag, null);
  assert.deepEqual(by("target").chips.map((c) => c.text), ["여성", "20대", "30대"]);
  assert.deepEqual(by("format").chips.map((c) => c.text), ["인스타 릴스", "30초"]);
  assert.deepEqual(headerRows({ ...b, format_parts: [] }).find((r) => r.key === "format")!.chips.map((c) => c.text), ["인스타 릴스 30초"]);
  assert.deepEqual(by("competitors").chips, [{ text: "예시 브랜드A", sub: "@example_a" }, { text: "@example_b", sub: null }]);
  assert.equal(by("competitors").label, "경쟁사 2");
  assert.equal(sourceTag("input"), null);
  assert.equal(sourceTag(null), null);
});

test("v2 고쳐서 다시 만들기: 보고서 요약 → 폼 → 입력", () => {
  const form = formFromBrief(TREND_REPORT_EXAMPLE.brief);
  assert.equal(form.goal, "브랜드 인지도");
  const input = buildInput(form, true);
  assert.equal(input.rebuild, true);
  assert.equal(input.goal, "브랜드 인지도");
  assert.deepEqual(input.target_ages, ["20", "30"]);
  assert.deepEqual(input.competitors, ["example_a", "example_b"]);
});

test("v2 타임라인 폭 3:12:10:5, 근거 id → DOM id, 핵심 근거 칩(중복 제거·최대 3)", () => {
  const w = beatWidths(["0-3", "3-15", "15-25", "25-30"]);
  assert.deepEqual(w, [10, 40, (10 / 30) * 100, (5 / 30) * 100]);
  assert.equal(Math.round(w.reduce((a, b) => a + b, 0)), 100);
  assert.equal(evidenceDomId("search:1"), "tr-ev-search-1");
  const beats = sortBeats(TREND_REPORT_EXAMPLE.sections.recommendation.beats);
  assert.deepEqual(keyEvidence(beats).map((e) => e.id), ["search:1", "competitor:1", "content:1"]);
  assert.equal(keyEvidence(beats, 2).length, 2);
  assert.deepEqual(beatEvidence(beats[2]), []);
});

test("v2 인쇄: 근거는 인쇄 중이면 펼친다", () => {
  assert.equal(evidenceExpanded(false, true), true);
  assert.equal(evidenceExpanded(false, false), false);
  assert.equal(evidenceListClass(false), "hidden print:block");
  assert.equal(evidenceListClass(true), "block");
});

test("v2 검색: 정렬(거른 값 → 기준값 작음 → 전체 → 검색량만 → 없음)과 표시", () => {
  const base = { search_reason: null, shopping_change_pct: null, shopping_reason: null };
  const rows = [
    { keyword: "n", search_change_pct: null, ...base, basis: "none" as const },
    { keyword: "v", search_change_pct: null, ...base, basis: "volume_only" as const, monthly_volume: 1200 },
    { keyword: "a", search_change_pct: 50, ...base, basis: "all" as const },
    { keyword: "s", search_change_pct: 300, ...base, basis: "filtered" as const, small_base: true },
    { keyword: "f1", search_change_pct: 5, ...base, basis: "filtered" as const },
    { keyword: "f2", search_change_pct: 20, ...base, basis: "filtered" as const },
    { keyword: "old", search_change_pct: 1, ...base },
  ];
  assert.deepEqual(sortSearchRows(rows).map((r) => r.keyword), ["f2", "f1", "old", "s", "a", "v", "n"]);
  assert.equal(searchKind({ ...rows[6], keyword: "x", search_change_pct: null }), "none");
  const s = searchMain(rows[3]);
  assert.deepEqual([s.text, s.small, s.bar], ["급증(기준값 작음)", "+300%", null]);
  assert.equal(searchMain(rows[1]).text, "월 검색 1,200");
  const who = { target_gender: "f" as const, target_ages: ["20", "30"] };
  assert.deepEqual(searchNotes({ ...rows[2], shopping_change_pct: 9, monthly_volume: 12300 }, who),
    ["월 검색 12,300", "쇼핑 클릭(여성 20·30대) +9%", "여성 20·30대로 거르면 값이 없음 → 전체 기준"]);
  // 쇼핑 값이 비면 백엔드가 나눈 이유를 그대로 (한 문장으로 뭉뚱그리지 않음)
  assert.deepEqual(searchNotes({ ...rows[0], shopping_change_pct: null, shopping_reason: "그 분야에 없는 키워드" }, who),
    ["쇼핑: 그 분야에 없는 키워드"]);
});

test("v2 해시태그 메타와 경쟁사 문장·요약", () => {
  const r = { tag: "x", accounts: 7, previous_accounts: 1, burst_posts: 2, used_by: ["a", "b", "c"] };
  assert.equal(hashtagMeta(r, { a: "톤28", b: "비플레인" }), "톤28 · 비플레인 · @c 외 4곳 · 터진 게시물 2");
  assert.equal(traitSentence({ label: "릴스 비율", value: "100%", baseline: "평소 35%" }), "잘 된 건 전부 릴스 (평소 35%)");
  assert.equal(traitSentence({ label: "릴스 비율", value: "80%", baseline: "평소 35%" }), "잘 된 건 릴스 80% (평소 35%)");
  assert.equal(traitSentence({ label: "첫 줄 질문", value: "3/5", baseline: "평소 1/5" }), "첫 줄이 질문으로 시작 (3/5, 평소 1/5)");
  assert.equal(traitSentence({ label: "올린 시간", value: "밤 9~12시", baseline: "평소 28%" }), "밤 9~12시에 올림 (평소 28%)");
  assert.equal(traitSentence({ label: "캡션 길이", value: "120자", baseline: "평소 60자" }), "캡션 120자 (평소 60자)");
  const rows = TREND_REPORT_EXAMPLE.sections.competitors.rows;
  assert.deepEqual(traitLines(rows[0])[0], "잘 된 건 전부 릴스 (평소 35%)");
  assert.deepEqual(traitLines({ ...rows[0], traits_structured: [] }), ["첫 장면에 손", "자막 크게"]);
  assert.equal(competitorSummary(rows), "경쟁사 2곳 중 1곳의 최고 게시물이 릴스");
  assert.deepEqual(sortCompetitors([rows[1], rows[0]]).map((x) => x.account), ["example_a", "example_d"]);
  assert.equal(competitorSummary([rows[1]]), null);
});

test("v2 근거와 한계: 묶음이 없으면 null(옛 목록으로)", () => {
  const g = limitGroups(TREND_REPORT_EXAMPLE)!;
  assert.deepEqual(g.map((x) => x.label), ["계산 방식", "볼 수 없는 것"]);
  assert.equal(limitGroups({}), null);
  assert.equal(limitGroups({ limit_groups: { method: [], cannot_see: [], this_report: [] } }), null);
});

test("skill이 아직 없으면(403·FORBIDDEN_SKILL) 준비 중, 그 밖은 실패", () => {
  assert.deepEqual(sectionState("데이터 분석 AI에서 분석 의뢰를 받지 못했습니다 (403)"), { kind: "missing" });
  assert.deepEqual(sectionState("데이터 AI가 요청을 처리하지 않았습니다 (FORBIDDEN_SKILL)"), { kind: "missing" });
  assert.equal(sectionState("데이터 분석 AI에서 분석 의뢰를 받지 못했습니다 (500)").kind, "failed");
});

// ---- 기획에 넘길 재료 (11/23): 시간별 콘티 대신. 옛 보고서(콘티만)는 그대로

const MATERIALS: TrendReportMaterial[] = [
  { kind: "hashtag", text: "#북토크 해시태그를 쓴다", evidence: ["#북토크 새로 뜸"], evidence_items: [{ id: "hashtag:1", text: "#북토크 새로 뜸", short: "#북토크 새로 뜸" }] },
  { kind: "format", text: "릴스 형식으로", evidence: ["민음사 릴스 좋아요 평소의 3.7배"], evidence_items: [{ id: "content:1", text: "민음사 릴스 좋아요 평소의 3.7배", short: "민음사 릴스 ×3.7" }] },
  { kind: "topic", text: "소설 주제로", evidence: ["옛 근거 문장"] },
  { kind: "writing", text: "첫 줄을 질문으로", evidence: [], evidence_items: [{ id: "hashtag:1", text: "#북토크 새로 뜸", short: "#북토크 새로 뜸" }] },
];

test("재료가 있으면 재료 4칸, 재료 없이 콘티만 있는 옛 보고서는 콘티, 둘 다 없으면 없음", () => {
  const old = TREND_REPORT_EXAMPLE.sections.recommendation;
  assert.equal(aiPickMode({ ...old, materials: MATERIALS }), "materials");
  assert.equal(aiPickMode(old), "beats"); // 옛 보고서 예시: materials 칸이 없다
  assert.equal(aiPickMode({ ...old, materials: [] }), "beats");
  assert.equal(aiPickMode({ ...old, beats: [], materials: [] }), "none");
  assert.deepEqual(MATERIALS.map((m) => MATERIAL_LABEL[m.kind]), ["해시태그", "형식", "관심 주제", "글쓰기"]);
  assert.equal(MATERIALS_NOTE, "시간별 장면은 기획이 짜요");
});

test("핵심 근거 칩·대화 카드 근거: 재료가 있으면 재료 순서, 옛 보고서는 시간 순서 콘티", () => {
  const old = TREND_REPORT_EXAMPLE.sections.recommendation;
  const withMaterials = { ...old, materials: MATERIALS };
  assert.deepEqual(keyEvidence(recommendationItems(withMaterials)).map((e) => e.short), ["#북토크 새로 뜸", "민음사 릴스 ×3.7"]);
  assert.deepEqual(recommendationItems(old), sortBeats(old.beats));
  assert.deepEqual(keyEvidence(recommendationItems(old)), keyEvidence(sortBeats(old.beats)));
  assert.deepEqual(beatEvidence(MATERIALS[2]), [{ id: null, text: "옛 근거 문장" }]); // 근거 id 없는 옛 문장은 그대로
});
