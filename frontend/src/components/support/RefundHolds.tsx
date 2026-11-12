import { useCallback, useEffect, useState } from "react";
import { shortDateTime, won } from "@/lib/format";
import { finance, humanTokenSet, SAFETY_SERVER, type ExecutionRecord, type Review } from "@/lib/finance";
import { ApproverField, deciderText, useApproverName } from "@/components/finance/ApproverName";

// 환불(refund_request) 안전 장치 보류: 환불은 고객지원 일이라 사람이 여기서 정한다.
// 보류 큐 API는 재무 화면과 같은 안전 장치 서버를 쓰고, 환불 건만 걸러 보여 준다.
// 결정은 안전 장치에 사람 토큰으로 보낸다. 승인한 뒤 처리 버튼을 다시 누르면 같은 키로 지급된다.

const WHY_HELD: Record<string, string> = {
  over_threshold: "금액 기준을 넘음",
  amount_mismatch: "적어 낸 금액과 계산이 다름",
  unregistered_action: "등록되지 않은 일",
  schema_invalid: "요청 내용이 잘못됨",
  review_timeout: "기한 안에 아무도 결정하지 않아 자동 취소",
};
const STATUS_KO: Record<Review["status"], string> = { pending: "결정 기다림", approved: "승인", rejected: "거절", expired: "기한 지남" };

const str = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : null);

export default function RefundHolds({ onDecided }: { onDecided?: () => void }) {
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [runs, setRuns] = useState<Record<string, ExecutionRecord>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [approver, setApprover, approverName] = useApproverName();

  const load = useCallback(async () => {
    try {
      const [all, executions] = await Promise.all([finance.reviews(), finance.executions()]);
      setReviews(all.filter((r) => r.action === "refund_request"));
      setRuns(Object.fromEntries(executions.map((e) => [e.execution_id, e])));
      setError(null);
    } catch (e) {
      setError(`${SAFETY_SERVER}에서 환불 보류를 받지 못했습니다. ${(e as Error).message}`);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function decide(id: string, decision: "approved" | "rejected") {
    if (!approverName) { setError("승인자 이름을 먼저 적으세요."); return; }
    setBusy(id + decision);
    try {
      await finance.decide(id, decision, undefined, false, approverName);
      await load();
      onDecided?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const pending = (reviews ?? []).filter((r) => r.status === "pending").sort((a, b) => a.expires_at.localeCompare(b.expires_at));
  const recent = (reviews ?? []).filter((r) => r.status !== "pending").slice(-5).reverse();

  return (
    <section aria-labelledby="refund-holds-title" className={`rounded-2xl p-4 ring-1 sm:p-5 ${pending.length ? "bg-amber-50 ring-amber-100" : "bg-surface ring-ink-100"}`}>
      <div className="flex flex-wrap items-end gap-x-6 gap-y-1">
        <h2 id="refund-holds-title" className="text-base font-semibold text-ink-900">환불 안전 장치 보류</h2>
        <p className={`text-sm font-semibold ${pending.length ? "text-amber-800" : "text-emerald-800"}`}>
          {reviews === null ? "불러오는 중" : pending.length ? `결정 기다림 ${pending.length}건` : "기다리는 환불 없음"}
        </p>
      </div>
      <p className="mt-0.5 text-xs leading-relaxed text-ink-500">
        환불 한 건 금액이 기준을 넘거나 적어 낸 금액이 계산과 다르면 안전 장치가 멈춰 사람의 결정을 기다립니다. 승인한 뒤 위의 처리 버튼을 다시 누르면 같은 건이 지급됩니다.
      </p>
      {error && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-4 py-2 text-xs font-medium text-red-800">{error}</p>}
      {!humanTokenSet && pending.length > 0 && <p className="mt-3 text-xs text-amber-800">이 계정에는 결정 권한이 없어 승인·거절 버튼이 꺼져 있습니다. 결정 권한이 있는 담당자 계정으로 들어오면 켜집니다.</p>}

      {pending.length > 0 && humanTokenSet && (
        <div className="mt-3"><ApproverField id="refund-holds-approver" value={approver} onChange={setApprover} small /></div>
      )}
      {pending.length > 0 && (
        <ul className="mt-4 space-y-3">
          {pending.map((r) => {
            const params = runs[r.execution_id]?.request.params ?? {};
            const claim = str(params.claim_id);
            const order = str(params.order_id);
            const amount = r.report.computed_amount ?? (typeof params.refund_amount === "number" ? params.refund_amount : null);
            return (
              <li key={r.review_id} className="grid gap-3 rounded-xl bg-surface p-4 shadow-sm sm:grid-cols-[150px_1fr_auto] sm:items-center">
                <p className="text-xl font-bold tabular-nums text-ink-900">{amount != null ? won(amount) : "금액 확인 필요"}</p>
                <div className="min-w-0 text-sm">
                  <p className="font-semibold">
                    {claim ? `신청 #${claim}` : "환불"}{order && <span className="font-normal text-ink-500"> · 주문 {order}</span>}
                    <span className="chip ml-2 bg-amber-100 text-amber-800">{WHY_HELD[r.report.hold_reason ?? ""] ?? "확인 필요"}</span>
                  </p>
                  {str(params.summary) && <p className="mt-0.5 break-all text-xs text-ink-500">{str(params.summary)}</p>}
                  <p className="mt-1 text-xs text-ink-500">보류 {shortDateTime(r.created_at)} · {shortDateTime(r.expires_at)}까지 결정</p>
                  <p className="mt-1.5 rounded-lg bg-brand-50 px-2 py-1 text-xs text-brand-700">
                    {r.report.hold_reason === "amount_mismatch"
                      ? "적어 낸 금액이 계산과 다릅니다. 보통 멈추고 클레임 금액을 다시 확인하세요."
                      : "클레임 화면에서 주문·결제 금액을 확인한 뒤 결정하세요."}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button className="btn-primary py-1.5 text-xs" disabled={!humanTokenSet || !approverName || busy !== null} onClick={() => decide(r.review_id, "approved")}>
                    {busy === r.review_id + "approved" ? "처리 중…" : "승인"}
                  </button>
                  <button className="btn-ghost py-1.5 text-xs" disabled={!humanTokenSet || !approverName || busy !== null} onClick={() => decide(r.review_id, "rejected")}>
                    {busy === r.review_id + "rejected" ? "처리 중…" : "거절"}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {recent.length > 0 && (
        <div className="mt-4 text-xs text-ink-500">
          <p className="font-semibold">최근 결정</p>
          <ul className="mt-1 space-y-0.5">
            {recent.map((r) => (
              <li key={r.review_id}>
                {STATUS_KO[r.status]} · {r.report.computed_amount != null ? won(r.report.computed_amount) : "금액 없음"} · {r.status !== "expired" && r.decided_by ? `${deciderText(r)} · ` : ""}{shortDateTime(r.decided_at ?? r.created_at)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
