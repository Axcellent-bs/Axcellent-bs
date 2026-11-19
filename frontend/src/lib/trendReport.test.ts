// node --test로 돈다 (npm test). 트렌드 분석 의뢰 탭 주소와 보고서 화면 글자만 시험한다.
import assert from "node:assert/strict";
import { test } from "node:test";
import { TREND_TABS, trendTabFromHash, trendTabHash } from "./contentLoop.ts";
import { sectionState } from "./segmentView.ts";
import { TREND_REPORT_EXAMPLE } from "./trendReportExample.ts";
import { TREND_REPORT_PRESETS } from "./trendReportPresets.ts";
import {
  EMPTY_FORM, barPct, buildInput, hasInput, keepPolling, pctText, reportHash, reportIdFromHash, shareText, sortBeats, sourceLabel, targetText,
} from "./trendReportView.ts";

test("하위 탭 순서: 감지 · 인사이트 · 넘김 · 성과 · 분석 의뢰 · 데이터 출처", () => {
  assert.deepEqual(TREND_TABS.map((t) => t.title), ["감지", "인사이트", "넘김", "성과", "분석 의뢰", "데이터 출처"]);
  const request = TREND_TABS.find((t) => t.key === "request")!;
  assert.deepEqual(request.stages, []); // 루프 단계와 별개
  assert.equal(trendTabHash(request), "#request");
});

test("탭 주소 변환: #request · 보고서 id가 붙어도 분석 의뢰 탭, 다른 탭은 그대로", () => {
  assert.equal(trendTabFromHash("#request").key, "request");
  assert.equal(trendTabFromHash("#request?id=rpt_1").key, "request");
  assert.equal(trendTabFromHash("#sources").key, "sources");
  assert.equal(trendTabFromHash("#detect?view=instagram").key, "detect");
  assert.equal(trendTabFromHash("#trend/campaign").key, "result"); // 옛 주소
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

test("skill이 아직 없으면(403·FORBIDDEN_SKILL) 준비 중, 그 밖은 실패", () => {
  assert.deepEqual(sectionState("데이터 분석 AI에서 분석 의뢰를 받지 못했습니다 (403)"), { kind: "missing" });
  assert.deepEqual(sectionState("데이터 AI가 요청을 처리하지 않았습니다 (FORBIDDEN_SKILL)"), { kind: "missing" });
  assert.equal(sectionState("데이터 분석 AI에서 분석 의뢰를 받지 못했습니다 (500)").kind, "failed");
});
