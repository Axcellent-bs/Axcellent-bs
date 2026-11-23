import { useCallback, useEffect, useMemo, useState } from "react";
import { useOfficeTalk, type OfficeTalk } from "@/components/office/useOfficeTalk";
import { useWorkRequests } from "@/components/office/useWorkRequests";
import type { ChatItem, OrchIncident } from "@/lib/agentChat";
import { api, ApiError } from "@/lib/api";
import { buildRooms, type ChatRoom } from "@/lib/chatRooms";
import { finance, type ExecutionRecord } from "@/lib/finance";
import { orchestrator } from "@/lib/orchestrator";
import type { CommLine } from "@/lib/types";
import { clearRoom, hideIncidentRoom, listHiddenRooms, listRoomClears, undoClearRoom, unhideIncidentRoom } from "@/lib/officeRoom";
import { clearMap } from "@/lib/roomCleanup";

const CLEANUP_POLL_MS = 30_000;

// 대화 기록(/agents/chat)과 AI 오피스(/agents/office)가 같이 쓰는 방 데이터.
// 출처 세 곳(총괄 사건·안전 관문 실행·공통 로그 소통)을 3초마다 읽고, 단톡방 사람 대화(useOfficeTalk)를 섞어
// 방 목록(lib/chatRooms)과 방별 대화를 만든다. 지금 보고 있는 방은 읽음으로 센다.

const POLL_MS = 3000;

/** unready = 서버는 답했지만 503 (공통 로그 읽기가 아직 연결 안 됨) */
export type Conn = "unknown" | "on" | "off" | "unready";

export interface ChatRooms {
  incidents: OrchIncident[];
  executions: ExecutionRecord[];
  comms: CommLine[];
  orchConn: Conn;
  safetyConn: Conn;
  commConn: Conn;
  commTruncated: boolean;
  /** 총괄이 목 모드라고 알려 줄 때만(GET /api/incidents의 meta.mock) 총괄 보류에 승인·거절 버튼을 보인다 */
  orchMock: boolean;
  loaded: boolean;
  refresh: () => Promise<void>;
  talk: OfficeTalk;
  all: ChatRoom;
  incidentRooms: ChatRoom[];
  /** 방 id → 그 방 대화 (전체·장애 방). 원래 기록 그대로(AI 오피스 그림 계산이 쓴다) */
  items: Record<string, ChatItem[]>;
  /** 방 id → 내 화면에 보일 대화 (/clear 한 시각 앞은 뺀다). 대화 칸은 이것을 그린다 */
  shown: Record<string, ChatItem[]>;
  /** 서버가 숨긴(모두에게) 장애·이슈 방 (방 목록에서 빠진 방) */
  hiddenIncidentRooms: ChatRoom[];
  /** 방 id → 내가 /clear 한 시각 */
  clears: Readonly<Record<string, string>>;
  /** 장애·이슈 방 숨기기·되돌리기, /clear·되돌리기. 실패하면 서버 문장으로 던진다 */
  cleanup: {
    hide: (room: string) => Promise<void>;
    unhide: (room: string) => Promise<void>;
    clear: (room: string) => Promise<void>;
    undoClear: (room: string) => Promise<void>;
  };
  /** 방 id → 안 읽은 말풍선 수 */
  unseen: Record<string, number>;
  /** 그 방을 지금까지 읽은 것으로 */
  markSeen: (room: string) => void;
}

/**
 * `enabled`가 false면 서버를 읽지 않는다(기본 true, AI 대화방·AI 오피스는 늘 읽는다).
 * `rooms`는 지금 화면에 보이는 방들(나눠 보기면 칸마다 하나). 이 방들만 읽음으로 센다
 */
