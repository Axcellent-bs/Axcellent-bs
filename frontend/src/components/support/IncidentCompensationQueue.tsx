import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { shortDateTime, won } from "@/lib/format";
import { humanTokenSet } from "@/lib/finance";
import { support, type IncidentQueue, type IncidentQueueList, type QueueRow, type QueueStatus } from "@/lib/support";

// 장애 보상 대기: 총괄이 장애 복구 뒤 영향 고객 목록을 넘기면 쿠폰을 바로 요청하지 않고 여기에 줄을 세운다.
// 사람이 고객별로 보고 승인한 고객만 마케팅 쿠폰 요청으로 넘어가고(지급·한도 검사는 마케팅·안전 장치가 한다),
// 거절한 고객은 쿠폰을 받지 않는다. 승인·거절은 사람 토큰으로 보낸다 (결정한 사람이 기록에 남는다).

const STATUS_KO: Record<QueueStatus, { label: string; tone: string }> = {
  pending: { label: "승인 대기", tone: "bg-amber-100 text-amber-800" },
  approved: { label: "승인됨 · 아직 못 보냄", tone: "bg-sky-100 text-sky-800" },
  held: { label: "안전 장치 보류", tone: "bg-amber-100 text-amber-800" },
  sent: { label: "쿠폰 요청함", tone: "bg-emerald-50 text-emerald-700" },
  rejected: { label: "거절", tone: "bg-ink-100 text-ink-700" },
};

type Pending = { context: string; kind: "approve" | "reject"; buyers: string[]; total: number };

const selectable = (r: QueueRow) => r.status === "pending" || r.status === "approved";

