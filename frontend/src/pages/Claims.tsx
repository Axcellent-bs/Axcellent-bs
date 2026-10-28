import { Link } from "@/lib/nav";
import { Suspense, useCallback, useEffect, useState } from "react";
import { Loading, OFFLINE, Stat, StatusChip } from "@/components/admin/bits";
import { api } from "@/lib/api";
import { ago, shortDateTime, won } from "@/lib/format";
import { AUDIT_KO, CLAIM_REASON_KO, CLAIM_STATUS_KO, CLAIM_TYPE_KO, INSPECTION_KO, OUTCOME_KO, RULE_KO } from "@/lib/labels";
import type { Claim, ClaimDetail, ClaimStats } from "@/lib/types";
import { useRouter, useSearchParams } from "@/lib/nav";

const FILTERS = ["all", "needs_review", "needs_info", "awaiting_return", "return_received", "completed", "rejected"] as const;
const LEVEL: Record<string, string> = { block: "bg-red-50 text-red-800", review: "bg-violet-50 text-violet-700", warn: "bg-amber-50 text-amber-800", info: "bg-ink-100 text-ink-700" };
const LEVEL_KO: Record<string, string> = { block: "거절 사유", review: "확인 필요", warn: "자동 보정", info: "정보" };

export default function ClaimsPage() {
  return <Suspense><Claims /></Suspense>;
}

