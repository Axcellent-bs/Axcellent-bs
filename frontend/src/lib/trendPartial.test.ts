// node --test로 돈다 (npm test). 섹션 상태 empty("봤지만 이번 기간에 없음"): 머리 칩 글자 · 일부 완료 이유에서 빼기 · 진행 %.
// 픽스처 모양은 백엔드 tests/test_trend_report_partial.py 결과 그대로 (hashtags·content status "empty" + 이유).
import assert from "node:assert/strict";
import { test } from "node:test";
import type { TrendReport } from "./data.ts";
import { progressPct } from "./chatCards.ts";
import { TREND_REPORT_EXAMPLE } from "./trendReportExample.ts";
import { SECTION_STATUS, sectionNotes } from "./trendReportView.ts";

function report(status: TrendReport["status"], over: Partial<Record<"hashtags" | "content" | "competitors", { status: string; reason: string | null }>>): TrendReport {
  const base = TREND_REPORT_EXAMPLE;
  const sections = { ...base.sections };
  for (const [k, v] of Object.entries(over)) {
    const key = k as "hashtags" | "content" | "competitors";
    sections[key] = { ...sections[key], ...v } as never;
  }
  return { ...base, status, sections };
}

test("머리 칩: empty는 '확인함 · 없음'(muted)", () => {
  assert.deepEqual(SECTION_STATUS.empty, { label: "확인함 · 없음", tone: "muted" });
  assert.equal(SECTION_STATUS.unknown.label, "자료 없음"); // 볼 수 없었던 칸은 그대로
});

test("일부 완료 이유: empty는 빼고 '확인했지만 없던 칸'으로 따로", () => {
  const r = report("partial", {
    hashtags: { status: "empty", reason: "읽은 계정 6곳이 같이 쓴 태그가 없음" },
    content: { status: "empty", reason: "읽은 계정 6곳에 이번 7일 터진 게시물이 없음" },
    competitors: { status: "unknown", reason: "경쟁사 계정을 하나도 읽지 못함" },
  });
  const { missing, empty } = sectionNotes(r);
  assert.deepEqual(missing, [{ key: "competitors", title: "경쟁사", reason: "경쟁사 계정을 하나도 읽지 못함" }]);
  assert.deepEqual(empty.map((e) => [e.title, e.reason]), [
    ["해시태그", "읽은 계정 6곳이 같이 쓴 태그가 없음"],
    ["콘텐츠", "읽은 계정 6곳에 이번 7일 터진 게시물이 없음"],
  ]);
});

test("모두 known·empty면 확인 못 한 칸 없음 · 예전 보고서(empty 없음)는 그대로", () => {
  const done = report("done", { hashtags: { status: "empty", reason: "x" } });
  assert.deepEqual(sectionNotes(done).missing, []);
  assert.equal(sectionNotes(done).empty.length, 1);
  assert.deepEqual(sectionNotes(TREND_REPORT_EXAMPLE).empty, []); // 예전 저장본에는 empty가 없다
});

test("진행 %: empty도 끝난 칸으로 센다", () => {
  const r = report("running", { hashtags: { status: "empty", reason: "x" }, content: { status: "running", reason: null } });
  const sections = { ...r.sections, recommendation: { ...r.sections.recommendation, status: "waiting" as const } };
  assert.equal(progressPct({ ...r, sections }), 60); // 검색·해시태그(empty)·경쟁사 = 3/5
});
