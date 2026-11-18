import { test } from "node:test";
import assert from "node:assert/strict";
import { DETECT_VIEWS, detectViewFromHash, hashWithView, viewParam } from "./detectView.ts";

test("기본은 종합, view 값이 있으면 그 칩", () => {
  assert.equal(detectViewFromHash(""), "all");
  assert.equal(detectViewFromHash("#detect"), "all");
  assert.equal(detectViewFromHash("#detect?view=all"), "all");
  assert.equal(detectViewFromHash("#detect?view=youtube"), "youtube");
  assert.equal(detectViewFromHash("#detect?view=tiktok"), "all"); // 없는 칩
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
  assert.deepEqual(DETECT_VIEWS.map((v) => v.key), ["all", "youtube", "instagram", "search"]);
});

test("인스타 칩 주소", () => {
  assert.equal(detectViewFromHash("#detect?view=instagram"), "instagram");
  assert.equal(detectViewFromHash("#detect?view=instagram&len=long"), "instagram"); // view가 있으면 영상 조건보다 먼저
  assert.equal(DETECT_VIEWS.find((v) => v.key === "instagram")?.label, "인스타");
  assert.equal(hashWithView("#detect", "instagram"), "#detect?view=instagram");
  const next = hashWithView("#detect?len=long&p=3d&view=youtube", "instagram");
  assert.equal(next, "#detect?len=long&p=3d&view=instagram");
  assert.equal(detectViewFromHash(next), "instagram");
  assert.equal(viewParam(next), "instagram");
});

test("검색 칩 주소", () => {
  assert.equal(detectViewFromHash("#detect?view=search"), "search");
  assert.equal(detectViewFromHash("#detect?view=search&len=long"), "search"); // view가 있으면 영상 조건보다 먼저
  assert.equal(hashWithView("#detect", "search"), "#detect?view=search");
  const next = hashWithView("#detect?len=long&p=3d&view=youtube", "search");
  assert.equal(next, "#detect?len=long&p=3d&view=search");
  assert.equal(detectViewFromHash(next), "search");
});
