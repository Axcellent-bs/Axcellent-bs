import { useCallback, useEffect, useMemo, useState } from "react";
import { Loading } from "@/components/admin/bits";
import {
  MIN_EVENTS, TONE_TEXT, VERDICT, agentName, readout, secondsKo,
  type AgentEvaluation, type AgentSummary, type DailyTrend, type EvaluatedEvent, type EvaluatedStep, type Verdict,
} from "@/lib/agentEval";
import { shortDate, shortDateTime } from "@/lib/format";
import { hr } from "@/lib/hr";
import { Link, useSearchParams } from "@/lib/nav";

// 에이전트 평가 화면: 장애 대응에서 각 에이전트(부서 AI)가 일한 시간을 기준과 견준 결과.
// 점수·등급·순위는 없다. 사람이 "제때 끝냈구나 / 늦었구나"를 한눈에 읽도록 문장, 점, 이유로만 보여 준다.
// 보여 주는 값은 인사 AI가 저장한 판정 그대로이고, 화면에서 다시 계산하지 않는다.

const errText = (e: unknown) => (e instanceof Error ? e.message : "불러오지 못했습니다");
const RECENT_DOTS = 12;
type Counted = "normal" | "check" | "blocked" | "handed_off" | "approval_expired" | "no_record" | "in_progress";
const ORDER: Counted[] = ["normal", "check", "handed_off", "blocked", "approval_expired", "no_record", "in_progress"];

function Section({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
  return (
    <section className="card space-y-4 p-6">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        {desc && <p className="mt-1 text-xs text-ink-500">{desc}</p>}
      </div>
      {children}
    </section>
  );
}

/** 이벤트 하나에서 에이전트가 한 일을 점 하나로. 모양·글자·색을 같이 쓰고, 누르면 아래 단계 시간으로 이동한다 */
function Dot({ d, onPick }: { d: AgentSummary["recent"][number]; onPick: () => void }) {
  const v = VERDICT[d.verdict];
  const hint = `${d.title} · ${v.label}${d.reason ? ` · ${d.reason}` : ""}`;
  return (
    <button type="button" title={hint} aria-label={hint} onClick={onPick} disabled={d.verdict === "absent"}
      className={`h-7 w-7 rounded-lg border border-ink-100 bg-surface text-sm font-bold leading-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${v.dot} ${d.verdict === "absent" ? "cursor-default opacity-60" : "hover:bg-ink-50"}`}>
      {v.mark}
    </button>
  );
}

function AgentCard({ a, onPick }: { a: AgentSummary; onPick: (ctx: string) => void }) {
  const r = readout(a);
  const counts = ORDER.map((v) => [v, a[v]] as const).filter(([, n]) => n > 0);
  const lastLate = a.recent.find((d) => d.verdict === "check" || d.verdict === "blocked" || d.verdict === "approval_expired");
  return (
    <article className="rounded-2xl border border-ink-100 bg-surface p-5">
      <header className="flex flex-wrap items-baseline gap-2">
        <h3 className="text-[15px] font-semibold">{agentName(a.dept)}</h3>
        <span className="text-xs text-ink-500">참여한 이벤트 {a.events}건</span>
      </header>
      <p className={`mt-2 text-sm font-medium ${TONE_TEXT[r.tone]}`}>{r.text}</p>
      {a.recent.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5" role="list" aria-label={`${agentName(a.dept)} 최근 이벤트`}>
          {a.recent.slice(0, RECENT_DOTS).map((d) => <span role="listitem" key={d.context_id}><Dot d={d} onPick={() => onPick(d.context_id)} /></span>)}
        </div>
      )}
      {counts.length > 0 && (
        <p className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {counts.map(([v, n]) => <span key={v} className={`chip ${VERDICT[v].chip}`}>{VERDICT[v].label} {n}</span>)}
        </p>
      )}
      {a.handed_off > 0 && <p className="mt-3 text-xs text-ink-500">사람에게 넘김은 실패나 지연이 아니에요.</p>}
      {lastLate?.reason && <p className="mt-3 text-xs text-ink-600">가장 최근 확인이 필요했던 이유: {lastLate.reason}</p>}
      {a.repeats.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-ink-500">
          {a.repeats.map((x) => <li key={x.event_type}>같은 유형의 이벤트에서 {x.count}번 확인이 필요했어요: {x.title}</li>)}
        </ul>
      )}
    </article>
  );
}

