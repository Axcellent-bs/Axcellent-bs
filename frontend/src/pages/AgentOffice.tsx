import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DIRECT_AGENTS, DIRECT_PREFIX, DirectChat, directAgentOf } from "@/components/office/DirectChat";
import { OfficeChat, type Typing } from "@/components/office/OfficeChat";
import { useChatRooms, type Conn } from "@/components/office/useChatRooms";
import { PixelOffice } from "@/components/office/PixelOffice";
import { ALL_ROOM, buildTimeline, incidentTitle, skillKo, type ChatItem } from "@/lib/agentChat";
import { mergeTalk } from "@/lib/officeTalk";
import { Link, useSearchParams } from "@/lib/nav";
import { CAMPAIGN_END, DESKS, REPLAY_END, deriveOffice, replayCampaign, replayIncident, type DeskKey, type DeskState, type OfficeStatus } from "@/lib/office";

// AI 오피스. 부서 AI가 지금 무엇을 하는지 픽셀 사무실로 보여 주고, 옆에 부서 단톡방을 둔다.
// 실시간은 대화 기록(/agents/chat)과 같은 방 목록·같은 대화를 본다(components/office/useChatRooms.ts):
// 단톡방 위 방 탭(전체 + 장애·이슈 방 + 열어 둔 1:1 탭)에서 고른 방의 대화만 보이고, 사무실 말풍선도 그 방 대화만 쓴다.
// 캐릭터를 누르면 그 부서 1:1 탭이 열린다(나만 보임). ?room=<방 id>로 들어오면 그 방 탭을 연다.
// 총괄이 꺼져 있으면 SCN-A 리플레이(총괄 응답 모양으로 만든 예시 사건)를 보여 준다. 리플레이·캠페인 리플레이는 방 탭 없이 예전처럼.

const POLL_MS = 3000;
type Mode = "live" | "replay" | "campaign";
const MODE_LABEL: Record<Mode, string> = { live: "실시간", replay: "장애 리플레이(SCN-A)", campaign: "캠페인 리플레이" };

const STATE_KO: Record<DeskState, string> = { idle: "대기", working: "작업 중", waiting: "승인 대기" };
const STATE_DOT: Record<DeskState, string> = { idle: "bg-ink-300", working: "bg-brand-500", waiting: "bg-amber-700" };
const STATUS_LOOK: Record<OfficeStatus, string> = {
  ok: "bg-ink-100 text-ink-500", alert: "bg-red-50 text-red-700", resolved: "bg-emerald-50 text-emerald-700", degraded: "bg-amber-50 text-amber-700",
};
const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const isHuman = (it: ChatItem) => it.id.startsWith("direct:");

