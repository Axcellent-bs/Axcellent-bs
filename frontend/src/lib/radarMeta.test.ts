import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildRadarRows, draftBody, draftOf, excludedReason, followersLabel, genderLabels, ageLabels, layerChips, layerCounts, layerCountsLine,
  megaNote, megaSorted, thinNote, toggleAge, viewsLabel, daysAgoLabel, tagSourceView, THIN_NOTE,
} from "./radarMeta.ts";
import type { MegaViewItem, MegaViews } from "./data.ts";

test("팔로워 글자: 만 단위, 모르면 —", () => {
  assert.equal(followersLabel(1_100_000), "110만");
  assert.equal(followersLabel(898_000), "89.8만");
  assert.equal(followersLabel(8_500), "8,500");
  assert.equal(followersLabel(null), "—");
  assert.equal(followersLabel(undefined), "—");
});

test("나이대·성별 글자", () => {
  assert.deepEqual(ageLabels(["60plus", "10s", "10s"]), ["10대", "60대 이상"]);
  assert.deepEqual(genderLabels(["male"]), ["남성"]);
  assert.deepEqual(genderLabels(["female"]), ["여성"]);
  assert.deepEqual(genderLabels(["male", "female"]), ["남녀"]);
  assert.deepEqual(genderLabels(["all"]), ["남녀"]);
  assert.deepEqual(genderLabels([]), []);
});

test("제외: 밈·크리에이터만, 팔로워를 알 때만", () => {
  assert.equal(excludedReason("creator", 12_000, 50_000), "팔로워 5만 미만이라 제외 (12,000명)");
  assert.equal(excludedReason("meme", 49_999, 50_000), "팔로워 5만 미만이라 제외 (49,999명)");
  assert.equal(excludedReason("meme", 50_000, 50_000), null);
  assert.equal(excludedReason("meme", null, 50_000), null);
  assert.equal(excludedReason("industry", 10, 50_000), null);
  assert.equal(excludedReason("brand", 10, 50_000), null);
});

const state = {
  accounts: ["shop_a", "biz_b"],
  meme_accounts: ["meme_c", "small_d", "shop_a"],
  min_followers: 50_000,
  meta: {
    meme_c: { kind: "meme" as const, ages: ["10s" as const, "20s" as const], genders: ["male" as const], topic: " 코미디 ", tag_source: "human" as const, followers: 1_100_000 },
    small_d: { kind: "creator" as const, ages: ["20s" as const, "60plus" as const], genders: ["all" as const], tag_source: "ai" as const, followers: 8_000 },
    biz_b: { kind: "industry" as const, ages: ["20s" as const], genders: ["female" as const], tag_source: "ai" as const, followers: 20_000 },
  },
};

test("표 줄: 순서·중복·종류 대체·제외", () => {
  const rows = buildRadarRows(state);
  assert.deepEqual(rows.map((r) => r.account), ["shop_a", "biz_b", "meme_c", "small_d"]);
  const [shop, biz, meme, small] = rows;
  assert.equal(shop.kind, "meme"); // 태그 없음 → 목록 소속. 두 목록에 다 있으면 밈이 우선
  assert.equal(buildRadarRows({ accounts: ["only_bench"] })[0].kind, "industry");
  assert.equal(shop.tagSource, null);
  assert.equal(shop.followersLabel, "—");
  assert.deepEqual(shop.ages, []);
  assert.equal(biz.excluded, null); // 업종은 팔로워가 적어도 제외하지 않는다
  assert.equal(meme.topic, "코미디");
  assert.equal(meme.followersLabel, "110만");
  assert.equal(small.excluded, "팔로워 5만 미만이라 제외 (8,000명)");
  assert.equal(buildRadarRows(null).length, 0);
});

test("밈 목록에만 있고 태그 없으면 밈", () => {
  const rows = buildRadarRows({ accounts: [], meme_accounts: ["x"] });
  assert.equal(rows[0].kind, "meme");
  assert.equal(rows[0].kindLabel, "밈·코미디");
});

