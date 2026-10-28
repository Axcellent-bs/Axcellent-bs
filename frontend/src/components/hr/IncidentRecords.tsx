import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/apiError";
import { adminRelay, relayEnabled, relayError, INCIDENT_STATUS, type Incident } from "@/lib/adminRelay";
import { ago, shortDateTime } from "@/lib/format";
import { hr, type AuditEntry, type HrCard } from "@/lib/hr";
import { auditKindKo, recordState } from "@/lib/incidentRecord";
import { Link } from "@/lib/nav";

// 결정 기록 화면: 최근 사건마다 인사 AI가 무엇을 기록했는지 번호 입력 없이 보여 준다.
// 사건 목록과 인사 기록 단계 상태는 총괄이 준 값이고, 시간순 기록과 카드는 hr이 저장한 값을 그대로 읽는다.

const POLL_MS = 10_000;
const CONF_KO = { low: "낮음", mid: "보통", high: "높음" } as const;
const errText = (e: unknown) => (e instanceof Error ? e.message : "요청에 실패했습니다");
const Chip = ({ label, tone }: { label: string; tone: string }) => <span className={`chip ${tone}`}>{label}</span>;

/** 확정된 결정 카드 한 장 */
export function DecisionCard({ card }: { card: HrCard }) {
  return (
    <article className="space-y-2 rounded-2xl border border-ink-100 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <b className="text-[15px]">{card.title}</b>
        <Chip label="확정" tone="bg-emerald-50 text-emerald-700" />
        {card.confidence && <Chip label={`확신 ${CONF_KO[card.confidence]}`} tone="bg-ink-100 text-ink-500" />}
      </div>
      <pre className="whitespace-pre-wrap rounded-lg bg-ink-50 px-3 py-2 text-xs leading-relaxed text-ink-700">{card.body}</pre>
      <p className="text-xs text-ink-500">
        {card.author}가 {ago(card.created_at)} 남김 · <span className="tabular-nums">{card.card_id}</span> · 사건 {card.context_id}
      </p>
    </article>
  );
}

export function IncidentRecords({ initialCtx }: { initialCtx: string | null }) {
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(initialCtx);

  const load = useCallback(async () => {
    if (!relayEnabled) return;
    try {
      const { data } = await adminRelay.incidents();
      setIncidents(data); setError(null);
      setSelected((cur) => cur ?? data.at(-1)?.context_id ?? null);
    } catch (e) { setError(relayError(e as { status?: number; message: string })); }
  }, []);
  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  if (!relayEnabled) {
    return <p className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">총괄 읽기 연결이 꺼져 있어 사건 목록을 보여 줄 수 없습니다. 아래 "사건 번호로 직접 찾기"를 쓰세요.</p>;
  }
  if (!incidents) return <p className="py-6 text-center text-sm text-ink-500">{error ?? "최근 사건을 불러오는 중…"}</p>;
  if (incidents.length === 0) {
    return <p className="rounded-2xl border border-dashed border-ink-300 px-5 py-8 text-center text-sm text-ink-500">아직 사건이 없습니다. 장애가 감지되어 총괄이 사건을 열면 여기에 나타납니다.</p>;
  }

  const list = [...incidents].reverse().slice(0, 12);
  const current = incidents.find((i) => i.context_id === selected) ?? null;
  return (
    <div className="space-y-3">
      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{error}</p>}
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <ul className="space-y-2" aria-label="최근 사건">
          {list.map((i) => {
            const rec = recordState(i);
            return (
              <li key={i.context_id}>
                <button onClick={() => setSelected(i.context_id)} aria-pressed={i.context_id === selected}
                  className={`w-full rounded-2xl border p-3 text-left text-sm transition ${i.context_id === selected ? "border-brand-500 bg-brand-50" : "border-ink-100 bg-surface hover:border-brand-500"}`}>
                  <span className="flex items-center justify-between gap-2">
                    <span className={`chip ${INCIDENT_STATUS[i.status].tone}`}>{INCIDENT_STATUS[i.status].label}</span>
                    <span className="text-xs text-ink-500">{ago(i.opened_at)}</span>
                  </span>
                  <span className="mt-1.5 block truncate font-semibold">{i.title}</span>
                  <span className="mt-1 flex items-center gap-2">
                    <span className={`chip ${rec.tone}`}>{rec.label}</span>
                  </span>
                  <span className="mt-1 block truncate text-xs text-ink-500">{i.context_id}</span>
                </button>
              </li>
            );
          })}
        </ul>
        {current ? <RecordDetail incident={current} /> : <p className="text-sm text-ink-500">왼쪽에서 사건을 고르세요.</p>}
      </div>
    </div>
  );
}

