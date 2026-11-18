// node --test로 돈다 (npm test). 겹침 건너뛰기와 가려진 화면 규칙.
import assert from "node:assert/strict";
import { test } from "node:test";
import { createPoller } from "./visibleInterval.ts";

function setup() {
  const s = { hidden: false, t: 0, calls: 0, release: null as null | (() => void) };
  const run = () => { s.calls++; return new Promise<void>((r) => { s.release = r; }); };
  const poller = createPoller(run, 1000, { isHidden: () => s.hidden, now: () => s.t });
  return { s, poller };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

test("앞 요청이 안 끝났으면 건너뛴다", async () => {
  const { s, poller } = setup();
  void poller.first();
  assert.equal(await poller.tick(), false);
  assert.equal(s.calls, 1);
  s.release!(); await flush();
  const p = poller.tick();
  assert.equal(s.calls, 2);
  s.release!(); assert.equal(await p, true);
});

test("가려져 있으면 받지 않고, 보이면 간격이 지났을 때만 한 번 받는다", async () => {
  const { s, poller } = setup();
  const f = poller.first(); s.release!(); await f;
  s.hidden = true; s.t = 5000;
  assert.equal(await poller.tick(), false);
  assert.equal(await poller.visible(), false);
  s.hidden = false; s.t = 500;
  assert.equal(await poller.visible(), false);
  s.t = 5000;
  const v = poller.visible(); assert.equal(s.calls, 2);
  s.release!(); assert.equal(await v, true);
});

test("첫 로드는 가려져 있어도 받는다", async () => {
  const { s, poller } = setup();
  s.hidden = true;
  const f = poller.first(); assert.equal(s.calls, 1);
  s.release!(); await f;
});

test("run이 던져도 다음 주기는 받는다", async () => {
  const s = { calls: 0 };
  const poller = createPoller(async () => { s.calls++; throw new Error("x"); }, 1000, { isHidden: () => false, now: () => 0 });
  await poller.first();
  assert.equal(await poller.tick(), true);
  assert.equal(s.calls, 2);
});
