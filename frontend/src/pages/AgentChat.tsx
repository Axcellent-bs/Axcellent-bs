import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { ALL_ROOM, messageCount, type ChatItem, type OrchIncident } from "@/lib/agentChat";
import { matchRoom, roomPeople, roomTime, type ChatRoom } from "@/lib/chatRooms";
import { Link, useRouter, useSearchParams } from "@/lib/nav";
import { DIRECT_AGENTS, DIRECT_PREFIX, DirectChat, directAgentOf } from "@/components/office/DirectChat";
import { Avatar, MessengerLog, PinNotice, StatusChip, TypingLines } from "@/components/office/Messenger";
import { TalkInput } from "@/components/office/TalkInput";
import { useChatRooms, type ChatRooms, type Conn } from "@/components/office/useChatRooms";
import { orchestrator } from "@/lib/orchestrator";
import { shopFaults, type FaultState } from "@/lib/shopFaults";
import { humanizePlanIds } from "@/lib/format";

// AI 대화방 · 대화 기록. 부서 AI들이 A2A로 주고받은 일과 사람이 건 말을 메신저처럼 보여 준다.
// 방: 전체 대화방(처음 열면 여기) / 장애·이슈 방(총괄 사건 하나 = 방 하나) / 1:1 대화(나만 보임).
// AI 오피스와 같은 방 목록·같은 대화를 본다(lib/chatRooms.ts, components/office/useChatRooms.ts).
// 말풍선 문장은 lib/agentChat.ts가 기록된 값으로만 만든다. 이 파일은 화면과 새로고침만 맡는다.
// 장애는 이 화면에서 일으키지 않는다(사람이 요청하면 운영자가 캣북스에 넣는다). 화면은 캣북스 결제 상태만 읽어 보여 준다.
//
// 나눠 보기: 방을 최대 3칸까지 나란히 본다. 칸마다 따로 스크롤하고 자기 입력창이 있다(입력은 그 칸의 방으로).
// - 보기 개수(1·2·3)는 이 브라우저에만 기억한다. 화면이 좁으면 1280px 미만 2칸, 900px 미만 1칸으로 줄이고,
//   줄어든 칸의 방은 닫지 않고 두었다가 다시 넓어지면 보인다
// - 주소: ?rooms=a,b,c (열린 방, 칸 순서). 예전 ?room=<방>은 1칸 보기로 연다 (AI 오피스 "대화 기록에서 크게 보기")
// - 데이터는 useChatRooms 한 번만 읽는다(칸 수만큼 요청이 늘지 않는다). 안 읽은 수는 보이는 칸의 방만 읽음 처리

const POLL_MS = 3000;
// AI들은 1초 안에 수십 건을 주고받는다. 새로 도착한 메시지는 줄 세워 하나씩 보여 준다 (밀리면 조금 빠르게)
const REVEAL_MS = 600;
const REVEAL_FAST_MS = 200;
const FAST_BACKLOG = 20;
const ORCH_CMD = "cd backend && bash scripts/run_all.sh";
const MAX_PANES = 3;
const COUNT_KEY = "axcellent-chat-split-count";
const DRAG_TYPE = "text/x-ax-chat-room";