export function useChatRooms({ enabled = true, rooms }: { enabled?: boolean; rooms: readonly string[] }): ChatRooms {
  const [incidents, setIncidents] = useState<OrchIncident[]>([]);
  const [executions, setExecutions] = useState<ExecutionRecord[]>([]);
  const [comms, setComms] = useState<CommLine[]>([]);
  const [orchConn, setOrchConn] = useState<Conn>("unknown");
  const [safetyConn, setSafetyConn] = useState<Conn>("unknown");
  const [commConn, setCommConn] = useState<Conn>("unknown");
  const [commTruncated, setCommTruncated] = useState(false);
  const [orchMock, setOrchMock] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    const [inc, exe, com] = await Promise.allSettled([orchestrator.incidents(), finance.executions(), api.communications()]);
    if (inc.status === "fulfilled") {
      setIncidents(inc.value.data);
      setOrchMock(inc.value.meta.mock === true);
      setOrchConn("on");
    } else { setOrchConn("off"); setOrchMock(false); }
    if (exe.status === "fulfilled") { setExecutions(exe.value); setSafetyConn("on"); } else setSafetyConn("off");
    if (com.status === "fulfilled") { setComms(com.value.data); setCommTruncated(com.value.meta?.truncated === true); setCommConn("on"); }
    else {
      setComms([]);
      setCommTruncated(false);
      setCommConn(com.reason instanceof ApiError && com.reason.status === 503 ? "unready" : "off");
    }
    setLoaded(true);
  }, []);

  // 3초마다 새로고침. 탭이 가려져 있으면 쉬고, 다시 보이면 바로 한 번
  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const t = setInterval(() => { if (!document.hidden) void refresh(); }, POLL_MS);
    const onVis = () => { if (!document.hidden) void refresh(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", onVis); };
  }, [enabled, refresh]);

  const talk = useOfficeTalk();
  // 고객 의뢰(사람 토큰이 있을 때만): 끝난 의뢰(ctx_wr_)의 뒤 조회 줄을 사건처럼 거른다. 의뢰 넘김 카드·오피스 자리와 같은 값
  const work = useWorkRequests(enabled);
  const requests = work.rows;

  // 11/23 대화방 정리: 서버가 숨긴 장애 방(모두에게)·내 /clear 시각. 다른 사람이 숨긴 방도 보이게 30초마다 다시 읽는다.
  // 사람 토큰이 없으면 읽지 못해 아무것도 빼지 않는다(지금처럼 보인다)
  const [hiddenIds, setHiddenIds] = useState<string[]>([]);
  const [clears, setClears] = useState<Record<string, string>>({});
  const loadCleanup = useCallback(async () => {
    const [h, c] = await Promise.allSettled([listHiddenRooms(), listRoomClears()]);
    if (h.status === "fulfilled") setHiddenIds(h.value);
    if (c.status === "fulfilled") setClears(clearMap(c.value));
  }, []);
  useEffect(() => {
    if (!enabled) return;
    void loadCleanup();
    const t = setInterval(() => { if (!document.hidden) void loadCleanup(); }, CLEANUP_POLL_MS);
    return () => clearInterval(t);
  }, [enabled, loadCleanup]);
  const cleanup = useMemo(() => ({
    hide: async (room: string) => { await hideIncidentRoom(room); await loadCleanup(); },
    unhide: async (room: string) => { await unhideIncidentRoom(room); await loadCleanup(); },
    clear: async (room: string) => { await clearRoom(room); await loadCleanup(); },
    undoClear: async (room: string) => { await undoClearRoom(room); await loadCleanup(); },
  }), [loadCleanup]);
  const hidden = useMemo(() => new Set(hiddenIds), [hiddenIds]);

  const { all, incidents: incidentRooms, hiddenIncidents: hiddenIncidentRooms, items, shown } = useMemo(
    () => buildRooms({ incidents, executions, comms, talk: talk.items, requests: requests ?? [], hidden, clears }),
    [incidents, executions, comms, talk.items, requests, hidden, clears],
  );

  // 읽음: 방마다 본 말풍선 수. 화면을 열 때 이미 있던 방은 지금까지를 읽은 것으로 두고(열자마자 배지가 쌓이지 않게),
  // 그 뒤에 새로 생긴 방(새 사건)은 0부터 센다
  const [seen, setSeen] = useState<Record<string, number>>({});
  const counts = useMemo(() => Object.fromEntries([all, ...incidentRooms].map((r) => [r.id, r.count])), [all, incidentRooms]);
  const [baselined, setBaselined] = useState(false);
  useEffect(() => {
    if (!loaded) return;
    setSeen((s) => {
      let next = s;
      for (const [id, n] of Object.entries(counts)) if (next[id] == null) next = { ...next, [id]: baselined ? 0 : n };
      return next;
    });
    if (!baselined) setBaselined(true);
  }, [loaded, counts, baselined]);
  const markSeen = useCallback((id: string) => {
    setSeen((s) => (s[id] === counts[id] ? s : { ...s, [id]: counts[id] ?? 0 }));
  }, [counts]);
  const shownKey = rooms.join(",");
  useEffect(() => { for (const r of shownKey ? shownKey.split(",") : []) markSeen(r); }, [shownKey, markSeen]);
  const unseen = useMemo(
    () => Object.fromEntries(Object.entries(counts).map(([id, n]) => [id, seen[id] == null ? 0 : Math.max(0, n - seen[id])])),
    [counts, seen],
  );

  return {
    incidents, executions, comms, orchConn, safetyConn, commConn, commTruncated, orchMock, loaded, refresh,
    talk, all, incidentRooms, items, unseen, markSeen, shown, hiddenIncidentRooms, clears, cleanup,
  };
}