test("층별 계정 수: 제외 계정은 빼고, 남녀는 둘 다에 센다", () => {
  const rows = buildRadarRows(state);
  const counts = layerCounts(rows);
  const by = Object.fromEntries(counts.map((c) => [c.age, c]));
  assert.deepEqual([by["10s"].total, by["10s"].male, by["10s"].female], [1, 1, 0]);
  assert.deepEqual([by["20s"].total, by["20s"].male, by["20s"].female], [2, 1, 1]); // small_d는 제외
  assert.equal(by["60plus"].total, 0);
  assert.match(layerCountsLine(counts), /^10대 1 \(남 1 · 여 0\) · 20대 2/);
  assert.equal(thinNote(counts, rows), THIN_NOTE);
});

test("60대 이상이 3곳 이상이면 얇다는 안내가 없고, 태그가 전혀 없으면 알리지 않는다", () => {
  const mk = (i: number) => ({ kind: "creator" as const, ages: ["60plus" as const], genders: ["all" as const], followers: 900_000 + i });
  const meta = { a: mk(1), b: mk(2), c: mk(3) };
  const rows = buildRadarRows({ accounts: ["a", "b", "c"], meta, min_followers: 50_000 });
  assert.equal(thinNote(layerCounts(rows), rows), null);
  const bare = buildRadarRows({ accounts: ["z"] });
  assert.equal(thinNote(layerCounts(bare), bare), null);
});

test("고치기 폼: 초기값·나이 토글·서버 본문", () => {
  const rows = buildRadarRows(state);
  const d = draftOf(rows[2]);
  assert.deepEqual(d, { kind: "meme", ages: ["10s", "20s"], gender: "male", topic: "코미디" });
  assert.deepEqual(toggleAge(["20s"], "10s"), ["10s", "20s"]);
  assert.deepEqual(toggleAge(["10s", "20s"], "10s"), ["20s"]);
  assert.deepEqual(draftBody("meme_c", { ...d, topic: "  " }), { account: "meme_c", kind: "meme", ages: ["10s", "20s"], genders: ["male"], topic: null });
  assert.equal(draftBody("a", { ...d, topic: "가".repeat(30) }).topic?.length, 20);
  assert.deepEqual(draftBody("a", { ...d, gender: "" }).genders, []);
});

test("태그 출처 칩", () => {
  assert.deepEqual(tagSourceView("human"), { label: "사람 확인", tone: "ok" });
  assert.deepEqual(tagSourceView("ai"), { label: "AI 추정", tone: "warn" });
  assert.deepEqual(tagSourceView(null), { label: "태그 없음", tone: "muted" });
});

test("조회수·며칠 전 글자", () => {
  assert.equal(viewsLabel(12_300_000), "1,230만회");
  assert.equal(viewsLabel(150_000_000), "1.5억회");
  assert.equal(viewsLabel(8_500), "8,500회");
  assert.equal(viewsLabel(null), "—");
  const now = Date.parse("2026-11-24T00:00:00Z");
  assert.equal(daysAgoLabel("2026-11-21T00:00:00Z", now), "3일 전");
  assert.equal(daysAgoLabel("2026-11-23T20:00:00Z", now), "오늘");
  assert.equal(daysAgoLabel(null, now), null);
  assert.equal(daysAgoLabel("2026-12-01T00:00:00Z", now), null);
});

const item = (id: string, views: number): MegaViewItem => ({
  platform: "youtube", account: "ch", item_id: id, views, posted_at: null, link: null, thumbnail_url: null, title: null,
  account_kind: null, audience_ages: [], audience_genders: [], topic: null,
});
const mega = (extra: Partial<MegaViews>): MegaViews => ({ status: "known", reason: null, instagram_min: 10_000_000, youtube_min: 10_000_000, days: 30, items: [], ...extra });

test("조회 천만 이상 안내와 정렬", () => {
  assert.equal(megaNote(mega({})), "최근 30일 안에 조회 1,000만회 이상 영상이 없어요");
  assert.equal(megaNote(mega({ status: "unknown", reason: "키가 없어요" })), "키가 없어요");
  assert.equal(megaNote(mega({ items: [item("a", 1)] })), null);
  assert.deepEqual(megaSorted([item("a", 1), item("b", 5)]).map((i) => i.item_id), ["b", "a"]);
});

test("영상 줄의 층 칩", () => {
  assert.deepEqual(layerChips({ account_kind: "meme", audience_ages: ["20s"], audience_genders: ["male"], topic: "뷰티" }), ["밈·코미디", "20대", "남성", "뷰티"]);
  assert.deepEqual(layerChips({}), []);
});
