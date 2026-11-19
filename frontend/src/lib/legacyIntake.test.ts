// node --test로 돈다 (npm test). 옛 자료 문의는 기본 목록에서 숨기고 켜면 보이는지.
import assert from "node:assert/strict";
import { test } from "node:test";
import { isLegacy, splitLegacy } from "./legacyIntake.ts";

const row = (id: number, legacy?: boolean) => ({ id, legacy_intake: legacy });
const list = [row(1), row(2, true), row(3, false), row(4, true)];

test("기본은 옛 자료를 빼고, 표시가 없거나 false면 일반 문의다", () => {
  const { shown, legacyCount } = splitLegacy(list, false);
  assert.deepEqual(shown.map((c) => c.id), [1, 3]);
  assert.equal(legacyCount, 2);
});

test("켜면 전부 보인다 (순서 그대로)", () => {
  const { shown, legacyCount } = splitLegacy(list, true);
  assert.deepEqual(shown.map((c) => c.id), [1, 2, 3, 4]);
  assert.equal(legacyCount, 2); // 켜도 옛 자료 수는 그대로 (필터 글자에 쓴다)
});

test("옛 자료가 하나도 없으면 목록을 그대로 돌려준다", () => {
  const plain = [row(1), row(2, false)];
  assert.equal(splitLegacy(plain, false).shown, plain);
  assert.equal(splitLegacy(plain, false).legacyCount, 0);
});

test("옛 응답처럼 표시가 아예 없으면 아무것도 숨기지 않는다", () => {
  assert.equal(isLegacy({}), false);
  assert.equal(splitLegacy([{ legacy_intake: undefined }], false).shown.length, 1);
});
