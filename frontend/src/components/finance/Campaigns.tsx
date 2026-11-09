import { finance, krw, signedKrw, type BudgetDecision, type CampaignVerdict } from "@/lib/finance";
import { shortDateTime } from "@/lib/format";
import { AppliedRules, Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// ⑥ 캠페인 재무: 마케팅이 보낸 성과를 수수료 기준 손익으로 적고, 예산 요청에 의견을 낸다(승인·반려는 하지 않는다).
// 쿠폰 발급과 환불은 판단하지 않는다. 끝난 쿠폰 금액을 비용에 적을 뿐이다.
// 숫자는 서버가 계산한 값 그대로다. 마케팅 연결 전에는 기록이 없어 비어 있다.

const VERDICT: Record<CampaignVerdict, { label: string; tone: string }> = {
  good: { label: "좋음", tone: TONE.ok },
  neutral: { label: "보통", tone: TONE.muted },
  bad: { label: "나쁨", tone: TONE.bad },
};
const DECISION: Record<BudgetDecision, { label: string; tone: string }> = {
  approved: { label: "권장", tone: TONE.ok },
  conditional: { label: "줄여서 권장", tone: TONE.warn },
  rejected: { label: "권하지 않음", tone: TONE.bad },
};
const pct = (v: number | null) => (v === null ? "-" : `${(v * 100).toFixed(1)}%`);

export function CampaignsSection({ tick }: { tick: number }) {
  const view = useResource("campaigns", () => finance.campaigns(), tick);
  const insights = useResource("finance-insights", () => finance.insights(), tick);
  return (
    <div className="space-y-4">
      <Panel id="campaign-pnl" title="캠페인 손익" meta={view.env?.meta}
        hint="회사 매출은 판매액이 아니라 수수료입니다. 손익 = 판매액의 수수료 − 광고비 − 회사 부담 쿠폰 − PG 수수료. 효율 판단은 이 단기 손익으로 하고, 재구매 포함 손익은 참고로만 보여 줍니다.">
        <ResourceBody res={view}>
          {(v) => (
            <div className="space-y-3">
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <div className="rounded-2xl border border-ink-100 px-4 py-3">
                  <dt className="text-sm text-ink-500">{v.budget.month} 캠페인 예산{v.budget.provisional ? " (임시값)" : ""}</dt>
                  <dd className="mt-1 text-xl font-semibold tabular-nums text-ink-900">{krw(v.budget.monthly_krw)}</dd>
                  <dd className="text-sm text-ink-500">{v.budget.basis}</dd>
                </div>
                <div className="rounded-2xl border border-ink-100 px-4 py-3">
                  <dt className="text-sm text-ink-500">쓴 돈과 진행 중인 돈</dt>
                  <dd className="mt-1 text-xl font-semibold tabular-nums text-ink-900">{v.budget.committed_krw === null ? "확인할 수 없음" : krw(v.budget.committed_krw)}</dd>
                </div>
                <div className="rounded-2xl border border-ink-100 px-4 py-3">
                  <dt className="text-sm text-ink-500">손익분기 ROAS</dt>
                  <dd className="mt-1 text-xl font-semibold tabular-nums text-ink-900">{v.break_even_roas === null ? "-" : `${v.break_even_roas}배`}</dd>
                </div>
              </dl>
              {v.running.length > 0 && (
                <p className="text-sm text-ink-700">결과를 기다리는 캠페인: {v.running.map((r) => `${r.campaign_id} ${krw(r.active_krw)}`).join(", ")}</p>
              )}
              {v.campaigns.length === 0 ? <Empty>아직 기록된 캠페인이 없습니다. 마케팅 AI 화면에서 캠페인을 시작하고 결과를 저장하면 여기에 적힙니다.</Empty> : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[920px] text-sm">
                    <thead className="text-left text-ink-500">
                      {/* 숫자 칸은 오른쪽 정렬, 손익(가장 중요한 숫자)은 진하게 */}
                      <tr>{(["캠페인", "채널", "광고비", "판매액", "증분 주문", "매출", "손익", "ROI", "재구매 포함 (참고)", "효율"] as const).map((h, i) => <th key={h} className={`pb-2 font-medium ${i >= 2 && i <= 8 ? "text-right" : ""} ${i === 9 ? "pl-4" : ""}`}>{h}</th>)}</tr>
                    </thead>
                    <tbody>
                      {v.campaigns.map((c) => (
                        <tr key={c.campaign_id} className="border-t border-ink-100" title={[`출처: ${c.source}`, c.effect_generated_at ? `측정 시각: ${c.effect_generated_at}` : "", c.measured_gmv_krw != null && c.measured_gmv_krw < 0 ? `측정 증분 판매액: ${c.measured_gmv_krw}원` : "", c.measured_orders != null && c.measured_orders < 0 ? `측정 증분 주문: ${c.measured_orders}건` : ""].filter(Boolean).join("\n")}>
                          <td className="py-2 font-medium text-ink-900">{c.campaign_name}</td>
                          <td className="py-2 text-ink-700">{c.channel}</td>
                          <td className="py-2 text-right tabular-nums">{krw(c.spend_krw)}</td>
                          <td className="py-2 text-right tabular-nums">{c.gmv_krw === null ? "자료 없음" : krw(c.gmv_krw)}</td>
                          <td className="py-2 text-right tabular-nums">{c.orders == null ? "자료 없음" : `${c.orders.toLocaleString("ko-KR")}건`}</td>
                          <td className="py-2 text-right tabular-nums">{c.revenue_krw === null ? "-" : krw(c.revenue_krw)}</td>
                          <td className={`py-2 text-right font-semibold tabular-nums ${c.profit_krw != null && c.profit_krw < 0 ? "text-red-700" : "text-ink-900"}`}>{c.profit_krw === null ? "-" : signedKrw(c.profit_krw)}</td>
                          <td className="py-2 text-right tabular-nums">{pct(c.roi)}</td>
                          <td className="py-2 text-right tabular-nums text-ink-500">{c.ltv_profit_krw === null ? "자료 부족" : signedKrw(c.ltv_profit_krw)}</td>
                          <td className="py-2 pl-4">{c.verdict ? <Chip label={VERDICT[c.verdict].label} tone={VERDICT[c.verdict].tone} /> : <Chip label="판단 대기" tone={TONE.muted} />}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {v.campaigns[0] && <AppliedRules rules={v.campaigns[0].applied_rules} />}
            </div>
          )}
        </ResourceBody>
      </Panel>

      <Panel id="campaign-reviews" title="예산 의견 (재무)"
        hint="재무는 비용을 승인하거나 반려하지 않습니다. 마케팅의 캠페인 예산 요청에 남은 예산, 연속 적자, 이달 손실, 현금 흐름, 실패 이력을 근거로 의견만 냅니다. 집행 여부는 안전 장치의 금액 기준(건당·하루 합계)과 사람이 정합니다. 쿠폰과 환불은 보지 않습니다.">
        <ResourceBody res={view}>
          {(v) => {
            if (v.reviews.length === 0) return <Empty>아직 예산 요청이 없습니다.</Empty>;
            // 같은 캠페인은 가장 최근 의견 한 줄만 보이고, 앞선 의견은 건수로만 (reviews는 최근 순)
            const latest = new Map<string, { r: (typeof v.reviews)[number]; earlier: number }>();
            for (const r of v.reviews) {
              const seen = latest.get(r.campaign_id);
              if (seen) seen.earlier += 1; else latest.set(r.campaign_id, { r, earlier: 0 });
            }
            return (
              <ul className="divide-y divide-ink-100">
                {[...latest.values()].slice(0, 10).map(({ r, earlier }) => (
                  <li key={r.review_id} className="grid gap-x-4 gap-y-1 py-3 first:pt-0 md:grid-cols-[minmax(10rem,14rem)_minmax(9rem,12rem)_1fr_auto]">
                    <div className="flex flex-wrap items-center gap-2">
                      <b className="text-ink-900">{r.campaign_name}</b>
                      <Chip label={DECISION[r.decision].label} tone={DECISION[r.decision].tone} />
                    </div>
                    <p className="text-sm tabular-nums text-ink-700">
                      {krw(r.requested_krw)} <span className="text-ink-500">→</span> <b className="font-medium text-ink-900">{krw(r.approved_limit_krw)}</b>
                      <span className="block text-xs text-ink-500">요청 → 재무 권장</span>
                    </p>
                    <div className="min-w-0 text-sm">
                      {r.decision !== "approved" && <p className="text-ink-700">{r.reasons.join(" · ")}</p>}
                      <details className="text-ink-500">
                        <summary className="cursor-pointer select-none hover:text-ink-700">근거 {r.applied_rules.length}개{r.decision === "approved" ? " · 모든 기준 통과" : ""}</summary>
                        <p className="mt-1 text-ink-700">{r.recommendation}</p>
                        <div className="mt-2"><AppliedRules rules={r.applied_rules} /></div>
                      </details>
                    </div>
                    <p className="text-sm tabular-nums text-ink-500 md:text-right">
                      {shortDateTime(r.reviewed_at)}
                      {earlier > 0 && <span className="block text-xs">앞선 의견 {earlier}건</span>}
                    </p>
                  </li>
                ))}
              </ul>
            );
          }}
        </ResourceBody>
      </Panel>

      <Panel id="campaign-lessons" title="실패 교훈" hint="적자 캠페인은 교훈으로 남아 그 채널의 다음 권장 금액을 줄입니다. 효율 좋음이 나오면 풀립니다.">
        <ResourceBody res={view}>
          {(v) => v.lessons.length === 0 ? <Empty>남은 교훈이 없습니다.</Empty> : (
            <ul className="space-y-1 text-sm text-ink-700">
              {v.lessons.map((x) => (
                <li key={x.lesson_id} className="flex flex-wrap items-center gap-2">
                  <Chip label={x.status === "open" ? "적용 중" : "해제됨"} tone={x.status === "open" ? TONE.warn : TONE.muted} />
                  {x.channel} · {x.campaign_name} · 손익 {signedKrw(x.profit_krw)} · ROI {pct(x.roi)}
                </li>
              ))}
            </ul>
          )}
        </ResourceBody>
      </Panel>

      <Panel id="campaign-insights" title="재무 인사이트" meta={insights.env?.meta} hint="한 건씩 보면 놓치기 쉬운 패턴입니다. 근거 숫자를 함께 보여 줍니다.">
        <ResourceBody res={insights}>
          {(list) => list.length === 0 ? <Empty>지금 알릴 패턴이 없습니다.</Empty> : (
            <ul className="space-y-3">
              {list.map((item) => (
                <li key={item.insight_id} className="rounded-2xl border border-ink-100 p-4">
                  <p className="flex flex-wrap items-center gap-2 font-medium text-ink-900">
                    <Chip label={item.severity === "warn" ? "주의" : "참고"} tone={item.severity === "warn" ? TONE.warn : TONE.info} />
                    {item.title}
                  </p>
                  {/* 근거 숫자를 먼저: 이름표 왼쪽, 값 오른쪽 */}
                  <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
                    {item.evidence.map((e, n) => (
                      <div key={`${e.label}-${n}`} className="flex items-baseline justify-between gap-3 border-b border-ink-100 py-1">
                        <dt className="text-ink-500">{e.label}</dt>
                        <dd className="tabular-nums font-medium text-ink-900">{e.value ?? "자료 없음"}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="mt-3 text-sm text-ink-500">{item.message}</p>
                  <p className="mt-2 rounded-xl bg-surface-2 px-3 py-2 text-sm text-ink-900"><span className="font-medium">권고</span> · {item.recommendation}</p>
                  <div className="mt-2"><AppliedRules rules={item.applied_rules} /></div>
                </li>
              ))}
            </ul>
          )}
        </ResourceBody>
      </Panel>

      <Panel id="campaign-opinions" title="총괄에 낸 의견" hint="총괄 AI에 A2A로 보냅니다. 총괄 쪽 받는 기능이 준비되기 전에는 전달 대기로 남고 자동으로 다시 보냅니다.">
        <ResourceBody res={view}>
          {(v) => v.opinions.length === 0 ? <Empty>아직 낸 의견이 없습니다.</Empty> : (
            <ul className="space-y-2 text-sm text-ink-700">
              {v.opinions.slice(0, 20).map((row) => (
                <li key={row.opinion.opinion_id}>
                  <p className="flex flex-wrap items-center gap-2">
                    <Chip label={row.status === "delivered" ? "전달됨" : "전달 대기"} tone={row.status === "delivered" ? TONE.ok : TONE.warn} />
                    {row.opinion.campaign_name ?? "재무 인사이트"} · {shortDateTime(row.opinion.submitted_at)}
                  </p>
                  <p>{row.opinion.recommendation}</p>
                </li>
              ))}
            </ul>
          )}
        </ResourceBody>
      </Panel>
    </div>
  );
}
