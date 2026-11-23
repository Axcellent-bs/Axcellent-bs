// 단톡방(전체 대화방·이슈 방)에서 부서 AI에게 건 대화의 순수 규칙 (components/office/useOfficeTalk.ts). node --test (officeTalkView.test.ts).
// 공통 약속 (11/20):
// 1. safety·hr·archive 대화 보내기 body에 선택 room_id(1~64자). 단톡방에서 보낼 때만 넣는다. 1:1 직접 대화(DirectChat)에서는 넣지 않는다
// 2. 대화 상세에 shared(기본 false)·owner. shared면 남의 대화라도 messages가 오고 restricted=false
// 3. 8곳 중계 부서(dashboard-api 창구)는 보내기 body를 바꾸지 않는다 (단톡방인지는 office-room 줄로 정한다).
//    POST /office-room이 403 NOT_THREAD_OWNER면 단톡방 줄을 만들지 않는다
import { roomChatCard, type RoomChatCard } from "./chatCards.ts";

/** 보내기 body에 room_id를 넣는 부서 (부서 서버가 직접 받는 창구). 그 밖 8곳은 dashboard-api 중계라 body를 바꾸지 않는다 */
export const ROOM_ID_AGENTS: ReadonlySet<string> = new Set(["safety", "hr", "archive"]);

export interface SendBody { thread_id?: string; message: string; room_id?: string; record_id?: string }

/** 단톡방에서 보내는 body. 이어 쓸 내 대화가 있으면 thread_id, safety·hr·archive면 room_id(1~64자일 때만) */
export function officeSendBody(x: { agent: string; room: string; threadId: string | null; message: string }): SendBody {
  const room = x.room.trim();
  return {
    ...(x.threadId ? { thread_id: x.threadId } : {}),
    message: x.message,
    ...(ROOM_ID_AGENTS.has(x.agent) && room.length >= 1 && room.length <= 64 ? { room_id: room } : {}),
  };
}

export interface ThreadLook { restricted?: boolean; shared?: boolean; summary?: string | null }

/** 단톡방에 그릴 모양: shared면 남의 대화라도 말풍선, restricted(1:1·예전 줄)면 지금처럼 한 줄 요약 */
export function threadDisplay(t: ThreadLook, who: string, agentName: string): { kind: "bubbles" } | { kind: "restricted"; text: string } {
  if (t.shared) return { kind: "bubbles" };
  if (t.restricted) return { kind: "restricted", text: `${who}님이 ${agentName}와 나눈 대화${t.summary ? `: ${t.summary}` : " (원문은 본인만 봐요)"}` };
  return { kind: "bubbles" };
}

/** 보낸 사람 이름: 내 대화면 내 이름, 남의 대화면 서버 owner, 없으면 단톡방 목록의 from_name */
export function senderName(x: { mine: boolean; accountName: string | null | undefined; owner: string | null | undefined; fromName: string | null | undefined }): string {
  if (x.mine) return x.accountName || "나";
  return x.owner?.trim() || x.fromName?.trim() || "다른 사람";
}

/**
 * 내가 이어 써도 되는 대화인지 (남의 thread_id로 보내지 않게).
 * - 단톡방 목록상 내가 건 대화(from_id = 내 계정)여야 한다
 * - 이 브라우저가 기억한 주인(owners: 대화 id → 계정 id)이 있으면 그것이 나여야 한다
 * - 서버 owner가 있으면 내 계정 id나 이름이어야 한다
 */
export function ownsThread(
  x: { fromId: string; threadId: string; owner?: string | null },
  me: { id: string; name?: string | null },
  owners: Record<string, string> = {},
): boolean {
  if (x.fromId !== me.id) return false;
  const local = owners[x.threadId];
  if (local && local !== me.id) return false;
  const owner = x.owner?.trim();
  if (owner && owner !== me.id && owner !== (me.name ?? "").trim()) return false;
  return true;
}

/** POST /office-room이 "남의 대화" 거절(403 NOT_THREAD_OWNER)인지 → 단톡방 줄을 만들지 않고 콘솔 경고만 */
/** 대화방 말풍선 항목에 붙일 데이터분석 카드 칸 (11/23). 분석 카드가 아니면 빈 칸이라 지금 그대로다.
 *  ChatCardView(1:1과 같은 컴포넌트)가 이 칸으로 [의뢰 넣기]·진행·완료 카드를 그린다 */
export function roomCardField(m: { id: string; card?: unknown }, threadId: string | null | undefined): { chatCard?: RoomChatCard } {
  const c = roomChatCard(m, threadId);
  return c ? { chatCard: c } : {};
}

export function notThreadOwner(e: unknown): boolean {
  if (!e || typeof e !== "object") return false;
  const x = e as { status?: unknown; code?: unknown };
  return x.status === 403 && x.code === "NOT_THREAD_OWNER";
}
