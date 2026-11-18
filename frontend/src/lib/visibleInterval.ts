import { useEffect, useRef } from "react";

// 주기 새로고침 규칙: 앞 요청이 안 끝났으면 건너뛰고, 탭이 가려져 있으면 받지 않는다.
// 다시 보이면 마지막으로 받은 뒤 간격이 지났을 때만 한 번 받는다. (수동 새로고침 버튼은 이 규칙을 거치지 않는다)

export type PollerDeps = { isHidden: () => boolean; now: () => number };

export function createPoller(run: () => Promise<unknown> | void, intervalMs: number, deps: PollerDeps) {
  let inFlight = false;
  let lastAt = deps.now();
  const start = async (): Promise<boolean> => {
    if (inFlight) return false;
    inFlight = true;
    lastAt = deps.now();
    try { await run(); } catch { /* 호출부가 오류를 처리한다 */ } finally { inFlight = false; }
    return true;
  };
  return {
    /** 마운트 직후 첫 로드. 가려져 있어도 받는다. */
    first: start,
    /** 타이머 한 번. 가려져 있거나 앞 요청이 남았으면 건너뛴다. */
    tick: (): Promise<boolean> => (deps.isHidden() ? Promise.resolve(false) : start()),
    /** 탭이 다시 보일 때. 간격이 지났을 때만 받는다. */
    visible: (): Promise<boolean> => (deps.isHidden() || deps.now() - lastAt < intervalMs ? Promise.resolve(false) : start()),
  };
}

/**
 * `immediate: false`면 마운트 때 첫 로드를 하지 않는다(호출부가 따로 첫 로드를 하거나, 처음 값이 다른 길로 오는 화면용).
 * 기본은 마운트 직후 한 번 부른다.
 */
export function useVisibleInterval(run: () => Promise<unknown> | void, intervalMs: number, opts: { immediate?: boolean } = {}) {
  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => {
    const poller = createPoller(() => runRef.current(), intervalMs, { isHidden: () => document.hidden, now: Date.now });
    if (opts.immediate !== false) void poller.first();
    const timer = window.setInterval(() => { void poller.tick(); }, intervalMs);
    const onVis = () => { void poller.visible(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", onVis); };
  }, [intervalMs]);
}