type Count = 1 | 2 | 3;
const readCount = (): Count => {
  try {
    const n = Number(localStorage.getItem(COUNT_KEY));
    return n === 2 || n === 3 ? n : 1;
  } catch { return 1; }
};
const saveCount = (n: Count) => { try { localStorage.setItem(COUNT_KEY, String(n)); } catch { /* 저장이 막혀도 이번 화면에서는 바뀐다 */ } };
/** 화면 폭이 허락하는 칸 수: 1280px 미만 2칸, 900px 미만 1칸 */
const widthLimit = (w: number): Count => (w < 900 ? 1 : w < 1280 ? 2 : 3);
function useWidthLimit(): Count {
  const [limit, setLimit] = useState<Count>(() => (typeof window === "undefined" ? 3 : widthLimit(window.innerWidth)));
  useEffect(() => {
    const on = () => setLimit(widthLimit(window.innerWidth));
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return limit;
}
/** 주소에서 열린 방: ?rooms=a,b,c 먼저, 없으면 예전 ?room= (1칸) */
function roomsFromUrl(params: URLSearchParams): { rooms: string[]; one: boolean } | null {
  const many = params.get("rooms");
  if (many) {
    const list = [...new Set(many.split(",").map((s) => s.trim()).filter(Boolean))].slice(0, MAX_PANES);
    if (list.length) return { rooms: list, one: false };
  }
  const one = params.get("room");
  return one ? { rooms: [one], one: true } : null;
}

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
  if (room?.kind === "all") return <span aria-hidden="true" className="chat-avatar flex h-9 w-9 shrink-0 items-center justify-center bg-brand-600 text-[11px] font-bold text-white">전체</span>;
  const open = room?.status === "open";
  return <span aria-hidden="true" className={`chat-avatar flex h-9 w-9 shrink-0 items-center justify-center text-[11px] font-bold text-white ${open ? "bg-red-700" : "bg-amber-700"}`}>장애</span>;
}

/** 칸 번호 배지 (목록 줄·칸 머리) */
function PaneNo({ n }: { n: number }) {
  return <span className="chat-avatar flex h-[18px] w-[18px] shrink-0 items-center justify-center bg-brand-600 text-[10px] font-bold text-white" aria-label={`${n}번 칸`}>{n}</span>;
}

// ---------------------------------------------------------------- 채팅 목록
interface RowActions {
  /** 그 방이 열린 칸 번호 (1부터). 없으면 null */
  paneOf: (id: string) => number | null;
  /** ＋를 보일지 (열 수 있는 칸이 있을 때만) */
  canAdd: (id: string) => boolean;
  open: (id: string) => void;
  add: (id: string) => void;
}

function RoomRow({ id, on, avatar, title, status, preview, time, unread, act }: {
  id: string; on: boolean; avatar: ReactNode; title: string; status?: string; preview?: string; time?: string; unread?: number; act: RowActions;
}) {
  const pane = act.paneOf(id);
  const onDrag = (e: DragEvent<HTMLLIElement>) => { e.dataTransfer.setData(DRAG_TYPE, id); e.dataTransfer.effectAllowed = "move"; };
  return (
    <li className="group relative" draggable onDragStart={onDrag}>
      <button type="button" onClick={() => act.open(id)} aria-current={on ? "true" : undefined}
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
          {pane != null ? <PaneNo n={pane} /> : time && <span className="text-[10px] tabular-nums text-ink-500">{time}</span>}
          {!!unread && !on && <span className="chat-badge bg-red-700 px-1.5 text-[10px] font-bold text-white" aria-label={`안 읽은 메시지 ${unread}개`}>{unread}</span>}
        </span>
      </button>
      {act.canAdd(id) && (
        <button type="button" onClick={() => act.add(id)} title="빈 칸(없으면 선택된 칸)에 열기" aria-label={`${title} 칸에 열기`}
          className="absolute right-2 top-1/2 -translate-y-1/2 border border-dashed border-brand-500 bg-surface px-1.5 text-[11px] font-bold text-brand-700 opacity-0 transition focus:opacity-100 group-hover:opacity-100">
          ＋
        </button>
      )}
    </li>
  );
}

function RoomList({ all, incidents, unseen, act, focusedRoom, narrow }: {
  all: ChatRoom; incidents: ChatRoom[]; unseen: Record<string, number>; act: RowActions; focusedRoom: string | null; narrow: boolean;
}) {
  const [q, setQ] = useState("");
  const shownIncidents = incidents.filter((r) => matchRoom(q, r.title, r.longTitle));
  const shownDirect = DIRECT_AGENTS.filter((a) => matchRoom(q, a.name));
  const showAll = matchRoom(q, all.title, "전체");
  return (
    <nav aria-label="채팅 목록" className={`card flex shrink-0 flex-col overflow-hidden ${narrow ? "lg:w-64" : "lg:w-80"}`}>
      <div className="p-3">
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="대화방·부서 검색" aria-label="대화방·부서 검색" className="field w-full text-xs" />
      </div>
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto pb-2 lg:max-h-[calc(100vh-270px)]">
        {showAll && (
          <ul>
            <RoomRow id={all.id} on={focusedRoom === all.id} avatar={<RoomAvatar room={all} />} title={all.title}
              preview={all.preview} time={roomTime(all.lastAt)} unread={unseen[all.id]} act={act} />
          </ul>
        )}
        <h2 className="px-4 pb-1 pt-3 text-[11px] font-semibold text-ink-500">장애·이슈 방</h2>
        {shownIncidents.length === 0 ? <p className="px-4 py-1 text-xs text-ink-500">{q ? "찾는 방이 없어요" : "아직 장애·이슈 방이 없어요"}</p> : (
          <ul>
            {shownIncidents.map((r) => (
              <RoomRow key={r.id} id={r.id} on={focusedRoom === r.id} avatar={<RoomAvatar room={r} />} title={r.title}
                status={r.status} preview={r.preview} time={roomTime(r.lastAt)} unread={unseen[r.id]} act={act} />
            ))}
          </ul>
        )}
        <h2 className="px-4 pb-1 pt-3 text-[11px] font-semibold text-ink-500">1:1 대화 · <span aria-hidden="true">🔒</span> 나만 보임</h2>
        {shownDirect.length === 0 ? <p className="px-4 py-1 text-xs text-ink-500">찾는 부서가 없어요</p> : (
          <ul>
            {shownDirect.map((a) => {
              const id = DIRECT_PREFIX + a.key;
              return (
                <RoomRow key={a.key} id={id} on={focusedRoom === id} avatar={<RoomAvatar agentKey={a.key} name={a.name} />} title={a.name}
                  preview={a.live ? undefined : "준비 중"} act={act} />
              );
            })}
          </ul>
        )}
      </div>
    </nav>
  );
}

// ---------------------------------------------------------------- 칸 하나
interface PaneProps {
  room: string;
  /** 칸 번호 (1부터) */
  n: number;
  /** 보이는 칸 수 */
  total: number;
  /** 칸이 좁다: 머리 버튼을 … 메뉴로 접고 참여 수·긴 이름을 숨긴다 (3칸, 또는 좁은 화면의 2칸) */
  folded: boolean;
  focused: boolean;
  onFocus: () => void;
  onClose?: () => void;
  onDropRoom: (id: string) => void;
  data: ChatRooms;
  busyHold: string | null;
  onDecide: (h: Extract<ChatItem, { kind: "hold" }>, approved: boolean) => void;
  /** 새 사건 방을 처음부터 하나씩 보여 줄 방 (한 번 쓰면 비운다) */
  replayRoom: { current: string | null };
}

const acceptsRoom = (e: DragEvent) => e.dataTransfer.types.includes(DRAG_TYPE);

/** 칸 테두리: 선택된 칸은 위쪽 브랜드 색 줄. 칸을 누르면 선택, 목록 줄을 끌어다 놓으면 이 칸에 연다 */
function PaneFrame({ focused, multi, onFocus, onDropRoom, label, children }: {
  focused: boolean; multi: boolean; onFocus: () => void; onDropRoom: (id: string) => void; label: string; children: ReactNode;
}) {
  const [over, setOver] = useState(false);
  return (
    <section aria-label={label} onMouseDownCapture={onFocus} onFocusCapture={onFocus}
      onDragOver={(e) => { if (acceptsRoom(e)) { e.preventDefault(); setOver(true); } }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { setOver(false); const id = e.dataTransfer.getData(DRAG_TYPE); if (id) { e.preventDefault(); onDropRoom(id); } }}
      className={`card relative flex min-h-[460px] min-w-0 flex-col overflow-hidden bg-chat ${multi && focused ? "shadow-[inset_0_3px_0_rgb(var(--brand-600))]" : ""} ${over ? "outline outline-2 -outline-offset-2 outline-brand-500" : ""}`}>
      {children}
    </section>
  );
}

/** 빈 칸: 끌어다 놓거나 ＋로 연다 */
function EmptyPane(props: Pick<PaneProps, "n" | "focused" | "onFocus" | "onDropRoom" | "total">) {
  return (
    <PaneFrame focused={props.focused} multi onFocus={props.onFocus} onDropRoom={props.onDropRoom} label={`${props.n}번 칸 (비어 있음)`}>
      <header className="flex items-center gap-2 border-b border-ink-900/[.07] px-3 py-2.5">
        <PaneNo n={props.n} /><span className="text-[13px] font-semibold text-ink-500">빈 칸</span>
      </header>
      <div className="m-3 flex flex-1 items-center justify-center border-2 border-dashed border-ink-300 p-4 text-center text-xs leading-relaxed text-ink-500">
        왼쪽 목록에서 방을 끌어다 놓거나<br />목록 줄의 ＋를 누르세요
      </div>
    </PaneFrame>
  );
}

/** 칸 머리 오른쪽 버튼. 3칸일 때는 … 메뉴로 접는다 */
function PaneActions({ links, folded }: { links: { href: string; label: string }[]; folded: boolean }) {
  if (!links.length) return null;
  if (!folded) {
    return <>{links.map((l) => <Link key={l.href} href={l.href} className="btn-ghost px-2 py-1 text-xs">{l.label}</Link>)}</>;
  }
  return (
    <details className="relative">
      <summary className="btn-ghost cursor-pointer list-none px-2 py-1 text-xs" aria-label="더 보기">…</summary>
      <div className="absolute right-0 z-20 mt-1 flex w-40 flex-col border border-ink-100 bg-surface py-1 shadow-lg">
        {links.map((l) => <Link key={l.href} href={l.href} className="px-3 py-1.5 text-xs hover:bg-ink-50">{l.label}</Link>)}
      </div>
    </details>
  );
}

function RoomPane(p: PaneProps) {
  const { room, data, total } = p;
  const { incidents, orchConn, commConn, commTruncated, orchMock, loaded, talk, all, incidentRooms } = data;
  const multi = total > 1;
  const { folded } = p;
  const direct = directAgentOf(room);
  const inc: OrchIncident | undefined = incidents.find((i) => i.context_id === room);
  const roomInfo: ChatRoom | undefined = room === ALL_ROOM ? all : incidentRooms.find((r) => r.id === room);
  const items = useMemo(() => data.items[room] ?? [], [data.items, room]);
  const isAll = room === ALL_ROOM;
  const title = direct ? `🔒 ${direct.name}` : roomInfo?.title ?? (room.startsWith("ctx_") ? "찾을 수 없는 방" : room);

  // ------------------------------------------------ 하나씩 보여 주기
  // 방을 열 때 이미 있던 기록은 한 번에(지난 기록을 다시 재생하지 않음), 그 뒤에 도착한 부서 기록은 하나씩.
  // 사람 대화는 바로 시각순으로 끼워 넣는다 (내 말풍선이 바로 붙게)
  const records = useMemo(() => items.filter((it) => !it.id.startsWith("direct:")), [items]);
  const human = useMemo(() => items.filter((it) => it.id.startsWith("direct:")), [items]);
  const [revealed, setRevealed] = useState(0);
  const primed = useRef(false);
  const { replayRoom } = p;
  useLayoutEffect(() => { primed.current = false; }, [room]);
  useLayoutEffect(() => {
    if (primed.current || !loaded) return;
    primed.current = true;
    if (replayRoom.current === room) { replayRoom.current = null; setRevealed(0); return; }
    setRevealed(records.length);
  }, [room, records.length, loaded, replayRoom]);
  useEffect(() => {
    if (!primed.current || revealed >= records.length) return;
    const backlog = records.length - revealed;
    const t = setTimeout(() => setRevealed((r) => r + 1), backlog > FAST_BACKLOG ? REVEAL_FAST_MS : REVEAL_MS);
    return () => clearTimeout(t);
  }, [revealed, records.length]);
  const shown = useMemo(() => {
    const base = records.slice(0, Math.min(revealed, records.length));
    if (!human.length) return base;
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
  const people = useMemo(() => roomPeople(items, DIRECT_AGENTS.map((a) => a.key)), [items]);

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

  const links = [
    ...(inc ? [{ href: `/orchestrator?ctx=${encodeURIComponent(inc.context_id)}`, label: "사건 자세히" }] : []),
    ...(direct ? [] : [{ href: `/agents/office?room=${encodeURIComponent(room)}`, label: "AI 오피스에서 보기" }]),
  ];
  const head = (
    <header className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 border-b border-ink-900/[.07] px-3 py-2.5">
      {multi && <PaneNo n={p.n} />}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <h2 className={`min-w-0 truncate font-semibold ${multi ? "text-[13px]" : "text-[15px]"}`}>{title}</h2>
          {people && !folded && <span className="text-xs text-ink-700">{people}</span>}
          <StatusChip status={roomInfo?.status} />
        </div>
        {roomInfo?.longTitle && !folded && <p className="mt-0.5 truncate text-xs text-ink-500">{roomInfo.longTitle}</p>}
      </div>
      <div className="ml-auto flex items-center gap-1.5">
        <PaneActions links={links} folded={folded} />
        {p.onClose && <button type="button" onClick={p.onClose} className="px-1.5 text-base leading-none text-ink-500 hover:text-ink-900" aria-label={`${p.n}번 칸 닫기`} title="칸 닫기">×</button>}
      </div>
    </header>
  );

  if (direct) {
    return (
      <PaneFrame focused={p.focused} multi={multi} onFocus={p.onFocus} onDropRoom={p.onDropRoom} label={`${title} 대화`}>
        {head}
        <DirectChat agent={direct} narrow={multi} bare />
      </PaneFrame>
    );
  }

  // 말을 걸 수 있는 방: 전체 대화방, 장애·이슈 방
  const talkable = isAll || !!inc;
  const answering = talkable ? DIRECT_AGENTS.filter((a) => talk.busyIn(room).includes(a.key)).map((a) => ({ key: a.key, name: a.name })) : [];
  return (
    <PaneFrame focused={p.focused} multi={multi} onFocus={p.onFocus} onDropRoom={p.onDropRoom} label={`${title} 대화`}>
      {head}
      {inc && <PinNotice inc={inc} />}
      <div ref={scroller} onScroll={onScroll} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-3 py-4">
        {shown.length === 0 && !typing ? (
          <EmptyState orchConn={orchConn} known={isAll || !!inc} />
        ) : (
          <>
            <CommNote conn={commConn} truncated={commTruncated} />
            <MessengerLog items={shown} talk={talkable ? talk : undefined} label={`${title} 대화`}
              hold={{ busyHold: p.busyHold, canDecide: orchConn === "on", orchMock, onDecide: p.onDecide }} />
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
    </PaneFrame>
  );
}

// ---------------------------------------------------------------- 페이지
export default function AgentChat() {
  const params = useSearchParams();
  const router = useRouter();
  const fromUrl = useMemo(() => roomsFromUrl(params), [params]);

  // 열린 방(칸 순서). 처음 열면 전체 대화방 한 칸
  const [rooms, setRooms] = useState<string[]>(() => fromUrl?.rooms ?? [ALL_ROOM]);
  const [count, setCountState] = useState<Count>(() => {
    const saved = readCount();
    if (fromUrl?.one) return 1;
    return fromUrl ? (Math.max(saved, fromUrl.rooms.length) as Count) : saved;
  });
  const setCount = (n: Count) => { setCountState(n); saveCount(n); };
  const [focus, setFocus] = useState(0);
  const limit = useWidthLimit();
  const shownCount = Math.min(count, limit) as Count;
  const visible = useMemo(() => rooms.slice(0, shownCount), [rooms, shownCount]);
  const focusIdx = Math.min(focus, shownCount - 1);
  const focusedRoom = visible[focusIdx] ?? null;

  // 주소 ↔ 열린 방. 내가 쓴 주소는 다시 읽지 않는다
  const written = useRef<string | null>(null);
  useEffect(() => {
    const key = params.toString();
    if (key === written.current || !fromUrl) return;
    setRooms(fromUrl.rooms);
    if (fromUrl.one) setCountState(1);
    setFocus(0);
  }, [params, fromUrl]);
  useEffect(() => {
    if (params.get("rooms") === rooms.join(",") && !params.has("room")) return;
    // 쉼표는 그대로 둔다 (?rooms=a,b,c 를 사람이 읽고 고칠 수 있게). 방 id에 쉼표는 없다
    const next = `rooms=${rooms.map(encodeURIComponent).join(",")}`;
    written.current = new URLSearchParams(next).toString();
    router.replace(`/agents/chat?${next}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rooms]);

  const data = useChatRooms({ rooms: visible });
  const { incidents, orchConn, safetyConn, commConn, orchMock, all, incidentRooms } = data;
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

  // ------------------------------------------------ 칸에 방 열기
  // 같은 방은 두 칸에 열지 않는다: 이미 보이는 칸에 있으면 그 칸을 선택만 한다
  const openAt = useCallback((i: number, id: string) => {
    const at = visible.indexOf(id);
    if (at >= 0) { setFocus(at); return; }
    const next = rooms.filter((r) => r !== id); // 줄어든 칸에 숨어 있던 같은 방은 뺀다
    const pos = Math.min(i, next.length);
    if (pos < next.length) next[pos] = id; else next.push(id);
    setRooms(next.slice(0, MAX_PANES));
    setFocus(pos);
  }, [visible, rooms]);
  const act: RowActions = {
    paneOf: (id) => { const i = visible.indexOf(id); return shownCount > 1 && i >= 0 ? i + 1 : null; },
    canAdd: (id) => shownCount > 1 && !visible.includes(id),
    open: (id) => openAt(focusIdx, id),
    // 빈 칸이 있으면 거기, 없으면 선택된 칸
    add: (id) => openAt(visible.length < shownCount ? visible.length : focusIdx, id),
  };
  const close = (i: number) => {
    setRooms((cur) => {
      const next = cur.filter((_, k) => k !== i);
      return next.length ? next : [ALL_ROOM];
    });
    setFocus((f) => Math.max(0, f >= i ? f - 1 : f));
  };

  // ------------------------------------------------ 새 이벤트가 열리면 선택된 칸이 장애 방일 때 그 방으로 옮겨 처음부터 차례로 보여 준다
  // 화면을 연 순간 이미 있던 이벤트는 옮기지 않는다. 1:1·전체 대화방(사람이 말을 거는 곳)에 있으면 옮기지 않는다
  const knownIncidents = useRef<Set<string> | null>(null);
  const replayRoom = useRef<string | null>(null);
  useEffect(() => {
    if (orchConn !== "on") return;
    const ids = incidents.map((i) => i.context_id);
    if (knownIncidents.current === null) { knownIncidents.current = new Set(ids); return; }
    const opened = ids.find((id) => !knownIncidents.current?.has(id));
    for (const id of ids) knownIncidents.current.add(id);
    if (!opened || !focusedRoom || focusedRoom.startsWith(DIRECT_PREFIX) || focusedRoom === ALL_ROOM) return;
    replayRoom.current = opened;
    openAt(focusIdx, opened);
  }, [incidents, orchConn, focusedRoom, focusIdx, openAt]);

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
  const cols = shownCount === 3 ? "lg:grid-cols-3" : shownCount === 2 ? "lg:grid-cols-2" : "";

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

      {/* 나눠 보기: 한 번에 볼 칸 수 */}
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
        <span>한 번에 보기</span>
        <div role="radiogroup" aria-label="한 번에 볼 방 개수" className="flex border border-ink-100 bg-surface">
          {([1, 2, 3] as const).map((n) => (
            <button key={n} type="button" role="radio" aria-checked={count === n} onClick={() => setCount(n)} disabled={n > limit}
              title={n > limit ? `화면이 좁아 지금은 ${limit}개까지 보여요` : undefined}
              className={`px-2.5 py-1 font-medium disabled:cursor-not-allowed disabled:opacity-40 ${count === n ? "bg-ink-900 text-app" : "text-ink-700 hover:bg-ink-50"}`}>
              {n}개
            </button>
          ))}
        </div>
        {shownCount > 1
          ? <span>목록 줄의 ＋를 누르거나 방을 칸으로 끌어다 놓아요. 그냥 누르면 선택된 칸의 방이 바뀌어요.</span>
          : count > 1 && <span>화면이 좁아 지금은 {limit}개만 보여요. 넓히면 나머지 칸이 다시 보여요.</span>}
      </div>

      {error && <p role="alert" className="rounded-xl bg-red-50 px-3 py-2 text-sm font-medium text-red-800">{error}</p>}

      {/* chat-round: 이 안의 말풍선·알약·아바타·배지만 둥글게 (styles.css) */}
      <div className="chat-round flex min-h-0 flex-1 flex-col gap-4 lg:flex-row">
        <RoomList all={all} incidents={incidentRooms} unseen={data.unseen} act={act} focusedRoom={focusedRoom} narrow={shownCount >= 3} />
        <div className={`grid min-h-0 min-w-0 flex-1 gap-3 ${cols}`}>
          {Array.from({ length: shownCount }, (_, i) => {
            const id = visible[i];
            const common = {
              n: i + 1, total: shownCount, folded: shownCount >= 3 || (shownCount === 2 && limit < 3), focused: i === focusIdx, onFocus: () => setFocus(i), onDropRoom: (r: string) => openAt(i, r),
            };
            if (!id) return <EmptyPane key={`empty-${i}`} {...common} />;
            return (
              <RoomPane key={`${i}:${id}`} room={id} {...common} onClose={shownCount > 1 ? () => close(i) : undefined}
                data={data} busyHold={busyHold} onDecide={decide} replayRoom={replayRoom} />
            );
          })}
        </div>
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
