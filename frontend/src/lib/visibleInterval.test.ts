import assert from "node:assert/strict";
import { describe, it, mock } from "node:test";
import { startVisibleInterval, type VisibleDoc } from "./visibleInterval.ts";

// 가짜 문서: hidden을 바꾸고 visibilitychange를 직접 알린다
function fakeDoc(): VisibleDoc & { setHidden(v: boolean): void } {
  const listeners = new Set<() => void>();
  const doc = {
    hidden: false,
    addEventListener: (_t: "visibilitychange", fn: () => void) => { listeners.add(fn); },
    removeEventListener: (_t: "visibilitychange", fn: () => void) => { listeners.delete(fn); },
    setHidden(v: boolean) { doc.hidden = v; listeners.forEach((fn) => fn()); },
  };
  return doc;
}

describe("startVisibleInterval", () => {
  it("정한 간격마다 부른다", () => {
    mock.timers.enable({ apis: ["setInterval", "Date"] });
    const doc = fakeDoc();
    let n = 0;
    const stop = startVisibleInterval(() => { n += 1; }, 1000, doc);
    mock.timers.tick(3000);
    assert.equal(n, 3);
    stop();
    mock.timers.tick(3000);
    assert.equal(n, 3);
    mock.timers.reset();
  });

  it("다른 탭을 보는 동안은 부르지 않고, 돌아왔을 때 간격이 지났으면 한 번 바로 부른다", () => {
    mock.timers.enable({ apis: ["setInterval", "Date"] });
    const doc = fakeDoc();
    let n = 0;
    const stop = startVisibleInterval(() => { n += 1; }, 1000, doc);
    mock.timers.tick(1000);
    assert.equal(n, 1);
    doc.setHidden(true);
    mock.timers.tick(5000);
    assert.equal(n, 1); // 가려진 동안 0번
    doc.setHidden(false);
    assert.equal(n, 2); // 돌아오자 한 번
    stop();
    mock.timers.reset();
  });

  it("돌아왔을 때 간격이 아직 안 지났으면 바로 부르지 않는다", () => {
    mock.timers.enable({ apis: ["setInterval", "Date"] });
    const doc = fakeDoc();
    let n = 0;
    const stop = startVisibleInterval(() => { n += 1; }, 1000, doc);
    mock.timers.tick(1000);
    doc.setHidden(true);
    mock.timers.tick(200);
    doc.setHidden(false);
    assert.equal(n, 1);
    stop();
    mock.timers.reset();
  });

  it("앞 호출이 아직 안 끝났으면 건너뛰고, 끝나면 다시 부른다", async () => {
    mock.timers.enable({ apis: ["setInterval", "Date"] });
    const doc = fakeDoc();
    let n = 0;
    let finish: () => void = () => {};
    const stop = startVisibleInterval(() => {
      n += 1;
      return new Promise<void>((resolve) => { finish = resolve; });
    }, 1000, doc);
    mock.timers.tick(1000);
    mock.timers.tick(1000);
    mock.timers.tick(1000);
    assert.equal(n, 1); // 오는 중이라 두 번은 건너뜀
    finish();
    await Promise.resolve();
    await Promise.resolve();
    mock.timers.tick(1000);
    assert.equal(n, 2);
    stop();
    mock.timers.reset();
  });

  it("호출이 실패해도(거절) 다음 차례에 다시 부른다", async () => {
    mock.timers.enable({ apis: ["setInterval", "Date"] });
    const doc = fakeDoc();
    let n = 0;
    const stop = startVisibleInterval(() => {
      n += 1;
      return Promise.reject(new Error("서버 오류"));
    }, 1000, doc);
    mock.timers.tick(1000);
    await Promise.resolve();
    await Promise.resolve();
    mock.timers.tick(1000);
    assert.equal(n, 2);
    stop();
    mock.timers.reset();
  });
});
