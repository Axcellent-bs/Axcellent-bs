import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ALL_ROOM, DIRECT_ROOM, INCIDENT_STATUS_KO, SAFETY_ROOM, buildTimeline, incidentTitle, messageCount,
  type ChatItem, type DemoRequest, type OrchIncident, type Tone,
} from "@/lib/agentChat";
import { api, ApiError } from "@/lib/api";
import { finance, type ExecutionRecord } from "@/lib/finance";
import { Link } from "@/lib/nav";
import { orchestrator } from "@/lib/orchestrator";
import type { CommLine } from "@/lib/types";

// AI 대화방. 부서 AI들이 A2A로 주고받은 일을 메신저처럼 보여 준다.
// 말풍선 문장은 lib/agentChat.ts가 기록된 값으로만 만든다. 이 파일은 화면과 새로고침만 맡는다.
// 출처 세 곳: 총괄(사건·단계), 안전 관문(돈이 나가는 실행), dashboard-api 공통 로그(부서 간 소통).
// 운영 환경에서는 총괄·안전 관문 기록도 dashboard-api를 거쳐 읽어야 한다 (지금은 개발용 직접 호출, 백엔드 후속 작업).

const POLL_MS = 3000;
const ORCH_CMD = "cd ax-backend && ORCHESTRATOR_MOCK=1 uv run uvicorn ax_orchestrator.main:app --app-dir orchestrator/src --port 8000";

/** unready = 서버는 답했지만 503 (공통 로그 읽기가 아직 연결 안 됨) */
type Conn = "unknown" | "on" | "off" | "unready";
const PROD_NOTE = "운영 환경에서는 대시보드가 총괄·안전 관문 기록도 dashboard-api를 거쳐 읽어야 합니다 (지금은 개발용으로 각 서버를 직접 부름).";

