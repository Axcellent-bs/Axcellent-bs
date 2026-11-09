import { useCallback, useEffect, useRef, useState } from "react";
import { kpiLive, type A2AResult, type Evidence } from "@/lib/data";

// 트렌드&콘텐츠 화면이 데이터 AI 스킬 하나를 받는 상태. 한 번 받은 응답을 여러 칸(요약 카드·카테고리·기획안)이 같이 쓴다.
// 토큰이 없으면(kpiLive = false) 부르지 않는다. reload를 누르면 다시 받는다(run = 누른 횟수)

/** ok면 응답 값(out)과 서버 근거(evidence: 부른 API·수집 시각) */
export type SkillState<T> = { kind: "loading" } | { kind: "ok"; out: T; evidence: Evidence | null } | { kind: "failed"; reason: string };

export function useSkill<T>(load: (run: number) => Promise<A2AResult<T>>): { state: SkillState<T>; run: number; reload: () => void } {
  const [run, setRun] = useState(0);
  const [state, setState] = useState<SkillState<T>>({ kind: "loading" });
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; });
  useEffect(() => {
    if (!kpiLive) return;
    let live = true;
    setState({ kind: "loading" });
    loadRef.current(run)
      .then((r) => live && setState({ kind: "ok", out: r.output, evidence: r.evidence ?? null }))
      .catch((e: Error) => live && setState({ kind: "failed", reason: e.message }));
    return () => { live = false; };
  }, [run]);
  const reload = useCallback(() => setRun((n) => n + 1), []);
  return { state, run, reload };
}

/** 응답이 왔으면 그 값, 아니면 null */
export const okOut = <T,>(s: SkillState<T>): T | null => (s.kind === "ok" ? s.out : null);

/** 응답이 왔으면 서버 근거, 아니면 null */
export const okEvidence = <T,>(s: SkillState<T>): Evidence | null => (s.kind === "ok" ? s.evidence : null);
