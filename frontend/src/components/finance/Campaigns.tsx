import { finance, krw, signedKrw, type BudgetDecision, type CampaignVerdict } from "@/lib/finance";
import { shortDateTime } from "@/lib/format";
import { AppliedRules, Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// ⑥ 캠페인 재무: 마케팅이 보낸 성과를 수수료 기준 손익으로 적고, 예산 요청을 판단한다.
// 쿠폰 발급과 환불은 판단하지 않는다. 끝난 쿠폰 금액을 비용에 적을 뿐이다.
// 숫자는 서버가 계산한 값 그대로다. 마케팅 연결 전에는 기록이 없어 비어 있다.

const VERDICT: Record<CampaignVerdict, { label: string; tone: string }> = {
  good: { label: "좋음", tone: TONE.ok },
  neutral: { label: "보통", tone: TONE.muted },
  bad: { label: "나쁨", tone: TONE.bad },
};
const DECISION: Record<BudgetDecision, { label: string; tone: string }> = {
  approved: { label: "승인", tone: TONE.ok },
  conditional: { label: "조건부 승인", tone: TONE.warn },
  rejected: { label: "거절", tone: TONE.bad },
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
                <div className="rounded-2xl bg-surface-2 px-3 py-2">
                  <dt className="text-sm text-ink-500">{v.budget.month} 캠페인 예산{v.budget.provisional ? " (임시값)" : ""}</dt>
                  <dd className="text-base font-semibold tabular-nums">{krw(v.budget.monthly_krw)}</dd>
                  <dd className="text-sm text-ink-500">{v.budget.basis}</dd>
                </div>
                <div className="rounded-2xl bg-surface-2 px-3 py-2">
                  <dt className="text-sm text-ink-500">쓴 돈과 진행 중인 돈</dt>
                  <dd className="text-base font-semibold tabular-nums">{v.budget.committed_krw === null ? "확인할 수 없음" : krw(v.budget.committed_krw)}</dd>
                </div>
                <div className="rounded-2xl bg-surface-2 px-3 py-2">
                  <dt className="text-sm text-ink-500">손익분기 ROAS</dt>
                  <dd className="text-base font-semibold tabular-nums">{v.break_even_roas === null ? "-" : `${v.break_even_roas}배`}</dd>
                </div>
              </dl>
              {v.running.length > 0 && (
                <p className="text-sm text-ink-700">결과를 기다리는 캠페인: {v.running.map((r) => `${r.campaign_id} ${krw(r.active_krw)}`).join(", ")}</p>
              )}
              {v.campaigns.length === 0 ? <Empty>아직 기록된 캠페인이 없습니다. 마케팅 AI 화면에서 캠페인을 시작하고 결과를 저장하면 여기에 적힙니다.</Empty> : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[840px] text-sm">
                    <thead className="text-left text-ink-500">
                      <tr>{["캠페인", "채널", "광고비", "판매액", "매출", "손익", "ROI", "재구매 포함 (참고)", "효율"].map((h) => <th key={h} className="pb-1 font-medium">{h}</th>)}</tr>
                    </thead>
                    <tbody>
                      {v.campaigns.map((c) => (
                        <tr key={c.campaign_id} className="border-t border-ink-100" title={`출처: ${c.source}`}>
                          <td className="py-1.5">{c.campaign_name}</td>
                          <td className="py-1.5">{c.channel}</td>
                          <td className="py-1.5 tabular-nums">{krw(c.spend_krw)}</td>
                          <td className="py-1.5 tabular-nums">{c.gmv_krw === null ? "자료 없음" : krw(c.gmv_krw)}</td>
                          <td className="py-1.5 tabular-nums">{c.revenue_krw === null ? "-" : krw(c.revenue_krw)}</td>
                          <td className="py-1.5 tabular-nums">{c.profit_krw === null ? "-" : signedKrw(c.profit_krw)}</td>
                          <td className="py-1.5 tabular-nums">{pct(c.roi)}</td>
                          <td className="py-1.5 tabular-nums">{c.ltv_profit_krw === null ? "자료 부족" : signedKrw(c.ltv_profit_krw)}</td>
                          <td className="py-1.5">{c.verdict ? <Chip label={VERDICT[c.verdict].label} tone={VERDICT[c.verdict].tone} /> : <Chip label="판단 대기" tone={TONE.muted} />}</td>
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

      <Panel id="campaign-reviews" title="예산 판단 (재무 브레이크)"
        hint="마케팅의 캠페인 예산 요청을 남은 예산, 연속 적자, 이달 손실, 현금 흐름, 실패 이력으로 판단합니다. 거절하면 안전 장치가 집행을 막습니다. 쿠폰과 환불은 판단하지 않습니다.">
        <ResourceBody res={view}>
          {(v) => v.reviews.length === 0 ? <Empty>아직 예산 요청이 없습니다.</Empty> : (
            <ul className="space-y-3">
              {v.reviews.slice(0, 10).map((r) => (
                <li key={r.review_id} className="rounded-2xl border border-ink-100 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <b className="text-ink-900">{r.campaign_name}</b>
                    <Chip label={DECISION[r.decision].label} tone={DECISION[r.decision].tone} />
                    <span className="text-sm tabular-nums text-ink-700">요청 {krw(r.requested_krw)} → 한도 {krw(r.approved_limit_krw)}</span>
                    <span className="ml-auto text-sm text-ink-500">{shortDateTime(r.reviewed_at)}</span>
                  </div>
                  <ul className="mt-1 list-disc pl-5 text-sm text-ink-700">{r.reasons.map((why) => <li key={why}>{why}</li>)}</ul>
                  <p className="mt-1 text-sm text-ink-700">{r.recommendation}</p>
                  <div className="mt-2"><AppliedRules rules={r.applied_rules} /></div>
                </li>
              ))}
            </ul>
          )}
        </ResourceBody>
      </Panel>

      <Panel id="campaign-lessons" title="실패 교훈" hint="적자 캠페인은 교훈으로 남아 그 채널의 다음 예산 한도를 줄입니다. 효율 좋음이 나오면 풀립니다.">
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
                    {item.title}
                    <Chip label={item.severity === "warn" ? "주의" : "참고"} tone={item.severity === "warn" ? TONE.warn : TONE.info} />
                  </p>
                  <p className="mt-1 text-sm text-ink-700">{item.message}</p>
                  <ul className="mt-1 list-disc pl-5 text-sm text-ink-700">{item.evidence.map((e, n) => <li key={`${e.label}-${n}`}>{e.label}: {e.value ?? "자료 없음"}</li>)}</ul>
                  <p className="mt-1 text-sm text-ink-700">권고: {item.recommendation}</p>
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
