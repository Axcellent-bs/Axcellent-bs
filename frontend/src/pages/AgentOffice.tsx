import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { OfficeChat, type Typing } from "@/components/office/OfficeChat";
import { PixelOffice } from "@/components/office/PixelOffice";
import { ALL_ROOM, buildTimeline, incidentTitle, skillKo, type OrchIncident } from "@/lib/agentChat";
import { api } from "@/lib/api";
import { finance, type ExecutionRecord } from "@/lib/finance";
import { Link, useSearchParams } from "@/lib/nav";
import { DESKS, REPLAY_END, deriveOffice, replayIncident, type DeskState, type OfficeStatus } from "@/lib/office";
import { orchestrator } from "@/lib/orchestrator";
import type { CommLine } from "@/lib/types";

// AI 오피스. 부서 AI가 지금 무엇을 하는지 픽셀 사무실로 보여 주고, 옆에 단톡방을 둔다.
// 데이터는 AI 대화방(/agents/chat)과 같은 세 곳(총괄·안전 관문·공통 로그)과 같은 buildTimeline을 쓴다.
// 총괄이 꺼져 있으면 SCN-A 리플레이(총괄 응답 모양으로 만든 예시 사건)를 보여 준다.

const POLL_MS = 3000;
type Mode = "live" | "replay";
type Conn = "unknown" | "on" | "off";

