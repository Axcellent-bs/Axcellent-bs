import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  INCIDENT_STATUS_KO, SAFETY_ROOM, buildTimeline, incidentTitle, messageCount,
  type ChatItem, type OrchIncident, type Tone,
} from "@/lib/agentChat";
import { api, ApiError } from "@/lib/api";
import { finance, type ExecutionRecord } from "@/lib/finance";
import { Link } from "@/lib/nav";
import { orchestrator } from "@/lib/orchestrator";
import { shopFaults, type FaultState } from "@/lib/shopFaults";
import type { CommLine } from "@/lib/types";

// AI 대화방. 부서 AI들이 A2A로 주고받은 일을 메신저처럼 보여 준다.
// 말풍선 문장은 lib/agentChat.ts가 기록된 값으로만 만든다. 이 파일은 화면과 새로고침만 맡는다.
// 출처 세 곳: 총괄(이벤트·단계), 안전 관문(돈이 나가는 실행), dashboard-api 공통 로그(부서 간 소통).
// 운영 환경에서는 총괄·안전 관문 기록도 dashboard-api를 거쳐 읽어야 한다 (지금은 개발용 직접 호출, 백엔드 후속 작업).
// 장애는 이 화면에서 일으키지 않는다(사람이 요청하면 운영자가 캣북스에 넣는다). 화면은 캣북스 결제 상태만 읽어 보여 준다.

const POLL_MS = 3000;
// AI들은 1초 안에 수십 건을 주고받는다. 새로 도착한 메시지는 줄 세워 하나씩 보여 준다 (밀리면 조금 빠르게)
const REVEAL_MS = 600;
const REVEAL_FAST_MS = 200;
const FAST_BACKLOG = 20;
const ORCH_CMD = "cd backend && bash scripts/run_all.sh";

/** unready = 서버는 답했지만 503 (공통 로그 읽기가 아직 연결 안 됨) */
type Conn = "unknown" | "on" | "off" | "unready";
const PROD_NOTE = "운영 환경에서는 AXcellent가 총괄·안전 관문 기록도 dashboard-api를 거쳐 읽어야 합니다 (지금은 개발용으로 각 서버를 직접 부름).";

const clock = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" }) : "");

// ---------------------------------------------------------------- 부서별 말풍선 색 (디자인 토큰만 사용)
const BUBBLE: Record<string, { bubble: string; avatar: string }> = {
  infra: { bubble: "bg-ink-100", avatar: "bg-ink-700 text-app" },
  data: { bubble: "bg-violet-50", avatar: "bg-violet-700 text-white dark:text-app" },
  planning: { bubble: "bg-surface-2", avatar: "bg-brand-500 text-white" },
  marketing: { bubble: "bg-accent-50", avatar: "bg-accent text-white" },
  support: { bubble: "bg-emerald-50", avatar: "bg-emerald-700 text-white dark:text-app" },
  finance: { bubble: "bg-amber-50", avatar: "bg-amber-700 text-white dark:text-app" },
  hr: { bubble: "bg-red-50", avatar: "bg-red-700 text-white dark:text-app" },
  safety: { bubble: "bg-brand-600 text-white", avatar: "bg-brand-strong text-white" },
  orchestrator: { bubble: "bg-brand-600 text-white", avatar: "bg-brand-strong text-white" },
};
const bubbleOf = (key: string) => BUBBLE[key] ?? { bubble: "bg-ink-100", avatar: "bg-ink-500 text-white" };

const TONE_MARK: Record<Tone, string> = {
  request: "", ok: "border-l-2 border-emerald-700/50", warn: "border-l-2 border-amber-700/60", fail: "border-l-2 border-red-700/60", muted: "opacity-80",
};
const TONE_ICON: Record<Tone, string> = { request: "", ok: "✓ ", warn: "⏸ ", fail: "✕ ", muted: "" };

const hhmm = (iso: string) => (iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "");

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

// ---------------------------------------------------------------- 타임라인 항목
function Avatar({ name, k, small = false }: { name: string; k: string; small?: boolean }) {
  return (
    <span aria-hidden="true" className={`flex ${small ? "h-5 w-5 text-[10px]" : "h-8 w-8"} shrink-0 items-center justify-center rounded-full text-xs font-semibold ${bubbleOf(k).avatar}`}>
      {name.slice(0, 1)}
    </span>
  );
}

