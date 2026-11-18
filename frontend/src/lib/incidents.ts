import { useSyncExternalStore } from "react";
import { relayError } from "@/lib/adminRelay";
import type { OrchIncident } from "@/lib/agentChat";
import { hr } from "@/lib/hr";

// 총괄 이슈 목록을 한 번만 읽어서 화면 여러 곳이 같이 쓴다(총괄·아카이브 AI 장애 기록·결정 기록).
// 읽는 곳이 하나라도 열려 있으면 10초마다 새로 받고, 모두 닫히면 멈춘다. 읽기 전용이다.
const POLL_MS = 10_000;

interface State {
  incidents: OrchIncident[] | null;
  error: string | null;
  updatedAt: Date | null;
}

let state: State = { incidents: null, error: null, updatedAt: null };
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;
let inflight: Promise<void> | null = null;

const emit = () => listeners.forEach((l) => l());

export function reloadIncidents(): Promise<void> {
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const incidents = await hr.incidents();
      state = { incidents, error: null, updatedAt: new Date() };
    } catch (e) {
      // 받지 못해도 마지막으로 받은 목록은 그대로 둔다
      state = { ...state, error: relayError(e as { status?: number; message: string }) };
    } finally {
      inflight = null;
      emit();
    }
  })();
  return inflight;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1) {
    void reloadIncidents();
    timer = setInterval(() => { if (!document.hidden) void reloadIncidents(); }, POLL_MS); // 다른 탭을 볼 때는 받지 않는다
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}

/** 총괄 이슈 목록. `incidents`가 null이면 아직 처음 받는 중이다 */
export function useIncidents(): State & { reload: () => Promise<void> } {
  const s = useSyncExternalStore(subscribe, () => state);
  return { ...s, reload: reloadIncidents };
}
