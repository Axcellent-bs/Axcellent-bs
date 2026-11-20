// "작업 기록 보기" 선택 (방과 상관없이 하나, AI 대화방·AI 오피스 오른쪽 칸이 같이 쓴다). node --test (quietLog.test.ts)
// 브라우저 저장소에 기억한다. 읽기·쓰기가 실패하면(사생활 보호 모드 등) 접힘(false)으로 두고, 이번 화면에서는 그대로 바뀐다

export const QUIET_LOG_KEY = "axcellent-chat-show-worklog";

type Store = Pick<Storage, "getItem" | "setItem"> | null | undefined;

export function readShowLog(store: Store): boolean {
  try { return store?.getItem(QUIET_LOG_KEY) === "1"; } catch { return false; }
}

/** 저장했으면 true. 실패해도 던지지 않는다 */
export function saveShowLog(store: Store, show: boolean): boolean {
  try { store?.setItem(QUIET_LOG_KEY, show ? "1" : "0"); return !!store; } catch { return false; }
}

/** 접힌 줄 안내 문구 */
export const quietLogText = (logCount: number, show: boolean) => (show ? `작업 기록 ${logCount}줄 보는 중` : `작업 기록 ${logCount}줄 접힘`);

type LaneItem = { kind: string; lane?: "log"; tone?: string };

/** 말풍선 수 (안 읽은 수·방 목록): 작업 기록 줄은 세지 않는다 */
export const chatMessageCount = (items: readonly LaneItem[]) => items.filter((i) => i.kind === "msg" && i.lane !== "log").length;

/** 대화 줄과 작업 기록 줄 나누기: chat(접은 화면에 보일 줄) · log(작업 기록 말풍선) · 실패 수(작업 기록 중 ✕) */
export function timelineLanes<T extends LaneItem>(items: readonly T[]): { chat: T[]; log: T[]; logCount: number; failCount: number } {
  const chat: T[] = [];
  const log: T[] = [];
  for (const it of items) (it.kind === "msg" && it.lane === "log" ? log : chat).push(it);
  return { chat, log, logCount: log.length, failCount: log.filter((it) => it.tone === "fail").length };
}
