import { ALL_ROOM, buildTimeline, deptOf, incidentTitle, messageCount, type ChatItem, type OrchIncident } from "@/lib/agentChat";
import type { ExecutionRecord } from "@/lib/finance";
import { incidentNo } from "@/lib/format";
import { mergeTalk } from "@/lib/officeTalk";
import type { CommLine } from "@/lib/types";

// AI 대화방의 방 목록과 방별 대화. 대화 기록(/agents/chat)과 AI 오피스(/agents/office)가 같이 쓴다.
// 방은 세 가지다.
// - 전체 대화방(ALL_ROOM): 모든 부서 기록 + 전체 대화방에서 사람이 건 말
// - 장애·이슈 방: 총괄 사건 하나 = 방 하나(방 id = context_id). 그 사건 기록 + 그 방에서 사람이 건 말
// - 1:1 대화(DIRECT_PREFIX + 부서): 나만 보인다. 대화는 DirectChat이 따로 읽고, 여기 기록과 섞지 않는다
// 값은 기록에 있는 것만 쓴다. 없는 값은 비워 둔다.

export type RoomKind = "all" | "incident" | "direct";

export interface ChatRoom {
  id: string;
  kind: RoomKind;
  /** 방 이름. 장애 방은 "짧은 유형 #짧은번호" */
  title: string;
  /** 장애 방의 긴 문장 이름 (방 머리 아래 보조 줄) */
  longTitle?: string;
  /** 장애 방의 총괄 사건 상태 (open·resolved·degraded) */
  status?: string;
  /** 말풍선 수 (안 읽은 수 계산에 쓴다) */
  count: number;
  /** 마지막 말 한 줄 ("보낸 쪽: 내용"). 말이 없으면 없음 */
  preview?: string;
  /** 마지막 말 시각 */
  lastAt?: string;
}

export interface RoomSources {
  incidents: OrchIncident[];
  executions: ExecutionRecord[];
  comms: CommLine[];
  /** 단톡방 사람 대화 (useOfficeTalk items, 방은 item.room) */
  talk: ChatItem[];
}

/** 장애 방 짧은 번호: 사건 번호 앞 4자리 ("#066f") */
export const shortNo = (contextId: string) => `#${incidentNo(contextId).slice(0, 4)}`;

/**
 * 장애 방 짧은 유형. 총괄 사건의 event_type("구성요소:지표", 총괄 naming.event_type)으로 먼저 찾고,
 * 없으면 구성요소만으로 찾는다. 둘 다 모르면 긴 제목 앞 12자 + "…".
 * 구성요소·지표 코드는 총괄 naming.py(COMPONENT_KO·METRIC_KO)와 인프라 감시(dejafix_watch)가 쓰는 값이다.
 */
export const SHORT_KIND_BY_EVENT: Record<string, string> = {
  "payment:error_rate_5xx": "결제 실패",
  "payment:http_5xx": "결제 실패",
  "payment:payment_fail_rate": "결제 실패",
  "payment:p95_latency_ms": "결제 지연",
  "payment:order_fail_rate": "주문 실패",
  "api:error_rate_5xx": "쇼핑몰 오류",
  "api:p95_latency_ms": "쇼핑몰 지연",
};
export const SHORT_KIND_BY_COMPONENT: Record<string, string> = {
  payment: "결제 장애", api: "쇼핑몰 장애", db: "주문 DB 장애", search: "검색 장애", shipping: "배송 장애",
};
/** 총괄 제목 끝 "(지표)"에서 지표 코드 (예전 제목 형식). event_type이 없을 때만 쓴다 */
const metricInTitle = (title: string) => /\(([a-z0-9_]+)\)\s*$/.exec(title)?.[1] ?? null;

export function shortKind(inc: Pick<OrchIncident, "title" | "component" | "event_type">): string {
  const eventType = inc.event_type ?? (metricInTitle(inc.title) ? `${inc.component}:${metricInTitle(inc.title)}` : null);
  const known = (eventType && SHORT_KIND_BY_EVENT[eventType]) || SHORT_KIND_BY_COMPONENT[inc.component];
  if (known) return known;
  const long = incidentTitle(inc);
  return long.length > 12 ? `${long.slice(0, 12)}…` : long;
}
/** 장애 방 이름(목록·탭·방 머리): "결제 실패 #066f". 긴 문장은 incidentTitle(방 머리 아래 보조 줄) */
export const incidentRoomTitle = (inc: Pick<OrchIncident, "title" | "component" | "context_id" | "event_type">) => `${shortKind(inc)} ${shortNo(inc.context_id)}`;
export const ALL_ROOM_TITLE = "전체 대화방";

