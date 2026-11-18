import { test } from "node:test";
import assert from "node:assert/strict";
import { GRID_MAX, gridItems, kindLabel, showGrid, thumbSrc } from "./igRadarGrid.ts";
import type { InstagramRadarGroup, InstagramRadarOutput, InstagramRadarPost } from "./data.ts";

const post = (id: string, ratio: number, extra: Partial<InstagramRadarPost> = {}): InstagramRadarPost => ({
  account: `acct-${id}`, media_id: id, media_product_type: "REELS", posted_at: null, metric: "view_count",
  value: 1000, account_median: 100, ratio, permalink: null, caption_excerpt: null,
  thumbnail_url: null, media_type: null, ...extra,
});

const group = (name: string, posts: InstagramRadarPost[], ad: InstagramRadarGroup["ad_material"] = "ok"): InstagramRadarGroup => ({
  name, description: null, description_reason: null, accounts: 2, posts: posts.length, best_ratio: 0,
  members: [], topic_category: null, ad_material: ad, ad_material_reason: "", top_posts: posts,
});

const out = (status: InstagramRadarOutput["status"], groups: InstagramRadarGroup[]) =>
  ({ status, groups } as unknown as InstagramRadarOutput);

test("묶음을 가로질러 media_id로 중복 제거, 배수 내림차순", () => {
  const items = gridItems([
    group("A", [post("1", 3), post("2", 8)], "caution"),
    group("B", [post("2", 8), post("3", 5)], "ok"),
  ]);
  assert.deepEqual(items.map((i) => i.post.media_id), ["2", "3", "1"]);
  assert.equal(items[0].group.name, "A"); // 먼저 나온 묶음의 판정
  assert.equal(items[0].group.ad_material, "caution");
});

test("최대 12개", () => {
  const many = Array.from({ length: 20 }, (_, i) => post(String(i), i));
  const items = gridItems([group("A", many)]);
  assert.equal(items.length, GRID_MAX);
  assert.equal(items[0].post.ratio, 19);
});

test("데이터가 없거나 첫 바퀴 진행 중이면 그리드를 숨긴다", () => {
  assert.equal(showGrid(out("unknown", [])), false); // 첫 바퀴 모으는 중
  assert.equal(showGrid(out("known", [])), false); // 터진 게시물 없음
  assert.equal(showGrid(out("known", [group("A", [])])), false);
  assert.equal(showGrid(out("unknown", [group("A", [post("1", 3)])])), false);
  assert.equal(showGrid(out("known", [group("A", [post("1", 3)])])), true);
  assert.deepEqual(gridItems([]), []);
});

test("종류 표시: 릴스 · 사진 · 캐러셀", () => {
  assert.equal(kindLabel({ media_product_type: "REELS", media_type: "VIDEO" }), "릴스");
  assert.equal(kindLabel({ media_product_type: "FEED", media_type: "IMAGE" }), "사진");
  assert.equal(kindLabel({ media_product_type: "FEED", media_type: "CAROUSEL_ALBUM" }), "캐러셀");
  assert.equal(kindLabel({ media_product_type: null, media_type: null }), null);
});

test("썸네일이 없거나 https가 아니면 회색 칸 (숫자·링크는 카드에 그대로)", () => {
  assert.equal(thumbSrc(null), null);
  assert.equal(thumbSrc(undefined), null);
  assert.equal(thumbSrc("http://a.cdninstagram.com/x.jpg"), null);
  assert.equal(thumbSrc("javascript:alert(1)"), null);
  assert.equal(thumbSrc("https://a.cdninstagram.com/x.jpg"), "https://a.cdninstagram.com/x.jpg");
});
