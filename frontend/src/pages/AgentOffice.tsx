import { useEffect, useMemo, useRef, useState } from "react";
import { DIRECT_AGENTS, DIRECT_PREFIX, DirectChat, directAgentOf } from "@/components/office/DirectChat";
import { OfficeChat, officeChatWidth, type Typing } from "@/components/office/OfficeChat";
import { useChatRooms, type Conn } from "@/components/office/useChatRooms";
import { RequestHandoffCard } from "@/components/office/RequestHandoffCard";
import { useWorkRequests } from "@/components/office/useWorkRequests";
import { PixelOffice } from "@/components/office/PixelOffice";
import { ALL_ROOM, incidentTitle, skillKo, type ChatItem } from "@/lib/agentChat";
import { Link, useSearchParams } from "@/lib/nav";
import { DESKS, deriveOffice, type DeskKey, type DeskState, type OfficeStatus } from "@/lib/office";
import { markSeen } from "@/lib/chatView";

// AI 오피스. 부서 AI가 지금 무엇을 하는지 픽셀 사무실로 보여 주고, 옆에 부서 단톡방을 둔다. 실시간만 있다.
// 대화 기록(/agents/chat)과 같은 방 목록·같은 대화를 본다(components/office/useChatRooms.ts):
// 단톡방 위 방 탭(전체 + 장애·이슈 방 + 열어 둔 1:1 탭)에서 고른 방의 대화만 보이고, 사무실 말풍선도 그 방 대화만 쓴다.
// 캐릭터를 누르면 그 부서 1:1 탭이 열린다(나만 보임). ?room=<방 id>로 들어오면 그 방 탭을 연다.
// 총괄이 꺼져 있으면 사무실 위에 연결 안내와 다시 연결 버튼을 둔다(예시 사건은 보여 주지 않는다).
// 11/20 1920×919: 대화 카드 541px 중 메시지 칸 223px뿐이었다(방 탭 줄·받는 AI 버튼 11개·안내 문장·페이지 머리가 세로를 먹음).
// 방 탭 줄 → 머리 줄의 방 드롭다운, 받는 AI → 입력칸 왼쪽 드롭다운, 안내·연결 칩 → ⓘ 접기, 고객 의뢰 넘김 → 접힌 한 줄.
// "⤢ 넓게": 오른쪽 칸 580px ↔ 화면 절반 (이 브라우저에 기억)

const POLL_MS = 3000;
const WIDE_KEY = "axcellent-office-chat-wide";
const readWide = () => { try { return localStorage.getItem(WIDE_KEY) === "1"; } catch { return false; } };
const saveWide = (v: boolean) => { try { localStorage.setItem(WIDE_KEY, v ? "1" : "0"); } catch { /* 저장이 막혀도 이번 화면에서는 바뀐다 */ } };

const STATE_KO: Record<DeskState, string> = { idle: "대기", working: "작업 중", waiting: "승인 대기" };
const STATE_DOT: Record<DeskState, string> = { idle: "bg-ink-300", working: "bg-brand-500", waiting: "bg-amber-700" };
const STATUS_LOOK: Record<OfficeStatus, string> = {
  ok: "bg-ink-100 text-ink-500", alert: "bg-red-50 text-red-700", resolved: "bg-emerald-50 text-emerald-700", degraded: "bg-amber-50 text-amber-700",
};
const isHuman = (it: ChatItem) => it.id.startsWith("direct:");