const isOpen = (inc: OrchIncident) => inc.status === "open";
const opened = (inc: OrchIncident) => (inc.opened_at ? Date.parse(inc.opened_at) : 0);

/** 장애·이슈 방 순서: 진행 중 먼저, 그다음 해결됨. 같은 묶음 안에서는 최근에 열린 것 먼저 */
export function sortIncidentRooms(incidents: OrchIncident[]): OrchIncident[] {
  return [...incidents].sort((a, b) => Number(isOpen(b)) - Number(isOpen(a)) || opened(b) - opened(a));
}

/** 방 하나의 대화: 기록(buildTimeline) + 그 방에서 사람이 건 말. 1:1 방은 비어 있다(DirectChat이 따로 그린다) */
export function roomItems(room: string, src: RoomSources): ChatItem[] {
  if (room !== ALL_ROOM && !src.incidents.some((i) => i.context_id === room)) return [];
  const base = buildTimeline(src.incidents, src.executions, src.comms, room);
  return mergeTalk(base, src.talk.filter((t) => t.room === room));
}

/** 마지막 말 한 줄과 시각 (말풍선·시스템 줄 중 마지막) */
function lastLine(items: ChatItem[]): { preview?: string; lastAt?: string } {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it.kind === "msg") return { preview: `${it.from}: ${it.text}`, lastAt: it.at || undefined };
    if (it.kind === "system") return { preview: it.text, lastAt: it.at || undefined };
    if (it.kind === "hold") return { preview: `사람 판단 필요: ${it.title}`, lastAt: it.at || undefined };
  }
  return {};
}

/** 전체 대화방 + 장애·이슈 방 (진행 중 먼저). 1:1 방은 화면이 부서 목록으로 따로 붙인다 */
export function buildRooms(src: RoomSources): { all: ChatRoom; incidents: ChatRoom[]; items: Record<string, ChatItem[]> } {
  const items: Record<string, ChatItem[]> = { [ALL_ROOM]: roomItems(ALL_ROOM, src) };
  for (const inc of src.incidents) items[inc.context_id] = roomItems(inc.context_id, src);
  const all: ChatRoom = { id: ALL_ROOM, kind: "all", title: ALL_ROOM_TITLE, count: messageCount(items[ALL_ROOM]), ...lastLine(items[ALL_ROOM]) };
  const incidents = sortIncidentRooms(src.incidents).map((inc): ChatRoom => ({
    id: inc.context_id,
    kind: "incident",
    title: incidentRoomTitle(inc),
    longTitle: incidentTitle(inc),
    status: inc.status,
    count: messageCount(items[inc.context_id]),
    ...lastLine(items[inc.context_id]),
  }));
  return { all, incidents, items };
}

/**
 * 방 머리 참여 수: "부서 N곳" (+ 사람이 말했으면 " · 사람 M명").
 * 부서는 `deptKeys`(부서 AI 목록, DIRECT_AGENTS 키)에 드는 쪽만 센다. 서비스 이름(coupon 등)은 그 부서로 묶는다.
 * 안전 관문·회사 기억·공통 로그 같은 시스템 기록은 세지 않는다. 아무도 없으면 null
 */
export function roomPeople(items: ChatItem[], deptKeys: readonly string[]): string | null {
  const known = new Set(deptKeys);
  const depts = new Set<string>();
  const people = new Set<string>();
  for (const it of items) {
    if (it.kind !== "msg") continue;
    if (it.fromKey === "human") { people.add(it.from); continue; }
    const dept = deptOf(it.fromKey);
    if (known.has(dept)) depts.add(dept);
  }
  if (!depts.size && !people.size) return null;
  return [depts.size ? `부서 ${depts.size}곳` : null, people.size ? `사람 ${people.size}명` : null].filter(Boolean).join(" · ");
}

/** 방 목록 시각: 오늘이면 "오후 4:53", 어제면 "어제", 그 전이면 "11월 10일" */
export function roomTime(iso: string | undefined, now = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const day = (x: Date) => x.toLocaleDateString("sv-SE");
  if (day(d) === day(now)) return d.toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
  const y = new Date(now);
  y.setDate(y.getDate() - 1);
  if (day(d) === day(y)) return "어제";
  return d.toLocaleDateString("ko-KR", { month: "long", day: "numeric" });
}

/** 방 검색: 방 이름·부서 이름에 글자가 들어 있으면 */
export const matchRoom = (q: string, ...names: (string | undefined)[]) => {
  const k = q.trim().toLowerCase();
  return !k || names.some((n) => n?.toLowerCase().includes(k));
};
