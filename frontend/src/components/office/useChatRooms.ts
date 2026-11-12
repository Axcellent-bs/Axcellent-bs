import { useCallback, useEffect, useMemo, useState } from "react";
import { useOfficeTalk, type OfficeTalk } from "@/components/office/useOfficeTalk";
import type { ChatItem, OrchIncident } from "@/lib/agentChat";
import { api, ApiError } from "@/lib/api";
import { buildRooms, type ChatRoom } from "@/lib/chatRooms";
import { finance, type ExecutionRecord } from "@/lib/finance";
import { orchestrator } from "@/lib/orchestrator";
import type { CommLine } from "@/lib/types";

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
  /** 방 id → 그 방 대화 (전체·장애 방) */
  items: Record<string, ChatItem[]>;
  /** 방 id → 안 읽은 말풍선 수 */
  unseen: Record<string, number>;
  /** 그 방을 지금까지 읽은 것으로 */
  markSeen: (room: string) => void;
}

/** `enabled`가 false면(오피스 리플레이) 서버를 읽지 않는다. `room`은 지금 보고 있는 방(읽음 처리) */
export function useChatRooms({ enabled = true, room }: { enabled?: boolean; room: string | null }): ChatRooms {
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
  const { all, incidents: incidentRooms, items } = useMemo(
    () => buildRooms({ incidents, executions, comms, talk: talk.items }),
    [incidents, executions, comms, talk.items],
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
  useEffect(() => { if (room) markSeen(room); }, [room, markSeen]);
  const unseen = useMemo(
    () => Object.fromEntries(Object.entries(counts).map(([id, n]) => [id, seen[id] == null ? 0 : Math.max(0, n - seen[id])])),
    [counts, seen],
  );

  return {
    incidents, executions, comms, orchConn, safetyConn, commConn, commTruncated, orchMock, loaded, refresh,
    talk, all, incidentRooms, items, unseen, markSeen,
  };
}