export default function IncidentCompensationQueue() {
  const [list, setList] = useState<IncidentQueueList | null>(null);
  const [picked, setPicked] = useState<Record<string, Set<string>>>({});
  const [confirm, setConfirm] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const env = await support.incidentQueue();
      setList(env.data);
      setError(null);
    } catch (e) {
      setError(`장애 보상 대기를 받지 못했습니다. ${(e as Error).message}`);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const incidents = (list?.incidents ?? []).filter((i) => i.rows.length > 0);
  const waiting = incidents.reduce((n, i) => n + i.pending_buyers, 0);

  function toggle(context: string, buyer: string) {
    setConfirm(null);
    setPicked((p) => {
      const next = new Set(p[context] ?? []);
      if (next.has(buyer)) next.delete(buyer); else next.add(buyer);
      return { ...p, [context]: next };
    });
  }
  function toggleAll(i: IncidentQueue, on: boolean) {
    setConfirm(null);
    setPicked((p) => ({ ...p, [i.context_id]: new Set(on ? i.rows.filter(selectable).map((r) => r.buyer_id) : []) }));
  }

  function ask(i: IncidentQueue, kind: "approve" | "reject") {
    const chosen = i.rows.filter((r) => picked[i.context_id]?.has(r.buyer_id) && (kind === "approve" ? selectable(r) : r.status === "pending"));
    if (!chosen.length) return;
    setNotice(null);
    setConfirm({ context: i.context_id, kind, buyers: chosen.map((r) => r.buyer_id), total: chosen.reduce((n, r) => n + r.total_value, 0) });
  }

  async function send() {
    if (!confirm) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api.admin.decideIncidentCompensation({
        incident_id: confirm.context,
        approve_buyer_ids: confirm.kind === "approve" ? confirm.buyers : [],
        reject_buyer_ids: confirm.kind === "reject" ? confirm.buyers : [],
      });
      setNotice(
        confirm.kind === "approve"
          ? `승인 ${out.approved}명 · 쿠폰 요청이 지급까지 끝난 고객 ${out.sent}명.${out.stopped_early ? " 마케팅에 닿지 않은 고객은 승인 상태로 남았습니다. 다시 승인하면 이어서 보냅니다." : ""}`
          : `거절 ${out.rejected}명. 이 고객들에게는 쿠폰을 요청하지 않습니다.`,
      );
      setPicked((p) => ({ ...p, [confirm.context]: new Set() }));
      setConfirm(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="incident-comp-title" className={`rounded-2xl p-4 ring-1 sm:p-5 ${waiting ? "bg-amber-50 ring-amber-100" : "bg-surface ring-ink-100"}`}>
      <div className="flex flex-wrap items-end gap-x-6 gap-y-1">
        <h2 id="incident-comp-title" className="text-base font-semibold text-ink-900">장애 보상 대기</h2>
        <p className={`text-sm font-semibold ${waiting ? "text-amber-800" : "text-emerald-800"}`}>
          {list === null ? "불러오는 중" : waiting ? `승인 대기 ${waiting}명` : "기다리는 장애 보상 없음"}
        </p>
      </div>
      <p className="mt-0.5 text-xs leading-relaxed text-ink-500">
        장애로 피해를 본 고객의 보상 쿠폰은 자동으로 나가지 않습니다. 고객별로 확인해 승인한 고객에게만 쿠폰을 요청하고, 거절한 고객은 요청하지 않습니다.
        금액 한도 검사와 지급은 승인한 뒤 마케팅·안전 장치가 합니다.
      </p>
      {error && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-4 py-2 text-xs font-medium text-red-800">{error}</p>}
      {notice && <p role="status" className="mt-3 rounded-xl bg-brand-50 px-4 py-2 text-xs font-medium text-brand-700">{notice}</p>}
      {!humanTokenSet && waiting > 0 && <p className="mt-3 text-xs text-amber-800">결정 권한(VITE_SAFETY_HUMAN_TOKEN)이 설정되지 않아 버튼이 꺼져 있습니다.</p>}

      {incidents.map((i) => {
        const chosen = picked[i.context_id] ?? new Set<string>();
        const canApprove = i.rows.filter((r) => chosen.has(r.buyer_id) && selectable(r)).length;
        const canReject = i.rows.filter((r) => chosen.has(r.buyer_id) && r.status === "pending").length;
        const pickable = i.rows.filter(selectable);
        const asking = confirm?.context === i.context_id ? confirm : null;
        return (
          <div key={i.context_id} className="mt-4 rounded-xl bg-surface p-4 shadow-sm">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <p className="text-sm font-semibold">사건 <span className="font-mono">{i.context_id}</span></p>
              <p className="text-xs text-ink-500">승인 대기 <b className="tabular-nums text-ink-900">{i.pending_buyers}명</b> · 합계 <b className="tabular-nums text-ink-900">{won(i.pending_total)}</b></p>
            </div>
            <div className="scroll-thin mt-2 overflow-x-auto">
              <table className="w-full min-w-[560px] text-left text-sm">
                <thead className="text-[11px] text-ink-500">
                  <tr>
                    <th className="w-8 pb-1 pr-2 font-medium">
                      <input type="checkbox" aria-label="승인·거절할 수 있는 고객 모두 고르기" disabled={!pickable.length || busy}
                        checked={pickable.length > 0 && pickable.every((r) => chosen.has(r.buyer_id))}
                        onChange={(e) => toggleAll(i, e.target.checked)} />
                    </th>
                    <th className="pb-1 pr-3 font-medium">고객</th><th className="pb-1 pr-3 font-medium">보상</th><th className="pb-1 pr-3 font-medium">올린 시각</th><th className="pb-1 font-medium">상태</th>
                  </tr>
                </thead>
                <tbody>
                  {i.rows.map((r) => (
                    <tr key={r.buyer_id} className="border-t border-ink-100 align-top">
                      <td className="py-2 pr-2">
                        {selectable(r) && (
                          <input type="checkbox" aria-label={`${r.buyer_id} 고르기`} disabled={busy} checked={chosen.has(r.buyer_id)} onChange={() => toggle(i.context_id, r.buyer_id)} />
                        )}
                      </td>
                      <td className="whitespace-nowrap py-2 pr-3 font-medium tabular-nums">{r.buyer_id}</td>
                      <td className="whitespace-nowrap py-2 pr-3 tabular-nums text-ink-700">{r.count}장 × {won(r.unit_value)} = <b className="text-ink-900">{won(r.total_value)}</b></td>
                      <td className="whitespace-nowrap py-2 pr-3 text-xs text-ink-500">{shortDateTime(r.created_at)}</td>
                      <td className="py-2">
                        <span className={`chip ${STATUS_KO[r.status].tone}`}>{STATUS_KO[r.status].label}</span>
                        {r.status === "held" && <p className="mt-1 text-[11px] text-ink-500">마케팅 한도를 넘어 안전 장치 보류 큐에서 사람이 결정합니다.</p>}
                        {r.decided_by && <p className="mt-1 text-[11px] text-ink-500">{r.decided_by} · {shortDateTime(r.decided_at ?? r.created_at)}</p>}
                        {r.coupon_id && <p className="mt-0.5 font-mono text-[11px] text-ink-500">{r.coupon_id}</p>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {asking ? (
              <div role="alertdialog" aria-label="결정 확인" className="mt-3 space-y-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm">
                <p>
                  {asking.kind === "approve"
                    ? <>선택한 <b>{asking.buyers.length}명</b>에게 쿠폰 합계 <b>{won(asking.total)}</b>을 요청합니다. 요청하면 되돌릴 수 없습니다.</>
                    : <>선택한 <b>{asking.buyers.length}명</b>을 거절합니다. 거절하면 이 고객들에게는 쿠폰을 요청하지 않고, 되돌릴 수 없습니다.</>}
                </p>
                <div className="flex gap-2">
                  <button className="btn-primary py-1.5 text-xs" disabled={busy} onClick={send}>{busy ? "처리 중…" : asking.kind === "approve" ? "승인하고 쿠폰 요청" : "거절하기"}</button>
                  <button className="btn-ghost py-1.5 text-xs" disabled={busy} onClick={() => setConfirm(null)}>취소</button>
                </div>
              </div>
            ) : (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button className="btn-primary py-1.5 text-xs" disabled={!humanTokenSet || busy || canApprove === 0} onClick={() => ask(i, "approve")}>
                  선택한 {canApprove}명 승인
                </button>
                <button className="btn-ghost py-1.5 text-xs" disabled={!humanTokenSet || busy || canReject === 0} onClick={() => ask(i, "reject")}>
                  선택한 {canReject}명 거절
                </button>
                <span className="text-[11px] text-ink-500">고객을 고른 뒤 누르면 한 번 더 확인합니다.</span>
              </div>
            )}
          </div>
        );
      })}

      {list !== null && incidents.length === 0 && !error && (
        <p className="mt-3 text-sm text-ink-500">총괄이 장애 복구 뒤 영향 고객을 올리면 여기에 나타납니다. 승인하기 전에는 아무에게도 쿠폰이 나가지 않습니다.</p>
      )}
      {(list?.notes ?? []).length > 0 && incidents.length > 0 && (
        <ul className="mt-3 list-disc space-y-0.5 pl-4 text-[11px] text-ink-500">
          {list?.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
    </section>
  );
}