function Message({ m }: { m: Extract<ChatItem, { kind: "msg" }> }) {
  const right = m.central;
  const look = bubbleOf(m.fromKey);
  return (
    <li className={`flex gap-2.5 ${right ? "flex-row-reverse" : ""}`}>
      <Avatar name={m.from} k={m.fromKey} />
      <div className={`flex min-w-0 max-w-[min(560px,85%)] flex-col ${right ? "items-end" : "items-start"}`}>
        <div className={`flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs ${right ? "justify-end" : ""}`}>
          <span className="font-semibold text-ink-900">{m.from}</span>
          <span className="chip bg-ink-100 px-1.5 py-0 text-[10px] text-ink-500">→ {m.to}</span>
          <time dateTime={m.at} className="tabular-nums text-[11px] text-ink-500">{hhmm(m.at)}{m.repeat && m.lastAt ? ` ~ ${hhmm(m.lastAt)}` : ""}</time>
          {m.repeat && <span className="chip bg-brand-50 px-1.5 py-0 text-[10px] font-semibold text-brand-700" title="같은 내용을 여러 번 주고받아 하나로 묶었습니다">×{m.repeat}</span>}
        </div>
        <div title={m.raw} className={`mt-1 rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${right ? "rounded-tr-md" : "rounded-tl-md"} ${look.bubble} ${right ? "" : TONE_MARK[m.tone]}`}>
          <p>{right ? "" : TONE_ICON[m.tone]}{m.text}</p>
          {m.facts.length > 0 && (
            <dl className="mt-1.5 flex flex-wrap gap-1.5">
              {m.facts.map((f) => (
                <div key={f.label} className="rounded-lg bg-surface/70 px-2 py-0.5 text-[11px] text-ink-700">
                  <dt className="inline text-ink-500">{f.label} </dt>
                  <dd className="inline font-medium tabular-nums">{f.value}</dd>
                </div>
              ))}
            </dl>
          )}
          {m.tag && <p className={`mt-1 text-[11px] ${right ? "text-white/70" : "text-ink-500"}`}>{m.tag}</p>}
        </div>
      </div>
    </li>
  );
}

function HoldBanner({ h, busy, canDecide, orchMock, onDecide }: {
  h: Extract<ChatItem, { kind: "hold" }>; busy: boolean; canDecide: boolean; orchMock: boolean; onDecide: (approved: boolean) => void;
}) {
  // 결정 경로는 하나: 실제 C 보류는 안전 관문 보류 큐에서만. 총괄 보류 버튼은 목 모드 데모일 때만 보인다
  // (총괄의 POST /api/incidents/{ctx}/holds/{step}은 사람 토큰을 받지만 목 모드에서만 열린다 — DSH-03)
  const orchHold = !h.link;
  const showButtons = orchHold && orchMock;
  const link = h.link ?? (orchMock ? undefined : "/finance");
  const outcome = { approved: "사람이 승인 → 다시 진행", rejected: "사람이 거절 → 진행하지 않음", timeout: "기한 안에 결정 없음 → 자동 거절", pending: "" }[h.state];
  return (
    <li className="rounded-2xl bg-ink-900 px-4 py-3 text-app dark:bg-surface-2 dark:text-ink-900 dark:ring-1 dark:ring-amber-700/40">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <p className="text-sm font-semibold">
          <span aria-hidden="true">🚩 </span>
          {h.state === "pending" ? "사람 판단 필요" : "인간 판단"}: {h.title}
        </p>
        <time dateTime={h.at} className="tabular-nums text-[11px] opacity-70">{hhmm(h.at)}</time>
        {h.state !== "pending" && <span className="chip ml-auto bg-white/15 text-current dark:bg-ink-100">{outcome}</span>}
        {h.state === "pending" && link && (
          <Link href={link} className="btn ml-auto bg-white/15 text-current hover:bg-white/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-700 dark:bg-ink-100">
            {orchHold ? "안전 관문 보류 큐에서 결정 (/finance) →" : "재무 AI 화면에서 결정 →"}
          </Link>
        )}
        {h.state === "pending" && showButtons && (
          <div className="ml-auto flex gap-2">
            <button className="btn bg-emerald-700 text-white hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700 dark:text-app"
              disabled={busy || !canDecide} onClick={() => onDecide(true)}>승인</button>
            <button className="btn bg-white/15 text-current hover:bg-white/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-700 dark:bg-ink-100"
              disabled={busy || !canDecide} onClick={() => onDecide(false)}>거절</button>
          </div>
        )}
      </div>
      <p className="mt-1 text-[11px] opacity-75">
        {h.detail}
        {showButtons && h.state === "pending" && " · 목 모드 데모 전용 버튼입니다. 총괄 AI는 스스로 승인하지 않고, 여기서 누른 결정을 그대로 전달합니다."}
        {orchHold && !orchMock && h.state === "pending" && " · 실제 모드에서는 C 등급 보류를 안전 관문 보류 큐 한 곳에서만 결정합니다 (이 화면의 총괄 결정 경로는 사람 확인이 없어 목 모드에서만 씁니다)."}
      </p>
    </li>
  );
}

