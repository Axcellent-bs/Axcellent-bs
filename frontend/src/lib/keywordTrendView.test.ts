// node --test로 돈다 (npm test). 급상승 검색어 패널: 칸 나누기 · 출처 칩 · 판정 못 함 모드 · 예전 응답 호환 · 머리 줄 · 분석 의뢰 채우기.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { RisingKeyword } from "./data.ts";
import {
  CANDIDATE_LABEL, RISING_BASIS, candidateChips, judgedLine, levelOf, pickFirst, requestFormForKeyword, sourceShort, sparkline, splitRising, topRising,
} from "./keywordTrendView.ts";
import { buildInput } from "./trendReportView.ts";

const row = (keyword: string, over: Partial<RisingKeyword> = {}): RisingKeyword => ({
  keyword, rank: 1, status: "unknown", ratio: null, score: null, reason: null, category: null, volume: null, monthly_volume: null,
  volume_reason: null, estimate: null, increase: null, increase_reason: null, news_count: null, news_source: null, news: [], spread: null,
  products: null, product_names: [], products_reason: null, ...over,
});

test("칸 나누기: level rising · warming · 나머지(normal·unknown), 서버 순서 그대로", () => {
  const rows = [
    row("수능일", { level: "rising", status: "rising", ratio: 3.4 }), row("다이어리", { level: "warming", status: "normal", ratio: 1.8 }),
    row("영어사전", { level: "normal", status: "normal", ratio: 1.1 }), row("한능검", { level: "rising", status: "rising", ratio: 2.6 }),
    row("미니노트", { level: "unknown" }),
  ];
  const s = splitRising(rows);
  assert.deepEqual(s.rising.map((r) => r.keyword), ["수능일", "한능검"]);
  assert.deepEqual(s.warming.map((r) => r.keyword), ["다이어리"]);
  assert.deepEqual(s.rest.map((r) => r.keyword), ["영어사전", "미니노트"]);
  assert.equal(s.noJudgment, false);
});

test("예전 응답(level 없음)은 status로 대신: rising → 급상승, normal → 평범, unknown → 판정 못 함 (warming 없음)", () => {
  assert.equal(levelOf(row("a", { status: "rising" })), "rising");
  assert.equal(levelOf(row("b", { status: "normal" })), "normal");
  assert.equal(levelOf(row("c", { status: "unknown" })), "unknown");
  assert.equal(levelOf(row("d", { status: "normal", level: "warming" })), "warming"); // level이 먼저
  const s = splitRising([row("x", { status: "rising", ratio: 2.2 }), row("y", { status: "normal", ratio: 1 })]);
  assert.deepEqual([s.rising.length, s.warming.length, s.rest.length], [1, 0, 1]);
});

test("전부 unknown이면 판정 못 함 모드: 구름·순위 대신 월 검색 큰 순 표", () => {
  const s = splitRising([row("작음", { monthly_volume: 10 }), row("모름"), row("큼", { monthly_volume: 9000 })]);
  assert.equal(s.noJudgment, true);
  assert.deepEqual(s.rest.map((r) => r.keyword), ["큼", "작음", "모름"]);
  assert.equal(splitRising([]).noJudgment, false); // 줄이 없으면 판정 못 함 모드가 아니다
});

test("후보 출처 칩: 정해진 이름·순서, 0과 모르는 출처는 숨김", () => {
  assert.deepEqual(candidateChips({ company: 4, new: 12, youtube: 0, related_up: 8, google: 2, pool: 0, weird: 5 }), [
    { key: "new", label: "새로 나타난 말", count: 12 }, { key: "related_up", label: "연관어 검색량 증가", count: 8 },
    { key: "google", label: "Google 인기", count: 2 }, { key: "company", label: "회사 기본 키워드", count: 4 },
  ]);
  assert.deepEqual(candidateChips(undefined), []);
  assert.equal(CANDIDATE_LABEL.pool, "검색량 상위");
  assert.deepEqual(sourceShort(["new", "youtube"]), ["새로 나타남", "YouTube 인기"]);
  assert.deepEqual(sourceShort(undefined), []);
});

test("머리 줄 '11-19 07:10 판정 · 데이터랩 6번', 직전 판정 재사용이면 날짜만, 종합 1위에 (MM-DD 판정)", () => {
  assert.equal(judgedLine({ judged_on: "2026-11-19", generated_at: "2026-11-18T22:10:00Z", naver_calls: 6 }), "11-19 07:10 판정 · 데이터랩 6번");
  assert.equal(judgedLine({ judged_on: "2026-11-18", generated_at: "2026-11-18T22:10:00Z", naver_calls: 0 }), "11-18 판정 · 데이터랩 0번");
  assert.equal(judgedLine({ judged_on: null, generated_at: "2026-11-18T22:10:00Z", naver_calls: 30 }), "11-19 07:10 판정 · 데이터랩 30번"); // 예전 응답
  assert.equal(judgedLine({ judged_on: null, generated_at: null, naver_calls: 1 }), null);
  const rising = [row("평범", { status: "normal" }), row("수능일", { level: "rising", status: "rising" })];
  assert.deepEqual(topRising({ rising, stale_reason: "몫을 다 씀", judged_on: "2026-11-18" }), { row: rising[1], stale: "(11-18 판정)" });
  assert.equal(topRising({ rising, stale_reason: null, judged_on: "2026-11-19" })?.stale, null);
  assert.equal(topRising({ rising: [row("x", { level: "warming" })], stale_reason: null, judged_on: null }), null);
  assert.ok(RISING_BASIS.includes("2배 이상 급상승") && RISING_BASIS.includes("1.5배 이상 오르는 중"));
});

test("단어를 누르면 그 카드가 맨 앞, 작은 그래프 점, 이 키워드로 분석 의뢰 폼", () => {
  assert.deepEqual(pickFirst([{ keyword: "a" }, { keyword: "b" }, { keyword: "c" }], "c").map((r) => r.keyword), ["c", "a", "b"]);
  assert.deepEqual(pickFirst([{ keyword: "a" }], "없음").map((r) => r.keyword), ["a"]);
  const s = sparkline([1, 2, 4], 1, 100, 40)!;
  assert.equal(s.line.split(" ").length, 3);
  assert.ok(s.baseY !== null && s.baseY > 0 && s.baseY < 40);
  assert.equal(sparkline([5], 1, 100, 40), null);
  assert.equal(sparkline([1, 2], null, 100, 40)?.baseY, null);
  const form = requestFormForKeyword("수능일");
  assert.equal(form.keywords, "수능일");
  assert.equal(form.requestText, "");
  assert.deepEqual(buildInput(form).keywords, ["수능일"]);
});
