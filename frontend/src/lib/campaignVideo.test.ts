// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import { looksLikeYoutubeUrl, videoFields } from "./campaignVideo.ts";

const URL_OK = "https://www.youtube.com/shorts/abc123";

test("URL이 없으면 선택과 상관없이 아무것도 보내지 않는다", () => {
  assert.deepEqual(videoFields("  ", null), { fields: {}, error: null });
  assert.deepEqual(videoFields("", "test"), { fields: {}, error: null });
});

test("URL을 넣고 선택하지 않으면 제출 불가 메시지", () => {
  const r = videoFields(URL_OK, null);
  assert.deepEqual(r.fields, {});
  assert.match(r.error ?? "", /시험 영상인지 시연 영상인지/);
});

test("시험 영상은 test=true, 시연 영상은 test=false", () => {
  assert.deepEqual(videoFields(URL_OK, "test"), { fields: { video_url: URL_OK, test: true }, error: null });
  assert.deepEqual(videoFields(` ${URL_OK} `, "demo"), { fields: { video_url: URL_OK, test: false }, error: null });
});

test("유튜브가 아닌 주소는 막는다", () => {
  assert.ok(videoFields("https://instagram.com/reel/x", "demo").error);
  assert.ok(videoFields("not a url", "demo").error);
  assert.equal(looksLikeYoutubeUrl("http://youtube.com/shorts/x"), false);
  assert.equal(looksLikeYoutubeUrl("https://youtu.be/abc"), true);
});
