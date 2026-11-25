// node --test로 돈다 (npm test). 보고서 인스타 경고 줄 · 표본 칸 (11/26 D).
import assert from "node:assert/strict";
import { test } from "node:test";
import type { InstagramRadarPost, TrendReport, TrendReportSample } from "./data.ts";
import { igWarning, sampleView } from "./trendSampleView.ts";

const S = (over: Partial<TrendReportSample> = {}): TrendReportSample => ({
  total: 167, matched: 38, human_checked: 0, korean: 120, korean_first: true, instagram_connected: true, instagram_read: 0, ...over,
});
const calls = (instagram: number) => ({ instagram, naver: 7, llm: 2 });

test("인스타 0번 → 경고, 연결 안 된 회사는 문구만 다르게", () => {
  assert.equal(igWarning({ calls: calls(0), sample: S() })?.title, "인스타 호출 0번 · 네이버 7번");
  assert.match(igWarning({ calls: calls(0), sample: S() })!.body, /인스타 근거 없이/);
  assert.equal(igWarning({ calls: calls(0), sample: S({ instagram_connected: false }) })?.title, "인스타 연결 안 됨 · 네이버 7번");
  assert.equal(igWarning({ calls: calls(0) })?.title, "인스타 호출 0번 · 네이버 7번"); // 예전 저장본
});

test("인스타를 불렀거나 오늘 저장분을 다시 썼으면 경고 없음", () => {
  assert.equal(igWarning({ calls: calls(44), sample: S() }), null);
  assert.equal(igWarning({ calls: calls(0), sample: S({ instagram_read: 12 }) }), null);
});

test("표본 칩: 전체 · 대상 맞음 · 사람 확인(0이면 모두 AI 추정) + 공지·광고 수", () => {
  const posts = [{ notice_ad: "공지 낱말 티켓" }, { notice_ad: null }, {}] as InstagramRadarPost[];
  const v = sampleView({ sample: S(), sections: { content: { posts } } as unknown as TrendReport["sections"] })!;
  assert.deepEqual(v.chips.map((c) => c.text), ["전체 167곳", "대상 맞음 38곳", "사람 확인 0곳 · 모두 AI 추정"]);
  assert.equal(v.chips[2].tone, "warn");
  assert.ok(v.notes.some((n) => n.includes("한국어 계정 근거를 순위 앞으로")));
  assert.ok(v.notes.includes('"갑자기 뜬 게시물" 3개 중 공지·광고 1개는 근거에서 뺐어요'));
  const checked = sampleView({ sample: S({ human_checked: 5, korean_first: false }), sections: { content: { posts: [] } } as unknown as TrendReport["sections"] })!;
  assert.equal(checked.chips[2].text, "사람 확인 5곳");
  assert.ok(!checked.notes.some((n) => n.includes("앞으로")));
  assert.equal(sampleView({ sample: null, sections: { content: { posts: [] } } as unknown as TrendReport["sections"] }), null);
});
