import { useSyncExternalStore } from "react";
import { readShowLog, saveShowLog } from "@/lib/quietLog";

// "작업 기록 보기" 토글: 방과 상관없이 하나 (AI 대화방 칸 여러 개·AI 오피스 오른쪽 칸이 같은 값을 본다). 저장 규칙은 lib/quietLog.ts

const storage = (): Storage | null => { try { return typeof window === "undefined" ? null : window.localStorage; } catch { return null; } };
let show = readShowLog(storage());
const subs = new Set<() => void>();
const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };

export function setShowLog(v: boolean) {
  show = v;
  saveShowLog(storage(), v); // 실패해도 이번 화면에서는 바뀐다
  for (const f of subs) f();
}

export function useQuietLog(): [boolean, (v: boolean) => void] {
  const v = useSyncExternalStore(subscribe, () => show, () => false);
  return [v, setShowLog];
}
