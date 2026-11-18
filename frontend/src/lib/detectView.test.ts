import { test } from "node:test";
import assert from "node:assert/strict";
import { DETECT_VIEWS, detectViewFromHash, hashWithView, viewParam } from "./detectView.ts";

test("기본은 종합, view 값이 있으면 그 칩", () => {
  assert.equal(detectViewFromHash(""), "all");
  assert.equal(detectViewFromHash("#detect"), "all");
  assert.equal(detectViewFromHash("#detect?view=all"), "all");
  assert.equal(detectViewFromHash("#detect?view=youtube"), "youtube");
  assert.equal(detectViewFromHash("#detect?view=instagram"), "all"); // 아직 없는 칩
});

test("view 없이 영상 조건만 있는 예전 주소는 유튜브", () => {
  assert.equal(detectViewFromHash("#detect?len=long&p=3d&sort=likes"), "youtube");
  assert.equal(detectViewFromHash("#detect?cat=게임"), "youtube");
  assert.equal(detectViewFromHash("#detect?view=all&len=long"), "all");
});

test("칩을 바꿔도 영상 조건은 그대로", () => {
  const next = hashWithView("#detect?len=long&p=3d&sort=likes", "all");
  assert.equal(next, "#detect?len=long&p=3d&sort=likes&view=all");
  assert.equal(hashWithView("", "youtube"), "#detect?view=youtube");
  assert.equal(viewParam(next), "all");
  assert.equal(viewParam("#detect?len=long"), null);
});

test("칩 목록은 배열 한 곳 (종합이 먼저)", () => {
  assert.deepEqual(DETECT_VIEWS.map((v) => v.key), ["all", "youtube"]);
});
