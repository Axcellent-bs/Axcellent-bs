import { finance, krw, percent, plainText, signedKrw, type DeadlineStatus, type VarianceLine } from "@/lib/finance";
import { Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// 마감 통제 3종. 숫자는 모두 재무 AI 서버가 계산한 값을 그대로 보여 준다(화면에서 다시 계산하지 않음).
// - 정산 기한 점검(③): 판매 대금을 법이 정한 날 안에 줄 수 있는지 미리 본다
// - 판매자 미지급금 롤포워드(③): 기초 + 발생 − 지급 = 기말이 맞는지 본다
// - 월간 변동 분석(①): 이번 달 손익이 지난달과 왜 달라졌는지 나눠 본다

const DEADLINE_TONE: Record<DeadlineStatus, string> = { overdue: TONE.bad, at_risk: TONE.bad, due_soon: TONE.warn, ok: TONE.ok };

export function DeadlineSection({ tick }: { tick: number }) {
  const res = useResource("deadlines", () => finance.deadlines(), tick);
  return (
    <Panel id="deadlines" title="판매 대금 지급 기한" meta={res.env?.meta}
      hint="구매가 확정된 판매 대금을 정해진 날 안에 판매자에게 줄 수 있는지 미리 봅니다. 재무 AI는 경보만 내고, 지급은 정산 묶음 → 안전 장치 보류 큐 승인으로만 합니다.">
      <ResourceBody res={res}>
        {(r) => (
          <div className="space-y-3">
            <p className="text-xs text-ink-500">
              기준: {r.basis} {r.deadline_days}일 안 · 임박 {r.warn_days}일 이하 · {r.since} 이후 결제분 · 배송 전이라 기한이 시작되지 않은 주문 {r.waiting_orders.toLocaleString("ko-KR")}건
            </p>
            <div className="flex flex-wrap gap-2 text-xs">
              {(["overdue", "at_risk", "due_soon", "ok"] as const).map((k) => (
                <Chip key={k} label={`${{ overdue: "기한 지남", at_risk: "기한 넘길 위험", due_soon: "기한 임박", ok: "정상" }[k]} ${r.counts[k]}곳`} tone={r.counts[k] && k !== "ok" ? DEADLINE_TONE[k] : TONE.muted} />
              ))}
            </div>
            {r.sellers.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-sm">
                  <thead className="text-left text-xs text-ink-500">
                    <tr>
                      <th className="pb-1 font-medium">판매자</th><th className="pb-1 font-medium">상태</th>
                      <th className="pb-1 text-right font-medium">미지급 판매 대금</th><th className="pb-1 font-medium">가장 이른 기한</th>
                      <th className="pb-1 font-medium">지급 예정</th><th className="pb-1 font-medium">막는 이유</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.sellers.map((s) => (
                      <tr key={s.seller_id} className="border-t border-ink-100 align-top">
                        <td className="py-1.5">{s.name}<span className="ml-1 text-xs text-ink-500">주문 {s.orders}건</span></td>
                        <td className="py-1.5"><Chip label={s.status_label} tone={DEADLINE_TONE[s.status]} /></td>
                        <td className="py-1.5 text-right tabular-nums">{krw(s.amount)}</td>
                        <td className="py-1.5 tabular-nums">{s.deadline} <span className={`text-xs ${s.days_left < 0 ? "text-red-700" : "text-ink-500"}`}>({s.days_left < 0 ? `${-s.days_left}일 지남` : `${s.days_left}일 남음`})</span></td>
                        <td className="py-1.5 tabular-nums">{s.expected_pay_date ?? <span className="text-xs text-amber-800">예정 없음</span>}</td>
                        <td className="py-1.5 text-xs text-ink-700">{s.hold_reasons.map((h) => plainText(h.label)).join(", ") || "–"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <Empty>지급 기한이 걸린 미지급 판매 대금이 없습니다.</Empty>}
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}

export function PayablesSection({ tick }: { tick: number }) {
  const res = useResource("payables", () => finance.payables(), tick);
  return (
    <Panel id="payables" title="판매자 미지급금 롤포워드" meta={res.env?.meta}
      hint="판매자에게 줄 돈(판매자 예수금)이 장부와 맞는지 보는 월 마감 통제입니다. 기초 + 이번 달 발생 − 이번 달 지급 = 기말. 기말이 음수인 판매자는 더 준 돈이 있다는 뜻이라 확인이 필요합니다.">
      <ResourceBody res={res}>
        {(p) => (
          <div className="space-y-3">
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {([["기초", p.totals.opening], ["발생 (+)", p.totals.accrued], ["지급 (−)", p.totals.paid], ["기말", p.totals.closing]] as const).map(([label, v]) => (
                <div key={label} className="rounded-2xl bg-surface-2 px-3 py-2">
                  <dt className="text-[11px] text-ink-500">{p.month} {label}</dt>
                  <dd className="text-base font-semibold tabular-nums">{krw(v)}</dd>
                </div>
              ))}
            </dl>
            <p className="text-xs text-ink-500">
              {p.balanced === true ? "기초 + 발생 − 지급 = 기말이 맞습니다." : p.balanced === false ? "합계가 맞지 않습니다. 재무 담당자에게 알리세요." : "주문·쿠폰 자료를 받지 못해 맞는지 확인할 수 없습니다."} {p.basis}
            </p>
            {p.overpaid.length > 0 && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">기말이 음수인 판매자 {p.overpaid.length}곳: 지급액이 발생액보다 많습니다. 환불·상계가 빠졌는지 확인하세요.</p>}
            {p.sellers.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-sm">
                  <thead className="text-left text-xs text-ink-500">
                    <tr><th className="pb-1 font-medium">판매자</th><th className="pb-1 text-right font-medium">기초</th><th className="pb-1 text-right font-medium">발생</th><th className="pb-1 text-right font-medium">지급</th><th className="pb-1 text-right font-medium">기말</th></tr>
                  </thead>
                  <tbody>
                    {p.sellers.map((s) => (
                      <tr key={s.seller_id} className={`border-t border-ink-100 ${s.overpaid ? "text-red-800" : ""}`}>
                        <td className="py-1.5">{s.name}</td>
                        <td className="py-1.5 text-right tabular-nums">{krw(s.opening)}</td>
                        <td className="py-1.5 text-right tabular-nums">{krw(s.accrued)}</td>
                        <td className="py-1.5 text-right tabular-nums">{krw(s.paid)}</td>
                        <td className="py-1.5 text-right font-medium tabular-nums">{krw(s.closing)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <Empty>이번 달 판매자 미지급금이 없습니다.</Empty>}
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}

function valueOf(l: VarianceLine, v: number | null): string {
  if (v == null) return "확인할 수 없음";
  if (l.code === "take_rate") return percent(v, 1);
  if (l.code === "order_count") return `${v.toLocaleString("ko-KR")}건`;
  return krw(v);
}

export function VarianceSection({ tick }: { tick: number }) {
  const res = useResource("variance", () => finance.variance(), tick);
  return (
    <Panel id="variance" title="월간 변동 분석" meta={res.env?.meta}
      hint="이번 달 손익이 지난달과 왜 달라졌는지 숫자로 나눠 봅니다. 숫자와 원인 나누기는 코드가 계산하고, 문장도 정해진 틀로 씁니다.">
      <ResourceBody res={res}>
        {(v) => (
          <div className="space-y-4">
            <ul className="space-y-1 rounded-2xl bg-surface-2 px-4 py-3 text-sm text-ink-700">
              {v.summary.map((s, i) => <li key={i}>{plainText(s)}</li>)}
            </ul>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[600px] text-sm">
                <thead className="text-left text-xs text-ink-500">
                  <tr><th className="pb-1 font-medium">항목 ({v.month})</th><th className="pb-1 text-right font-medium">이번 달</th><th className="pb-1 text-right font-medium">지난달</th><th className="pb-1 text-right font-medium">변동</th><th className="pb-1 text-right font-medium">작년 같은 달</th></tr>
                </thead>
                <tbody>
                  {v.lines.map((l) => (
                    <tr key={l.code} className={`border-t border-ink-100 ${l.flag ? "bg-amber-50" : ""}`}>
                      <th scope="row" className="py-1.5 pr-3 text-left font-normal">{l.label}{l.flag && <span className="ml-1 text-xs text-amber-800">크게 움직임</span>}</th>
                      <td className="py-1.5 text-right tabular-nums">{valueOf(l, l.current)}</td>
                      <td className="py-1.5 text-right tabular-nums text-ink-500">{valueOf(l, l.previous)}</td>
                      <td className="py-1.5 text-right tabular-nums">
                        {l.diff == null ? "–" : l.code === "take_rate" ? `${l.diff > 0 ? "+" : ""}${(l.diff * 100).toFixed(1)}%p` : l.code === "order_count" ? `${l.diff > 0 ? "+" : ""}${l.diff.toLocaleString("ko-KR")}건` : signedKrw(l.diff)}
                        {l.pct != null && l.code !== "take_rate" && <span className="ml-1 text-xs text-ink-500">({l.pct > 0 ? "+" : ""}{(l.pct * 100).toFixed(0)}%)</span>}
                      </td>
                      <td className="py-1.5 text-right tabular-nums text-ink-500">{valueOf(l, l.last_year)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {v.drivers && (
              <p className="text-sm text-ink-700">
                수수료 매출 변동 {signedKrw(v.drivers.commission_change)} = 거래량 효과 {signedKrw(v.drivers.volume_effect)} + 수수료율·구성 효과 {signedKrw(v.drivers.rate_mix_effect)}
              </p>
            )}
            {v.categories.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[480px] text-sm">
                  <thead className="text-left text-xs text-ink-500">
                    <tr><th className="pb-1 font-medium">분류</th><th className="pb-1 text-right font-medium">총거래액 (지난달)</th><th className="pb-1 text-right font-medium">수수료 매출 (지난달)</th><th className="pb-1 text-right font-medium">수수료 변동</th></tr>
                  </thead>
                  <tbody>
                    {v.categories.map((c) => (
                      <tr key={c.category} className="border-t border-ink-100">
                        <td className="py-1.5">{c.category}</td>
                        <td className="py-1.5 text-right tabular-nums">{krw(c.gmv)} <span className="text-xs text-ink-500">({krw(c.gmv_prev)})</span></td>
                        <td className="py-1.5 text-right tabular-nums">{krw(c.commission)} <span className="text-xs text-ink-500">({krw(c.commission_prev)})</span></td>
                        <td className="py-1.5 text-right tabular-nums">{signedKrw(c.commission_diff)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}
