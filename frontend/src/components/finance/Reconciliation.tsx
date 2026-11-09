import { useState } from "react";
import { humanId, shortDateTime } from "@/lib/format";
import {
  DEFAULT_RECON_FILTER, RECON_ORDER, finance, humanTokenSet, krw, plainText,
  type PgCoverage, type ReconItem, type ReconStatus,
} from "@/lib/finance";
import { Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// ② 매출·PG 대사: 주문 ↔ 결제 ↔ PG 정산을 건별로 맞춘 결과. 일치하지 않는 건만 사람이 메모한다.

export const RECON_KO: Record<ReconStatus, { label: string; hint: string; tone: string }> = {
  mismatch: { label: "금액 다름", hint: "주문 금액과 PG가 알려 준 금액이 다릅니다", tone: TONE.bad },
  missing_in_pg: { label: "PG에 기록 없음", hint: "결제는 됐는데 PG 정산 자료에 없습니다", tone: TONE.bad },
  fee_anomaly: { label: "수수료 이상", hint: "PG 수수료가 계약한 비율과 다릅니다", tone: TONE.warn },
  pending: { label: "정산 전", hint: "PG가 아직 돈을 보내기 전입니다. 보통 기다리면 됩니다", tone: TONE.info },
  matched: { label: "일치", hint: "주문·결제·PG 금액이 모두 맞습니다", tone: TONE.ok },
};

function NoteForm({ item, onSaved }: { item: ReconItem; onSaved: (next: ReconItem) => void }) {
  const [text, setText] = useState(item.note ?? "");
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const changed = text.trim() !== (item.note ?? "").trim();

  async function save() {
    setSaving(true);
    setProblem(null);
    setSaved(false);
    try {
      const res = await finance.reconNote(item.payment_id, text.trim());
      onSaved(res.data);
      setSaved(true);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="mt-2" onSubmit={(e) => { e.preventDefault(); if (changed && humanTokenSet && !saving) void save(); }}>
      <label className="sr-only" htmlFor={`note-${item.payment_id}`}>처리 메모</label>
      <div className="flex gap-2">
        <input id={`note-${item.payment_id}`} className="field py-1.5" maxLength={300} placeholder="처리 메모 (예: PG사에 문의함)" value={text}
          onChange={(e) => { setText(e.target.value); setSaved(false); }} disabled={!humanTokenSet} />
        <button type="submit" className="btn-ghost shrink-0" disabled={!humanTokenSet || !changed || saving}>{saving ? "저장 중…" : "메모 저장"}</button>
      </div>
      {saved && <p className="mt-1 text-sm text-emerald-700" aria-live="polite">메모를 저장했습니다.</p>}
      {problem && <p role="alert" className="mt-1 text-sm text-red-700">{problem}</p>}
    </form>
  );
}

/** 한 번에 그리는 줄 수. 정산 전까지 켜면 수천 줄이 될 수 있다 */
const PAGE = 100;

export function ReconciliationSection({ tick }: { tick: number }) {
  const [limit, setLimit] = useState(PAGE);
  const [date, setDate] = useState("");
  const [shown, setShown] = useState<ReconStatus[]>(DEFAULT_RECON_FILTER);
  const wanted = RECON_ORDER.filter((s) => shown.includes(s));
  const res = useResource(`recon:${date}:${wanted.join(",")}`, () => finance.reconciliation(date || null, wanted), tick);
  const toggle = (s: ReconStatus) => setShown((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  async function pgAction(run: () => Promise<string>) {
    setBusy(true);
    setMessage(null);
    try {
      setMessage(await run());
      await res.reload();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const uploadPg = (file: File) =>
    pgAction(async () => {
      const got = (await finance.uploadPgFile(file)).data;
      return `PG 정산 ${(got.rows ?? 0).toLocaleString("ko-KR")}줄을 읽었습니다. 판매일 ${got.period?.from} ~ ${got.period?.to}은 이 파일과 맞춥니다.`;
    });
  const clearPg = () =>
    pgAction(async () => {
      const got = (await finance.clearPg("화면에서 올린 PG 정산 자료 빼기")).data;
      return `올린 PG 정산 ${(got.rows ?? 0).toLocaleString("ko-KR")}줄을 뺐습니다. 다시 결제 기록으로 만든 모의 자료와 맞춥니다.`;
    });

  const saveItem = (next: ReconItem) =>
    res.set((cur) => ({ ...cur, data: { ...cur.data, items: cur.data.items.map((i) => (i.payment_id === next.payment_id ? next : i)) } }));

  const tools = (
    <>
      <label className={`btn-ghost py-1 text-sm ${!humanTokenSet || busy ? "pointer-events-none opacity-50" : "cursor-pointer"}`}
        title="PG 관리자 화면에서 받은 정산 내역 CSV. 그 판매일 기간은 이 파일과 맞춥니다">
        {busy ? "처리 중…" : "PG 정산 파일 올리기"}
        <input type="file" accept=".csv,.txt,text/csv" className="sr-only" disabled={!humanTokenSet || busy}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadPg(f); e.target.value = ""; }} />
      </label>
      <label className="flex items-center gap-1 text-sm text-ink-500">
        <span className="sr-only">날짜</span>
        <input type="date" className="field w-auto py-1" value={date} onChange={(e) => setDate(e.target.value)} />
      </label>
      {date ? <button type="button" className="btn-quiet" onClick={() => setDate("")}>최근 7일</button> : <span className="text-sm text-ink-500">최근 7일</span>}
    </>
  );

  return (
    <Panel id="reconciliation" title="② 매출·PG 대사" titleHidden meta={res.env?.meta} tools={tools}
      hint="대사는 '우리 주문 기록'과 '결제대행사(PG)가 보낸 정산 자료'를 한 건씩 맞춰 보는 일입니다. 재무 AI가 자동으로 맞추고, 맞지 않는 건만 보여 줍니다. 처리한 내용은 메모로 남기세요. 실제 PG와 연결 전이라, PG 정산 파일을 올린 기간은 그 파일과, 그 밖의 날은 결제 기록으로 만든 모의 자료와 맞춥니다.">
      <ResourceBody res={res}>
        {(r) => {
          const items = r.items
            .filter((i) => shown.includes(i.status))
            .sort((a, b) => RECON_ORDER.indexOf(a.status) - RECON_ORDER.indexOf(b.status));
          return (
            <div className="space-y-4">
              {message && <p role="status" className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-ink-700">{message}</p>}
              <PgSourceLine coverage={r.pg_coverage} onClear={humanTokenSet && !busy ? clearPg : undefined} />
              <div role="group" aria-label="상태로 거르기" className="flex flex-wrap gap-2">
                {RECON_ORDER.map((s) => {
                  const on = shown.includes(s);
                  return (
                    <button key={s} type="button" aria-pressed={on} onClick={() => toggle(s)} title={RECON_KO[s].hint}
                      className={`chip py-1 text-sm ring-1 transition ${on ? `${RECON_KO[s].tone} ring-current` : "bg-surface text-ink-500 ring-ink-100 hover:bg-ink-100"}`}>
                      {RECON_KO[s].label} <b className="tabular-nums">{r.counts[s] ?? 0}</b>
                    </button>
                  );
                })}
              </div>

              {!shown.includes("pending") && (r.counts.pending ?? 0) > 0 && (
                <p className="text-sm text-ink-500">
                  정산 전 {(r.counts.pending ?? 0).toLocaleString("ko-KR")}건은 PG가 아직 돈을 보내기 전이라 할 일이 아닙니다. 보려면 위 "정산 전"을 누르세요.
                </p>
              )}

              <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
                {([
                  ["주문 금액 합계", r.totals.order_amount, "우리 쇼핑몰 기록"],
                  ["PG 금액 합계", r.totals.pg_amount, "결제대행사가 알려 준 금액"],
                  ["PG 수수료", r.totals.pg_fee, "결제대행사가 떼는 돈"],
                  ["입금 예정액", r.totals.pay_out, "수수료를 빼고 받을 돈"],
                ] as const).map(([label, v, sub]) => (
                  <div key={label} title={sub} className="rounded-2xl border border-ink-100 px-4 py-3">
                    <dt className="text-sm text-ink-500">{label}</dt>
                    <dd className={`mt-1 text-xl font-semibold tabular-nums ${v == null ? "text-ink-500" : "text-ink-900"}`}>{krw(v)}</dd>
                    <dd className="mt-0.5 text-xs text-ink-500">{sub}</dd>
                  </div>
                ))}
              </dl>

              {items.length ? (
                <ul className="divide-y divide-ink-100">
                  {items.slice(0, limit).map((i) => (
                    <li key={i.payment_id} className="grid gap-2 py-3 md:grid-cols-[150px_1fr_minmax(240px,320px)] md:items-start md:gap-4">
                      <div>
                        <Chip label={RECON_KO[i.status]?.label ?? "확인 필요"} tone={RECON_KO[i.status]?.tone ?? TONE.muted} />
                        <p className="mt-1 break-all text-sm text-ink-500" title={i.order_id}>{humanId(i.order_id)}</p>
                        <p className="break-all text-sm text-ink-500" title={i.payment_id}>{humanId(i.payment_id)}</p>
                        {i.paid_at && <p className="text-sm text-ink-500">{shortDateTime(i.paid_at)} 결제</p>}
                      </div>
                      <div className="min-w-0 text-sm">
                        <p className="text-ink-900">{i.reason ? plainText(i.reason) : RECON_KO[i.status]?.hint}</p>
                        <p className="mt-1 text-sm tabular-nums text-ink-500">
                          주문 {krw(i.order_amount)} · PG {krw(i.pg_amount)} · 수수료 {krw(i.fee)}
                          {i.expected_fee != null && i.expected_fee !== i.fee && ` (원래 ${krw(i.expected_fee)})`}
                        </p>
                        <p className="text-sm tabular-nums text-ink-500">
                          입금 {krw(i.pay_out_amount)} · {i.paid_out_date ? `${i.paid_out_date} 입금` : "입금일 아직 없음"}
                        </p>
                      </div>
                      <div>
                        {i.status === "matched" || i.status === "pending" ? (
                          i.note && <p className="text-sm text-ink-500">메모: {plainText(i.note)}</p>
                        ) : (
                          <NoteForm key={i.payment_id} item={i} onSaved={saveItem} />
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>
                  {Object.values(r.counts).every((n) => !n) ? "이 기간에는 맞춰 볼 결제가 없습니다."
                    : shown.length === 0 ? "위에서 볼 상태를 하나 이상 고르세요."
                      : "고른 상태에 해당하는 건이 없습니다. 맞지 않는 건이 없다는 뜻입니다."}
                </Empty>
              )}
              {r.shown && r.shown.total > r.items.length && (
                <p className="text-sm text-ink-500">
                  건수가 많아 최근 {r.items.length.toLocaleString("ko-KR")}건만 받았습니다 (전체 {r.shown.total.toLocaleString("ko-KR")}건). 위에서 날짜를 골라 좁혀 보세요.
                </p>
              )}
              {items.length > limit && (
                <button type="button" className="btn-ghost" onClick={() => setLimit((n) => n + PAGE)}>
                  {Math.min(PAGE, items.length - limit)}건 더 보기 (남은 {(items.length - limit).toLocaleString("ko-KR")}건)
                </button>
              )}
              {!humanTokenSet && <p className="text-sm text-amber-800">사람 권한(VITE_SAFETY_HUMAN_TOKEN)이 설정되지 않아 메모를 저장할 수 없습니다.</p>}
            </div>
          );
        }}
      </ResourceBody>
    </Panel>
  );
}

/** 이번 대사가 어느 PG 자료와 맞춘 것인지. 모의 자료와 맞춘 건은 실제 PG 확인이 아니다 */
function PgSourceLine({ coverage, onClear }: { coverage?: PgCoverage; onClear?: () => void }) {
  if (!coverage) return null;
  const n = (v: number) => v.toLocaleString("ko-KR");
  if (!coverage.uploaded) {
    return <p className="text-sm text-ink-500">PG 자료: 결제 기록으로 만든 모의 자료 {n(coverage.generated)}건 (올린 PG 정산 파일 없음)</p>;
  }
  const days = coverage.periods.map((p) => (p.from === p.to ? p.from : `${p.from} ~ ${p.to}`)).join(", ");
  return (
    <p className="flex flex-wrap items-center gap-2 text-sm text-ink-700">
      <span>PG 자료: 올린 PG 정산 파일 {n(coverage.uploaded)}건 (판매일 {days}){coverage.generated ? ` · 결제 기록으로 만든 모의 자료 ${n(coverage.generated)}건` : ""}</span>
      {onClear && <button type="button" className="btn-quiet py-0.5 text-sm" onClick={onClear}>올린 PG 자료 빼기</button>}
    </p>
  );
}
