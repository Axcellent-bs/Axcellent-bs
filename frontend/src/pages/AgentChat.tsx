import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ALL_ROOM, messageCount, type ChatItem, type OrchIncident } from "@/lib/agentChat";
import { matchRoom, roomTime, type ChatRoom } from "@/lib/chatRooms";
import { Link, useSearchParams } from "@/lib/nav";
import { DIRECT_AGENTS, DIRECT_PREFIX, DirectChat, directAgentOf } from "@/components/office/DirectChat";
import { Avatar, MessengerLog, PinNotice, StatusChip, TypingLines } from "@/components/office/Messenger";
import { TalkInput } from "@/components/office/TalkInput";
import { useChatRooms, type Conn } from "@/components/office/useChatRooms";
import { orchestrator } from "@/lib/orchestrator";
import { shopFaults, type FaultState } from "@/lib/shopFaults";
import { humanizePlanIds } from "@/lib/format";

// AI 대화방 · 대화 기록. 부서 AI들이 A2A로 주고받은 일과 사람이 건 말을 메신저처럼 보여 준다.
// 방: 전체 대화방(처음 열면 여기) / 장애·이슈 방(총괄 사건 하나 = 방 하나) / 1:1 대화(나만 보임).
// AI 오피스와 같은 방 목록·같은 대화를 본다(lib/chatRooms.ts, components/office/useChatRooms.ts).
// 말풍선 문장은 lib/agentChat.ts가 기록된 값으로만 만든다. 이 파일은 화면과 새로고침만 맡는다.
// 장애는 이 화면에서 일으키지 않는다(사람이 요청하면 운영자가 캣북스에 넣는다). 화면은 캣북스 결제 상태만 읽어 보여 준다.
// ?room=<방 id>로 들어오면 그 방을 연다 (AI 오피스 "대화 기록에서 크게 보기").

const POLL_MS = 3000;
// AI들은 1초 안에 수십 건을 주고받는다. 새로 도착한 메시지는 줄 세워 하나씩 보여 준다 (밀리면 조금 빠르게)
const REVEAL_MS = 600;
const REVEAL_FAST_MS = 200;
const FAST_BACKLOG = 20;
const ORCH_CMD = "cd backend && bash scripts/run_all.sh";