/** 날짜별 이벤트 수와, 그중 확인이 필요했던 이벤트 수 */
function Trend({ data }: { data: DailyTrend[] }) {
  if (data.length === 0) return <p className="text-sm text-ink-500">날짜별로 볼 이벤트가 아직 없어요.</p>;
  const top = Math.max(1, ...data.map((d) => d.events));
  const W = 56;
  const H = 120;
  return (
    <div className="overflow-x-auto">
      <svg role="img" aria-label="날짜별 이벤트 수와 확인이 필요했던 이벤트 수" width={Math.max(260, data.length * W + 16)} height={H + 44}>
        {data.map((d, i) => {
          const x = 8 + i * W;
          const total = (d.events / top) * H;
          const look = (d.with_check / top) * H;
          return (
            <g key={d.day}>
              <title>{`${shortDate(d.day)}: 이벤트 ${d.events}건 중 확인이 필요했던 이벤트 ${d.with_check}건`}</title>
              <rect x={x} y={H - total} width={W - 18} height={total} rx={4} className="fill-emerald-200" />
              <rect x={x} y={H - look} width={W - 18} height={look} rx={4} className="fill-amber-400" />
              <text x={x + (W - 18) / 2} y={H + 16} textAnchor="middle" className="fill-ink-600 text-[11px]">{shortDate(d.day)}</text>
              <text x={x + (W - 18) / 2} y={H + 32} textAnchor="middle" className="fill-ink-500 text-[10px]">{d.with_check}/{d.events}건</text>
            </g>
          );
        })}
      </svg>
      <p className="mt-1 flex flex-wrap gap-x-4 text-xs text-ink-500">
        <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-emerald-200" />이벤트 수</span>
        <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-amber-400" />확인이 필요했던 이벤트</span>
      </p>
    </div>
  );
}

/** 단계 하나의 시간 막대: 일한 시간 · 승인 대기 · 사람 할 일함 대기를 색으로 나눈다 */
function StepRow({ s, scale }: { s: EvaluatedStep; scale: number }) {
  const v = VERDICT[s.verdict];
  const parts = [
    { key: "work", label: "일한 시간", sec: s.work_seconds, cls: "bg-brand-500" },
    { key: "appr", label: "승인 대기", sec: s.approval_wait_seconds, cls: "bg-amber-400" },
    { key: "human", label: "사람 할 일함 대기", sec: s.human_wait_seconds, cls: "bg-ink-300" },
  ];
  const timed = parts.filter((p) => p.sec != null && p.sec > 0).map((p) => `${p.label} ${secondsKo(p.sec)}`).join(" · ");
  const text = timed || s.reason || "시간 기록 없음";
  return (
    <li className="grid gap-1 py-3 sm:grid-cols-[13rem_1fr] sm:gap-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <b className="text-sm">{s.label}</b>
          <span className={`chip ${v.chip}`}><span aria-hidden>{v.mark}</span> {v.label}</span>
        </div>
        <p className="text-xs text-ink-500">{agentName(s.dept)}{s.attempts > 1 ? ` · 시도 ${s.attempts}회` : ""}</p>
      </div>
      <div className="min-w-0">
        <div className="flex h-3.5 overflow-hidden rounded-full bg-ink-100" role="img" aria-label={text}>
          {parts.map((p) => (p.sec && p.sec > 0 ? <div key={p.key} className={p.cls} style={{ width: `${Math.max(1.5, (p.sec / scale) * 100)}%` }} /> : null))}
        </div>
        <p className="mt-1 text-xs text-ink-600">{timed ? `${timed}${s.reason ? ` — ${s.reason}` : ""}` : text}</p>
      </div>
    </li>
  );
}

function EventTimeline({ ev }: { ev: EvaluatedEvent }) {
  const scale = Math.max(1, ...ev.steps.map((s) => (s.work_seconds ?? 0) + (s.approval_wait_seconds ?? 0) + (s.human_wait_seconds ?? 0)));
  const look = ev.steps.filter((s) => s.verdict === "check" || s.verdict === "blocked" || s.verdict === "approval_expired");
  const skipped = ev.steps.filter((s) => s.verdict === "no_record");
  const handed = ev.steps.filter((s) => s.verdict === "handed_off");
  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-700">
        {look.length === 0 ? "확인이 필요한 단계는 없어요." : `확인이 필요한 단계: ${look.map((s) => s.label).join(", ")}.`}
        {handed.length > 0 && ` 사람에게 넘긴 단계(실패나 지연이 아니에요): ${handed.map((s) => s.label).join(", ")}.`}
        {skipped.length > 0 && ` 시간 기록이 없는 단계: ${skipped.map((s) => s.label).join(", ")}.`}
      </p>
      <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100 px-4">
        {ev.steps.map((s) => <StepRow key={s.step_id} s={s} scale={scale} />)}
      </ul>
      <p className="flex flex-wrap gap-x-4 text-xs text-ink-500">
        <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-brand-500" />일한 시간 (평가에 쓰는 시간)</span>
        <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-amber-400" />승인 대기 (사람을 기다린 시간, 평가 제외)</span>
        <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-ink-300" />사람 할 일함 대기 (평가 제외)</span>
      </p>
    </div>
  );
}

