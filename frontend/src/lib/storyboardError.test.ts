// node --test로 돈다 (npm test). 스토리보드 실패 사유 표시.
import assert from "node:assert/strict";
import { test } from "node:test";
import { NGINX_BLOCKED_CODE } from "./apiError.ts";
import { STORYBOARD_FAILED, storyboardFailure } from "./storyboardError.ts";

test("502 JSON 오류는 서버 사유를 붙여 보여 준다", () => {
  const body = { error: { code: "LLM_FAILED", message: "장면을 6칸 다 만들지 못했다(0칸). 다시 해 보세요" } };
  const e = storyboardFailure(body, 502);
  assert.equal(e.message, "스토리보드를 만들지 못했어요 — 장면을 6칸 다 만들지 못했다(0칸). 다시 해 보세요");
  assert.equal(e.code, "LLM_FAILED");
  assert.equal(e.status, 502);
});

test("JSON이 아닌 오류(프록시 HTML 등)는 일반 문구로", () => {
  for (const body of [undefined, null, "<html>Bad Gateway</html>", {}, { detail: "x" }, { error: {} }, { error: { message: 5 } }]) {
    assert.equal(storyboardFailure(body, 502).message, STORYBOARD_FAILED);
  }
});

test("서버 문장만 쓰고 공백·길이를 다듬는다", () => {
  const long = `줄 바꿈\n  있는 ${"가".repeat(500)}`;
  const e = storyboardFailure({ error: { message: long } }, 400);
  assert.ok(!e.message.includes("\n"));
  assert.ok(e.message.length <= STORYBOARD_FAILED.length + 3 + 200);
});

test("앞단(nginx) 차단은 기존 안내 문구 그대로", () => {
  const e = storyboardFailure(undefined, 403);
  assert.equal(e.code, NGINX_BLOCKED_CODE);
  assert.ok(!e.message.startsWith(STORYBOARD_FAILED));
});