function Claims() {
  const router = useRouter();
  const params = useSearchParams();
  const filter = params.get("status") ?? "all";
  const selected = Number(params.get("id")) || null;
  const [rows, setRows] = useState<Claim[] | null>(null);
  const [stats, setStats] = useState<ClaimStats | null>(null);
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([api.admin.claims(), api.admin.claimStats()]);
      setRows(r); setStats(s); setError(null);
    } catch { setError(OFFLINE); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setNote(""); if (selected) api.admin.claim(selected).then(setDetail).catch((e) => setError(e.message)); else setDetail(null); }, [selected]);

  const go = (q: Record<string, string>) => { const sp = new URLSearchParams({ ...(filter !== "all" && { status: filter }), ...(selected && { id: String(selected) }), ...q }); for (const [k, v] of [...sp]) if (!v || v === "all") sp.delete(k); router.push(`/claims?${sp}`); };

  async function act(fn: () => Promise<ClaimDetail>) {
    setBusy(true); setError(null);
    try { setDetail(await fn()); setNote(""); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  if (!rows) return <Loading error={error} />;
  const shown = rows.filter((c) => filter === "all" || c.status === filter);
  const count = (s: string) => rows.filter((c) => s === "all" || c.status === s).length;

  return (
    <div className="flex h-full flex-col gap-3 p-4 lg:px-6">
      <div className="flex shrink-0 flex-wrap gap-3">
        <Stat label="전체 클레임" value={stats?.total ?? 0} />
        <Stat label="상담원 검토" value={stats?.agent_reviews ?? 0} sub={`규칙 판정을 뒤집음 ${stats?.agent_overrides ?? 0}건`} />
        <Stat label="가장 많이 걸린 규칙" value={<span className="text-base">{Object.keys(stats?.rule_hits ?? {})[0] ? RULE_KO[Object.keys(stats!.rule_hits)[0]] : "–"}</span>} sub={`${Object.values(stats?.rule_hits ?? {})[0] ?? 0}건`} />
      </div>
      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-xs font-medium text-red-800">{error}</p>}
      <div className="flex min-h-0 flex-1 gap-3">
        <section className="card flex w-[430px] shrink-0 flex-col overflow-hidden" aria-label="클레임 목록">
          <div className="flex flex-wrap gap-1 border-b border-ink-100 p-3">
            {FILTERS.map((f) => (
              <button key={f} onClick={() => go({ status: f, id: "" })} aria-pressed={filter === f}
                className={`chip py-1 ${filter === f ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>{f === "all" ? "전체" : CLAIM_STATUS_KO[f]} {count(f)}</button>
            ))}
          </div>
          <ul className="scroll-thin flex-1 overflow-y-auto">
            {shown.map((c) => (
              <li key={c.id}>
                <button onClick={() => go({ id: String(c.id) })} className={`w-full border-l-2 px-4 py-3 text-left transition ${selected === c.id ? "border-brand-600 bg-brand-50" : "border-transparent hover:bg-ink-50"}`}>
                  <div className="flex items-center gap-2 text-sm"><b>{c.customer}</b><span className="text-xs text-ink-500">#{c.id} · 주문 {c.order_no}</span><span className="ml-auto text-[11px] text-ink-500">{ago(c.created_at)}</span></div>
                  <p className="mt-0.5 text-xs text-ink-700">{CLAIM_TYPE_KO[c.type]} · {CLAIM_REASON_KO[c.reason]}{c.payable > 0 && ` · ${won(c.payable)}`}</p>
                  <div className="mt-1.5 flex flex-wrap gap-1"><StatusChip status={c.status} />
                    {c.reasons.filter((r) => ["block", "review"].includes(r.level)).slice(0, 2).map((r) => <span key={r.code} className={`chip ${LEVEL[r.level]}`}>{RULE_KO[r.code] ?? r.code}</span>)}
                    {(c.abuse?.score ?? 0) >= 60 && <span className="chip bg-red-50 text-red-700">점검 점수 {c.abuse?.score}</span>}</div>
                </button>
              </li>
            ))}
            {!shown.length && <li className="px-4 py-10 text-center text-xs text-ink-500">해당 상태의 클레임이 없습니다.</li>}
          </ul>
        </section>

        <section className="card scroll-thin min-w-0 flex-1 overflow-y-auto p-6" aria-label="클레임 상세">
          {!detail ? <p className="py-24 text-center text-sm text-ink-500">왼쪽에서 클레임을 선택하면 판정 근거와 영수증을 볼 수 있습니다.</p> : (
            <div className="space-y-6">
              <header className="flex flex-wrap items-center gap-2">
                <h1 className="text-lg font-semibold">{detail.customer} <span className="text-sm font-normal text-ink-500">· 신청 #{detail.id} · 주문 {detail.order_no}</span></h1>
                <StatusChip status={detail.status} />
                <span className="chip bg-ink-100 text-ink-700">{OUTCOME_KO[detail.outcome ?? ""] ?? detail.outcome}</span>
                {detail.decided_by && detail.decided_by !== "system" && <span className="chip bg-brand-50 text-brand-700">{detail.decided_by} 결정</span>}
                {detail.conversation_id && <Link className="text-xs underline" href={`/console?id=${detail.conversation_id}`}>연결된 문의</Link>}
              </header>
              <p className="text-sm text-ink-700">{CLAIM_TYPE_KO[detail.type]} · {CLAIM_REASON_KO[detail.reason]}{detail.unsealed && " · 개봉/사용함"} · 사진 {detail.evidence_count}장{detail.claimed_amount != null && ` · 고객 신청 금액 ${won(detail.claimed_amount)}`}</p>
              {detail.description && <p className="rounded-xl bg-surface-2 px-4 py-3 text-sm italic text-ink-700">“{detail.description}”</p>}

              {detail.status === "needs_review" && (
                <div className="rounded-xl border border-violet-200 bg-violet-50 p-4">
                  <p className="text-sm font-semibold text-violet-700">상담원 판단이 필요합니다</p>
                  {detail.hold_reason && <p className="mt-1 text-xs text-violet-700">보류 사유: {detail.hold_reason}</p>}
                  <textarea className="field mt-2 h-16 resize-none text-xs" placeholder="판단 사유 메모 (감사 기록에 남습니다)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="판단 사유 메모" />
                  <div className="mt-2 flex gap-2">
                    <button className="btn-primary" disabled={busy} onClick={() => act(() => api.admin.approve(detail.id, note))}>승인</button>
                    <button className="btn-ghost" disabled={busy} onClick={() => act(() => api.admin.reject(detail.id, note))}>거절</button>
                  </div>
                  <p className="mt-2 text-[11px] text-violet-700">승인하면 {detail.return_received_at ? "보상 큐로 넘어가고" : detail.requires_return ? "반품 송장이 발급되고" : "바로 지급되고"}, 승인·거절 모두 규칙 판정과 함께 기록됩니다.</p>
                </div>
              )}
              {detail.status === "awaiting_return" && (
                <div className="flex flex-wrap items-center gap-2 rounded-xl bg-brand-50 p-4 text-sm text-brand-700">반품 송장 <b className="font-mono">{detail.tracking}</b> — 물류센터 입고 처리(데모):
                  <button className="btn-primary py-1 text-xs" disabled={busy} onClick={() => act(() => api.admin.receive(detail.id, "ok"))}>입고 · 검수 정상</button>
                  <button className="btn-ghost py-1 text-xs" disabled={busy} onClick={() => act(() => api.admin.receive(detail.id, "mismatch", "사유와 다른 상품 상태"))}>입고 · 불일치</button></div>
              )}

              <section>
                <h2 className="panel-title mb-2">자동 판정 근거</h2>
                <ul className="space-y-1.5">
                  {detail.reasons.map((r, i) => (
                    <li key={i} className={`rounded-lg px-3 py-2 text-xs leading-relaxed ${LEVEL[r.level]}`}>
                      <span className="mr-1.5 font-semibold">{LEVEL_KO[r.level]} · {RULE_KO[r.code] ?? r.code}</span>{r.message}
                      {r.rule && <span className="ml-1.5 font-mono text-[10px] opacity-70">{r.rule}</span>}
                    </li>
                  ))}
                  {!detail.reasons.length && <li className="text-xs text-ink-500">걸린 규칙이 없습니다.</li>}
                </ul>
                <dl className="mt-3 grid grid-cols-2 gap-2 text-center text-xs sm:grid-cols-4">
                  <div className="rounded-lg bg-surface-2 p-2"><dt className="text-ink-500">환불 예정</dt><dd className="mt-0.5 font-semibold tabular-nums">{won(detail.expected_refund)}</dd></div>
                  <div className="rounded-lg bg-surface-2 p-2"><dt className="text-ink-500">적립금 반환</dt><dd className="mt-0.5 font-semibold tabular-nums">{won(detail.points_back)}</dd></div>
                  <div className="rounded-lg bg-surface-2 p-2"><dt className="text-ink-500">보상 적립금</dt><dd className="mt-0.5 font-semibold tabular-nums">{won(detail.compensation)}</dd></div>
                  <div className="rounded-lg bg-surface-2 p-2"><dt className="text-ink-500">지급 완료</dt><dd className="mt-0.5 font-semibold tabular-nums">{won(detail.refund + detail.compensation_paid)}</dd></div>
                </dl>
              </section>

              {detail.abuse && detail.abuse.score > 0 && (
                <section>
                  <h2 className="panel-title mb-2">내부 점검 신호 <span className="normal-case tracking-normal text-red-700">(고객에게 노출되지 않음)</span></h2>
                  <div className="rounded-xl border border-ink-100 p-3 text-xs">
                    <p className="font-semibold">점수 {detail.abuse.score} / 100 <span className="font-normal text-ink-500">· 60 이상이면 자동 승인하지 않고 상담원이 봅니다</span></p>
                    <ul className="mt-1.5 space-y-0.5 text-ink-700">{detail.abuse.signals.map((s, i) => <li key={i}>+{s.points} · {s.message}</li>)}</ul>
                  </div>
                </section>
              )}

              <section>
                <h2 className="panel-title mb-2">영수증 (주문 {detail.receipt.order_no})</h2>
                <table className="w-full text-xs">
                  <thead className="text-ink-500"><tr className="text-left"><th className="py-1 font-medium">상품</th><th className="py-1 text-right font-medium">수량</th><th className="py-1 text-right font-medium">결제 현금</th><th className="py-1 text-right font-medium">적립금</th><th className="py-1 text-right font-medium">환불 수량</th></tr></thead>
                  <tbody>{detail.receipt.items.map((i) => <tr key={i.id} className="border-t border-ink-100"><td className="py-1.5">{i.title}</td><td className="py-1.5 text-right tabular-nums">{i.qty}</td><td className="py-1.5 text-right tabular-nums">{won(i.paid_amount)}</td><td className="py-1.5 text-right tabular-nums">{won(i.points_share)}</td><td className="py-1.5 text-right tabular-nums">{i.refunded_qty}</td></tr>)}</tbody>
                </table>
                <p className="mt-1.5 text-[11px] text-ink-500">상품 {won(detail.receipt.items_total)} + 배송비 {won(detail.receipt.shipping_fee)} − 적립금 {won(detail.receipt.points_used)} = 결제 {won(detail.receipt.paid_amount)} · 배송 예정 {shortDateTime(detail.receipt.expected_delivery_at)} · 도착 {shortDateTime(detail.receipt.delivered_at)}</p>
              </section>

              {detail.return_received_at && (
                <section className="rounded-xl bg-surface-2 p-3 text-xs">입고 {shortDateTime(detail.return_received_at)} · 검수 <b>{INSPECTION_KO[detail.inspection ?? "pending"]}</b>{detail.inspection_note && ` — ${detail.inspection_note}`}
                  {detail.status === "return_received" && detail.inspection === "pending" && (
                    <span className="ml-3 inline-flex gap-1.5"><button className="btn-ghost py-0.5 text-xs" disabled={busy} onClick={() => act(() => api.admin.inspect(detail.id, "ok"))}>검수 정상</button><button className="btn-ghost py-0.5 text-xs" disabled={busy} onClick={() => act(() => api.admin.inspect(detail.id, "mismatch", "사유와 다른 상품 상태"))}>사유 불일치</button></span>)}</section>
              )}
              {detail.agent_note && <p className="rounded-xl bg-brand-50 px-4 py-3 text-xs text-brand-700"><b>상담원 메모</b> {detail.agent_note}</p>}

              <section>
                <h2 className="panel-title mb-2">처리 기록</h2>
                <ol className="space-y-1.5 border-l-2 border-ink-100 pl-4 text-xs">
                  {detail.audit.map((a, i) => (
                    <li key={i}><span className="font-semibold">{AUDIT_KO[a.action] ?? a.action}</span> <span className="text-ink-500">· {a.actor} · {shortDateTime(a.at)}</span>
                      {a.action === "decided" && <span className="ml-1 text-ink-500">→ {OUTCOME_KO[String(a.detail.outcome)] ?? String(a.detail.outcome)}{Number(a.detail.abuse_score) > 0 && ` (점검 ${String(a.detail.abuse_score)})`}</span>}
                      {a.action === "agent_override" && <span className="ml-1 text-ink-500">규칙: {OUTCOME_KO[String(a.detail.system_outcome)] ?? String(a.detail.system_outcome)} → 상담원 {String(a.detail.agent_action) === "approve" ? "승인" : "거절"}</span>}
                      {a.action === "paid" && <span className="ml-1 text-ink-500">환불 {won(Number(a.detail.refund_cash))} · 보상 {won(Number(a.detail.compensation))}</span>}</li>
                  ))}
                </ol>
              </section>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
