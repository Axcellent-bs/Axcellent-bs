// node --test로 돈다 (npm test). 영상 요청 버튼을 띄울지 (11/25).
import assert from "node:assert/strict";
import { test } from "node:test";
import { canAskVideo, usesOriginalReference } from "./videoAsk.ts";

test("스토리보드 그림이 끝나면 영상을 요청할 수 있다", () => {
  assert.equal(canAskVideo("done", undefined, "real_photo"), true);
  assert.equal(canAskVideo("done", "failed", undefined), true);
});

test("AI 가상 인물·그림 캐릭터는 스토리보드 그림 없이도 요청할 수 있다(원본을 참조로 쓴다)", () => {
  assert.equal(canAskVideo(undefined, undefined, "virtual_human"), true);
  assert.equal(canAskVideo("pending", undefined, "illustration"), true);
  assert.equal(usesOriginalReference("virtual_human"), true);
});

test("실제 사람 사진·옛 기록은 스토리보드 그림을 기다린다", () => {
  assert.equal(canAskVideo(undefined, undefined, "real_photo"), false);
  assert.equal(canAskVideo("running", undefined, null), false);
  assert.equal(usesOriginalReference(undefined), false);
});

test("영상이 승인 대기·만드는 중·완료면 다시 띄우지 않는다", () => {
  for (const v of ["awaiting_approval", "running", "done"]) {
    assert.equal(canAskVideo("done", v, "virtual_human"), false);
  }
});
