import { useCallback, useEffect, useState } from "react";
import { Blockers, Loading, OFFLINE, StatusChip } from "@/components/admin/bits";
import { api } from "@/lib/api";
import { shortDateTime, won } from "@/lib/format";
import { CLAIM_REASON_KO, CLAIM_TYPE_KO, INSPECTION_KO } from "@/lib/labels";
import type { QueueRun, QueueView } from "@/lib/types";

export default function QueuePage() {
  const [view, setView] = useState<QueueView | null>(null);
  const [run, setRun] = useState<QueueRun | null>(null);
  const [limit, setLimit] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setView(await api.admin.queue()); setError(null); } catch { setError(OFFLINE); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function act(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try { await fn(); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  if (!view) return <Loading error={error} />;
  const ready = view.queue.filter((e) => e.ready).length;
  const resultOf = (id: number) => run?.results.find((r) => r.claim_id === id);

  return (
    <div className="space-y-6 px-8 pb-10 pt-2">
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <h1 className="text-xl font-semibold">보상 큐</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-500">
            반품 상품이 물류센터에 <b>입고된 순서</b>대로 줄을 섭니다. 검수가 정상이고 금액이 자동 처리 한도 이내이며(교환은 재고가 있을 때) 상담원 보류가 없으면 순서대로 환불·보상이 지급됩니다.
            조건이 안 맞는 건은 사유를 달고 줄에 남고, 뒤의 건을 막지 않습니다. 재고는 먼저 입고된 건에 먼저 배정됩니다.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-ink-500">최대
            <input className="field w-16 py-1.5 text-center" inputMode="numeric" placeholder="전체" value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ""))} aria-label="처리 개수 제한" />건
          </label>
          <button className="btn-primary" disabled={busy !== null || ready === 0}
            onClick={() => act("process", async () => setRun(await api.admin.processQueue(limit ? { limit: Number(limit) } : {})))}>
            {busy === "process" ? "처리 중…" : `준비된 ${ready}건 순서대로 처리`}
          </button>
        </div>
      </div>
      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-xs font-medium text-red-800">{error}</p>}
      {run && (
        <div role="status" className="rounded-xl border border-brand-100 bg-brand-50 px-4 py-3 text-sm text-brand-700">
          방금 처리: 지급 <b>{run.paid}</b>건 · 보류 <b>{run.held}</b>건
          <ul className="mt-1 space-y-0.5 text-xs">
            {run.results.filter((r) => r.result === "paid").map((r) => <li key={r.claim_id}>#{r.claim_id} 지급 — 환불 {won(r.refund ?? 0)}, 보상 적립금 {won(r.compensation ?? 0)}</li>)}
          </ul>
        </div>
      )}

      <section className="card overflow-hidden">
        <h2 className="border-b border-ink-100 px-5 py-3 text-sm font-semibold">처리 순서 <span className="font-normal text-ink-500">입고 {view.queue.length}건</span></h2>
        {!view.queue.length && <p className="px-5 py-10 text-center text-sm text-ink-500">입고된 반품이 없습니다. 아래 ‘입고 대기’에서 입고 처리를 해 보세요.</p>}
        <ol>
          {view.queue.map((e) => {
            const r = resultOf(e.claim_id);
            return (
              <li key={e.claim_id} className="grid gap-3 border-t border-ink-100 px-5 py-3 first:border-t-0 md:grid-cols-[40px_1.3fr_1fr_1.4fr] md:items-start">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-600 text-sm font-bold text-white" aria-label={`${e.position}번째`}>{e.position}</span>
                <div className="min-w-0 text-sm">
                  <p className="font-semibold">{e.customer} <span className="font-normal text-ink-500">· 주문 {e.order_no} · 신청 #{e.claim_id}</span></p>
                  <p className="truncate text-xs text-ink-500">{e.items.map((i) => `${i.title} × ${i.qty}`).join(", ")}</p>
                  <p className="mt-0.5 text-xs text-ink-500">{CLAIM_TYPE_KO[e.type]} · {CLAIM_REASON_KO[e.reason]}</p>
                </div>
                <div className="text-xs">
                  <p>입고 <b>{shortDateTime(e.received_at)}</b></p>
                  <p className="mt-0.5">검수 <span className={e.inspection === "ok" ? "text-emerald-700" : "text-amber-700"}>{INSPECTION_KO[e.inspection ?? "pending"]}</span></p>
                  <p className="mt-0.5 tabular-nums">환불 {won(e.amount)}{e.compensation > 0 && ` + 보상 ${won(e.compensation)}`}</p>
                </div>
                <div className="space-y-1.5">
                  <span className={`chip ${e.ready ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{e.ready ? "처리 가능" : "대기 — 조건 미충족"}</span>
                  {!e.ready && <Blockers items={e.blockers} />}
                  {!e.ready && e.inspection === "pending" && (
                    <div className="flex gap-1.5">
                      <button className="btn-ghost py-1 text-xs" disabled={busy !== null} onClick={() => act("ins", async () => { await api.admin.inspect(e.claim_id, "ok"); })}>검수 정상</button>
                      <button className="btn-ghost py-1 text-xs" disabled={busy !== null} onClick={() => act("ins", async () => { await api.admin.inspect(e.claim_id, "mismatch", "사유와 다른 상품 상태"); })}>사유 불일치</button>
                    </div>
                  )}
                  {r?.result === "paid" && <span className="chip bg-emerald-50 text-emerald-700">방금 지급됨</span>}
                </div>
              </li>
            );
          })}
        </ol>
      </section>

      <section className="card overflow-hidden">
        <h2 className="border-b border-ink-100 px-5 py-3 text-sm font-semibold">입고 대기 <span className="font-normal text-ink-500">고객이 반송 중 {view.awaiting_return.length}건 · 물류센터 입고 처리(데모)</span></h2>
        {!view.awaiting_return.length && <p className="px-5 py-8 text-center text-sm text-ink-500">반송 중인 상품이 없습니다.</p>}
        <ul>
          {view.awaiting_return.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-3 border-t border-ink-100 px-5 py-3 text-sm first:border-t-0">
              <StatusChip status={c.status} />
              <span className="min-w-0 flex-1"><b>{c.customer}</b> <span className="text-xs text-ink-500">· 신청 #{c.id} · {CLAIM_TYPE_KO[c.type]} · {CLAIM_REASON_KO[c.reason]} · 송장 <span className="font-mono">{c.tracking}</span></span>
                <span className="block truncate text-xs text-ink-500">{c.lines.map((l) => `${l.title} × ${l.qty}`).join(", ")}</span></span>
              <div className="flex gap-1.5">
                <button className="btn-primary py-1 text-xs" disabled={busy !== null} onClick={() => act("recv", async () => { await api.admin.receive(c.id, "ok"); })}>입고 · 검수 정상</button>
                <button className="btn-ghost py-1 text-xs" disabled={busy !== null} onClick={() => act("recv", async () => { await api.admin.receive(c.id, "pending"); })}>입고 · 검수 대기</button>
                <button className="btn-ghost py-1 text-xs" disabled={busy !== null} onClick={() => act("recv", async () => { await api.admin.receive(c.id, "mismatch", "사유와 다른 상품 상태"); })}>입고 · 불일치</button>
              </div>
            </li>
          ))}
        </ul>
        <p className="border-t border-ink-100 px-5 py-2 text-[11px] text-ink-500">입고 버튼을 누른 시각이 줄서기 순서가 됩니다. 먼저 누른 건이 앞에 섭니다.</p>
      </section>
    </div>
  );
}
