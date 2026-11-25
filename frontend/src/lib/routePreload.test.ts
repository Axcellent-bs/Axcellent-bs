// node --test로 돈다 (npm test). 11/25 처음 연 주소의 페이지 코드 미리 받기
import assert from "node:assert/strict";
import { test } from "node:test";
import { preloadRoute, type RoutePreload } from "./routePreload.ts";

const table = (calls: string[]): RoutePreload[] => [
  ["/finance/guide", () => { calls.push("guide"); return Promise.resolve(); }],
  ["/finance", () => { calls.push("finance"); return Promise.resolve(); }],
];

test("맞는 첫 항목 하나만 받는다", () => {
  const calls: string[] = [];
  assert.equal(preloadRoute("/finance/daily", table(calls)), "/finance");
  assert.equal(preloadRoute("/finance/guide", table(calls)), "/finance/guide");
  assert.deepEqual(calls, ["finance", "guide"]);
});

test("앞글자만 같은 다른 주소는 받지 않는다", () => {
  const calls: string[] = [];
  assert.equal(preloadRoute("/financed", table(calls)), null);
  assert.equal(preloadRoute("/", table(calls)), null);
  assert.deepEqual(calls, []);
});

test("받기에 실패해도 던지지 않는다 (그릴 때 lazy가 다시 받는다)", async () => {
  let n = 0;
  const load = () => { n += 1; return Promise.reject(new Error("offline")); };
  assert.equal(preloadRoute("/x", [["/x", load]]), "/x");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(n, 1);
});