function Item({ it, busyHold, orchOn, orchMock, onDecide }: { it: ChatItem; busyHold: string | null; orchOn: boolean; orchMock: boolean; onDecide: (h: Extract<ChatItem, { kind: "hold" }>, approved: boolean) => void }) {
  switch (it.kind) {
    case "msg":
      return <Message m={it} />;
    case "hold":
      return <HoldBanner h={it} busy={busyHold === it.id} canDecide={orchOn} orchMock={orchMock} onDecide={(a) => onDecide(it, a)} />;
    case "system":
      return (
        <li className="flex justify-center">
          <p className="max-w-[90%] rounded-full bg-ink-50 px-3 py-1 text-center text-[11px] text-ink-500 ring-1 ring-ink-100">
            {it.text} <time dateTime={it.at} className="ml-1 tabular-nums">{hhmm(it.at)}</time>
          </p>
        </li>
      );
    case "separator":
      return it.variant === "day" ? (
        <li role="separator" className="flex items-center gap-3 pt-2 text-[11px] font-medium text-ink-500">
          <span className="h-px flex-1 bg-ink-100" /><span title={it.raw} className={it.raw ? "cursor-help" : undefined}>{it.text}</span><span className="h-px flex-1 bg-ink-100" />
        </li>
      ) : (
        <li role="separator" className="pt-1">
          <p title={it.raw} className={`text-center text-xs font-semibold text-ink-700 ${it.raw ? "cursor-help" : ""}`}>{it.text}</p>
        </li>
      );
  }
}

// ---------------------------------------------------------------- 방 목록
interface Room { id: string; title: string; sub?: string; status?: string; count: number }

const STATUS_TONE: Record<string, string> = {
  open: "bg-brand-50 text-brand-700", resolved: "bg-emerald-50 text-emerald-700", degraded: "bg-amber-50 text-amber-700",
};