export default function AgentEvaluationPage() {
  const ctxParam = useSearchParams().get("ctx"); // 결정 기록 화면의 "단계별 시간 보기"로 들어올 때
  const [data, setData] = useState<AgentEvaluation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(ctxParam);

  const load = useCallback(async () => {
    try { setData(await hr.agentEvaluation()); setError(null); }
    catch (e) { setError(errText(e)); }
  }, []);
  useEffect(() => {
    load();
    const poll = setInterval(load, 20000);
    return () => clearInterval(poll);
  }, [load]);
  useEffect(() => { if (ctxParam) setPicked(ctxParam); }, [ctxParam]);

  const current = useMemo(
    () => data?.events.find((e) => e.context_id === picked) ?? data?.events[0] ?? null,
    [data, picked],
  );

  if (!data) return <Loading error={error} />;
  const few = data.events.length < MIN_EVENTS;

  return (
    <div className="space-y-8 px-8 pb-12 pt-2">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">에이전트 평가</h1>
          <p className="mt-1 text-sm text-ink-500">장애가 났을 때 각 에이전트가 맡은 일을 제때 끝냈는지 봅니다. 점수나 순위는 없고, 늦었다면 이유를 함께 보여 줘요.</p>
        </div>
        <div className="ml-auto text-xs text-ink-500">{shortDateTime(data.collected_at)} 기준</div>
      </header>

      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">{error}</p>}

      <section aria-label="평가 기준" className="rounded-2xl bg-ink-50 px-5 py-4 text-sm text-ink-700">
        <p>
          <b>기준: 임시 기준 {data.criteria_version}판{data.criteria_updated ? ` (${data.criteria_updated})` : ""}</b> — 일한 시간이 <b>{data.normal_max_seconds}초 이하</b>이고
          {" "}다시 시도하지 않았으면 제때 끝낸 것, 그 밖에는 확인이 필요한 것으로 봅니다.
        </p>
        <p className="mt-1 text-xs text-ink-500">시간만 본 결과예요. 일의 품질을 평가한 것이 아니고, 확인이 필요하다는 건 잘못했다는 뜻이 아니라 한 번 살펴보라는 뜻이에요.</p>
      </section>

      {data.events.length === 0 ? (
        <Section title="아직 평가할 이벤트가 없어요">
          <p className="text-sm text-ink-600">장애 이벤트가 끝나면 여기에 나타나요.</p>
        </Section>
      ) : (
        <>
          <Section title="에이전트별 현황" desc={few ? `이벤트가 ${MIN_EVENTS}건 이상 쌓이면 문장으로 요약해 드려요.` : "점을 누르면 그 이벤트의 단계별 시간으로 이동해요. 왼쪽이 가장 최근이에요."}>
            <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
              {data.agents.map((a) => <AgentCard key={a.dept} a={a} onPick={(ctx) => { setPicked(ctx); document.getElementById("event-steps")?.scrollIntoView({ behavior: "smooth", block: "start" }); }} />)}
            </div>
            <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-500">
              {(["normal", "check", "handed_off", "blocked", "no_record", "absent"] as Verdict[]).map((v) => (
                <span key={v}><b className={VERDICT[v].dot}>{VERDICT[v].mark}</b> {VERDICT[v].label}</span>
              ))}
            </p>
          </Section>

          <Section title="날짜별 흐름" desc="하루 이벤트 수와, 그중 확인이 필요했던 이벤트 수예요.">
            <Trend data={data.trend} />
          </Section>

          <div id="event-steps" className="scroll-mt-4">
            <Section title="이벤트별 단계 시간" desc="이벤트를 고르면 단계마다 시간을 나눠서 볼 수 있어요.">
              <select className="field" aria-label="이벤트 고르기" value={current?.context_id ?? ""} onChange={(e) => setPicked(e.target.value)}>
                {data.events.map((e) => <option key={e.context_id} value={e.context_id}>{e.title}{e.at ? ` (${shortDateTime(e.at)})` : ""}</option>)}
              </select>
              {current && (
                <>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-500">
                    {current.criteria_version !== data.criteria_version && <span>그때 기준: 임시 기준 {current.criteria_version}판</span>}
                    <Link href={`/ops?ctx=${encodeURIComponent(current.context_id)}`} className="font-semibold text-brand-600 underline underline-offset-2">결정 기록에서 보기 →</Link>
                    <Link href={`/orchestrator?ctx=${encodeURIComponent(current.context_id)}`} className="font-semibold text-brand-600 underline underline-offset-2">총괄 이벤트에서 보기 →</Link>
                  </div>
                  <EventTimeline ev={current} />
                </>
              )}
            </Section>
          </div>
        </>
      )}
    </div>
  );
}
