// 영상 목적 블록 (11/26 K 시안 ⓪). node --test src/lib/videoGoal.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { goalBlock, goalLine, goalOf, GOAL_INFO } from "./videoGoal.ts";
import { toVideoPlan } from "./videoPlan.ts";

const RAW = {
  concept: "겨울 밤 책 한 권", duration_s: 15, aspect: "9:16", script: "s",
  scenes: [{ n: 1, start_s: 0, end_s: 15, visual: "v", caption: "", prompt: "p" }],
  style: "st", negative: "ng", written_by: "llm",
};
const PROP42 = "캣북스 겨울 독서 홍보 숏폼 영상 만들어줘. 끝에 캣북스 채널 이름 넣어줘.";

test("goal을 읽는다 (모르는 값은 버린다)", () => {
  assert.equal(toVideoPlan({ ...RAW, goal: "promo" })?.goal, "promo");
  assert.equal(toVideoPlan({ ...RAW, goal: "viral" })?.goal, undefined);
  assert.equal(toVideoPlan(RAW)?.goal, undefined);
});

test("목적 블록 4종 문구 (흐름·성공 기준은 재현 님 표)", () => {
  const want = {
    fun: ["재미", "재밌는 릴스, 끝에 채널 이름만", "끝까지 본 비율·공유"],
    info: ["정보", "문제 제기 → 팁·추천 2~3개 → 채널 이름", "저장·팔로우"],
    promo: ["홍보", "브랜드·상품을 소품·자막으로 → 채널 이름", "저장·팔로우"],
    ad: ["광고", "혜택 → 쓰는 장면 → 행동 문구", "클릭·구매"],
  } as const;
  for (const goal of ["fun", "info", "promo", "ad"] as const) {
    const b = goalBlock({ goal, writtenBy: "llm", ctaKo: "채널에서 더 보기" });
    assert.ok(b);
    assert.deepEqual([b.label, b.flow, b.success], [...want[goal]]);
    assert.equal(GOAL_INFO[goal].label, want[goal][0]);
    assert.deepEqual(b.chips.map((c) => [c.label, c.on]), [["재미", goal === "fun"], ["정보", goal === "info"], ["홍보", goal === "promo"], ["광고", goal === "ad"]]);
    assert.equal(b.cta, "채널에서 더 보기");
  }
});

test("왜: 의뢰문 낱말 · 사람이 고름", () => {
  assert.equal(goalBlock({ goal: "promo", writtenBy: "llm" }, PROP42)?.why, '의뢰문 "홍보"');
  assert.equal(goalBlock({ goal: "info", writtenBy: "human" }, PROP42)?.why, "사람이 고름");
  assert.equal(goalBlock({ goal: "fun", writtenBy: "llm" }, "15초 영상")?.why, "");
});

test("AXcellent 솔루션 광고는 B2B 템플릿", () => {
  const b = goalBlock({ goal: "ad", purpose: "solution_ad", writtenBy: "llm", ctaKo: "데모 신청하기" });
  assert.equal(b?.flow, "문제 → 해결 → 문의 (B2B 템플릿)");
  assert.equal(b?.success, "문의·데모");
});

test("goal 없는 예전 제작안은 purpose로 짐작, 둘 다 없으면 블록 없음", () => {
  assert.deepEqual(goalOf({ purpose: "channel_content" }), { goal: "fun", guessed: true });
  assert.deepEqual(goalOf({ purpose: "client_offer" }), { goal: "ad", guessed: true });
  assert.deepEqual(goalOf({ purpose: "solution_ad" }), { goal: "ad", guessed: true });
  assert.equal(goalOf({}), null);
  assert.equal(goalBlock({ writtenBy: "rule" }), null);
  assert.match(goalBlock({ purpose: "channel_content", writtenBy: "llm" })?.why ?? "", /짐작/);
});

test("의뢰 카드 목적 한 줄", () => {
  assert.equal(goalLine({ goal: "promo", writtenBy: "llm", ctaKo: "채널에서 더 많은 소식 보기" }), '목적 · 홍보 · 끝 행동 "채널에서 더 많은 소식 보기"');
  assert.equal(goalLine({ writtenBy: "rule" }), "");
});
