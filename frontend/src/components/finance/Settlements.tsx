import { useRef, useState } from "react";
import {
  FinanceApiError, SAFETY_SERVER, batchOf, finance, isoWeekOf, krw, percent, periodKo, readyPayout, shiftWeek,
  type BatchStatus, type SettlementBatch, type StatementProduct, type StatementStatus,
} from "@/lib/finance";
import { SHOP_WEB_URL } from "@/lib/env";
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
// 쇼핑몰 판매자·상품과 잇기: 번호는 shop-api 것 그대로라 쇼핑몰 상품 화면의 "판매자 sel_…"과 같다
function SoldProducts({ products }: { products?: StatementProduct[] }) {
  if (!products?.length) return null;
  return (
    <details className="mt-1 text-sm">
      <summary className="cursor-pointer text-ink-500">판매 상품 {products.length}개</summary>
      <ul className="mt-1 space-y-0.5">
        {products.map((p) => (
          <li key={p.product_id} className="flex items-baseline justify-between gap-3">
            <span className="min-w-0">
              {SHOP_WEB_URL
                ? <a className="text-brand-600 hover:underline" href={`${SHOP_WEB_URL}/products/${encodeURIComponent(p.product_id)}`} target="_blank" rel="noreferrer">{p.name}</a>
                : <span className="text-ink-700">{p.name}</span>}{" "}
              <code className="font-mono text-ink-500">{p.product_id}</code>
            </span>
            <span className="shrink-0 tabular-nums text-ink-500">{p.qty.toLocaleString("ko-KR")}개 · {krw(p.gross)}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

const BATCH_KO: Record<BatchStatus, string> = {
  none: "아직 지급 승인을 요청하지 않았습니다.",
  requested: "지급 승인을 요청했습니다. 안전 장치 보류 큐에서 사람이 승인하기를 기다립니다.",
  approved: "지급이 승인됐습니다. (데모에서는 기록만 하고 실제로 송금하지 않습니다)",
  rejected: "지급 요청이 거절됐습니다. 내용을 확인하고 다시 요청할 수 있습니다.",
};
// 거절된 묶음이 왜 끝났는지 (없으면 BATCH_KO.rejected)
const CLOSED_KO: Record<string, string> = {
  expired: "정한 시간 안에 아무도 결정하지 않아 지급 요청이 자동으로 멈췄습니다. 다시 요청할 수 있습니다.",
  review_missing: "안전 장치에 이 요청 기록이 없습니다(안전 장치 재시작 등). 승인된 적이 없어 지급되지 않았으니 다시 요청하세요.",
};
/** 정산 기간 끝(다음 주 월요일 0시, 그날은 빠짐)을 그 주 일요일로 보여 준다: "2026-11-09…" → "2026-11-08" */
function lastDay(to: string): string {
  const d = new Date(`${to.slice(0, 10)}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}
/** 빼는 금액: 54000 → "−54,000원", 0 → "0원", 없음 → "확인할 수 없음" */
const minus = (n: number | null) => (n ? `−${krw(n)}` : krw(n));
export function SettlementsSection({ tick, onChanged, onGoToQueue }: { tick: number; onChanged: () => void; onGoToQueue: (reviewId: string | null) => void }) {
  // 보는 정산 주. null이면 서버 기본(지난주). 지난주보다 앞선 주에 못 준 정산이 있으면 그 주를 열어 요청한다
  const [period, setPeriod] = useState<string | null>(null);
  const latest = useRef<string | null>(null); // 서버 기본 주(지난주)
  const res = useResource(`settlements:${period ?? ""}`, () => finance.settlements(period), tick);
  const deadlines = useResource("settlement-deadlines", () => finance.deadlines(), tick);
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
      hint="판매자에게 줄 돈(정산금) = 판매액 − 판매 수수료 − 판매자가 부담한 쿠폰 − 환불. 오픈마켓처럼 구매가 확정된(배송 완료) 주문만 그 확정일이 속한 주에 정산하고, 아직 확정되지 않은 주문은 확정되는 주의 정산으로 넘깁니다. 정산금이 0원 이하인 판매자만 보류하고, 나머지를 묶어 지급 승인을 요청합니다.">
      <ResourceBody res={res}>
        {(s) => {
          const total = readyPayout(s.statements);
          const readyCount = s.statements.filter((x) => x.status === "ready").length;
          const inFlight = s.batch.status === "requested" || s.batch.status === "approved";
          const canRequest = readyCount > 0 && !inFlight && !sending;
          // 기한 점검에서 아직 지급되지 않은(예정일이 지난) 판매 대금이 걸린 주 중 지금 보는 주가 아닌 주
          const unpaidWeeks = new Map<string, string[]>();
          for (const d of deadlines.env?.data.sellers ?? []) {
            if (d.status === "ok" || d.expected_pay_date) continue;
            const week = isoWeekOf(d.oldest_confirmed);
            if (!week || week === s.period) continue;
            unpaidWeeks.set(week, [...(unpaidWeeks.get(week) ?? []), d.name]);
          }
          const goTo = (p: string | null) => { setSent(null); setProblem(null); setPeriod(p); };
          const prev = shiftWeek(s.period, -1);
          if (period === null) latest.current = s.period;
          // 지난주보다 뒤(아직 끝나지 않은 이번 주)는 열지 않는다
          const next = shiftWeek(s.period, 1);
          const nextOk = !!next && !!latest.current && next <= latest.current;
          return (
            <div className="space-y-4">
              {[...unpaidWeeks].sort(([a], [b]) => a.localeCompare(b)).map(([week, names]) => (
                <div key={week} role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-900 ring-1 ring-amber-100">
                  <span className="min-w-0 flex-1">
                    <b>{periodKo(week)}</b> 정산이 아직 지급되지 않았습니다 ({names.join(", ")}). 지급 예정일이 지나 기한을 넘길 위험이 있습니다. 그 주를 열어 지급 승인을 요청하세요.
                  </span>
                  <button type="button" className="btn-ghost" onClick={() => goTo(week)}>{periodKo(week)} 열기</button>
                </div>
              ))}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <p className="text-sm text-ink-700">
                  정산 기간 <b>{periodKo(s.period)}</b>{s.from && s.to && <span className="text-ink-500"> ({s.from.slice(0, 10)} ~ {lastDay(s.to)})</span>}
                  {s.pay_date && <> · 지급 예정일 <b>{s.pay_date}</b></>}
                </p>
                <div className="ml-auto flex gap-2">
                  <button type="button" className="btn-ghost" disabled={!prev} onClick={() => goTo(prev)}>← 이전 주</button>
                  {period !== null && <button type="button" className="btn-ghost" onClick={() => goTo(null)}>지난주로</button>}
                  <button type="button" className="btn-ghost" disabled={!nextOk} onClick={() => goTo(next)}>다음 주 →</button>
                </div>
              </div>

              {s.statements.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[860px] text-sm">
                    <thead className="text-left text-sm text-ink-500">
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
                          <td className="py-2"><b className="text-ink-900">{x.name}</b><div className="text-sm text-ink-500">주문 {x.orders}건 <code className="font-mono text-sm">{x.seller_id}</code></div><SoldProducts products={x.products} /></td>
                          <td className="py-2 text-right tabular-nums">{krw(x.gross)}</td>
                          <td className="py-2 text-right tabular-nums">{minus(x.commission)}<div className="text-sm text-ink-500">{percent(x.commission_rate)}</div></td>
                          <td className="py-2 text-right tabular-nums">{minus(x.seller_coupon)}</td>
                          <td className="py-2 text-right tabular-nums">{minus(x.refunds)}</td>
                          <td className="py-2 text-right font-semibold tabular-nums">{krw(x.payout)}</td>
                          <td className="py-2 pl-4">
                            {x.hold_reasons.length
                              ? <div className="flex flex-wrap gap-1">{x.hold_reasons.map((h) => <Chip key={h.code} label={h.label} tone={TONE.warn} />)}</div>
                              : <span className="text-sm text-ink-500">없음</span>}
                            {x.pending_confirmation && x.pending_confirmation.orders > 0 && (
                              <div className="mt-1 text-sm text-ink-500" title="아직 구매가 확정되지 않은 주문은 이번 정산금에 들지 않고, 확정되는 주의 정산으로 넘어갑니다">
                                다음 정산으로 넘김 {x.pending_confirmation.orders.toLocaleString("ko-KR")}건 · {krw(x.pending_confirmation.gross)}
                              </div>
                            )}
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
                        <p className="text-sm text-ink-500">지급 승인을 요청한 판매자 {s.batch.seller_ids.length}곳 정산금 합계</p>
                        <p className="text-2xl font-bold tabular-nums text-ink-900">{krw(s.batch.total_payout)}</p>
                      </>
                    ) : (
                      <>
                        <p className="text-sm text-ink-500">지급 준비된 판매자 {readyCount}곳 정산금 합계</p>
                        <p className={`text-2xl font-bold tabular-nums ${total == null ? "text-ink-500" : "text-ink-900"}`}>{krw(total)}</p>
                      </>
                    )}
                  </div>
                  <div className="ml-auto flex flex-col items-start gap-1 sm:items-end">
                    <button className="btn-primary" disabled={!canRequest} onClick={() => requestBatch(s.period)}>
                      {sending ? "요청하는 중…" : "정산 묶음 지급 승인 요청"}
                    </button>
                    <span className="text-sm text-ink-500">
                      {inFlight ? "이미 요청한 묶음이 있습니다." : readyCount === 0 ? "지급 준비된 판매자가 없습니다." : "누르면 안전 장치 보류 큐로 갑니다. 돈은 아직 나가지 않습니다."}
                    </span>
                  </div>
                </div>
                <p className="mt-2 text-sm text-ink-700">{(s.batch.status === "rejected" && s.batch.closed_reason && CLOSED_KO[s.batch.closed_reason]) || BATCH_KO[s.batch.status] || "지급 상태를 확인할 수 없습니다."}</p>
                {s.batch.review_id && (
                  <p className="mt-1 text-sm text-ink-500">
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

            </div>
          );
        }}
      </ResourceBody>
    </Panel>
  );
}
