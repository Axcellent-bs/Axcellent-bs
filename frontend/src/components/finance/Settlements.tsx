import { useEffect, useState } from "react";
import { humanId, shortDateTime } from "@/lib/format";
import {
  FinanceApiError, SAFETY_SERVER, batchOf, finance, krw, percent, periodKo, readyPayout,
  type BatchStatus, type MarketOp, type SettlementBatch, type StatementStatus,
} from "@/lib/finance";
import { Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// ③ 판매자 정산: 판매자별로 줄 돈을 계산하고, 보류 없는 판매자만 묶어 지급 승인을 요청한다.
// 요청은 안전 장치 보류 큐로 가고, 사람이 거기서 승인한다. 데모에서는 실제로 송금하지 않는다.

const STATUS_KO: Record<StatementStatus, { label: string; tone: string }> = {
  ready: { label: "지급 준비됨", tone: TONE.ok },
  held: { label: "보류", tone: TONE.warn },
  requested: { label: "승인 요청됨", tone: TONE.info },
  approved: { label: "승인됨", tone: TONE.ok },
  rejected: { label: "거절됨", tone: TONE.bad },
};
const BATCH_KO: Record<BatchStatus, string> = {
  none: "아직 지급 승인을 요청하지 않았습니다.",
  requested: "지급 승인을 요청했습니다. 안전 장치 보류 큐에서 사람이 승인하기를 기다립니다.",
  approved: "지급이 승인됐습니다. (데모에서는 기록만 하고 실제로 송금하지 않습니다)",
  rejected: "지급 요청이 거절됐습니다. 내용을 확인하고 다시 요청할 수 있습니다.",
};
/** 빼는 금액: 54000 → "−54,000원", 0 → "0원", 없음 → "확인할 수 없음" */
const minus = (n: number | null) => (n ? `−${krw(n)}` : krw(n));
const SELLER_KO: Record<string, string> = { approved: "승인", rejected: "거절", held: "사람 확인 중", undetermined: "판단 보류" };
const HOLD_KO: Record<string, string> = { held: "정산 멈춤", pending_review: "사람 확인 중", rejected: "멈추지 않음" };

function MarketOps({ tick, sellers }: { tick: number; sellers: Record<string, string> }) {
  const [ops, setOps] = useState<MarketOp[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    finance.marketOps().then((o) => { if (live) { setOps(o); setError(null); } }).catch((e: Error) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [tick]);
  return (
    <details className="rounded-2xl border border-ink-100 px-4 py-3">
      <summary className="cursor-pointer text-sm font-semibold text-ink-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">판매자 심사·정산 멈춤 기록</summary>
      <p className="mt-1 text-xs text-ink-500">입점 심사 결과와, 문제가 있어 정산을 멈춘 판매자입니다. 정산을 멈춘 판매자는 정산 묶음에서 빠집니다.</p>
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      {ops && (ops.length ? (
        <ul className="mt-2 divide-y divide-ink-100 text-sm">
          {ops.slice(0, 10).map((m, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2 py-2">
              <span className="font-medium">{m.skill === "seller_review" ? "입점 심사" : "정산 멈춤"}</span>
              <span className="text-ink-700" title={m.seller_id}>{humanId(m.seller_id, sellers)}{m.risk_score != null && ` · 위험 점수 ${m.risk_score}점`}</span>
              <span className="ml-auto flex items-center gap-2 text-xs text-ink-500">
                {shortDateTime(m.at)}
                <Chip label={m.skill === "seller_review" ? SELLER_KO[m.decision ?? ""] ?? "확인 필요" : HOLD_KO[m.status ?? ""] ?? "확인 필요"}
                  tone={m.decision === "approved" || m.status === "held" ? TONE.ok : m.decision === "rejected" ? TONE.bad : TONE.muted} />
              </span>
            </li>
          ))}
        </ul>
      ) : <p className="mt-2 text-sm text-ink-500">아직 기록이 없습니다.</p>)}
    </details>
  );
}

export function SettlementsSection({ tick, onChanged, onGoToQueue }: { tick: number; onChanged: () => void; onGoToQueue: (reviewId: string | null) => void }) {
  const res = useResource("settlements", () => finance.settlements(), tick);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<SettlementBatch | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function requestBatch(period: string) {
    setSending(true);
    setProblem(null);
    setSent(null);
    try {
      const out = await finance.requestSettlementBatch(period);
      setSent(batchOf(out.data));
      await res.reload();
      onChanged();
    } catch (e) {
      const err = e as FinanceApiError;
      setProblem(err.code === "safety_unavailable" || err.status === 503
        ? `${SAFETY_SERVER}가 응답하지 않아 요청하지 않았습니다. 지급 기록도 남지 않았습니다. 안전 장치를 켠 뒤 다시 누르세요.`
        : err.message);
    } finally {
      setSending(false);
    }
  }

  return (
    <Panel id="settlements" title="③ 판매자 정산" titleHidden meta={res.env?.meta}
      hint="판매자에게 줄 돈(정산금) = 판매액 − 판매 수수료 − 판매자가 부담한 쿠폰 − 환불. 처리 중인 환불·교환이 있거나 위험한 판매자는 자동으로 보류하고, 나머지만 묶어 지급 승인을 요청합니다.">
      <ResourceBody res={res}>
        {(s) => {
          const total = readyPayout(s.statements);
          const readyCount = s.statements.filter((x) => x.status === "ready").length;
          const inFlight = s.batch.status === "requested" || s.batch.status === "approved";
          const canRequest = readyCount > 0 && !inFlight && !sending;
          return (
            <div className="space-y-4">
              <p className="text-sm text-ink-700">
                정산 기간 <b>{periodKo(s.period)}</b>{s.from && s.to && <span className="text-ink-500"> ({s.from.slice(0, 10)} ~ {s.to.slice(0, 10)})</span>}
                {s.pay_date && <> · 지급 예정일 <b>{s.pay_date}</b></>}
              </p>

              {s.statements.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[860px] text-sm">
                    <thead className="text-left text-xs text-ink-500">
                      <tr>
                        <th className="pb-2 font-medium">판매자</th>
                        <th className="pb-2 text-right font-medium">판매액</th>
                        <th className="pb-2 text-right font-medium">수수료(율)</th>
                        <th className="pb-2 text-right font-medium" title="판매자가 비용을 내기로 한 쿠폰">판매자 부담 쿠폰</th>
                        <th className="pb-2 text-right font-medium">환불</th>
                        <th className="pb-2 text-right font-medium">정산금</th>
                        <th className="pb-2 pl-4 font-medium">보류 사유</th>
                        <th className="pb-2 font-medium">상태</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.statements.map((x) => (
                        <tr key={x.seller_id} className="border-t border-ink-100 align-top">
                          <td className="py-2"><b className="text-ink-900">{x.name}</b><div className="text-xs text-ink-500">주문 {x.orders}건 <code className="font-mono text-[10px]">{x.seller_id}</code></div></td>
                          <td className="py-2 text-right tabular-nums">{krw(x.gross)}</td>
                          <td className="py-2 text-right tabular-nums">{minus(x.commission)}<div className="text-xs text-ink-500">{percent(x.commission_rate)}</div></td>
                          <td className="py-2 text-right tabular-nums">{minus(x.seller_coupon)}</td>
                          <td className="py-2 text-right tabular-nums">{minus(x.refunds)}</td>
                          <td className="py-2 text-right font-semibold tabular-nums">{krw(x.payout)}</td>
                          <td className="py-2 pl-4">
                            {x.hold_reasons.length
                              ? <div className="flex flex-wrap gap-1">{x.hold_reasons.map((h) => <Chip key={h.code} label={h.label} tone={TONE.warn} />)}</div>
                              : <span className="text-xs text-ink-500">없음</span>}
                          </td>
                          <td className="py-2"><Chip label={STATUS_KO[x.status]?.label ?? "확인 필요"} tone={STATUS_KO[x.status]?.tone ?? TONE.muted} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <Empty>이 기간에 정산할 판매자가 없습니다.</Empty>}

              <div className="rounded-2xl bg-surface-2 px-4 py-3">
                <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
                  <div>
                    {inFlight ? (
                      <>
                        <p className="text-xs text-ink-500">지급 승인을 요청한 판매자 {s.batch.seller_ids.length}곳 정산금 합계</p>
                        <p className="text-2xl font-bold tabular-nums text-ink-900">{krw(s.batch.total_payout)}</p>
                      </>
                    ) : (
                      <>
                        <p className="text-xs text-ink-500">지급 준비된 판매자 {readyCount}곳 정산금 합계</p>
                        <p className={`text-2xl font-bold tabular-nums ${total == null ? "text-ink-500" : "text-ink-900"}`}>{krw(total)}</p>
                      </>
                    )}
                  </div>
                  <div className="ml-auto flex flex-col items-start gap-1 sm:items-end">
                    <button className="btn-primary" disabled={!canRequest} onClick={() => requestBatch(s.period)}>
                      {sending ? "요청하는 중…" : "정산 묶음 지급 승인 요청"}
                    </button>
                    <span className="text-xs text-ink-500">
                      {inFlight ? "이미 요청한 묶음이 있습니다." : readyCount === 0 ? "지급 준비된 판매자가 없습니다." : "누르면 안전 장치 보류 큐로 갑니다. 돈은 아직 나가지 않습니다."}
                    </span>
                  </div>
                </div>
                <p className="mt-2 text-sm text-ink-700">{BATCH_KO[s.batch.status] ?? "지급 상태를 확인할 수 없습니다."}</p>
                {s.batch.review_id && (
                  <p className="mt-1 text-xs text-ink-500">
                    확인 번호 <code>{s.batch.review_id}</code> · 묶음 {s.batch.seller_ids.length}곳 {krw(s.batch.total_payout)}{" "}
                    {s.batch.status === "requested" && <button type="button" className="font-medium text-brand-700 underline" onClick={() => onGoToQueue(s.batch.review_id)}>보류 큐에서 승인하기</button>}
                  </p>
                )}
              </div>

              {sent && (
                <div aria-live="polite" className="rounded-xl bg-brand-50 px-4 py-3 text-sm text-brand-700">
                  <p className="font-semibold">안전 장치 보류 큐로 보냈습니다.</p>
                  <p className="mt-0.5">
                    {sent.review_id ? <>확인 번호 <code>{sent.review_id}</code>. </> : ""}
                    아래 <b>안전 장치 보류 큐</b>에서 사람이 승인하면 지급이 기록됩니다. 정한 시간 안에 결정하지 않으면 자동으로 취소됩니다.
                  </p>
                  <button type="button" className="mt-1 font-medium underline" onClick={() => onGoToQueue(sent.review_id)}>보류 큐로 가기</button>
                </div>
              )}
              {problem && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">{problem}</p>}

              <MarketOps tick={tick} sellers={Object.fromEntries(s.statements.map((x) => [x.seller_id, x.name]))} />
            </div>
          );
        }}
      </ResourceBody>
    </Panel>
  );
}
