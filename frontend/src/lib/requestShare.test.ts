// node --test로 돈다 (npm test). 같은 읽기 요청 함께 받기와 시간 제한.
import assert from "node:assert/strict";
import { test } from "node:test";
import { _resetInflight, copyShared, isShareableGet, isTimeoutError, shareInflight, timeoutSignal } from "./requestShare.ts";

test("같은 키 요청이 오는 중이면 한 번만 실행하고 답을 같이 받는다", async () => {
  _resetInflight();
  let calls = 0;
  let release!: (v: number) => void;
  const run = () => { calls++; return new Promise<number>((r) => { release = r; }); };
  const a = shareInflight("k", run);
  const b = shareInflight("k", run);
  assert.equal(calls, 1);
  release(7);
  assert.deepEqual(await Promise.all([a, b]), [7, 7]);
});

test("끝나면 비워서 다음 요청은 새로 보낸다 (오래된 값을 돌려주지 않는다)", async () => {
  _resetInflight();
  let calls = 0;
  const run = async () => ++calls;
  assert.equal(await shareInflight("k", run), 1);
  assert.equal(await shareInflight("k", run), 2);
});

test("실패해도 비운다 — 다음 요청이 새로 간다", async () => {
  _resetInflight();
  let calls = 0;
  const run = async () => { calls++; if (calls === 1) throw new Error("실패"); return "ok"; };
  await assert.rejects(shareInflight("k", run), /실패/);
  assert.equal(await shareInflight("k", run), "ok");
});

test("키가 다르면 따로 보낸다", async () => {
  _resetInflight();
  let calls = 0;
  const run = async () => ++calls;
  const [a, b] = await Promise.all([shareInflight("a", run), shareInflight("b", run)]);
  assert.notEqual(a, b);
});

test("같이 받은 답은 호출마다 복사본이라 한 곳이 고쳐도 다른 곳이 영향받지 않는다", () => {
  const shared = [{ id: 2 }, { id: 1 }];
  const one = copyShared(shared);
  const two = copyShared(shared);
  one.sort((x, y) => x.id - y.id);
  assert.deepEqual(two.map((x) => x.id), [2, 1]);
  assert.equal(copyShared(undefined), undefined);
});

test("읽기(GET)만 함께 받는다 — 쓰기·본문·머리글·신호가 있으면 따로 보낸다", () => {
  assert.equal(isShareableGet(), true);
  assert.equal(isShareableGet({ method: "get" }), true);
  assert.equal(isShareableGet({ method: "POST" }), false);
  assert.equal(isShareableGet({ body: "x" }), false);
  assert.equal(isShareableGet({ headers: { a: "b" } }), false);
  assert.equal(isShareableGet({ signal: new AbortController().signal }), false);
});

test("시간 제한 신호가 시간이 지나면 TimeoutError로 끊긴다", async () => {
  const s = timeoutSignal(20);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(s.aborted, true);
  assert.equal(isTimeoutError(s.reason), true);
  assert.equal(isTimeoutError(new Error("x")), false);
});
