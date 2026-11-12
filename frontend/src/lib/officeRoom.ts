// 오피스 단톡방 대화 목록 (dashboard-api `/api/dashboard/office-room`).
// 단톡방(AI 오피스 단톡방·AI 대화방 전체 대화방)에서 부서 AI에게 건 대화는 모두에게 보인다. 1:1 직접 대화는 올리지 않는다.
// 목록에는 "어느 부서의 어느 대화를 누가 걸었는지"와 마지막으로 보낸 시각만 있고, 원문은 각 부서 대화 창구에서 읽는다.
import { DASHBOARD_API_URL, SAFETY_HUMAN_TOKEN } from "@/lib/env";

export interface OfficeEntry {
  agent: string;
  threadId: string;
  fromId: string;
  fromName: string;
  createdAt: string;
  updatedAt: string;
  /** 대화가 오간 방: 전체 대화방 "all", 장애·이슈 방은 총괄 사건 context_id. 예전 서버(칸 없음)는 "all" */
  roomId: string;
}

/** 전체 대화방 방 번호 (dashboard-api office_room.ALL_ROOM, lib/agentChat ALL_ROOM과 같은 값) */
export const ROOM_ALL = "all";

/** 목록을 다시 읽는 간격(ms). 목록 읽기는 부서 AI를 부르지 않는다 */
export const ROOM_POLL_MS = 4000;

const URL = `${DASHBOARD_API_URL}/api/dashboard/office-room`;
const headers = () => ({ "Content-Type": "application/json", ...(SAFETY_HUMAN_TOKEN ? { Authorization: `Bearer ${SAFETY_HUMAN_TOKEN}` } : {}) });

const toEntry = (x: Record<string, unknown>): OfficeEntry | null =>
  typeof x.agent === "string" && typeof x.thread_id === "string"
    ? {
        agent: x.agent,
        threadId: x.thread_id,
        fromId: String(x.from_id ?? ""),
        fromName: String(x.from_name ?? ""),
        createdAt: String(x.created_at ?? ""),
        updatedAt: String(x.updated_at ?? ""),
        roomId: typeof x.room_id === "string" && x.room_id ? x.room_id : ROOM_ALL,
      }
    : null;

async function call(init?: RequestInit): Promise<unknown> {
  const res = await fetch(URL, { cache: "no-store", ...init, headers: headers() });
  const body = (await res.json().catch(() => null)) as { data?: unknown; error?: { message?: string } } | null;
  if (!res.ok || !body) throw new Error(body?.error?.message ?? `단톡방 목록 서버 응답 ${res.status}`);
  return body.data;
}

/** 최근 24시간 안에 말이 오간 단톡방 대화 (최근 것이 먼저) */
export async function listOfficeRoom(): Promise<OfficeEntry[]> {
  const data = await call();
  return Array.isArray(data) ? data.map((x) => toEntry(x as Record<string, unknown>)).filter((x): x is OfficeEntry => !!x) : [];
}

/** 단톡방에서 말을 보냈다고 올린다 (보낼 때마다. 처음이면 새 줄, 있으면 마지막 시각만 바뀐다. 방은 처음 올린 방 그대로) */
export async function touchOfficeRoom(x: { agent: string; threadId: string; fromId: string; fromName: string; roomId: string }): Promise<OfficeEntry | null> {
  const data = await call({
    method: "POST",
    body: JSON.stringify({ agent: x.agent, thread_id: x.threadId, from_id: x.fromId, from_name: x.fromName.slice(0, 40) || "이름 없음", room_id: x.roomId }),
  });
  return data && typeof data === "object" ? toEntry(data as Record<string, unknown>) : null;
}
