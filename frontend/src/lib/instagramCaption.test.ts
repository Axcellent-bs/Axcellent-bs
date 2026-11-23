// node --test로 돈다 (npm test). 인스타 로그인 연결 응답 읽기와 릴스 캡션 해시태그 칸.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readInstagramAccount, readInstagramReturn, readYoutubeReturn, KEY_LABEL, KEY_NAMES } from "./companyAi.ts";
import { parseHashtags } from "./instagramCaption.ts";

test("해시태그 칸: 공백·쉼표로 나누고 #을 빼며 겹치면 하나, 최대 5개", () => {
  assert.deepEqual(parseHashtags("#AI직원 AI, 릴스  #AI"), ["AI직원", "AI", "릴스"]);
  assert.deepEqual(parseHashtags("a b c d e f g"), ["a", "b", "c", "d", "e"]);
  assert.deepEqual(parseHashtags("   "), []);
});

test("인스타 동의에서 돌아온 # 뒤 값은 instagram_ 칸만 읽는다", () => {
  assert.deepEqual(readInstagramReturn("#instagram_code=c1&instagram_state=s.1"), { code: "c1", state: "s.1" });
  assert.equal(readInstagramReturn("#youtube_code=c1&youtube_state=s.1"), null);
  assert.equal(readYoutubeReturn("#instagram_code=c1&instagram_state=s.1"), null);
});

test("인스타 로그인 연결 응답: 모양이 맞을 때만 읽고, 프로필 주소는 instagram.com https만", () => {
  const got = readInstagramAccount({
    app_configured: true, connected: true, kind: "fb", username: "catbooks.lab", profile_url: "https://www.instagram.com/catbooks.lab/",
    expires_at: "2027-01-22T00:00:00+00:00", refresh_error: null, needs_reconnect: false, user_id_mismatch: true, connected_by: "민지",
  });
  assert.deepEqual(got, {
    app_configured: true, connected: true, kind: "fb", username: "catbooks.lab", profile_url: "https://www.instagram.com/catbooks.lab/",
    expires_at: "2027-01-22T00:00:00+00:00", refresh_error: null, needs_reconnect: false, user_id_mismatch: true, connected_by: "민지",
  });
  const odd = readInstagramAccount({ connected: true, kind: "x", profile_url: "javascript:alert(1)", needs_reconnect: "yes" });
  assert.equal(odd?.kind, null);
  assert.equal(odd?.profile_url, null);
  assert.equal(odd?.needs_reconnect, false);
  assert.equal(odd?.app_configured, false);
  assert.equal(readInstagramAccount({ app_configured: true }), null);
  assert.ok(KEY_NAMES.includes("MARKETING_IG_OAUTH_REFRESH_TOKEN"));
  assert.equal(KEY_LABEL.MARKETING_IG_OAUTH_REFRESH_TOKEN, "인스타 로그인 연결");
});
