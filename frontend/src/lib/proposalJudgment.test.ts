// node --test로 돈다 (npm test).
// 방향을 고르면 백엔드가 할 일·이유·목표를 새로 쓴다. 화면 입력 칸이 그 새 글로 다시 그려져야
// 예전 글이 다시 저장되지 않는다 (11/18 리뷰 Critical 1).
import assert from "node:assert/strict";
import { test } from "node:test";
import { judgmentKey } from "./proposals.ts";

const base = { id: "PROP-7", idea: "예전 할 일", why: "예전 이유", target: { label: "조회", value: "506만" } };

test("방향을 고르면 할 일·이유·목표 칸이 새로 그려진다", () => {
  const before = judgmentKey(base);
  assert.notEqual(judgmentKey({ ...base, angle: "ride_trend", idea: "새 할 일" }), before);
  assert.notEqual(judgmentKey({ ...base, why: "새 이유" }), before);
  assert.notEqual(judgmentKey({ ...base, target: { label: "조회", value: "1만" } }), before);
});

test("글이 그대로면 칸도 그대로다 (입력 중에 지워지지 않게)", () => {
  assert.equal(judgmentKey({ ...base }), judgmentKey(base));
});
