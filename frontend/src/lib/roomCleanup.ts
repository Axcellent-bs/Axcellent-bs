// 대화방 정리 (11/23, 화면 시안 ①·③). 순수 계산만 한다: `npm test`(node --test)에서 그대로 돈다.
// ① 장애·이슈 방 삭제 = 모두에게 숨김. 서버(dashboard-api office-room hidden)가 숨긴 방은 방 목록에서 뺀다.
//    해결된 사건만 지울 수 있다(진행 중이면 메뉴 항목을 흐리게). 기록은 서버에 남고 "숨긴 방 보기"에서 되돌린다.
//    내 브라우저에서만 숨기기(lib/hiddenRooms.ts)는 그대로 따로 있다.
// ③ /clear = 나만 지우기. 단톡방은 서버에 지운 시각(cleared_at)을 두고 그 앞 말풍선·카드를 내 화면에서만 뺀다.
//    1:1 AI 대화는 새 대화를 시작한다(이전 대화는 "지난 대화"에 남음).
import type { ChatItem } from "./agentChat.ts";

// ---- ③ /clear
export const CLEAR_COMMAND = "/clear";
export const CLEAR_HINT = "이 방의 지금까지 대화를 내 화면에서 지워요";
export const DIRECT_CLEAR_HINT = "새 대화를 시작해요 (AI가 앞 대화를 잊어요)";
export const NEW_THREAD_NOTE = "새 대화를 시작했어요";
// 11/24 1:1 [새 대화] 확인은 브라우저 confirm() 대신 버튼 아래 화면 안 상자로 받는다 (/clear와 같은 새 대화 시작 함수)
export const NEW_THREAD_CONFIRM = { head: "새 대화를 시작할까요?", body: "지금 대화는 '내 지난 대화'에 남아요." };
/** [새 대화]를 누르면 먼저 물어보나: 남의 대화를 보는 중(readOnly)이면 묻지 않고 바로 내 대화로 돌아간다 */
export const newThreadNeedsConfirm = (readOnly: boolean) => !readOnly;

/** 입력이 /clear 명령인가 (앞뒤 빈칸만 허용). 명령이면 메시지로 보내지 않는다 */
export const isClearCommand = (text: string) => text.trim().toLowerCase() === CLEAR_COMMAND;

/** "/"로 시작하면 보일 명령 안내 한 줄. /clear로 이어질 수 없는 글이면 null */
export function commandHint(text: string, direct = false): string | null {
  const t = text.trimStart().toLowerCase();
  if (!t.startsWith("/") || /\s/.test(t.trimEnd())) return null;
  if (!CLEAR_COMMAND.startsWith(t.trimEnd())) return null;
  return `${CLEAR_COMMAND} · ${direct ? DIRECT_CLEAR_HINT : CLEAR_HINT}`;
}

const time = (iso: string | null | undefined) => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? null : t;
};

/** 지운 시각 뒤의 말풍선·카드만 (내 화면에서만). 시각이 없는 줄은 지운 앞쪽으로 본다. 지운 적 없으면 그대로 */
export function afterClear<T extends Pick<ChatItem, "at">>(items: readonly T[], clearedAt: string | null | undefined): T[] {
  const cut = time(clearedAt);
  if (cut === null) return [...items];
  return items.filter((it) => {
    const at = time(it.at);
    return at !== null && at > cut;
  });
}

/** 구분선 문구: "HH:mm · 여기까지 지웠어요 (나만 안 보여요)" (뒤에 [되돌리기]) */
export function clearDividerText(clearedAt: string): string {
  const d = new Date(clearedAt);
  const hm = Number.isNaN(d.getTime()) ? "" : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")} · `;
  return `${hm}여기까지 지웠어요 (나만 안 보여요)`;
}

/** 서버 목록(room_id·cleared_at) → 방 id → 지운 시각 */
export const clearMap = (rows: readonly { room_id: string; cleared_at: string | null }[]) =>
  Object.fromEntries(rows.filter((r) => r.cleared_at).map((r) => [r.room_id, r.cleared_at as string])) as Record<string, string>;

// ---- ① 장애·이슈 방 삭제 (모두에게 숨김)
export const HIDE_LABEL = "방 삭제 (모두에게 숨김)";
export const HIDE_WAIT = "해결된 뒤 삭제할 수 있어요";
const RUNNING = new Set(["open", "degraded"]);

/** 진행 중인 장애인가 (open·degraded). 상태를 모르면 진행 중으로 본다(서버도 확인 못 하면 막는다) */
export const isRunningIncident = (status: string | undefined) => !status || RUNNING.has(status);

/** ⋯ 메뉴의 장애 방 삭제 항목 모양 */
export function incidentHideItem(status: string | undefined): { label: string; sub: string; disabled: boolean; danger: boolean } {
  return isRunningIncident(status)
    ? { label: HIDE_LABEL, sub: HIDE_WAIT, disabled: true, danger: true }
    : { label: HIDE_LABEL, sub: "팀 모두의 방 목록에서 사라져요 · 되돌릴 수 있어요", disabled: false, danger: true };
}

/** 확인 상자 문구 (시안 그대로) */
export const hideConfirm = (name: string) => ({
  head: `${name} 방을 삭제할까요?`,
  body: "팀 모두의 방 목록에서 사라져요. 장애 기록과 대화는 서버에 남고, '숨긴 방 보기'에서 되돌릴 수 있어요.",
});

/** 방 목록에서 서버가 숨긴 방을 뺀다 */
export const withoutServerHidden = <R extends { id: string }>(rooms: readonly R[], hidden: ReadonlySet<string>) =>
  rooms.filter((r) => !hidden.has(r.id));

/** "숨긴 방 N개 보기" 목록: 서버 숨김(되돌리기)과 내 브라우저 숨김(다시 보이기)을 함께. 같은 방이면 서버 숨김으로 한 번 */
export function hiddenRows(server: readonly string[], local: readonly string[]): { id: string; source: "server" | "local" }[] {
  const seen = new Set<string>();
  const out: { id: string; source: "server" | "local" }[] = [];
  for (const id of server) if (!seen.has(id)) { seen.add(id); out.push({ id, source: "server" }); }
  for (const id of local) if (!seen.has(id)) { seen.add(id); out.push({ id, source: "local" }); }
  return out;
}
