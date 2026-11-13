// node --test로 돈다 (npm test). 앞단 서버(nginx)가 막은 403/405와 앱이 준 403을 구분하는지.
import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError, failureError, isNginxBlocked, NGINX_BLOCKED_CODE, NGINX_BLOCKED_MESSAGE, statusOnlyError } from "./apiError.ts";
import { mediaHttpErrorKo, publishHttpErrorKo } from "./briefMedia.ts";

// 본문을 JSON으로 읽지 못하면(nginx 기본 오류는 HTML) 호출부는 undefined를 넘긴다.
test("HTML 403은 nginx_blocked와 안내 문구로", () => {
  const e = failureError(undefined, 403);
  assert.ok(e instanceof ApiError);
  assert.equal(e.status, 403);
  assert.equal(e.code, NGINX_BLOCKED_CODE);
  assert.equal(e.message, NGINX_BLOCKED_MESSAGE);
  assert.match(e.message, /허용하지 않습니다/);
  assert.ok(!/요청 실패/.test(e.message));
  assert.ok(!/\/|https?:|Bearer|token/i.test(e.message.replace("(nginx)", "")), "경로·주소·토큰이 문구에 없다");
});

test("JSON 봉투가 있는 403은 서버 문장을 그대로 쓴다", () => {
  const e = failureError({ error: { code: "FORBIDDEN", message: "권한이 없습니다" } }, 403);
  assert.equal(e.message, "권한이 없습니다");
  assert.equal(e.code, "FORBIDDEN");
  assert.equal(e.status, 403);
  assert.equal(failureError({ detail: "x" }, 403).code ?? null, null);
  assert.equal(failureError({ detail: "x" }, 403).message, "요청 실패 (403)");
});

test("405도 JSON이 아니면 nginx_blocked", () => {
  assert.equal(failureError(undefined, 405).code, NGINX_BLOCKED_CODE);
  assert.equal(failureError(null, 405).message, NGINX_BLOCKED_MESSAGE);
  assert.equal(failureError({ error: { message: "허용하지 않는 메서드" } }, 405).message, "허용하지 않는 메서드");
});

test("다른 상태와 200에는 영향이 없다", () => {
  assert.equal(isNginxBlocked(200, undefined), false);
  assert.equal(isNginxBlocked(401, undefined), false);
  assert.equal(isNginxBlocked(404, undefined), false);
  assert.equal(isNginxBlocked(500, undefined), false);
  assert.equal(failureError(undefined, 500).message, "요청 실패 (500)");
  assert.equal(failureError(undefined, 500).code ?? null, null);
  assert.equal(failureError(undefined, 502, "연결 실패").message, "연결 실패");
});

test("상태만 담는 호출: 앞단 차단만 구분하고 서버 문장은 쓰지 않는다", () => {
  assert.equal(statusOnlyError(undefined, 403).code, NGINX_BLOCKED_CODE);
  const app = statusOnlyError({ error: { message: "서버 문장" } }, 403);
  assert.equal(app.message, "요청 실패 (403)");
  assert.equal(app.code ?? null, null);
});

test("미디어·게시 문구: nginx_blocked면 안내, 아니면 기존 문구", () => {
  assert.equal(mediaHttpErrorKo(403, NGINX_BLOCKED_CODE), NGINX_BLOCKED_MESSAGE);
  assert.equal(publishHttpErrorKo(403, NGINX_BLOCKED_CODE), NGINX_BLOCKED_MESSAGE);
  assert.match(mediaHttpErrorKo(403), /권한/);
  assert.match(publishHttpErrorKo(403, null), /권한/);
});
