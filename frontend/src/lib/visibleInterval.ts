import { useEffect, useRef } from "react";

/** `visibilityState`만 보는 아주 작은 문서 모양(테스트에서 가짜로 끼운다) */
export interface VisibleDoc {
  hidden: boolean;
  addEventListener(type: "visibilitychange", listener: () => void): void;
  removeEventListener(type: "visibilitychange", listener: () => void): void;
}

/**
 * 몇 초마다 새로 받는 화면용 반복 호출. 멈추려면 돌려받은 함수를 부른다.
 * - 다른 탭을 보는 동안(`document.hidden`)은 부르지 않는다. 돌아왔을 때 `ms`가 지났으면 한 번 바로 부른다.
 * - 앞 호출(Promise)이 아직 안 끝났으면 이번 차례는 건너뛴다(같은 요청이 겹쳐 나가지 않게).
 */
export function startVisibleInterval(
  run: () => void | Promise<unknown>,
  ms: number,
  doc: VisibleDoc = document,
  now: () => number = Date.now,
): () => void {
  let last = now();
  let busy = false;
  const fire = () => {
    if (busy) return;
    last = now();
    let out: void | Promise<unknown>;
    try {
      out = run();
    } catch {
      return;
    }
    if (out && typeof (out as Promise<unknown>).then === "function") {
      busy = true;
      const done = () => { busy = false; };
      (out as Promise<unknown>).then(done, done);
    }
  };
  const timer = setInterval(() => { if (!doc.hidden) fire(); }, ms);
  const onShow = () => { if (!doc.hidden && now() - last >= ms) fire(); };
  doc.addEventListener("visibilitychange", onShow);
  return () => {
    clearInterval(timer);
    doc.removeEventListener("visibilitychange", onShow);
  };
}

/** React 훅: `startVisibleInterval`을 화면이 열려 있는 동안 켠다. `run`이 바뀌어도 타이머는 다시 만들지 않는다 */
export function useVisibleInterval(run: () => void | Promise<unknown>, ms: number): void {
  const runRef = useRef(run);
  useEffect(() => { runRef.current = run; });
  useEffect(() => startVisibleInterval(() => runRef.current(), ms), [ms]);
}
