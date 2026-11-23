// node --test (npm test). 인쇄 직전 이미지 미리 받기 (lib/printImages.ts): eager로 바꾸기 · load/error 기다리기 · 3초 상한 · 못 받은 것 숨김
import assert from "node:assert/strict";
import { test } from "node:test";
import { PRINT_IMG_FAILED, PRINT_IMG_WAIT_MS, waitForImages, type ImgLike } from "./printImages.ts";

/** 가짜 img: fire("load"|"error")로 끝낸다 */
function fakeImg(state: { complete?: boolean; naturalWidth?: number; loading?: string } = {}) {
  const fns: Record<string, (() => void)[]> = { load: [], error: [] };
  const classes = new Set<string>();
  const img: ImgLike & { fire(t: "load" | "error"): void; classes: Set<string>; hooks(): number } = {
    loading: state.loading ?? "lazy",
    complete: state.complete ?? false,
    naturalWidth: state.naturalWidth ?? 0,
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c) },
    addEventListener: (t, fn) => { fns[t].push(fn); },
    removeEventListener: (t, fn) => { fns[t] = fns[t].filter((f) => f !== fn); },
    fire(t) { this.complete = true; this.naturalWidth = t === "load" ? 54 : 0; for (const f of [...fns[t]]) f(); },
    classes,
    hooks: () => fns.load.length + fns.error.length,
  };
  return img;
}

/** 가짜 시계: run()으로 상한 타이머를 당긴다 */
function fakeClock() {
  let pending: { fn: () => void; ms: number } | null = null;
  return {
    set: (fn: () => void, ms: number) => { pending = { fn, ms }; return 1; },
    clear: () => { pending = null; },
    run: () => { const p = pending; pending = null; p?.fn(); },
    ms: () => pending?.ms ?? null,
  };
}

test("lazy 이미지를 eager로 바꾸고, 모두 load/error되면 바로 끝난다 (못 받은 것만 숨김 클래스)", async () => {
  const a = fakeImg(), b = fakeImg();
  const clock = fakeClock();
  const p = waitForImages([a, b], PRINT_IMG_WAIT_MS, clock.set, clock.clear);
  assert.equal(a.loading, "eager");
  assert.equal(b.loading, "eager");
  assert.equal(clock.ms(), 3000);
  a.fire("load");
  b.fire("error");
  assert.deepEqual(await p, { failed: 1, timedOut: false });
  assert.equal(clock.ms(), null); // 상한 타이머는 지움
  assert.equal(a.classes.has(PRINT_IMG_FAILED), false);
  assert.equal(b.classes.has(PRINT_IMG_FAILED), true);
  assert.equal(a.hooks() + b.hooks(), 0); // 듣기 정리
});

test("3초 상한: 안 끝난 이미지가 있어도 기다리지 않고 끝내고, 안 끝난 것은 숨긴다", async () => {
  const slow = fakeImg(), ok = fakeImg();
  const clock = fakeClock();
  const p = waitForImages([slow, ok], 3000, clock.set, clock.clear);
  ok.fire("load");
  clock.run(); // 3초 지남
  assert.deepEqual(await p, { failed: 1, timedOut: true });
  assert.equal(slow.classes.has(PRINT_IMG_FAILED), true);
  assert.equal(ok.classes.has(PRINT_IMG_FAILED), false);
  slow.fire("load"); // 늦게 와도 다시 끝나지 않는다
});

test("이미 받은 이미지만 있으면 타이머 없이 바로 끝나고, 깨진 것(크기 0)은 숨긴다. 지난 인쇄의 숨김 클래스는 지운다", async () => {
  const done = fakeImg({ complete: true, naturalWidth: 54, loading: "eager" });
  const broken = fakeImg({ complete: true, naturalWidth: 0 });
  done.classes.add(PRINT_IMG_FAILED);
  const clock = fakeClock();
  assert.deepEqual(await waitForImages([done, broken], 3000, clock.set, clock.clear), { failed: 1, timedOut: false });
  assert.equal(clock.ms(), null);
  assert.equal(done.classes.has(PRINT_IMG_FAILED), false);
  assert.equal(broken.classes.has(PRINT_IMG_FAILED), true);
  assert.deepEqual(await waitForImages([], 3000, clock.set, clock.clear), { failed: 0, timedOut: false });
});