// ---------------------------------------------------------------- 연결 칩
function ConnChip({ label, conn }: { label: string; conn: Conn }) {
  const tone = conn === "on" ? "bg-emerald-50 text-emerald-700" : conn === "off" ? "bg-red-50 text-red-700" : conn === "unready" ? "bg-amber-50 text-amber-700" : "bg-ink-100 text-ink-500";
  const dot = conn === "on" ? "bg-emerald-700" : conn === "off" ? "bg-red-700" : conn === "unready" ? "bg-amber-700" : "bg-ink-500";
  const word = { on: "켜짐", off: "꺼짐", unready: "준비 안 됨(503)", unknown: "확인 중" }[conn];
  return (
    <span className={`chip ${tone}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden="true" />
      {label} {word}
    </span>
  );
}

/** 방 아바타: 전체(보라) · 장애(진행 중 빨강, 끝나면 주황) · 1:1(부서 색) */
function RoomAvatar({ room, agentKey, name }: { room?: ChatRoom; agentKey?: string; name?: string }) {
  if (agentKey) return <Avatar name={name ?? agentKey} k={agentKey} />;
  if (room?.kind === "all") return <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[13px] bg-brand-600 text-[11px] font-bold text-white">전체</span>;
  const open = room?.status === "open";
  return <span aria-hidden="true" className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[13px] text-[11px] font-bold text-white ${open ? "bg-red-700" : "bg-amber-700"}`}>장애</span>;
}

// ---------------------------------------------------------------- 채팅 목록
function RoomRow({ on, onClick, avatar, title, status, preview, time, unread }: {
  on: boolean; onClick: () => void; avatar: ReactNode; title: string; status?: string; preview?: string; time?: string; unread?: number;
}) {
  return (
    <li>
      <button type="button" onClick={onClick} aria-current={on ? "true" : undefined}
        className={`flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-500 ${on ? "bg-brand-50" : "hover:bg-ink-50"}`}>
        {avatar}
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className={`truncate text-[13px] font-semibold ${on ? "text-brand-700" : "text-ink-900"}`}>{title}</span>
            <StatusChip status={status} />
          </span>
          {preview && <span className="mt-0.5 block truncate text-xs text-ink-500">{humanizePlanIds(preview)}</span>}
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1">
          {time && <span className="text-[10px] tabular-nums text-ink-500">{time}</span>}
          {!!unread && !on && <span className="rounded-full bg-red-700 px-1.5 text-[10px] font-bold text-white" aria-label={`안 읽은 메시지 ${unread}개`}>{unread}</span>}
        </span>
      </button>
    </li>
  );
}

function RoomList({ all, incidents, selected, unseen, onSelect }: {
  all: ChatRoom; incidents: ChatRoom[]; selected: string; unseen: Record<string, number>; onSelect: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  const shownIncidents = incidents.filter((r) => matchRoom(q, r.title));
  const shownDirect = DIRECT_AGENTS.filter((a) => matchRoom(q, a.name));
  const showAll = matchRoom(q, all.title, "전체");
  return (
    <nav aria-label="채팅 목록" className="card flex shrink-0 flex-col overflow-hidden lg:w-80">
      <div className="p-3">
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="대화방·부서 검색" aria-label="대화방·부서 검색" className="field w-full text-xs" />
      </div>
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto pb-2 lg:max-h-[calc(100vh-230px)]">
        {showAll && (
          <ul>
            <RoomRow on={selected === all.id} onClick={() => onSelect(all.id)} avatar={<RoomAvatar room={all} />} title={all.title}
              preview={all.preview} time={roomTime(all.lastAt)} unread={unseen[all.id]} />
          </ul>
        )}
        <h2 className="px-4 pb-1 pt-3 text-[11px] font-semibold text-ink-500">장애·이슈 방</h2>
        {shownIncidents.length === 0 ? <p className="px-4 py-1 text-xs text-ink-500">{q ? "찾는 방이 없어요" : "아직 장애·이슈 방이 없어요"}</p> : (
          <ul>
            {shownIncidents.map((r) => (
              <RoomRow key={r.id} on={selected === r.id} onClick={() => onSelect(r.id)} avatar={<RoomAvatar room={r} />} title={r.title}
                status={r.status} preview={r.preview} time={roomTime(r.lastAt)} unread={unseen[r.id]} />
            ))}
          </ul>
        )}
        <h2 className="px-4 pb-1 pt-3 text-[11px] font-semibold text-ink-500">1:1 대화 · <span aria-hidden="true">🔒</span> 나만 보임</h2>
        {shownDirect.length === 0 ? <p className="px-4 py-1 text-xs text-ink-500">찾는 부서가 없어요</p> : (
          <ul>
            {shownDirect.map((a) => {
              const id = DIRECT_PREFIX + a.key;
              return (
                <RoomRow key={a.key} on={selected === id} onClick={() => onSelect(id)} avatar={<RoomAvatar agentKey={a.key} name={a.name} />} title={a.name}
                  preview={a.live ? undefined : "준비 중"} />
              );
            })}
          </ul>
        )}
      </div>
    </nav>
  );
}

// ---------------------------------------------------------------- 페이지
export default function AgentChat() {
  const params = useSearchParams();
  const asked = params.get("room");
  // 처음 열면 전체 대화방. ?room=이 있으면 그 방
  const [room, setRoom] = useState<string>(asked || ALL_ROOM);
  useEffect(() => { if (asked) setRoom(asked); }, [asked]);

  const data = useChatRooms({ room });
  const { incidents, orchConn, safetyConn, commConn, commTruncated, orchMock, loaded, talk, all, incidentRooms } = data;
  const [busyHold, setBusyHold] = useState<string | null>(null);
  const [fault, setFault] = useState<FaultState | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 캣북스 결제 상태 (실제 모드일 때만)
  useEffect(() => {
    if (orchConn !== "on" || orchMock) return;
    let live = true;
    const read = () => shopFaults.state().then((f) => { if (live) setFault(f); }, () => { if (live) setFault(null); });
    read();
    const t = setInterval(() => { if (!document.hidden) read(); }, POLL_MS);
    return () => { live = false; clearInterval(t); };
  }, [orchConn, orchMock]);

  const direct = directAgentOf(room);
  const inc: OrchIncident | undefined = incidents.find((i) => i.context_id === room);
  const roomInfo: ChatRoom | undefined = room === ALL_ROOM ? all : incidentRooms.find((r) => r.id === room);
  const items = useMemo(() => data.items[room] ?? [], [data.items, room]);

  // ------------------------------------------------ 새 이벤트가 열리면 그 방으로 옮겨 처음부터 차례로 보여 준다
  // 화면을 연 순간 이미 있던 이벤트는 옮기지 않는다. 1:1·전체 대화방(사람이 말을 거는 곳)에 있으면 옮기지 않는다
  const knownIncidents = useRef<Set<string> | null>(null);
  const replayRoom = useRef<string | null>(null);
  useEffect(() => {
    if (orchConn !== "on") return;
    const ids = incidents.map((i) => i.context_id);
    if (knownIncidents.current === null) { knownIncidents.current = new Set(ids); return; }
    const opened = ids.find((id) => !knownIncidents.current?.has(id));
    for (const id of ids) knownIncidents.current.add(id);
    if (opened) { replayRoom.current = opened; setRoom((r) => (r.startsWith(DIRECT_PREFIX) || r === ALL_ROOM ? r : opened)); }
  }, [incidents, orchConn]);

  // ------------------------------------------------ 하나씩 보여 주기
  // 방을 열 때 이미 있던 기록은 한 번에(지난 기록을 다시 재생하지 않음), 그 뒤에 도착한 부서 기록은 하나씩.
  // 사람 대화는 바로 시각순으로 끼워 넣는다 (내 말풍선이 바로 붙게)
  const records = useMemo(() => items.filter((it) => !it.id.startsWith("direct:")), [items]);
  const human = useMemo(() => items.filter((it) => it.id.startsWith("direct:")), [items]);
  const [revealed, setRevealed] = useState(0);
  const primed = useRef(false);
  useLayoutEffect(() => { primed.current = false; }, [room]);
  useLayoutEffect(() => {
    if (primed.current || !loaded) return;
    primed.current = true;
    if (replayRoom.current === room) { replayRoom.current = null; setRevealed(0); return; }
    setRevealed(records.length);
  }, [room, records.length, loaded]);
  useEffect(() => {
    if (!primed.current || revealed >= records.length) return;
    const backlog = records.length - revealed;
    const t = setTimeout(() => setRevealed((r) => r + 1), backlog > FAST_BACKLOG ? REVEAL_FAST_MS : REVEAL_MS);
    return () => clearTimeout(t);
  }, [revealed, records.length]);
  const shown = useMemo(() => {
    const base = records.slice(0, Math.min(revealed, records.length));
    if (!human.length) return base;
    // 시각순으로 끼워 넣기 (기록의 순서는 그대로)
    const out: ChatItem[] = [];
    let h = 0;
    for (const t of base) {
      const at = t.at ? Date.parse(t.at) : NaN;
      while (!Number.isNaN(at) && h < human.length && Date.parse(human[h].at) < at) out.push(human[h++]);
      out.push(t);
    }
    while (h < human.length) out.push(human[h++]);
    return out;
  }, [records, revealed, human]);
  const typing = primed.current && revealed < records.length;

  // 참여 부서: 이 방에서 말한 AI (사람 제외)
  const deptCount = useMemo(() => new Set(items.filter((it) => it.kind === "msg" && it.fromKey && it.fromKey !== "human" && it.from !== "담당 미정").map((it) => (it as Extract<ChatItem, { kind: "msg" }>).fromKey)).size, [items]);

  // ------------------------------------------------ 스크롤: 맨 아래에 붙어 있으면 따라가고, 위로 올렸으면 "새 메시지" 알약
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const lastLen = useRef(0);
  const [fresh, setFresh] = useState(0);
  const autoScrollAt = useRef(0);
  const toBottom = useCallback((smooth = false) => {
    const el = scroller.current;
    if (el) {
      autoScrollAt.current = Date.now();
      el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
    }
    pinned.current = true;
    setFresh(0);
  }, []);
  useLayoutEffect(() => { lastLen.current = 0; pinned.current = true; }, [room]);
  useLayoutEffect(() => {
    const now = messageCount(shown);
    const added = now - lastLen.current;
    const first = lastLen.current === 0;
    lastLen.current = now;
    if (pinned.current) toBottom(!first);
    else if (added > 0) setFresh((f) => f + added);
  }, [shown, toBottom]);
  const onScroll = () => {
    const el = scroller.current;
    if (!el || Date.now() - autoScrollAt.current < 600) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (pinned.current) setFresh(0);
  };

  async function decide(h: Extract<ChatItem, { kind: "hold" }>, approved: boolean) {
    if (!h.contextId || !h.stepId) return;
    setBusyHold(h.id);
    setError(null);
    try {
      await orchestrator.decideHold(h.contextId, h.stepId, approved);
      await data.refresh();
    } catch (e) {
      setError(`결정을 전달하지 못했습니다: ${(e as Error).message}`);
    } finally {
      setBusyHold(null);
    }
  }

  const orchOn = orchConn === "on";
  const isAll = room === ALL_ROOM;
  // 말을 걸 수 있는 방: 전체 대화방, 장애·이슈 방
  const talkable = isAll || !!inc;
  const answering = talkable ? DIRECT_AGENTS.filter((a) => talk.busyIn(room).includes(a.key)).map((a) => ({ key: a.key, name: a.name })) : [];
  const title = roomInfo?.title ?? (room.startsWith("ctx_") ? "찾을 수 없는 방" : room);

  return (
    <div className="flex h-full flex-col gap-4 p-4 lg:p-6">
      <header className="flex flex-wrap items-start gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight">AI 대화방 · 대화 기록</h1>
          <p className="mt-1 text-sm text-ink-500">
            AI들이 서로 일을 부탁하고 결과를 알려 준 실제 기록입니다(A2A). 장애·이슈가 생기면 방이 따로 열리고, AI 오피스 단톡방과 같은 방을 봅니다.
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <ConnChip label="총괄 AI" conn={orchConn} />
            <ConnChip label="안전 관문" conn={safetyConn} />
            <ConnChip label="공통 로그" conn={commConn} />
            {orchOn && !orchMock && (
              <span className={`chip ${fault?.active ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-700"}`} title="캣북스 쇼핑몰 결제 서버 상태 (장애는 운영자가 넣고 뺀다)">
                캣북스 결제 {fault == null ? "확인 중" : fault.active ? (fault.failing ? "장애 중" : `느림(${fault.delay_ms}ms)`) : "정상"}{fault?.degraded ? " · 감지됨" : ""}
              </span>
            )}
            <span className="chip bg-ink-100 text-ink-500">{POLL_MS / 1000}초마다 새로고침</span>
          </div>
        </div>
      </header>

      {error && <p role="alert" className="rounded-xl bg-red-50 px-3 py-2 text-sm font-medium text-red-800">{error}</p>}

      <div className="flex min-h-0 flex-1 flex-col gap-4 lg:flex-row">
        <RoomList all={all} incidents={incidentRooms} selected={room} unseen={data.unseen} onSelect={setRoom} />

        {direct ? <DirectChat agent={direct} /> : (
        <section aria-label={`${title} 대화`} className="card relative flex min-h-[460px] min-w-0 flex-1 flex-col overflow-hidden bg-chat">
          <header className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 border-b border-ink-900/[.07] px-4 py-3">
            <h2 className="min-w-0 truncate text-[15px] font-semibold">{title}</h2>
            {deptCount > 0 && <span className="text-xs text-ink-700">참여 부서 {deptCount}곳</span>}
            <StatusChip status={roomInfo?.status} />
            {!isAll && (
              <div className="ml-auto flex gap-1.5">
                {inc && <Link href={`/orchestrator?ctx=${encodeURIComponent(inc.context_id)}`} className="btn-ghost px-2.5 py-1 text-xs">사건 자세히</Link>}
                <Link href={`/agents/office?room=${encodeURIComponent(room)}`} className="btn-ghost px-2.5 py-1 text-xs">AI 오피스에서 보기</Link>
              </div>
            )}
            {isAll && <Link href={`/agents/office?room=${encodeURIComponent(room)}`} className="btn-ghost ml-auto px-2.5 py-1 text-xs">AI 오피스에서 보기</Link>}
          </header>
          {inc && <PinNotice inc={inc} />}
          <div ref={scroller} onScroll={onScroll} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-4">
            {shown.length === 0 && !typing ? (
              <EmptyState orchConn={orchConn} known={isAll || !!inc} />
            ) : (
              <>
                <CommNote conn={commConn} truncated={commTruncated} />
                <MessengerLog items={shown} talk={talkable ? talk : undefined} label={`${title} 대화`}
                  hold={{ busyHold, canDecide: orchOn, orchMock, onDecide: decide }} />
                {typing && <p className="mt-2 animate-pulse px-1 text-xs text-ink-500" aria-live="polite">AI들이 이야기하는 중… ({records.length - revealed}개 남음)</p>}
              </>
            )}
          </div>
          <TypingLines names={answering} />
          {talkable && <TalkInput talk={talk} onSent={() => toBottom(true)} where="chat" room={room} roomName={inc ? title : undefined} />}
          {fresh > 0 && (
            <button onClick={() => toBottom(true)}
              className="btn-primary absolute bottom-24 left-1/2 -translate-x-1/2 shadow-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500">
              새 메시지 {fresh}개 ↓
            </button>
          )}
        </section>
        )}
      </div>
    </div>
  );
}

/** 공통 로그가 503이거나 꺼졌을 때 / 잘렸을 때 한 줄 안내 */
function CommNote({ conn, truncated }: { conn: Conn; truncated: boolean }) {
  if (conn === "unready") return <p className="mb-3 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">{COMM_503}</p>;
  if (conn === "off") return <p className="mb-3 rounded-xl bg-surface px-3 py-2 text-xs text-ink-500">{COMM_OFF}</p>;
  if (truncated) return <p className="mb-3 rounded-xl bg-surface px-3 py-2 text-xs text-ink-500">공통 로그가 많아 앞부분만 받았습니다 (최대 1000줄).</p>;
  return null;
}
const COMM_503 = "공통 로그: 준비 안 됨(503) — dashboard-api에 공통 로그 읽기가 아직 연결되지 않았습니다. 부서끼리 주고받은 대화는 연결 뒤에 보입니다.";
const COMM_OFF = "공통 로그: dashboard-api(VITE_DASHBOARD_API_URL)에 연결할 수 없어 부서끼리 주고받은 대화를 불러오지 못했습니다.";

/** 빈 방: 한 줄. 총괄이 꺼져 있으면 켜는 방법 */
function EmptyState({ orchConn, known }: { orchConn: Conn; known: boolean }) {
  if (orchConn === "off") {
    return (
      <div className="mx-auto max-w-xl py-14 text-sm text-ink-700">
        <p className="font-semibold text-ink-900">총괄 AI가 꺼져 있습니다.</p>
        <p className="mt-1 text-ink-500">backend/에서 실제 모드로 켜면 캣북스 쇼핑몰 장애로 AI들이 함께 일하는 과정을 볼 수 있습니다.</p>
        <pre className="mt-3 overflow-x-auto rounded-xl bg-surface p-3 font-mono text-xs text-ink-700"><code>{ORCH_CMD}</code></pre>
      </div>
    );
  }
  if (orchConn === "unknown") return <p className="py-16 text-center text-sm text-ink-500">불러오는 중…</p>;
  return <p className="py-16 text-center text-sm text-ink-500">{known ? "아직 이 방에서 주고받은 말이 없어요." : "이 방을 찾을 수 없어요. 왼쪽 목록에서 방을 골라 주세요."}</p>;
}
