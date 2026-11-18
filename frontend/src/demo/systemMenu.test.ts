// node --test로 돈다. 시스템 관리 메뉴: 관리자가 아니면 도구 연결만.
import assert from "node:assert/strict";
import { test } from "node:test";
import { TOOLS_HREF, preConnectMenu, systemMenuFor } from "./systemMenu.ts";

test("관리자가 아니면 시스템 관리 아래 도구 연결만", () => {
  assert.deepEqual(systemMenuFor(false).map((m) => m.href), [TOOLS_HREF]);
  assert.equal(systemMenuFor(false)[0].label, "도구 연결");
});

test("관리자는 네 개", () => {
  assert.deepEqual(systemMenuFor(true).map((m) => m.href), ["/system/connect", "/system/members", "/system/tools"]);
});

test("회사 연결 전 관리자는 회사 연결만", () => {
  assert.deepEqual(preConnectMenu().map((m) => m.href), ["/system/connect"]);
});

test("메뉴를 바꿔도 원래 목록은 그대로", () => {
  systemMenuFor(true).pop();
  assert.equal(systemMenuFor(true).length, 3);
});
