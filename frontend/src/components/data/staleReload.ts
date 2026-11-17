// 예전 결과를 먼저 받았을 때(basis.stale) 한 번 다시 부르기 (11/17).
// 같은 예전 결과(계산 시각이 같음)에는 한 번만 다시 부른다. 매출 분석·고객·시장 분석 칸이 같이 쓴다.
import { useEffect, useRef } from "react";
import { STALE_RELOAD_MS } from "@/lib/salesView";

export function useStaleReload(stale: boolean, stamp: string | null, reload: () => unknown): void {
  const ref = useRef(reload);
  useEffect(() => { ref.current = reload; });
  useEffect(() => {
    if (!stale) return;
    const t = setTimeout(() => { void ref.current(); }, STALE_RELOAD_MS);
    return () => clearTimeout(t);
  }, [stale, stamp]);
}
