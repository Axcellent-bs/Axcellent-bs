import { useEffect, useSyncExternalStore } from "react";
import { SAFETY_HUMAN_TOKEN } from "@/lib/env";
import { onHumanTokenChange } from "@/lib/humanToken";
import { orchestrator } from "@/lib/orchestrator";
import type { WorkRequestRow, WorkRequestTask } from "@/lib/workRequests";

// 총괄 고객 의뢰 목록(GET /api/requests)을 10초마다 한 번만 읽어 같은 페이지의 쓰는 곳이 나눠 쓴다:
// 고객 의뢰 넘김 카드(RequestHandoffCard) · AI 오피스 자리 불(deriveOffice requests) · 대화방 의뢰 진행 카드(RequestProgressCard) · 대화방 조회 줄 거르기.
// 고객 글이 들어 있어 사람 토큰으로만 읽는다. 토큰이 없으면 읽지 않고 rows는 늘 null(지금 화면 그대로).

export const WORK_REQUESTS_POLL_MS = 10_000;

export interface WorkRequestsState {
  hasToken: boolean;
  /** 아직 못 읽었으면 null */
  rows: WorkRequestRow[] | null;
  tasks: WorkRequestTask[];
  failed: boolean;
}

let state: WorkRequestsState = { hasToken: SAFETY_HUMAN_TOKEN.length > 0, rows: null, tasks: [], failed: false };
const listeners = new Set<() => void>();
let users = 0;
let timer: ReturnType<typeof setInterval> | null = null;

const emit = (next: Partial<WorkRequestsState>) => {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
};

/** 지금 한 번 다시 읽는다 (사람 확인 결정 뒤 등) */
export async function refreshWorkRequests(): Promise<void> {
  if (!state.hasToken) return;
  try {
    const { data, meta } = await orchestrator.workRequests();
    emit({ rows: data, tasks: meta.tasks ?? [], failed: false });
  } catch {
    emit({ failed: true });
  }
}

function poll() {
  if (timer || !state.hasToken) return;
  void refreshWorkRequests();
  timer = setInterval(() => void refreshWorkRequests(), WORK_REQUESTS_POLL_MS);
}
function start() {
  users += 1;
  if (users === 1) poll();
}
function stop() {
  users -= 1;
  if (users <= 0 && timer) { clearInterval(timer); timer = null; users = 0; }
}

// 로그인·로그아웃으로 사람 토큰이 생기거나 없어지면 따라간다: 생기면 (보는 곳이 있을 때) 읽기 시작, 없어지면 멈추고 비운다
onHumanTokenChange(() => {
  const hasToken = SAFETY_HUMAN_TOKEN.length > 0;
  if (!hasToken && timer) { clearInterval(timer); timer = null; }
  emit(hasToken ? { hasToken } : { hasToken, rows: null, tasks: [], failed: false });
  if (hasToken && users > 0) poll();
});

/** enabled=false면 읽기를 시작하지 않는다(useChatRooms의 enabled를 따른다). 읽은 값은 같은 페이지가 함께 쓴다 */
export function useWorkRequests(enabled = true): WorkRequestsState {
  useEffect(() => {
    if (!enabled) return;
    start();
    return stop;
  }, [enabled]);
  return useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => state);
}
