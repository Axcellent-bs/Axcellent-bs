import { useEffect, useRef } from "react";

/**
 * 처음 한 번 받고, 그 뒤 `ms`마다 다시 받는다.
 * - 앞 요청이 아직 안 끝났으면 이번 새로 고침은 건너뛴다.
 * - 다른 탭을 보고 있으면(`document.hidden`) 받지 않고, 다시 돌아오면 바로 한 번 받는다.
 * `load`가 바뀌면(예: 월 선택) 새로 시작한다.
 */
export function usePoll(load: () => Promise<unknown>, ms: number): void {
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; });
  useEffect(() => {
    let live = true;
    let inflight = false;
    const run = async () => {
      if (inflight) return;
      inflight = true;
      try { await loadRef.current(); } catch { /* 오류는 load 안에서 처리한다 */ } finally { inflight = false; }
    };
    const tick = () => { if (live && !document.hidden) void run(); };
    void run();
    const timer = setInterval(tick, ms);
    document.addEventListener("visibilitychange", tick);
    return () => { live = false; clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [load, ms]);
}
