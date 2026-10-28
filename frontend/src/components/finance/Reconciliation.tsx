import { useState } from "react";
import { humanId, shortDateTime } from "@/lib/format";
import {
  DEFAULT_RECON_FILTER, RECON_ORDER, finance, humanTokenSet, krw, plainText,
  type ReconItem, type ReconStatus,
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
      {saved && <p className="mt-1 text-xs text-emerald-700" aria-live="polite">메모를 저장했습니다.</p>}
      {problem && <p role="alert" className="mt-1 text-xs text-red-700">{problem}</p>}
    </form>
  );
}

export function ReconciliationSection({ tick }: { tick: number }) {
  const [date, setDate] = useState("");
  const res = useResource(`recon:${date}`, () => finance.reconciliation(date || null), tick);
  const [shown, setShown] = useState<ReconStatus[]>(DEFAULT_RECON_FILTER);
  const toggle = (s: ReconStatus) => setShown((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  const saveItem = (next: ReconItem) =>
    res.set((cur) => ({ ...cur, data: { ...cur.data, items: cur.data.items.map((i) => (i.payment_id === next.payment_id ? next : i)) } }));

  const tools = (
    <>
      <label className="flex items-center gap-1 text-xs text-ink-500">
        <span className="sr-only">날짜</span>
        <input type="date" className="field w-auto py-1" value={date} onChange={(e) => setDate(e.target.value)} />
      </label>
      {date ? <button type="button" className="btn-quiet" onClick={() => setDate("")}>최근 7일</button> : <span className="text-xs text-ink-500">최근 7일</span>}
    </>
  );

  return (
    <Panel id="reconciliation" title="② 매출·PG 대사" titleHidden meta={res.env?.meta} tools={tools}
      hint="대사는 '우리 주문 기록'과 '결제대행사(PG)가 보낸 정산 자료'를 한 건씩 맞춰 보는 일입니다. 재무 AI가 자동으로 맞추고, 맞지 않는 건만 보여 줍니다. 처리한 내용은 메모로 남기세요.">
      <ResourceBody res={res}>
        {(r) => {
          const items = r.items
            .filter((i) => shown.includes(i.status))
            .sort((a, b) => RECON_ORDER.indexOf(a.status) - RECON_ORDER.indexOf(b.status));
          return (
            <div className="space-y-4">
              <div role="group" aria-label="상태로 거르기" className="flex flex-wrap gap-2">
                {RECON_ORDER.map((s) => {
                  const on = shown.includes(s);
                  return (
                    <button key={s} type="button" aria-pressed={on} onClick={() => toggle(s)} title={RECON_KO[s].hint}
                      className={`chip py-1 text-xs ring-1 transition ${on ? `${RECON_KO[s].tone} ring-current` : "bg-surface text-ink-500 ring-ink-100 hover:bg-ink-100"}`}>
                      {RECON_KO[s].label} <b className="tabular-nums">{r.counts[s] ?? 0}</b>
                    </button>
                  );
                })}
              </div>

              <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
                {([
                  ["주문 금액 합계", r.totals.order_amount, "우리 쇼핑몰 기록"],
                  ["PG 금액 합계", r.totals.pg_amount, "결제대행사가 알려 준 금액"],
                  ["PG 수수료", r.totals.pg_fee, "결제대행사가 떼는 돈"],
                  ["입금 예정액", r.totals.pay_out, "수수료를 빼고 받을 돈"],
                ] as const).map(([label, v, sub]) => (
                  <div key={label} className="rounded-2xl bg-surface-2 px-3 py-2">
                    <dt className="text-[11px] text-ink-500">{label}</dt>
                    <dd className={`text-base font-semibold tabular-nums ${v == null ? "text-ink-500" : ""}`}>{krw(v)}</dd>
                    <dd className="text-[11px] text-ink-500">{sub}</dd>
                  </div>
                ))}
              </dl>

              {items.length ? (
                <ul className="divide-y divide-ink-100">
                  {items.map((i) => (
                    <li key={i.payment_id} className="grid gap-2 py-3 md:grid-cols-[150px_1fr_minmax(240px,320px)] md:items-start md:gap-4">
                      <div>
                        <Chip label={RECON_KO[i.status]?.label ?? "확인 필요"} tone={RECON_KO[i.status]?.tone ?? TONE.muted} />
                        <p className="mt-1 break-all text-xs text-ink-500" title={i.order_id}>{humanId(i.order_id)}</p>
                        <p className="break-all text-xs text-ink-500" title={i.payment_id}>{humanId(i.payment_id)}</p>
                        {i.paid_at && <p className="text-xs text-ink-500">{shortDateTime(i.paid_at)} 결제</p>}
                      </div>
                      <div className="min-w-0 text-sm">
                        <p className="text-ink-900">{i.reason ? plainText(i.reason) : RECON_KO[i.status]?.hint}</p>
                        <p className="mt-1 text-xs tabular-nums text-ink-500">
                          주문 {krw(i.order_amount)} · PG {krw(i.pg_amount)} · 수수료 {krw(i.fee)}
                          {i.expected_fee != null && i.expected_fee !== i.fee && ` (원래 ${krw(i.expected_fee)})`}
                        </p>
                        <p className="text-xs tabular-nums text-ink-500">
                          입금 {krw(i.pay_out_amount)} · {i.paid_out_date ? `${i.paid_out_date} 입금` : "입금일 아직 없음"}
                        </p>
                      </div>
                      <div>
                        {i.status === "matched" ? (
                          i.note && <p className="text-xs text-ink-500">메모: {plainText(i.note)}</p>
                        ) : (
                          <NoteForm key={i.payment_id} item={i} onSaved={saveItem} />
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>
                  {r.items.length === 0 ? "이 기간에는 맞춰 볼 결제가 없습니다."
                    : shown.length === 0 ? "위에서 볼 상태를 하나 이상 고르세요."
                      : "고른 상태에 해당하는 건이 없습니다. 맞지 않는 건이 없다는 뜻입니다."}
                </Empty>
              )}
              {!humanTokenSet && <p className="text-xs text-amber-800">사람 권한(VITE_SAFETY_HUMAN_TOKEN)이 설정되지 않아 메모를 저장할 수 없습니다.</p>}
            </div>
          );
        }}
      </ResourceBody>
    </Panel>
  );
}