export default function AgentOffice() {
  const params = useSearchParams();
  const [now, setNow] = useState(() => Date.now());

  // ------------------------------------------------ 방: 전체 / 장애·이슈 방 / 1:1 탭(나만 보임)
  const asked = params.get("room");
  const [room, setRoom] = useState<string>(asked || ALL_ROOM);
  const [openDirect, setOpenDirect] = useState<string[]>(() => (asked && directAgentOf(asked) ? [asked.slice(DIRECT_PREFIX.length)] : []));
  useEffect(() => {
    if (!asked) return;
    setRoom(asked);
    const d = directAgentOf(asked);
    if (d) setOpenDirect((o) => (o.includes(d.key) ? o : [...o, d.key]));
  }, [asked]);
  const openTalk = (key: string) => {
    if (!DIRECT_AGENTS.some((a) => a.key === key)) return;
    setOpenDirect((o) => (o.includes(key) ? o : [...o, key]));
    setRoom(DIRECT_PREFIX + key);
  };
  // ⤢ 넓게: 오른쪽 단톡방 580px ↔ 화면 절반 (사무실 그림은 남은 폭에 맞춘다)
  const [wide, setWide] = useState(readWide);
  const toggleWide = () => setWide((w) => { saveWide(!w); return !w; });
  const closeTalk = (key: string) => {
    setOpenDirect((o) => o.filter((k) => k !== key));
    setRoom((r) => (r === DIRECT_PREFIX + key ? ALL_ROOM : r));
  };

  // ------------------------------------------------ 대화 기록과 같은 방 데이터
  const liveRooms = useMemo(() => [room], [room]);
  const data = useChatRooms({ rooms: liveRooms });
  const { incidents, orchConn, safetyConn, commConn, talk } = data;
  const [retrying, setRetrying] = useState(false);
  const retry = () => {
    setRetrying(true);
    void data.refresh().finally(() => setRetrying(false));
  };

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 150);
    return () => clearInterval(t);
  }, []);

  // ------------------------------------------------ 타임라인 → 사무실
  const direct = directAgentOf(room);
  // 고른 방의 부서 기록만(사람 대화 제외) 사무실 말풍선에 쓴다. 1:1 탭이면 말풍선 없음
  const items = useMemo(() => {
    if (direct) return [] as ChatItem[];
    return (data.items[room] ?? []).filter((it) => !isHuman(it));
  }, [direct, data.items, room]);

  // 말풍선 id → 처음 본 시각 (0 = 이미 본 것, 걸어가기·말풍선 재생 없음). lib/chatView.ts markSeen:
  // 화면을 연(방을 바꾼) 시각 − 5초보다 이른 줄은 늦게 들어와도 0이다. 방을 바꿔 들어온 그 방의 예전 줄, 의뢰 목록이 늦게 와
  // 거르기가 바뀐 줄, 공통 로그 창(1000줄)이 움직여 들어온 줄이 지금 일처럼 다시 재생되지 않게 한다. 보류 승인(:approved)도 같은 규칙
  const seenRef = useRef(new Map<string, number>());
  const openedAt = useMemo(() => Date.now(), [room]);
  const liveReady = orchConn !== "unknown";
  const seen = useMemo(() => markSeen(seenRef.current, items, openedAt, Date.now()), [items, openedAt]);

  // 고객 의뢰(wr_) 단계로도 자리 불을 켠다 (10초마다, 사람 토큰 없으면 읽지 않음). 고객 의뢰 넘김 카드와 같은 값을 쓴다
  const work = useWorkRequests();
  const baseView = useMemo(
    () => deriveOffice(incidents, items, seen, now, { requests: work.rows }),
    [incidents, items, seen, now, work.rows],
  );

  // ------------------------------------------------ 사람이 부서 AI에게 말 걸기 (단톡방 대화는 모두에게 보인다. 1:1 직접 대화와는 따로)
  // 답을 기다리는 부서가 놀고 있으면 자리에서 "작업 중"으로 (총괄 일로 바쁜 자리·상태는 그대로). 걷지는 않고 불꽃만 켜진다
  const busyStr = talk.busyKeys.join(",");
  const view = useMemo(() => {
    let v = baseView;
    for (const key of busyStr ? busyStr.split(",") : []) {
      const d = v.desks[key as DeskKey];
      if (!d || d.state !== "idle") continue;
      v = { ...v, desks: { ...v.desks, [key]: { ...d, state: "working", skill: "질문 답변", bubble: d.bubble ?? { text: "답하는 중", kind: "think" } } } };
    }
    return v;
  }, [baseView, busyStr]);
  // 단톡방: 고른 방의 대화(기록 + 그 방 사람 대화)
  const chatItems = useMemo(() => data.items[room] ?? [], [data.items, room]);
  // 사람 대화의 처음 본 시각 (기존 seenRef·deriveOffice 입력과 따로). 처음 불러온 지난 대화는 0 = 애니메이션 없음
  const humanSeenRef = useRef(new Map<string, number>());
  const humanSeen = useMemo(() => {
    const m = humanSeenRef.current;
    for (const it of talk.items) if (!m.has(it.id)) m.set(it.id, talk.quiet.has(it.id) ? 0 : Date.now());
    return m;
  }, [talk.items, talk.quiet]);

  const freshIds = useMemo(() => {
    const fresh = (m: ReadonlyMap<string, number>, id: string) => { const at = m.get(id) ?? 0; return at > 0 && now - at < 1500; };
    return new Set([...items.filter((it) => fresh(seen, it.id)), ...talk.items.filter((it) => fresh(humanSeen, it.id))].map((it) => it.id));
  }, [items, seen, talk.items, humanSeen, now]);
  const typing: Typing[] = DESKS.filter((d) => view.desks[d.key].state === "working").map((d) => ({
    key: d.key, name: d.key === "orchestrator" ? "총괄 AI" : `${d.label} Agent`, what: skillKo(view.desks[d.key].skill) || "작업",
  }));
  // 이 방에서 답을 기다리는 부서는 "답을 쓰는" 줄을 하나 더 (총괄 일로 이미 작업 중 줄이 있으면 그대로)
  for (const key of talk.busyIn(room)) {
    if (typing.some((t) => t.key === key || t.key === `${key}:talk`)) continue;
    const agent = DIRECT_AGENTS.find((a) => a.key === key);
    if (agent) typing.push({ key: `${key}:talk`, name: agent.name, what: "답을 쓰는" });
  }

  const inc = view.incident;
  const statusText = {
    ok: "정상 운영", alert: inc ? `${incidentTitle(inc)} 대응 중` : "대응 중", resolved: "장애 해결", degraded: "일부 미완료로 종료",
  }[view.status];
  const officeLabel = `부서 AI 사무실. ${statusText}. ` + DESKS.map((d) => `${d.label} ${STATE_KO[view.desks[d.key].state]}`).join(", ");

  // 진행 중인 장애 방 (가장 최근에 열린 것). 사무실 오른쪽 위에 표시
  const liveRoom = data.incidentRooms.find((r) => r.status === "open");
  const roomInfo = room === ALL_ROOM ? data.all : data.incidentRooms.find((r) => r.id === room);
  const roomInc = incidents.find((i) => i.context_id === room);

  // 방 고르기: 탭 줄 대신 드롭다운 하나 (전체 · 장애·이슈 방 · 열어 둔 1:1). 안 읽은 수는 다른 방 합계 배지 + 항목마다 "새 N"
  const roomOptions: RoomOption[] = [
    { id: ALL_ROOM, label: "전체 대화방", unread: data.unseen[ALL_ROOM] ?? 0 },
    ...data.incidentRooms.map((r) => ({ id: r.id, label: `${r.status === "open" ? "● " : "✓ "}${r.title}`, unread: data.unseen[r.id] ?? 0 })),
    ...openDirect.flatMap((key) => {
      const a = DIRECT_AGENTS.find((x) => x.key === key);
      return a ? [{ id: DIRECT_PREFIX + key, label: `🔒 ${a.name.replace(/\s*AI$/, "")} 1:1`, unread: 0, close: () => closeTalk(key) }] : [];
    }),
  ];
  const picker = <RoomPicker room={room} options={roomOptions} onPick={setRoom} />;

  return (
    // xl 이상: 왼쪽 칸(제목·의뢰 넘김·사무실)과 오른쪽 칸(단톡방) 두 칸. 단톡방이 제목 옆에서 맨 아래까지 쓰고 대화만 안에서 스크롤된다.
    // 왼쪽 칸이 길어지면 왼쪽만 안에서 스크롤된다. xl 미만은 위에서 아래로 쌓고 페이지가 스크롤된다.
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-4 xl:flex-row xl:overflow-hidden lg:p-6">
      <style>{"@keyframes officePop{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}"}</style>
      <div className="flex min-w-0 shrink-0 flex-col gap-4 xl:min-h-0 xl:flex-1 xl:shrink xl:overflow-y-auto">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="text-xl font-semibold tracking-tight">AI 오피스</h1>
        <details className="group relative">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs text-ink-500 hover:text-ink-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
            <span aria-hidden="true">ⓘ</span> 설명 · 연결
            {[orchConn, safetyConn, commConn].some((c) => c === "off" || c === "unready") && <span className="h-1.5 w-1.5 rounded-full bg-red-700" aria-label="꺼진 연결 있음" />}
          </summary>
          <div className="absolute left-0 z-30 mt-1 w-[min(36rem,85vw)] space-y-2 rounded-xl border border-ink-100 bg-surface p-3 shadow-lg">
            <p className="text-sm text-ink-500">
              부서 AI가 지금 무슨 일을 하는지 사무실로 봅니다. 총괄 AI가 일을 맡기면 그 부서가 총괄 AI에게 와서 받아 가고, 일하는 동안 불꽃이 일어납니다. 사람 승인이 필요하면 🚩 승인 대기로 깜빡입니다.
              {" "}오른쪽 단톡방은 <Link href="/agents/chat" className="font-medium text-brand-700 underline-offset-2 hover:underline">대화 기록</Link>과 같은 방이라 어디서 말해도 양쪽에 보입니다. 캐릭터를 누르면 그 부서와 1:1로 이야기합니다(나만 보임).
            </p>
            <div className="flex flex-wrap gap-1.5">
              <ConnChip label="중앙 Agent" conn={orchConn} />
              <ConnChip label="안전 관문" conn={safetyConn} />
              <ConnChip label="공통 로그" conn={commConn} />
              <span className="chip bg-ink-100 text-ink-500">{POLL_MS / 1000}초마다 새로고침</span>
            </div>
          </div>
        </details>
      </header>

      {orchConn === "off" && (
        <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <span className="flex-1">총괄 AI에 연결할 수 없어요. 연결되면 바로 사무실이 움직여요.</span>
          <button type="button" className="btn-ghost px-3 py-1 text-xs" onClick={retry} disabled={retrying}>
            {retrying ? "연결하는 중…" : "다시 연결"}
          </button>
        </div>
      )}

      <RequestHandoffCard compact />

        <section aria-label="부서 AI 사무실" className="card flex min-w-0 shrink-0 flex-col gap-3 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="card-title">부서 AI 사무실</h2>
            <span className="text-xs text-ink-500">{DESKS.length}자리 · 총괄 AI·안전 처리 로그 포함</span>
            <span className={`chip ml-auto ${STATUS_LOOK[view.status]}`}>
              <span className={`h-1.5 w-1.5 rounded-full bg-current ${view.status === "alert" ? "animate-pulse" : ""}`} aria-hidden="true" />
              {liveReady ? statusText : "불러오는 중…"}
            </span>
            {liveRoom && (
              <button type="button" onClick={() => setRoom(liveRoom.id)} title="이 방 대화 보기"
                className="chip rounded-full bg-red-50 font-semibold text-red-700 hover:bg-red-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-700">
                <span aria-hidden="true">●</span> {liveRoom.title} 진행 중
              </button>
            )}
          </div>
          <PixelOffice view={view} label={officeLabel} onPick={openTalk} />
          <ul className="flex flex-wrap gap-1.5" aria-label="부서별 상태">
            {DESKS.map((d) => {
              const st = view.desks[d.key].state;
              return (
                <li key={d.key} className="chip bg-ink-50 py-1 text-ink-700 ring-1 ring-ink-100">
                  <span className={`h-1.5 w-1.5 rounded-full ${STATE_DOT[st]}`} aria-hidden="true" />
                  <b className="font-semibold">{d.label}</b>
                  <span className="text-ink-500">{STATE_KO[st]}{st === "working" && view.desks[d.key].skill ? ` · ${skillKo(view.desks[d.key].skill)}` : ""}</span>
                </li>
              );
            })}
          </ul>
        </section>
      </div>

        {direct ? (
          <div className={`chat-round card flex min-h-[460px] min-w-0 xl:min-h-0 flex-1 flex-col overflow-hidden xl:flex-none ${officeChatWidth(wide)}`}>
            <header className="flex items-center gap-2 border-b border-ink-900/[.07] px-3 py-2">
              <div className="min-w-0 flex-1">{picker}</div>
              <button type="button" onClick={toggleWide} aria-pressed={wide} className="btn-ghost hidden shrink-0 px-2 py-1 text-xs xl:inline-flex">⤢ {wide ? "좁게" : "넓게"}</button>
            </header>
            <DirectChat agent={direct} narrow />
          </div>
        ) : (
          <OfficeChat
            items={chatItems}
            typing={typing}
            freshIds={freshIds}
            picker={picker}
            wide={wide}
            onWide={toggleWide}
            title={roomInfo?.title ?? "찾을 수 없는 방"}
            status={roomInfo?.status}
            longTitle={roomInfo?.longTitle}
            inc={roomInc}
            room={room}
            talk={talk}
          />
        )}
    </div>
  );
}