/** 사건 하나: 인사 기록 단계 상태, hr이 한 일(시간순), 그 사건에서 만든 카드 */
function RecordDetail({ incident }: { incident: Incident }) {
  const rec = recordState(incident);
  const ctx = incident.context_id;
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [cards, setCards] = useState<HrCard[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setEntries(null); setCards(null); setErr(null);
    const run = async () => {
      try {
        const [e, c] = await Promise.all([hr.activity(ctx), hr.decisionCards(ctx)]);
        if (alive) { setEntries(e); setCards(c); setErr(null); }
      } catch (x) {
        if (alive) setErr(x instanceof ApiError ? x.message : errText(x));
      }
    };
    run();
    const timer = setInterval(run, POLL_MS);
    return () => { alive = false; clearInterval(timer); };
  }, [ctx]);

  return (
    <div className="min-w-0 space-y-4">
      <div className="card space-y-2 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-[15px] font-semibold">{incident.title}</h3>
          <Chip label={rec.label} tone={rec.tone} />
          <Link href={`/orchestrator?ctx=${encodeURIComponent(ctx)}`} className="ml-auto text-xs font-semibold text-brand-600 underline underline-offset-2">총괄 사건에서 보기 →</Link>
        </div>
        <p className="text-xs text-ink-500">{ctx}</p>
        {rec.step
          ? <p className="text-xs text-ink-500">총괄 기록: {rec.step.note ? `${rec.step.note}` : "메모 없음"}{rec.step.finished_at ? ` · ${shortDateTime(rec.step.finished_at)}` : ""}</p>
          : <p className="text-xs text-ink-500">이 사건에는 인사 기록 단계가 없습니다.</p>}
        {!rec.done && incident.status !== "open" && rec.step && <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">사건은 끝났지만 인사 기록이 완료되지 않았습니다. 총괄 사건 화면의 단계와 메모를 확인하세요.</p>}
      </div>

      {err && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{err}</p>}

      <div className="card space-y-3 p-5">
        <h3 className="text-[15px] font-semibold">AI가 한 일</h3>
        {!entries ? <p className="text-xs text-ink-500">불러오는 중…</p>
          : entries.length === 0 ? <p className="text-xs text-ink-500">이 사건에서 인사 AI가 기록한 일이 없습니다.</p>
          : (
            <ol className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
              {entries.map((r) => (
                <li key={r.seq} className="flex flex-wrap items-baseline gap-x-3 px-4 py-2 text-xs">
                  <span className="tabular-nums text-ink-500">{shortDateTime(r.at)}</span>
                  <span className="font-medium">{auditKindKo(r.kind)}</span>
                  {auditKindKo(r.kind) !== r.kind && <span className="text-ink-500">{r.kind}</span>}
                  <span className="text-ink-500">{Object.entries(r.payload).map(([k, val]) => `${k}: ${String(val)}`).join(" · ")}</span>
                </li>
              ))}
            </ol>
          )}
      </div>

      <div className="card space-y-3 p-5">
        <h3 className="text-[15px] font-semibold">이 사건에서 만든 결정 카드</h3>
        {!cards ? <p className="text-xs text-ink-500">불러오는 중…</p>
          : cards.length === 0 ? <p className="text-xs text-ink-500">확정된 카드가 없습니다. 확신이 낮으면 후보로 남아 위 "지금 사람이 할 일"에 올라옵니다.</p>
          : <div className="space-y-3">{cards.map((c) => <DecisionCard key={c.card_id} card={c} />)}</div>}
      </div>
    </div>
  );
}
