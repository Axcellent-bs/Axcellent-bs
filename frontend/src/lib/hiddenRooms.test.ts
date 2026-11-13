// node --test로 돈다 (npm test). 숨긴 방 저장이 막혀도 화면이 정상(숨김 없음)으로 그려지는지.
import assert from "node:assert/strict";
import { test } from "node:test";
import { HIDDEN_KEY, loadHidden, saveHidden, withHidden, withoutHidden, type KeyStore } from "./hiddenRooms.ts";

const memory = (): KeyStore & { data: Record<string, string> } => {
  const data: Record<string, string> = {};
  return { data, getItem: (k) => data[k] ?? null, setItem: (k, v) => { data[k] = v; } };
};
const broken: KeyStore = {
  getItem: () => { throw new Error("SecurityError"); },
  setItem: () => { throw new Error("QuotaExceededError"); },
};

test("숨기고 다시 읽으면 그대로", () => {
  const s = memory();
  assert.equal(saveHidden(withHidden([], "ctx_inc_1"), s), true);
  assert.deepEqual(loadHidden(s), ["ctx_inc_1"]);
  assert.deepEqual(withoutHidden(loadHidden(s), "ctx_inc_1"), []);
});

test("저장소가 막히면 숨김 없이(빈 목록) 정상 동작, 쓰기는 false", () => {
  assert.deepEqual(loadHidden(broken), []);
  assert.equal(saveHidden(["all"], broken), false);
  assert.deepEqual(loadHidden(null), []);
  assert.equal(saveHidden(["all"], null), false);
});

test("값이 깨졌거나 모양이 다르면 빈 목록, 문자열만 남긴다", () => {
  const s = memory();
  s.data[HIDDEN_KEY] = "{not json";
  assert.deepEqual(loadHidden(s), []);
  s.data[HIDDEN_KEY] = JSON.stringify({ a: 1 });
  assert.deepEqual(loadHidden(s), []);
  s.data[HIDDEN_KEY] = JSON.stringify(["all", 3, "", "all", "direct:dev"]);
  assert.deepEqual(loadHidden(s), ["all", "direct:dev"]);
});