const STATE_KO: Record<DeskState, string> = { idle: "대기", working: "작업 중", waiting: "승인 대기" };
const STATE_DOT: Record<DeskState, string> = { idle: "bg-ink-300", working: "bg-brand-500", waiting: "bg-amber-700" };
const STATUS_LOOK: Record<OfficeStatus, string> = {
  ok: "bg-ink-100 text-ink-500", alert: "bg-red-50 text-red-700", resolved: "bg-emerald-50 text-emerald-700", degraded: "bg-amber-50 text-amber-700",
};
const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export default function AgentOffice() {
  const params = useSearchParams();
  const [mode, setMode] = useState<Mode>(params.has("replay") ? "replay" : "live");
  const [autoNote, setAutoNote] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  // ------------------------------------------------ 실시간: AI 대화방과 같은 세 출처
  const [incidents, setIncidents] = useState<OrchIncident[]>([]);
  const [executions, setExecutions] = useState<ExecutionRecord[]>([]);
  const [comms, setComms] = useState<CommLine[]>([]);
  const [orchConn, setOrchConn] = useState<Conn>("unknown");
  const [safetyConn, setSafetyConn] = useState<Conn>("unknown");
  const [commConn, setCommConn] = useState<Conn>("unknown");

  const refresh = useCallback(async () => {
    const [inc, exe, com] = await Promise.allSettled([orchestrator.incidents(), finance.executions(), api.communications()]);
    if (inc.status === "fulfilled") { setIncidents(inc.value.data); setOrchConn("on"); } else setOrchConn("off");
    if (exe.status === "fulfilled") { setExecutions(exe.value); setSafetyConn("on"); } else setSafetyConn("off");
    if (com.status === "fulfilled") { setComms(com.value.data); setCommConn("on"); } else { setComms([]); setCommConn("off"); }
  }, []);

  useEffect(() => {
    if (mode !== "live") return;
    refresh();
    const t = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
    return () => clearInterval(t);
  }, [mode, refresh]);

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
  const replayT = Math.max(0, Math.min(REPLAY_END, ((frozenAt ?? now) - replayBase) / 1000));
  const replayDone = replayT >= REPLAY_END;
  const restart = () => { setFrozenAt(null); setReplayBase(Date.now() + 400); seenRef.current = new Map(); };
  const togglePlay = () => {
    if (replayDone) restart();
    else if (frozenAt === null) setFrozenAt(Date.now());
    else { setReplayBase((b) => b + Date.now() - frozenAt); setFrozenAt(null); }
  };
  useEffect(() => { if (mode === "replay") restart(); }, [mode]);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 150);
    return () => clearInterval(t);
  }, []);

  // ------------------------------------------------ 타임라인 → 사무실
  const shownIncidents = useMemo(
    () => (mode === "live" ? incidents : replayT >= 0.6 ? [replayIncident(replayT, replayBase)] : []),
    [mode, incidents, replayT, replayBase],
  );
  const items = useMemo(
    () => buildTimeline(shownIncidents, mode === "live" ? executions : [], mode === "live" ? comms : [], ALL_ROOM),
    [shownIncidents, executions, comms, mode],
  );

  // 말풍선 id → 처음 본 시각. 실시간은 연결 직후 이미 있던 기록을 0(말풍선 없음)으로 둔다
  const seenRef = useRef(new Map<string, number>());
  const liveReady = mode === "replay" || orchConn !== "unknown";
  const seen = useMemo(() => {
    const m = seenRef.current;
    const first = m.size === 0 && mode === "live";
    for (const it of items) if (!m.has(it.id)) m.set(it.id, first ? 0 : Date.now());
    return m;
  }, [items, mode]);
  useEffect(() => { seenRef.current = new Map(); }, [mode]);

  const view = useMemo(() => deriveOffice(shownIncidents, items, seen, now), [shownIncidents, items, seen, now]);
  const freshIds = useMemo(() => new Set(items.filter((it) => { const at = seen.get(it.id) ?? 0; return at > 0 && now - at < 1500; }).map((it) => it.id)), [items, seen, now]);
  const typing: Typing[] = DESKS.filter((d) => view.desks[d.key].state === "working").map((d) => ({
    key: d.key, name: d.key === "orchestrator" ? "총괄" : `${d.label} Agent`, what: skillKo(view.desks[d.key].skill) || "작업",
  }));

  const inc = view.incident;
  const statusText = {
    ok: "정상 운영", alert: inc ? `${incidentTitle(inc)} 대응 중` : "대응 중", resolved: "장애 해결", degraded: "일부 미완료로 종료",
  }[view.status];
  const officeLabel = `부서 AI 사무실. ${statusText}. ` + DESKS.map((d) => `${d.label} ${STATE_KO[view.desks[d.key].state]}`).join(", ");

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-4 lg:p-6">
      <style>{"@keyframes officePop{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}"}</style>
      <header className="flex flex-wrap items-start gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight">AI 오피스</h1>
          <p className="mt-1 text-sm text-ink-500">
            부서 AI가 지금 무슨 일을 하는지 사무실로 봅니다. 총괄이 일을 맡기면 그 부서로 걸어가고, 사람 승인이 필요하면 🚩 승인 대기로 깜빡입니다.
            기록과 문장은 <Link href="/agents/chat" className="font-medium text-brand-700 underline-offset-2 hover:underline">AI 대화방</Link>과 같습니다.
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
            {(["live", "replay"] as const).map((m) => (
              <button key={m} role="radio" aria-checked={mode === m} onClick={() => { setAutoNote(false); setMode(m); }}
                className={`rounded-full px-3.5 py-1 text-sm font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${mode === m ? "bg-surface text-ink-900 shadow-sm" : "text-ink-500 hover:text-ink-900"}`}>
                {m === "live" ? "실시간" : "SCN-A 리플레이"}
              </button>
            ))}
          </div>
          {mode === "replay" && (
            <div className="flex items-center gap-2 text-xs text-ink-500">
              <div className="h-1.5 w-36 overflow-hidden rounded-full bg-ink-100" aria-hidden="true">
                <div className="h-full bg-brand-500" style={{ width: `${(replayT / REPLAY_END) * 100}%` }} />
              </div>
              <span className="w-[84px] tabular-nums">{mmss(replayT)} / {mmss(REPLAY_END)}</span>
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

      <div className="flex min-h-0 flex-1 flex-col gap-4 xl:flex-row">
        <section aria-label="부서 AI 사무실" className="card flex min-w-0 shrink-0 flex-col gap-3 p-4 xl:flex-1">
          <div className="flex items-center gap-2">
            <h2 className="card-title">부서 AI 사무실</h2>
            <span className="text-xs text-ink-500">8자리 · 총괄 포함</span>
            <span className={`chip ml-auto ${STATUS_LOOK[view.status]}`}>
              <span className={`h-1.5 w-1.5 rounded-full bg-current ${view.status === "alert" ? "animate-pulse" : ""}`} aria-hidden="true" />
              {liveReady ? statusText : "불러오는 중…"}
            </span>
          </div>
          <PixelOffice view={view} label={officeLabel} />
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

        <OfficeChat
          items={items.slice(-80)}
          typing={typing}
          freshIds={freshIds}
          title="부서 단톡방"
          sub={mode === "replay" ? "SCN-A 리플레이" : inc ? `${incidentTitle(inc)} 외 전체 기록` : "전체 기록"}
        />
      </div>
    </div>
  );
}

function ConnChip({ label, conn }: { label: string; conn: Conn }) {
  const tone = conn === "on" ? "bg-emerald-50 text-emerald-700" : conn === "off" ? "bg-red-50 text-red-700" : "bg-ink-100 text-ink-500";
  return <span className={`chip ${tone}`}>{label} {{ on: "켜짐", off: "꺼짐", unknown: "확인 중" }[conn]}</span>;
}
