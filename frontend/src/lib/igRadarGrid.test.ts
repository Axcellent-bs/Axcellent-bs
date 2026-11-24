import { test } from "node:test";
import assert from "node:assert/strict";
import { GRID_MAX, engagementLabel, gridItems, gridView, kindLabel, mergedItems, showChips, showGrid, thumbSrc } from "./igRadarGrid.ts";
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

const meme = (id: string, ratio: number, eng: number | null = null): InstagramRadarPost =>
  post(id, ratio, { source_kind: "meme", follower_engagement: eng });

test("밈 게시물을 합치고 media_id로 중복 제거 (묶음 판정이 먼저)", () => {
  const items = mergedItems([group("A", [post("1", 3)], "caution")], [meme("1", 3), meme("2", 9)]);
  assert.deepEqual(items.map((i) => i.post.media_id), ["2", "1"]);
  assert.equal(items[0].group, null); // 묶음 없는 밈 게시물: 광고 재료 칩 없음
  assert.equal(items[1].group?.ad_material, "caution");
  assert.equal(items[1].post.source_kind, undefined); // 묶음 쪽 게시물이 이겼다
});

test("칩 필터: 전체 · 벤치마크 · 밈 (source_kind가 없으면 벤치마크)", () => {
  const groups = [group("A", [post("1", 3), post("3", 2, { source_kind: "benchmark" })])];
  const memes = [meme("2", 9)];
  assert.deepEqual(gridView(groups, memes, "all").map((i) => i.post.media_id), ["2", "1", "3"]);
  assert.deepEqual(gridView(groups, memes, "benchmark").map((i) => i.post.media_id), ["1", "3"]);
  assert.deepEqual(gridView(groups, memes, "meme").map((i) => i.post.media_id), ["2"]);
  assert.deepEqual(gridView(groups, undefined, "meme"), []);
});

test("밈 칩 정렬: 팔로워 대비 반응 내림차순, 값 없는 건 맨 뒤", () => {
  const memes = [meme("a", 9, 0.01), meme("b", 5, null), meme("c", 2, 0.08), meme("d", 7, 0.03)];
  assert.deepEqual(gridView([], memes, "meme", "ratio").map((i) => i.post.media_id), ["a", "d", "b", "c"]);
  assert.deepEqual(gridView([], memes, "meme", "engagement").map((i) => i.post.media_id), ["c", "d", "a", "b"]);
  // 밈 칩이 아니면 정렬 스위치는 영향 없음
  assert.deepEqual(gridView([], memes, "all", "engagement").map((i) => i.post.media_id), ["a", "d", "b", "c"]);
});

test("합친 그리드도 최대 12개", () => {
  const memes = Array.from({ length: 20 }, (_, i) => meme(`m${i}`, i));
  assert.equal(gridView([], memes).length, GRID_MAX);
});

test("밈 게시물만 있어도 판정이 끝났으면 그리드를 보이고, 칩은 밈 게시물이 있을 때만", () => {
  const o = (status: InstagramRadarOutput["status"], groups: InstagramRadarGroup[], meme_posts?: InstagramRadarPost[]) =>
    ({ status, groups, meme_posts } as unknown as InstagramRadarOutput);
  assert.equal(showGrid(o("known", [], [meme("1", 4)])), true);
  assert.equal(showGrid(o("unknown", [], [meme("1", 4)])), false);
  assert.equal(showGrid(o("known", [], [])), false);
  assert.equal(showChips({ meme_posts: [meme("1", 4)] }), true);
  assert.equal(showChips({ meme_posts: [] }), false);
  assert.equal(showChips({}), false);
});

test("팔로워 대비 반응 문구는 서버 값 ×100 소수 1자리, 밈 게시물만", () => {
  assert.equal(engagementLabel(meme("1", 3, 0.042)), "팔로워 대비 반응 4.2%");
  assert.equal(engagementLabel(meme("1", 3, null)), null);
  assert.equal(engagementLabel(post("1", 3, { follower_engagement: 0.5 })), null);
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