export default function AgentOffice() {
  const params = useSearchParams();
  const [mode, setMode] = useState<Mode>(params.has("campaign") ? "campaign" : params.has("replay") ? "replay" : "live");
  const isReplay = mode !== "live";
  const [autoNote, setAutoNote] = useState(false);
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
  const closeTalk = (key: string) => {
    setOpenDirect((o) => o.filter((k) => k !== key));
    setRoom((r) => (r === DIRECT_PREFIX + key ? ALL_ROOM : r));
  };

  // ------------------------------------------------ 실시간: 대화 기록과 같은 방 데이터
  const liveRooms = useMemo(() => (mode === "live" ? [room] : []), [mode, room]);
  const data = useChatRooms({ enabled: mode === "live", rooms: liveRooms });
  const { incidents, orchConn, safetyConn, commConn, talk } = data;

  // 총괄이 꺼져 있으면 처음 한 번만 리플레이로 바꿔 준다
  const autoSwitched = useRef(false);
  useEffect(() => {
    if (mode === "live" && orchConn === "off" && !autoSwitched.current) {
      autoSwitched.current = true;
      setAutoNote(true);
      setMode("replay");
    }
  }, [mode, orchConn]);

  // ------------------------------------------------ 리플레이
  const [replayBase, setReplayBase] = useState(() => Date.now() + 600);
  const [frozenAt, setFrozenAt] = useState<number | null>(null);
  const replayEnd = mode === "campaign" ? CAMPAIGN_END : REPLAY_END;
  const replayT = Math.max(0, Math.min(replayEnd, ((frozenAt ?? now) - replayBase) / 1000));
  const replayDone = replayT >= replayEnd;
  const restart = () => { setFrozenAt(null); setReplayBase(Date.now() + 400); seenRef.current = new Map(); };
  const togglePlay = () => {
    if (replayDone) restart();
    else if (frozenAt === null) setFrozenAt(Date.now());
    else { setReplayBase((b) => b + Date.now() - frozenAt); setFrozenAt(null); }
  };
  useEffect(() => { if (mode !== "live") restart(); }, [mode]);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 150);
    return () => clearInterval(t);
  }, []);

  // ------------------------------------------------ 타임라인 → 사무실
  const shownIncidents = useMemo(
    () => (mode === "live" ? incidents : mode === "replay" && replayT >= 0.6 ? [replayIncident(replayT, replayBase)] : []),
    [mode, incidents, replayT, replayBase],
  );
  const campaign = useMemo(() => (mode === "campaign" ? replayCampaign(replayT, replayBase) : null), [mode, replayT, replayBase]);
  const direct = mode === "live" ? directAgentOf(room) : undefined;
  // 실시간: 고른 방의 부서 기록만(사람 대화 제외) 사무실 말풍선에 쓴다. 1:1 탭이면 말풍선 없음
  const items = useMemo(() => {
    if (campaign) return campaign.items;
    if (mode !== "live") return buildTimeline(shownIncidents, [], [], ALL_ROOM);
    if (direct) return [] as ChatItem[];
    return (data.items[room] ?? []).filter((it) => !isHuman(it));
  }, [campaign, mode, shownIncidents, direct, data.items, room]);

  // 말풍선 id → 처음 본 시각. 실시간은 연결 직후 이미 있던 기록을 0(말풍선 없음)으로 둔다
  const seenRef = useRef(new Map<string, number>());
  const liveReady = isReplay || orchConn !== "unknown";
  const seen = useMemo(() => {
    const m = seenRef.current;
    const first = m.size === 0 && mode === "live";
    for (const it of items) {
      if (!m.has(it.id)) m.set(it.id, first ? 0 : Date.now());
      // 보류 카드는 승인돼도 id가 같아서 승인을 처음 본 시각을 따로 둔다 ("👤 승인")
      if (it.kind === "hold" && it.state === "approved" && !m.has(`${it.id}:approved`)) m.set(`${it.id}:approved`, first ? 0 : Date.now());
    }
    return m;
  }, [items, mode]);
  useEffect(() => { seenRef.current = new Map(); }, [mode]);

  const baseView = useMemo(
    () => deriveOffice(shownIncidents, items, seen, now, campaign ? { desks: campaign.desks, board: campaign.board, overlay: campaign.overlay } : {}),
    [shownIncidents, items, seen, now, campaign],
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
  // 단톡방: 실시간은 고른 방의 대화(기록 + 그 방 사람 대화), 리플레이는 전체 대화방 사람 대화를 구분선 아래에 붙인다
  const roomTalk = useMemo(() => talk.items.filter((it) => it.room === (mode === "live" ? room : ALL_ROOM)), [talk.items, mode, room]);
  const chatItems = useMemo(
    () => (mode === "live" ? data.items[room] ?? [] : mergeTalk(items, roomTalk, true)),
    [mode, data.items, room, items, roomTalk],
  );
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
    key: d.key, name: d.key === "orchestrator" ? "총괄" : `${d.label} Agent`, what: skillKo(view.desks[d.key].skill) || "작업",
  }));
  // 이 방에서 답을 기다리는 부서는 "답을 쓰는" 줄을 하나 더 (총괄 일로 이미 작업 중 줄이 있으면 그대로)
  const talkRoom = mode === "live" ? room : ALL_ROOM;
  for (const key of talk.busyIn(talkRoom)) {
    if (typing.some((t) => t.key === key || t.key === `${key}:talk`)) continue;
    const agent = DIRECT_AGENTS.find((a) => a.key === key);
    if (agent) typing.push({ key: `${key}:talk`, name: agent.name, what: "답을 쓰는" });
  }

  const inc = view.incident;
  const statusText = mode === "campaign" ? (replayDone ? "캠페인 리플레이 끝" : "캠페인 리플레이 진행 중") : {
    ok: "정상 운영", alert: inc ? `${incidentTitle(inc)} 대응 중` : "대응 중", resolved: "장애 해결", degraded: "일부 미완료로 종료",
  }[view.status];
  const officeLabel = `부서 AI 사무실. ${statusText}. ` + DESKS.map((d) => `${d.label} ${STATE_KO[view.desks[d.key].state]}`).join(", ");

  // 진행 중인 장애 방 (가장 최근에 열린 것). 사무실 오른쪽 위에 표시
  const liveRoom = mode === "live" ? data.incidentRooms.find((r) => r.status === "open") : undefined;
  const roomInfo = room === ALL_ROOM ? data.all : data.incidentRooms.find((r) => r.id === room);
  const roomInc = mode === "live" ? incidents.find((i) => i.context_id === room) : undefined;

  const tabs = mode === "live" ? (
    <div role="tablist" aria-label="대화방" className="scroll-thin flex gap-1.5 overflow-x-auto border-b border-ink-100 bg-surface px-3 py-2.5">
      <RoomTab on={room === ALL_ROOM} onClick={() => setRoom(ALL_ROOM)} label="전체" unread={data.unseen[ALL_ROOM]} />
      {data.incidentRooms.map((r) => (
        <RoomTab key={r.id} on={room === r.id} onClick={() => setRoom(r.id)} label={r.title} unread={data.unseen[r.id]}
          mark={r.status === "open" ? <span className="h-1.5 w-1.5 rounded-full bg-red-700" aria-label="진행 중" /> : <span aria-label="끝남">✓</span>} />
      ))}
      {openDirect.map((key) => {
        const a = DIRECT_AGENTS.find((x) => x.key === key);
        if (!a) return null;
        return (
          <RoomTab key={key} on={room === DIRECT_PREFIX + key} onClick={() => setRoom(DIRECT_PREFIX + key)} label={`${a.name.replace(/\s*AI$/, "")} 1:1`} dashed
            mark={<span aria-hidden="true">🔒</span>} onClose={() => closeTalk(key)} />
        );
      })}
    </div>
  ) : undefined;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-4 lg:p-6">
      <style>{"@keyframes officePop{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}"}</style>
      <header className="flex flex-wrap items-start gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight">AI 오피스</h1>
          <p className="mt-1 text-sm text-ink-500">
            부서 AI가 지금 무슨 일을 하는지 사무실로 봅니다. 총괄이 일을 맡기면 그 부서가 총괄에게 와서 받아 가고, 일하는 동안 불꽃이 일어납니다. 사람 승인이 필요하면 🚩 승인 대기로 깜빡입니다.
            {" "}오른쪽 단톡방은 <Link href="/agents/chat" className="font-medium text-brand-700 underline-offset-2 hover:underline">대화 기록</Link>과 같은 방이라 어디서 말해도 양쪽에 보입니다. 캐릭터를 누르면 그 부서와 1:1로 이야기합니다(나만 보임).
          </p>
          {mode === "live" && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              <ConnChip label="중앙 Agent" conn={orchConn} />
              <ConnChip label="안전 관문" conn={safetyConn} />
              <ConnChip label="공통 로그" conn={commConn} />
              <span className="chip bg-ink-100 text-ink-500">{POLL_MS / 1000}초마다 새로고침</span>
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-2">
          <div role="radiogroup" aria-label="보기 방식" className="flex rounded-full bg-ink-100 p-1">
            {(["live", "replay", "campaign"] as const).map((m) => (
              <button key={m} role="radio" aria-checked={mode === m} onClick={() => { setAutoNote(false); setMode(m); }}
                className={`rounded-full px-3.5 py-1 text-sm font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${mode === m ? "bg-surface text-ink-900 shadow-sm" : "text-ink-500 hover:text-ink-900"}`}>
                {MODE_LABEL[m]}
              </button>
            ))}
          </div>
          {isReplay && (
            <div className="flex items-center gap-2 text-xs text-ink-500">
              <div className="h-1.5 w-36 overflow-hidden rounded-full bg-ink-100" aria-hidden="true">
                <div className="h-full bg-brand-500" style={{ width: `${(replayT / replayEnd) * 100}%` }} />
              </div>
              <span className="w-[84px] tabular-nums">{mmss(replayT)} / {mmss(replayEnd)}</span>
              <button className="btn-ghost px-3 py-1 text-xs" onClick={togglePlay}>
                {replayDone ? "다시 재생" : frozenAt === null ? "일시정지" : "계속"}
              </button>
            </div>
          )}
        </div>
      </header>

      {mode === "replay" && (
        <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {autoNote ? "중앙 Agent(총괄)에 연결할 수 없어 리플레이를 보여 줍니다. " : ""}
          리플레이는 결제 장애(시나리오 A, 롤백 C 등급 보류)를 총괄 응답 모양으로 만든 <b>예시 사건</b>입니다. 숫자는 시연용 값입니다.
        </p>
      )}
      {mode === "campaign" && (
        <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">
          캠페인 리플레이는 <b>예시 흐름</b>입니다. 숫자는 시연용 값입니다. (데이터 → 기획 → 사람 확인 → 마케팅 → 재무 → 데이터 → 기획 → 재무)
        </p>
      )}

      <div className="flex min-h-0 flex-1 flex-col gap-4 xl:flex-row">
        <section aria-label="부서 AI 사무실" className="card flex min-w-0 shrink-0 flex-col gap-3 p-4 xl:flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="card-title">부서 AI 사무실</h2>
            <span className="text-xs text-ink-500">9자리 · 총괄 포함</span>
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
          <PixelOffice view={view} label={officeLabel} onPick={mode === "live" ? openTalk : undefined} />
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

        {direct ? (
          <div className="chat-round card flex min-h-[460px] min-w-0 flex-1 flex-col overflow-hidden xl:w-[580px] xl:flex-none">
            {tabs}
            <DirectChat agent={direct} narrow />
          </div>
        ) : (
          <OfficeChat
            items={chatItems.slice(-80)}
            typing={typing}
            freshIds={freshIds}
            tabs={tabs}
            title={mode === "live" ? roomInfo?.title ?? "찾을 수 없는 방" : "부서 단톡방"}
            status={mode === "live" ? roomInfo?.status : undefined}
            longTitle={mode === "live" ? roomInfo?.longTitle : undefined}
            sub={mode === "replay" ? "SCN-A 리플레이" : mode === "campaign" ? "캠페인 리플레이" : undefined}
            inc={roomInc}
            room={talkRoom}
            talk={talk}
            big={mode === "live"}
          />
        )}
      </div>
    </div>
  );
}