// 이 화면에서 시작한 사건이 어느 시나리오였는지 기억한다 (방 이름에 쓰는 편의 기능, 없어도 동작)
const SCENARIO_KEY = "agentChat.scenarios";
function loadScenarioNames(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(SCENARIO_KEY) ?? "{}") as Record<string, string>; } catch { return {}; }
}
function saveScenarioNames(v: Record<string, string>) {
  try { localStorage.setItem(SCENARIO_KEY, JSON.stringify(v)); } catch { /* 저장 못 해도 화면은 그대로 */ }
}
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
function Avatar({ name, k }: { name: string; k: string }) {
  return (
    <span aria-hidden="true" className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${bubbleOf(k).avatar}`}>
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
          <time dateTime={m.at} className="tabular-nums text-[11px] text-ink-500">{hhmm(m.at)}</time>
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
  // (총괄의 POST /api/incidents/{ctx}/holds/{step}은 사람 토큰을 확인하지 않음 — DSH-03)
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
        {showButtons && h.state === "pending" && " · 목 모드 데모 전용 버튼입니다. 중앙 Agent는 스스로 승인하지 않고, 여기서 누른 결정을 그대로 전달합니다."}
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
const DEMO_OPTIONS: { key: keyof DemoRequest; label: string }[] = [
  { key: "hold_rollback", label: "롤백을 C 등급 보류로 (사람 승인 대기)" },
  { key: "fail_marketing", label: "공지 게시 한 번 실패 (재시도)" },
  { key: "wrong_owner", label: "공지 게시 소관 아님 (이관)" },
  { key: "infra_card_empty", label: "인프라 카드 비어 있음 (건너뜀)" },
];

// 이름 붙인 장애 시나리오. 총괄 목 모드의 옵션 조합이다 (장애 종류 자체는 결제 장애 plan 하나뿐)
const SCENARIOS: { id: string; label: string; hint: string; req: DemoRequest }[] = [
  { id: "auto", label: "결제 장애 — 모두 자동 해결", hint: "진단·롤백·공지·보상·손실 리포트까지 사람 없이 끝납니다.", req: {} },
  { id: "hold", label: "롤백 확신도 낮음 — 사람 승인", hint: "원인 진단의 확신이 낮아 롤백이 🚩 사람 승인 대기로 멈춥니다. 승인·거절해 보세요.", req: { hold_rollback: true } },
  { id: "retry", label: "공지 게시 실패 — 재시도로 복구", hint: "마케팅 공지가 한 번 실패하고 중앙 Agent가 다시 시도합니다.", req: { fail_marketing: true } },
  { id: "transfer", label: "공지 담당 착오 — 다른 부서로 이관", hint: "공지 요청을 받은 부서가 '소관 아님'으로 돌려보내 중앙 Agent가 이관합니다.", req: { wrong_owner: true } },
  { id: "partial", label: "인프라 AI 연결 끊김 — 일부 건너뛰고 종료", hint: "인프라 카드가 비어 진단·롤백을 건너뛰고 부분 종료합니다.", req: { infra_card_empty: true } },
  { id: "storm", label: "겹친 사고 — 실패·이관·보류 한꺼번에", hint: "공지 실패, 이관, 롤백 승인 대기가 한 사건에서 모두 일어납니다.", req: { hold_rollback: true, fail_marketing: true, wrong_owner: true } },
  { id: "random", label: "무작위", hint: "위 옵션을 무작위로 섞어 시작합니다.", req: {} },
  { id: "custom", label: "직접 고르기", hint: "아래 옵션을 직접 골라 시작합니다.", req: {} },
];

function randomDemo(): DemoRequest {
  const req: DemoRequest = {};
  for (const o of DEMO_OPTIONS) if (Math.random() < 0.4) req[o.key] = true;
  return req;
}

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
  const [room, setRoom] = useState(ALL_ROOM);
  const [seen, setSeen] = useState<Record<string, number>>({});
  const [scenario, setScenario] = useState("hold");
  const [demo, setDemo] = useState<DemoRequest>({ hold_rollback: true });
  const picked = SCENARIOS.find((s) => s.id === scenario) ?? SCENARIOS[0];
  const [starting, setStarting] = useState(false);
  const [scenarioNames, setScenarioNames] = useState<Record<string, string>>(loadScenarioNames);
  const [busyHold, setBusyHold] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [inc, exe, com] = await Promise.allSettled([orchestrator.incidents(), finance.executions(), api.communications()]);
    if (inc.status === "fulfilled") { setIncidents(inc.value.data); setOrchMock(inc.value.meta.mock === true); setOrchConn("on"); }
    else { setOrchConn("off"); setOrchMock(false); }
    if (exe.status === "fulfilled") { setExecutions(exe.value); setSafetyConn("on"); } else setSafetyConn("off");
    if (com.status === "fulfilled") { setComms(com.value.data); setCommTruncated(com.value.meta?.truncated === true); setCommConn("on"); }
    else {
      setComms([]);
      setCommTruncated(false);
      setCommConn(com.reason instanceof ApiError && com.reason.status === 503 ? "unready" : "off");
    }
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
    for (const id of [ALL_ROOM, SAFETY_ROOM, DIRECT_ROOM, ...incidents.map((i) => i.context_id)]) {
      c[id] = messageCount(buildTimeline(incidents, executions, comms, id));
    }
    return c;
  }, [incidents, executions, comms]);

  const rooms: Room[] = useMemo(() => [
    { id: ALL_ROOM, title: "전체", count: counts[ALL_ROOM] ?? 0 },
    ...[...incidents].reverse().map((inc) => {
      const name = scenarioNames[inc.context_id];
      return {
        id: inc.context_id,
        title: name ?? incidentTitle(inc),
        sub: `${clock(inc.opened_at)} 시작${name ? ` · ${incidentTitle(inc)}` : ""}`,
        status: inc.status,
        count: counts[inc.context_id] ?? 0,
      };
    }),
    {
      id: DIRECT_ROOM, title: "부서 간 직통",
      sub: commConn === "unready" ? "공통 로그 준비 안 됨" : commConn === "off" ? "연결 안 됨" : "사건에 묶이지 않은 부서끼리의 대화",
      count: counts[DIRECT_ROOM] ?? 0,
    },
    { id: SAFETY_ROOM, title: "안전 관문(돈이 나가는 일)", sub: safetyConn === "off" ? "연결 안 됨" : undefined, count: counts[SAFETY_ROOM] ?? 0 },
  ], [incidents, counts, safetyConn, commConn, scenarioNames]);

  // 지금 보고 있는 방은 읽음 처리
  useEffect(() => { setSeen((s) => (s[room] === counts[room] ? s : { ...s, [room]: counts[room] ?? 0 })); }, [room, counts]);
  const unseen = useMemo(() => Object.fromEntries(rooms.map((r) => [r.id, seen[r.id] == null ? 0 : Math.max(0, r.count - seen[r.id])])), [rooms, seen]);

  const items = useMemo(() => buildTimeline(incidents, executions, comms, room), [incidents, executions, comms, room]);

  // ------------------------------------------------ 스크롤: 맨 아래에 붙어 있으면 따라가고, 위로 올렸으면 "새 메시지" 알약
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const lastLen = useRef(0);
  const [fresh, setFresh] = useState(0);
  const toBottom = useCallback(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
    pinned.current = true;
    setFresh(0);
  }, []);
  useLayoutEffect(() => { lastLen.current = 0; pinned.current = true; }, [room]);
  useLayoutEffect(() => {
    const added = messageCount(items) - lastLen.current;
    lastLen.current = messageCount(items);
    if (pinned.current) toBottom();
    else if (added > 0) setFresh((f) => f + added);
  }, [items, toBottom]);
  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (pinned.current) setFresh(0);
  };

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const req = scenario === "custom" ? demo : scenario === "random" ? randomDemo() : picked.req;
      const { context_id } = await orchestrator.startDemo(req);
      // 진행 중인 사건에 합쳐지면 이미 붙은 이름을 덮지 않는다
      setScenarioNames((m) => {
        if (m[context_id]) return m;
        const next = { ...m, [context_id]: scenario === "custom" ? "직접 고른 시나리오" : picked.label };
        saveScenarioNames(next);
        return next;
      });
      await refresh();
      setRoom(context_id);
    } catch (e) {
      setError(`장애 시나리오를 시작하지 못했습니다: ${(e as Error).message}`);
    } finally {
      setStarting(false);
    }
  }

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
            부서 AI들이 A2A로 일을 주고받은 기록입니다. 문장은 기록된 사실(skill·결과·금액)로 만들고, 사람이 결정할 일은 🚩로 표시합니다.
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <ConnChip label="중앙 Agent" conn={orchConn} />
            <ConnChip label="안전 관문" conn={safetyConn} />
            <ConnChip label="공통 로그" conn={commConn} />
            <span className="chip bg-ink-100 text-ink-500">{POLL_MS / 1000}초마다 새로고침</span>
          </div>
        </div>
        <div className="flex w-full max-w-sm flex-col items-end gap-1.5">
          <div className="flex w-full items-center gap-2">
            <label htmlFor="scenario" className="sr-only">장애 시나리오</label>
            <select id="scenario" value={scenario} onChange={(e) => setScenario(e.target.value)}
              className="min-w-0 flex-1 rounded-lg border border-ink-300 bg-surface px-2 py-1.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
              {SCENARIOS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
            <span title={orchOn ? "고른 시나리오로 판매 사이트 결제 장애를 주입합니다 (총괄 목 모드)" : "중앙 Agent(총괄)가 꺼져 있어 시작할 수 없습니다"}>
              <button className="btn-primary shrink-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
                disabled={!orchOn || starting} onClick={start}>
                {starting ? "시작하는 중…" : "장애 시나리오 시작"}
              </button>
            </span>
          </div>
          <p className="w-full text-right text-xs text-ink-500">{picked.hint} 진행 중인 결제 장애가 있으면 같은 사건에 합쳐집니다.</p>
          {scenario === "custom" && (
            <fieldset className="w-full space-y-1 text-left text-xs text-ink-500">
              <legend className="sr-only">장애 시나리오 옵션</legend>
              {DEMO_OPTIONS.map((o) => (
                <label key={o.key} className="flex items-center gap-2">
                  <input type="checkbox" checked={!!demo[o.key]} onChange={(e) => setDemo((d) => ({ ...d, [o.key]: e.target.checked }))} />
                  {o.label}
                </label>
              ))}
            </fieldset>
          )}
        </div>
      </header>

      {error && <p role="alert" className="rounded-xl bg-red-50 px-3 py-2 text-sm font-medium text-red-800">{error}</p>}

      <div className="flex min-h-0 flex-1 flex-col gap-4 lg:flex-row">
        <RoomList rooms={rooms} selected={room} unseen={unseen} onSelect={setRoom} />

        <section aria-label="대화 기록" className="card relative flex min-h-[420px] min-w-0 flex-1 flex-col overflow-hidden">
          <div ref={scroller} onScroll={onScroll} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-5 lg:px-6">
            {roomEmpty ? (
              <EmptyState orchConn={orchConn} safetyConn={safetyConn} commConn={commConn} room={room} />
            ) : (
              <>
              {orchConn === "off" && room === ALL_ROOM && (
                <p className="mx-auto mb-4 max-w-3xl rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">중앙 Agent(총괄)가 꺼져 있어 안전 관문 기록만 보입니다. 켜는 방법: <code className="font-mono">{ORCH_CMD}</code></p>
              )}
              {room === ALL_ROOM && <CommNote conn={commConn} truncated={commTruncated} />}
              <ol role="log" aria-live="polite" aria-relevant="additions" aria-label="부서 AI 대화" className="mx-auto flex max-w-3xl flex-col gap-4">
                {items.map((it) => <Item key={it.id} it={it} busyHold={busyHold} orchOn={orchOn} orchMock={orchMock} onDecide={decide} />)}
              </ol>
              <p className="mx-auto mt-6 max-w-3xl text-center text-[11px] text-ink-500">{PROD_NOTE}</p>
              </>
            )}
          </div>
          {fresh > 0 && (
            <button onClick={toBottom}
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
  if (conn === "unready") return <p className="mx-auto mb-4 max-w-3xl rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">{COMM_503}</p>;
  if (conn === "off") return <p className="mx-auto mb-4 max-w-3xl rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-500">{COMM_OFF}</p>;
  if (truncated) return <p className="mx-auto mb-4 max-w-3xl rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-500">공통 로그가 많아 앞부분만 받았습니다 (최대 1000줄).</p>;
  return null;
}
const COMM_503 = "공통 로그: 준비 안 됨(503) — dashboard-api에 공통 로그 읽기가 아직 연결되지 않았습니다 (ax-backend #249). 부서 간 직통 대화는 연결 뒤에 보입니다. 목 모드(DASHBOARD_MOCK=1)에서는 예시 대화가 나옵니다.";
const COMM_OFF = "공통 로그: dashboard-api(VITE_DASHBOARD_API_URL)에 연결할 수 없어 부서 간 직통 대화를 불러오지 못했습니다.";

function EmptyState({ orchConn, safetyConn, commConn, room }: { orchConn: Conn; safetyConn: Conn; commConn: Conn; room: string }) {
  if (room === DIRECT_ROOM) {
    return (
      <div className="mx-auto max-w-md py-16 text-center text-sm text-ink-500">
        {commConn === "unready" ? COMM_503
          : commConn === "off" ? COMM_OFF
          : commConn === "unknown" ? "불러오는 중…"
          : "아직 사건에 묶이지 않은 부서 간 대화가 없습니다. 공통 로그에서 부서끼리 직접 주고받은 A2A 요청·응답이 여기에 쌓입니다."}
        <p className="mt-4 text-[11px]">{PROD_NOTE}</p>
      </div>
    );
  }
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
        <p className="font-semibold text-ink-900">중앙 Agent(총괄)가 꺼져 있습니다.</p>
        <p className="mt-1 text-ink-500">ax-backend에서 목 모드로 켜면 가짜 부서 7곳과 함께 장애 시나리오를 돌려 볼 수 있습니다.</p>
        <pre className="mt-3 overflow-x-auto rounded-xl bg-ink-50 p-3 font-mono text-xs text-ink-700 ring-1 ring-ink-100"><code>{ORCH_CMD}</code></pre>
        <p className="mt-2 text-xs text-ink-500">다른 포트에서 켰다면 루트 .env의 ORCHESTRATOR_PROXY_TARGET(개발 서버 프록시 대상)을 바꾸고 개발 서버를 다시 시작하세요.</p>
      </div>
    );
  }
  if (orchConn === "unknown") return <p className="py-16 text-center text-sm text-ink-500">불러오는 중…</p>;
  return (
    <div className="mx-auto max-w-md py-16 text-center text-sm text-ink-500">
      아직 사건이 없습니다. 위의 <b className="text-ink-900">장애 시나리오 시작</b>을 눌러 보세요.
      {commConn === "unready" && <p className="mt-4 text-xs">{COMM_503}</p>}
      <p className="mt-4 text-[11px]">{PROD_NOTE}</p>
    </div>
  );
}