interface RoomOption { id: string; label: string; unread: number; close?: () => void }

/** 방 고르기 드롭다운 (지금 방 이름이 보이고 키보드로 고른다). 다른 방의 안 읽은 수 합계는 옆 배지, 항목마다 "새 N". 1:1 탭은 × 로 닫는다 */
function RoomPicker({ room, options, onPick }: { room: string; options: RoomOption[]; onPick: (id: string) => void }) {
  const cur = options.find((o) => o.id === room);
  const others = options.reduce((n, o) => n + (o.id === room ? 0 : o.unread), 0);
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <select value={cur ? room : ""} onChange={(e) => onPick(e.target.value)} aria-label="대화방 고르기"
        className="field min-w-0 max-w-[16rem] truncate py-1 text-sm font-semibold text-ink-900">
        {!cur && <option value="" disabled>찾을 수 없는 방</option>}
        {options.map((o) => <option key={o.id} value={o.id}>{o.label}{o.unread && o.id !== room ? ` · 새 ${o.unread}` : ""}</option>)}
      </select>
      {others > 0 && <span className="chat-badge shrink-0 bg-red-700 px-1.5 text-[10px] font-bold text-white" aria-label={`다른 방에 안 읽은 메시지 ${others}개`}>{others}</span>}
      {cur?.close && <button type="button" onClick={cur.close} aria-label={`${cur.label} 닫기`} title="1:1 닫기" className="shrink-0 px-1 text-sm text-ink-500 hover:text-ink-900">×</button>}
    </span>
  );
}

function ConnChip({ label, conn }: { label: string; conn: Conn }) {
  const tone = conn === "on" ? "bg-emerald-50 text-emerald-700" : conn === "off" ? "bg-red-50 text-red-700" : conn === "unready" ? "bg-amber-50 text-amber-700" : "bg-ink-100 text-ink-500";
  return <span className={`chip ${tone}`}>{label} {{ on: "켜짐", off: "꺼짐", unready: "준비 안 됨(503)", unknown: "확인 중" }[conn]}</span>;
}