function RoomTab({ on, onClick, label, unread, mark, dashed = false, onClose }: {
  on: boolean; onClick: () => void; label: string; unread?: number; mark?: ReactNode; dashed?: boolean; onClose?: () => void;
}) {
  // 새로 고른 탭(캐릭터를 눌러 연 1:1 탭 등)이 탭 줄 밖에 있으면 보이게 옮긴다
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (on) btn.current?.scrollIntoView({ block: "nearest", inline: "nearest" }); }, [on]);
  return (
    <span className={`flex shrink-0 items-center rounded-full border text-xs ${on ? "border-transparent bg-ink-900 text-app" : "border-transparent bg-ink-50 text-ink-700 hover:bg-ink-100"} ${dashed && !on ? "!border-dashed !border-ink-300" : ""}`}>
      <button type="button" role="tab" aria-selected={on} onClick={onClick} title={label} ref={btn}
        className="flex max-w-[170px] items-center gap-1.5 rounded-full px-3 py-1.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
        {mark}
        <span className="truncate">{label}</span>
        {!!unread && !on && <span className="chat-badge bg-red-700 px-1.5 text-[10px] font-bold text-white" aria-label={`안 읽은 메시지 ${unread}개`}>{unread}</span>}
      </button>
      {onClose && (
        <button type="button" onClick={onClose} aria-label={`${label} 탭 닫기`} className="mr-1.5 rounded-full px-1 text-[11px] opacity-70 hover:opacity-100">×</button>
      )}
    </span>
  );
}

function ConnChip({ label, conn }: { label: string; conn: Conn }) {
  const tone = conn === "on" ? "bg-emerald-50 text-emerald-700" : conn === "off" ? "bg-red-50 text-red-700" : conn === "unready" ? "bg-amber-50 text-amber-700" : "bg-ink-100 text-ink-500";
  return <span className={`chip ${tone}`}>{label} {{ on: "켜짐", off: "꺼짐", unready: "준비 안 됨(503)", unknown: "확인 중" }[conn]}</span>;
}
