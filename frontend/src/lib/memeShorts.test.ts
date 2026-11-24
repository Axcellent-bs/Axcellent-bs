import { test } from "node:test";
import assert from "node:assert/strict";
import { perSubscriberLabel, shortsNote, splitShorts } from "./memeShorts.ts";
import type { MemeShortsSearch, MemeShortsVideo } from "./data.ts";

const vid = (id: string, big: boolean, rel: number | null = 1): MemeShortsVideo => ({
  video_id: id, keyword: "k", title: null, channel_id: null, channel_title: null, published_at: null,
  views: 100, subscribers: 10, views_per_subscriber: rel, big_channel: big, video_url: null, thumbnail_url: null,
});
const search = (extra: Partial<MemeShortsSearch>): MemeShortsSearch => ({
  status: "known", reason: null, day: null, keywords: [], searched: [], limits: [], units: 0, items: [], removed: [], searched_at: null, ...extra,
});

test("큰 채널(구독자 10만 이상)은 따로 나누고 서버 순서를 지킨다", () => {
  const { small, big } = splitShorts([vid("a", false), vid("b", true), vid("c", false), vid("d", true)]);
  assert.deepEqual(small.map((v) => v.video_id), ["a", "c"]);
  assert.deepEqual(big.map((v) => v.video_id), ["b", "d"]);
  assert.deepEqual(splitShorts(undefined), { small: [], big: [] });
});

test("구독자 대비 배수 문구는 소수 1자리, 모르면 null", () => {
  assert.equal(perSubscriberLabel({ views_per_subscriber: 12.34 }), "구독자 대비 12.3배");
  assert.equal(perSubscriberLabel({ views_per_subscriber: 3 }), "구독자 대비 3.0배");
  assert.equal(perSubscriberLabel({ views_per_subscriber: null }), null);
});

test("안내 문장: 키워드 없음 · 모름(서버 이유) · 영상 있음", () => {
  assert.match(shortsNote(search({ status: "not_set" }))!, /밈 키워드가 없어요/);
  assert.equal(shortsNote(search({ status: "unknown", reason: "쿼터 부족" })), "쿼터 부족");
  assert.equal(shortsNote(search({ items: [vid("a", false)] })), null);
  assert.equal(shortsNote(search({ items: [] })), "찾은 쇼츠가 없어요.");
});
