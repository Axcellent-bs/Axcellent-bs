// AI 대화방 직접 대화 중 dashboard-api 계열 창구(마케팅 등). 개발 AI(/api/planning/dev-chat)는 planning.ts의 chatApi를 쓴다.
// 응답: `{data, meta}` 봉투, 실패는 `{error:{code,message}}`. 쓰기(POST)에는 사람 토큰을 붙인다. 지어낸 값은 없다.
import { SAFETY_HUMAN_TOKEN } from "@/lib/env";
import type { ChatThread as BaseThread } from "@/lib/planningData";

/** 대화 하나. 서버가 남의 대화를 가리면 messages가 비고 restricted·summary가 온다 */
export type ChatThread = BaseThread & { who?: string; summary?: string; restricted?: boolean; messageCount?: number };

/** 대화 목록·상세·보내기 주소. 닫기(`/<id>/close`)는 `directChatApi(ep, {closable: true})`로 켠 창구만 쓴다 */
export interface ChatEndpoint { base: string; path: string }

export class DirectChatError extends Error {
  constructor(message: string, readonly status: number, readonly code: string) { super(message); }
}

/** 답을 기다리는 중(`answering`)이면 이 간격(ms)마다 상세를 다시 읽는다 */
export const POLL_MS = 2000;
export const isAnswering = (t: { status: string } | null | undefined) => t?.status === "answering";

export type FailKind = "preparing" | "forbidden" | "other";
/**
 * 실패를 화면 상태로 가른다.
 * - preparing: 백엔드가 아직 없음(404·연결 실패) 또는 LLM 키 미설정(409 LLM_NOT_CONFIGURED) → "준비 중"
 * - forbidden: 403 → 입력을 막고 이유를 보여 준다
 */
export function classifyFailure(e: unknown): FailKind {
  if (e instanceof DirectChatError) {
    if (e.status === 403) return "forbidden";
    if (e.status === 404 || e.status === 502 || e.status === 503 || e.status === 504) return "preparing";
    if (e.status === 409 && e.code === "LLM_NOT_CONFIGURED") return "preparing";
    return "other";
  }
  // fetch가 연결 못 하면 TypeError
  return e instanceof TypeError ? "preparing" : "other";
}

const camel = (k: string) => k.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
function camelize(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(camelize);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [camel(k), camelize(x)]));
  return v;
}
type Json = Record<string, unknown>;
const isThread = (x: unknown): x is Json => !!x && typeof x === "object" && typeof (x as Json).id === "string" && Array.isArray((x as Json).messages);
export const toThread = (x: Json): ChatThread => camelize(x) as ChatThread;
export const toThreads = (d: unknown): ChatThread[] => (Array.isArray(d) ? d.filter(isThread).map(toThread) : []);

async function request(ep: ChatEndpoint, suffix: string, init?: RequestInit): Promise<unknown> {
  const res = await fetch(`${ep.base}${ep.path}${suffix}`, {
    cache: "no-store",
    ...init,
    headers: { "Content-Type": "application/json", ...(SAFETY_HUMAN_TOKEN ? { Authorization: `Bearer ${SAFETY_HUMAN_TOKEN}` } : {}) },
  });
  const body = (await res.json().catch(() => ({}))) as { data?: unknown; error?: { code?: string; message?: string }; detail?: unknown };
  if (!res.ok) throw new DirectChatError(body.error?.message ?? (typeof body.detail === "string" ? body.detail : `서버 응답 ${res.status}`), res.status, body.error?.code ?? `HTTP_${res.status}`);
  return body.data;
}

/** `closable`: 이 창구가 `POST <주소>/<id>/close`(대화 끝내기)를 갖고 있을 때만 켠다 (고객지원) */
export const directChatApi = (ep: ChatEndpoint, opts: { closable?: boolean } = {}) => {
  const one = (d: unknown) => {
    if (!isThread(d)) throw new DirectChatError("대화 서버가 대화를 돌려주지 않았어요", 502, "NO_THREAD");
    return toThread(d);
  };
  return {
    list: () => request(ep, "").then(toThreads),
    get: (id: string) => request(ep, `/${encodeURIComponent(id)}`).then(one),
    send: (x: { thread_id?: string; message: string }) => request(ep, "", { method: "POST", body: JSON.stringify(x) }).then(one),
    ...(opts.closable ? { close: (id: string) => request(ep, `/${encodeURIComponent(id)}/close`, { method: "POST", body: "{}" }).then(one) } : {}),
  };
};