function RoomList({ rooms, selected, unseen, onSelect }: { rooms: Room[]; selected: string; unseen: Record<string, number>; onSelect: (id: string) => void }) {
  return (
    <nav aria-label="대화방 목록" className="card shrink-0 overflow-hidden lg:w-72">
      <h2 className="px-4 pb-1 pt-4 text-sm font-semibold">대화방</h2>
      <ul className="scroll-thin flex gap-1 overflow-x-auto px-2 pb-3 lg:max-h-[calc(100vh-260px)] lg:flex-col lg:overflow-y-auto lg:overflow-x-visible">
        {rooms.map((r) => {
          const on = r.id === selected;
          const n = unseen[r.id] ?? 0;
          return (
            <li key={r.id} className="shrink-0 lg:shrink">
              <button onClick={() => onSelect(r.id)} aria-current={on ? "true" : undefined}
                className={`w-full min-w-[180px] rounded-xl px-3 py-2.5 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${on ? "bg-brand-50" : "hover:bg-ink-50"}`}>
                <div className="flex items-center gap-2">
                  <span className={`truncate text-sm ${on ? "font-semibold text-brand-700" : "font-medium"}`}>{r.title}</span>
                  <span className="ml-auto shrink-0 text-[11px] tabular-nums text-ink-500">{r.count}</span>
                  {n > 0 && !on && <span className="chip shrink-0 bg-accent px-1.5 text-white" aria-label={`새 메시지 ${n}개`}>{n}</span>}
                </div>
                {(r.sub || r.status) && (
                  <div className="mt-1 flex items-center gap-1.5">
                    {r.status && <span className={`chip shrink-0 whitespace-nowrap ${STATUS_TONE[r.status] ?? "bg-ink-100 text-ink-500"}`}>{INCIDENT_STATUS_KO[r.status] ?? r.status}</span>}
                    {r.sub && <span className="truncate text-[11px] text-ink-500">{r.sub}</span>}
                  </div>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

// ---------------------------------------------------------------- 페이지
export default function AgentChat() {
  const [incidents, setIncidents] = useState<OrchIncident[]>([]);
  const [executions, setExecutions] = useState<ExecutionRecord[]>([]);
  const [orchConn, setOrchConn] = useState<Conn>("unknown");
  const [safetyConn, setSafetyConn] = useState<Conn>("unknown");
  const [comms, setComms] = useState<CommLine[]>([]);
  const [commConn, setCommConn] = useState<Conn>("unknown");
  const [commTruncated, setCommTruncated] = useState(false);
  // 총괄이 목 모드라고 알려 줄 때만(GET /api/incidents의 meta.mock) 총괄 보류에 승인·거절 버튼을 보인다
  const [orchMock, setOrchMock] = useState(false);
  const [room, setRoom] = useState<string>(SAFETY_ROOM);
  const roomPicked = useRef(false);
  const [seen, setSeen] = useState<Record<string, number>>({});
  const [busyHold, setBusyHold] = useState<string | null>(null);
  const [fault, setFault] = useState<FaultState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    const [inc, exe, com] = await Promise.allSettled([orchestrator.incidents(), finance.executions(), api.communications()]);
    if (inc.status === "fulfilled") {
      setIncidents(inc.value.data);
      setOrchMock(inc.value.meta.mock === true);
      setOrchConn("on");
      if (inc.value.meta.mock !== true) shopFaults.state().then(setFault, () => setFault(null));
    }
    else { setOrchConn("off"); setOrchMock(false); }
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
    refresh();
    const t = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
    const onVis = () => { if (!document.hidden) refresh(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", onVis); };
  }, [refresh]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const id of [SAFETY_ROOM, ...incidents.map((i) => i.context_id)]) {
      c[id] = messageCount(buildTimeline(incidents, executions, comms, id));
    }
    return c;
  }, [incidents, executions, comms]);

  const rooms: Room[] = useMemo(() => [
    ...[...incidents].reverse().map((inc) => ({
      id: inc.context_id,
      title: incidentTitle(inc),
      sub: `${clock(inc.opened_at)} 시작`,
      status: inc.status,
      count: counts[inc.context_id] ?? 0,
    })),
    { id: SAFETY_ROOM, title: "안전 관문(돈이 나가는 일)", sub: safetyConn === "off" ? "연결 안 됨" : undefined, count: counts[SAFETY_ROOM] ?? 0 },
  ], [incidents, counts, safetyConn]);

  // 지금 보고 있는 방은 읽음 처리
  useEffect(() => { setSeen((s) => (s[room] === counts[room] ? s : { ...s, [room]: counts[room] ?? 0 })); }, [room, counts]);
  const unseen = useMemo(() => Object.fromEntries(rooms.map((r) => [r.id, seen[r.id] == null ? 0 : Math.max(0, r.count - seen[r.id])])), [rooms, seen]);

  const items = useMemo(() => buildTimeline(incidents, executions, comms, room), [incidents, executions, comms, room]);

  // ------------------------------------------------ 새 이벤트가 열리면 그 방으로 옮겨 처음부터 차례로 보여 준다
  // 화면을 연 순간 이미 있던 이벤트는 옮기지 않는다
  const knownIncidents = useRef<Set<string> | null>(null);
  const replayRoom = useRef<string | null>(null);
  useEffect(() => {
    if (orchConn !== "on") return;
    const ids = incidents.map((i) => i.context_id);
    if (!roomPicked.current) {
      roomPicked.current = true;
      if (ids.length) setRoom(ids[ids.length - 1]);
    }
    if (knownIncidents.current === null) { knownIncidents.current = new Set(ids); return; }
    const opened = ids.find((id) => !knownIncidents.current?.has(id));
    for (const id of ids) knownIncidents.current.add(id);
    if (opened) { replayRoom.current = opened; setRoom(opened); }
  }, [incidents, orchConn]);

  // ------------------------------------------------ 하나씩 보여 주기
  // 방을 열 때 이미 있던 기록은 한 번에(지난 기록을 다시 재생하지 않음), 그 뒤에 도착한 것은 하나씩
  const [revealed, setRevealed] = useState(0);
  const primed = useRef(false);
  useLayoutEffect(() => { primed.current = false; }, [room]);
  useLayoutEffect(() => {
    if (primed.current || !loaded) return;
    primed.current = true;
    if (replayRoom.current === room) { replayRoom.current = null; setRevealed(0); return; }
    setRevealed(items.length);
  }, [room, items.length, loaded]);
  useEffect(() => {
    if (!primed.current || revealed >= items.length) return;
    const backlog = items.length - revealed;
    const t = setTimeout(() => setRevealed((r) => r + 1), backlog > FAST_BACKLOG ? REVEAL_FAST_MS : REVEAL_MS);
    return () => clearTimeout(t);
  }, [revealed, items.length]);
  const shown = useMemo(() => items.slice(0, Math.min(revealed, items.length)), [items, revealed]);
  const typing = primed.current && revealed < items.length;
  // 참석자: 이 방에서 말한 쪽과 받은 쪽 (처음 등장한 순서)
  const participants = useMemo(() => {
    const seen = new Map<string, { key: string; name: string }>();
    for (const it of items) {
      if (it.kind !== "msg") continue;
      // 맡은 AI가 없는 단계("담당 미정")·받는 쪽이 기록되지 않은 줄은 참석자가 아니다
      if (it.from !== "담당 미정" && !seen.has(it.from)) seen.set(it.from, { key: it.fromKey, name: it.from });
      if (it.to && it.to !== "받는 쪽 미기록" && it.to !== "담당 미정" && !seen.has(it.to)) seen.set(it.to, { key: it.to, name: it.to });
    }
    return [...seen.values()];
  }, [items]);

  // ------------------------------------------------ 스크롤: 맨 아래에 붙어 있으면 따라가고, 위로 올렸으면 "새 메시지" 알약
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const lastLen = useRef(0);
  const [fresh, setFresh] = useState(0);
  // 화면이 스스로 내린 스크롤은 "사람이 위로 올림"으로 보지 않는다 (부드러운 스크롤 도중의 scroll 이벤트)
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
      await refresh();
    } catch (e) {
      setError(`결정을 전달하지 못했습니다: ${(e as Error).message}`);
    } finally {
      setBusyHold(null);
    }
  }

  const orchOn = orchConn === "on";
  const roomEmpty = items.length === 0;

  return (
    <div className="flex h-full flex-col gap-4 p-4 lg:p-6">
      <header className="flex flex-wrap items-start gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight">AI 대화방</h1>
          <p className="mt-1 text-sm text-ink-500">
            AI들이 서로 일을 부탁하고 결과를 알려 준 실제 기록입니다(A2A). 사람이 결정할 일은 🚩로 표시합니다.
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
        <RoomList rooms={rooms} selected={room} unseen={unseen} onSelect={setRoom} />

        <section aria-label="대화 기록" className="card relative flex min-h-[420px] min-w-0 flex-1 flex-col overflow-hidden">
          {participants.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 border-b border-ink-100 px-4 py-2.5 lg:px-6" aria-label="참석자">
              <span className="mr-1 text-xs font-semibold text-ink-700">참석자 {participants.length}</span>
              {participants.map((p) => (
                <span key={p.key} className="flex items-center gap-1.5 rounded-full bg-ink-50 py-0.5 pl-0.5 pr-2.5 text-xs text-ink-700 ring-1 ring-ink-100">
                  <Avatar name={p.name} k={p.key} small />
                  {p.name}
                </span>
              ))}
            </div>
          )}
          <div ref={scroller} onScroll={onScroll} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-5 lg:px-6">
            {roomEmpty ? (
              <EmptyState orchConn={orchConn} safetyConn={safetyConn} commConn={commConn} room={room} />
            ) : (
              <>
              {orchConn === "off" && room === SAFETY_ROOM && (
                <p className="mb-4 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">총괄 AI가 꺼져 있어 안전 관문 기록만 보입니다. 켜는 방법: <code className="font-mono">{ORCH_CMD}</code></p>
              )}
              {room !== SAFETY_ROOM && <CommNote conn={commConn} truncated={commTruncated} />}
              <ol role="log" aria-live="polite" aria-relevant="additions" aria-label="부서 AI 대화" className="flex flex-col gap-4">
                {shown.map((it) => <Item key={it.id} it={it} busyHold={busyHold} orchOn={orchOn} orchMock={orchMock} onDecide={decide} />)}
                {typing && <p className="animate-pulse px-1 text-xs text-ink-500" aria-live="polite">AI들이 이야기하는 중… ({items.length - revealed}개 남음)</p>}
              </ol>
              <p className="mt-6 text-center text-[11px] text-ink-500">{PROD_NOTE}</p>
              </>
            )}
          </div>
          {fresh > 0 && (
            <button onClick={() => toBottom(true)}
              className="btn-primary absolute bottom-4 left-1/2 -translate-x-1/2 shadow-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500">
              새 메시지 {fresh}개 ↓
            </button>
          )}
        </section>
      </div>
    </div>
  );
}

/** 공통 로그가 503이거나 꺼졌을 때 / 잘렸을 때 한 줄 안내 */
function CommNote({ conn, truncated }: { conn: Conn; truncated: boolean }) {
  if (conn === "unready") return <p className="mb-4 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">{COMM_503}</p>;
  if (conn === "off") return <p className="mb-4 rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-500">{COMM_OFF}</p>;
  if (truncated) return <p className="mb-4 rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-500">공통 로그가 많아 앞부분만 받았습니다 (최대 1000줄).</p>;
  return null;
}
const COMM_503 = "공통 로그: 준비 안 됨(503) — dashboard-api에 공통 로그 읽기가 아직 연결되지 않았습니다 (ax-backend #249). 부서끼리 주고받은 대화는 연결 뒤에 보입니다.";
const COMM_OFF = "공통 로그: dashboard-api(VITE_DASHBOARD_API_URL)에 연결할 수 없어 부서끼리 주고받은 대화를 불러오지 못했습니다.";

function EmptyState({ orchConn, safetyConn, commConn, room }: { orchConn: Conn; safetyConn: Conn; commConn: Conn; room: string }) {
  if (room === SAFETY_ROOM) {
    return (
      <div className="mx-auto max-w-md py-16 text-center text-sm text-ink-500">
        {safetyConn === "off"
          ? <>안전 관문(포트 8001)에 연결할 수 없습니다. ax-backend의 safety 서버를 켜 주세요.</>
          : <>아직 안전 관문을 거친 실행 요청이 없습니다. 부서 AI가 돈이 나가는 일을 요청하면 여기에 쌓입니다.</>}
      </div>
    );
  }
  if (orchConn === "off") {
    return (
      <div className="mx-auto max-w-xl py-14 text-sm text-ink-700">
        <p className="font-semibold text-ink-900">총괄 AI가 꺼져 있습니다.</p>
        <p className="mt-1 text-ink-500">backend/에서 실제 모드로 켜면 캣북스 쇼핑몰 장애로 AI들이 함께 일하는 과정을 볼 수 있습니다.</p>
        <pre className="mt-3 overflow-x-auto rounded-xl bg-ink-50 p-3 font-mono text-xs text-ink-700 ring-1 ring-ink-100"><code>{ORCH_CMD}</code></pre>
        <p className="mt-2 text-xs text-ink-500">다른 포트에서 켰다면 루트 .env의 ORCHESTRATOR_PROXY_TARGET(개발 서버 프록시 대상)을 바꾸고 개발 서버를 다시 시작하세요.</p>
      </div>
    );
  }
  if (orchConn === "unknown") return <p className="py-16 text-center text-sm text-ink-500">불러오는 중…</p>;
  return (
    <div className="mx-auto max-w-md py-16 text-center text-sm text-ink-500">
      아직 이벤트가 없습니다. 위의 <b className="text-ink-900">장애 시나리오 시작</b>을 눌러 보세요.
      {commConn === "unready" && <p className="mt-4 text-xs">{COMM_503}</p>}
      <p className="mt-4 text-[11px]">{PROD_NOTE}</p>
    </div>
  );
}
