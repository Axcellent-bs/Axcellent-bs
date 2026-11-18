import { finance, krw, signedKrw, type BudgetDecision, type CampaignVerdict } from "@/lib/finance";
import { shortDateTime } from "@/lib/format";
import { Link } from "@/lib/nav";
import { AppliedRules, Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// ⑥ 캠페인 예산: 마케팅이 보낸 성과를 수수료 기준 손익으로 적고, 예산 요청에 의견을 낸다(승인·반려는 하지 않는다).
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
// 칸마다 숫자가 어디서 오고 어디로 가는지. 기록이 비어 있을 때도 어느 화면에서 채워지는지 보이게 한다
type Wire = { label: string; href: string };
const W = {
  marketing: { label: "마케팅 AI · 캠페인", href: "/marketing/campaign" },
  dataPerf: { label: "데이터 AI · 트렌드 > 성과", href: "/data/trend#result" },
  planning: { label: "기획 AI · 기획 제안", href: "/planning/proposals" },
  orchestrator: { label: "총괄 AI · 재무가 낸 의견", href: "/orchestrator#orch-finance" },
  safety: { label: "안전 처리 로그 · 사람 결정 대기", href: "/safety" },
} satisfies Record<string, Wire>;
// ask: 상대가 재무에 묻고 재무 답이 같은 곳으로 돌아가는 칸. "받는 곳 → 보내는 곳"에 같은 이름이 두 번 나오지 않게 한 줄로 쓴다
function Wiring({ from, to, ask }: { from?: Wire[]; to?: Wire[]; ask?: { who: Wire; back: string } }) {
  const links = (xs: Wire[]) => xs.map((x, i) => (
    <span key={x.href}>{i > 0 && " · "}<Link href={x.href} className="font-medium text-brand-700 underline">{x.label}</Link></span>
  ));
  if (ask) {
    return (
      <span className="mt-1 block text-xs text-ink-500">
        묻는 곳: {links([ask.who])} <span aria-hidden>⇄</span> 재무 답: {ask.back}
      </span>
    );
  }
  return (
    <span className="mt-1 block text-xs text-ink-500">
      받는 곳: {links(from ?? [])}{to && to.length > 0 && <> <span aria-hidden>→</span> 보내는 곳: {links(to)}</>}
    </span>
  );
}

// 마케팅 자동 전송(campaign_auto.AUTO_SOURCE). 광고비가 승인 예산이라 손익이 잠정이다. 값을 바꾸면 마케팅과 맞춘다
const AUTO_BUDGET_SOURCE = "marketing.auto.approved_budget";

const pct = (v: number | null) => (v === null ? "-" : `${(v * 100).toFixed(1)}%`);

export function CampaignsSection({ tick }: { tick: number }) {
  const view = useResource("campaigns", () => finance.campaigns(), tick);
  const insights = useResource("finance-insights", () => finance.insights(), tick);
  return (
    <div className="space-y-4">
      <Panel id="campaign-pnl" title="캠페인 손익" meta={view.env?.meta}
        hint={<>회사 매출은 판매액이 아니라 수수료입니다. 손익 = 판매액의 수수료 − 광고비 − 제작비 − 회사 부담 쿠폰 − PG 수수료. 효율 판단은 이 단기 손익으로 하고, 재구매 포함 손익은 참고로만 보여 줍니다. ROI(투자 대비 수익률)는 손익 ÷ 쓴 돈으로, 100만 원을 써서 20만 원이 남으면 20%입니다. ROAS는 광고비 1원당 판매액입니다.<Wiring from={[W.marketing, W.dataPerf]} /></>}>
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
                  <dd className="text-sm text-ink-500">{v.break_even_roas === null ? "광고비 1원으로 판매액이 얼마나 나와야 손해가 아닌지입니다." : `광고비의 ${v.break_even_roas}배는 팔아야 손해가 아닙니다. 회사 몫은 판매액의 수수료에서 결제 수수료를 뺀 만큼이라 이 배수가 높습니다.`}</dd>
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
                          <td className="py-2 font-medium text-ink-900">{c.campaign_name}{c.source === AUTO_BUDGET_SOURCE && <> <span title="마케팅이 캠페인 종료 뒤 자동으로 보낸 결과입니다. 광고비가 실제 집행액이 아니라 승인 예산이라 손익이 실제와 다를 수 있습니다. 사람이 실제 집행액으로 다시 저장하면 이 표시가 사라집니다."><Chip label="예산 기준(잠정)" tone={TONE.warn} /></span></>}</td>
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
              {v.campaigns[0] && <AppliedRules rules={v.campaigns[0].applied_rules} plain />}
            </div>
          )}
        </ResourceBody>
      </Panel>

      <Panel id="campaign-categories" title="콘텐츠 카테고리별 제작비"
        hint={<>쇼츠·릴스를 카테고리(예: 데이터 AI 숏츠 형식인 챌린지·상황극·스케치, 또는 사람이 정한 아재개그)별로 묶어 조회당 제작비를 봅니다. 조회수는 데이터 AI가 잰 게시 7일 값만 쓰고, 7일이 안 된 영상은 측정 중으로 셉니다. 다음 회차 제안은 재무 의견이고, 기획 AI가 영상 기획안을 넘기기 전에 물어 갑니다. 비중은 기획·마케팅과 사람이 정합니다.<Wiring from={[W.marketing, W.dataPerf]} to={[W.planning]} /></>}>
        <ResourceBody res={view}>
          {(v) => {
            const rows = v.categories ?? [];
            if (rows.length === 0) return <Empty>카테고리를 적은 캠페인이 아직 없습니다. 마케팅이 결과에 카테고리·제작비를, 데이터 AI가 7일 조회수를 보내면 여기에 모입니다.</Empty>;
            const shift = (insights.env?.data ?? []).find((i) => i.kind === "budget_shift")?.shift;
            const next = (row: (typeof rows)[number]) => {
              if (!shift || row.latest_production_krw === null) return "-";
              if (row.content_category === shift.to) return `${krw(row.latest_production_krw + shift.amount_krw)} (+${krw(shift.amount_krw)})`;
              if (row.content_category === shift.from) return `${krw(row.latest_production_krw - shift.amount_krw)} (−${krw(shift.amount_krw)})`;
              return krw(row.latest_production_krw);
            };
            return (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-sm">
                  <thead className="text-left text-ink-500">
                    <tr>{(["카테고리", "잰 캠페인", "제작비 합", "7일 조회수", "조회당 제작비", "측정 중", "다음 회차 제작비 (재무 의견)"] as const).map((h, i) => <th key={h} className={`pb-2 font-medium ${i >= 1 ? "text-right" : ""}`}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.content_category} className="border-t border-ink-100">
                        <td className="py-2 font-medium text-ink-900">{row.content_category}</td>
                        <td className="py-2 text-right tabular-nums">{row.campaigns.length}개</td>
                        <td className="py-2 text-right tabular-nums">{row.campaigns.length ? krw(row.production_cost_krw) : "-"}</td>
                        <td className="py-2 text-right tabular-nums">{row.campaigns.length ? `${row.views.toLocaleString("ko-KR")}회` : "-"}</td>
                        <td className="py-2 text-right font-semibold tabular-nums text-ink-900">{row.cost_per_view === null ? "측정 전" : `${row.cost_per_view.toLocaleString("ko-KR")}원`}</td>
                        <td className="py-2 text-right tabular-nums text-ink-500">{row.measuring ? `${row.measuring}개` : "-"}</td>
                        <td className="py-2 text-right tabular-nums">{next(row)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          }}
        </ResourceBody>
      </Panel>

      <Panel id="campaign-planning" title="기획 AI가 물어본 제작비" hint={<>기획 AI가 영상 기획안을 마케팅에 넘기기 직전에 그 카테고리의 최신 제작비를 재무에 직접 묻습니다. 재배분 알림은 총괄 AI를 거쳐 가고, 넘기기 직전 숫자 확인은 여기서 직접 합니다. 재무는 위 "콘텐츠 카테고리별 제작비" 숫자로 답하고, 기획은 그 숫자를 기획안 근거에 붙여 마케팅에 넘깁니다. 조회당 제작비가 가장 싼 카테고리가 가장 비싼 카테고리보다 2배 이상 싸면, 다음 회차에 싼 쪽 제작비를 30% 올리고 그만큼 비싼 쪽에서 빼자고 답합니다(전체 제작비는 그대로). 정하는 건 기획·마케팅과 사람입니다.<Wiring ask={{ who: W.planning, back: "같은 기획 제안의 근거에 붙어 마케팅으로 넘어갑니다" }} /></>}>
        <ResourceBody res={view}>
          {(v) => {
            const asks = v.budget_asks ?? [];
            const old = v.planning_insights ?? [];
            const ASK: Record<string, { label: string; tone: string }> = {
              advice: { label: "답함", tone: TONE.ok }, measuring: { label: "재는 중", tone: TONE.muted }, no_data: { label: "기록 없음", tone: TONE.muted },
            };
            return (
              <div className="space-y-3">
                {asks.length === 0 ? <Empty>아직 기획 AI가 물어본 적이 없습니다. 기획 AI에서 카테고리가 있는 영상 기획안을 확정해 마케팅에 넘기면 생깁니다.</Empty> : (
                  <ul className="divide-y divide-ink-100 text-sm">
                    {asks.slice(0, 10).map((a) => (
                      <li key={`${a.asked_at}-${a.proposal_id}`} className="grid gap-x-4 gap-y-1 py-2 first:pt-0 md:grid-cols-[minmax(9rem,12rem)_1fr_auto]">
                        <span className="flex flex-wrap items-center gap-2"><Chip label={ASK[a.status].label} tone={ASK[a.status].tone} /><b className="text-ink-900">{a.content_category ?? "전체"}</b></span>
                        <span className="text-ink-700">{a.summary}</span>
                        <span className="tabular-nums text-ink-500 md:text-right">{a.proposal_id ?? "-"} · {shortDateTime(a.asked_at)}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {old.length > 0 && (
                  <details className="text-sm text-ink-500">
                    <summary className="cursor-pointer select-none hover:text-ink-700">재무가 기획에 직접 보내던 예전 재배분 의견 {old.length}건 (11/18부터는 총괄 AI가 넘깁니다)</summary>
                    <ul className="mt-2 space-y-1 text-ink-700">
                      {old.slice(0, 10).map((row) => (
                        <li key={row.insight.insight_id} className="flex flex-wrap items-center gap-2">
                          <Chip label={row.status === "delivered" ? "전달됨" : "전달 대기"} tone={row.status === "delivered" ? TONE.ok : TONE.warn} />
                          {row.insight.title}
                          {row.proposal_id && <span className="text-ink-500">· 기획 제안 {row.proposal_id}</span>}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            );
          }}
        </ResourceBody>
      </Panel>

      <Panel id="campaign-reviews" title="예산 의견 (재무)"
        hint={<>재무는 비용을 승인하거나 반려하지 않습니다. 마케팅의 캠페인 예산 요청에 남은 예산, 연속 적자, 이달 손실, 현금 흐름, 실패 이력을 근거로 의견만 냅니다. 집행 여부는 안전 장치의 금액 기준(건당·하루 합계)과 사람이 정합니다. 쿠폰과 환불은 보지 않습니다.<Wiring from={[W.marketing]} to={[W.marketing, W.safety]} /></>}>
        <ResourceBody res={view}>
          {(v) => {
            if (v.reviews.length === 0) return <Empty>아직 예산 요청이 없습니다. 마케팅 AI 화면에서 캠페인 예산을 요청하면 재무 의견이 여기에 적힙니다.</Empty>;
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
                        <div className="mt-2"><AppliedRules rules={r.applied_rules} plain /></div>
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

      <Panel id="campaign-opinions" title="총괄 AI에 낸 의견" hint={<>재무가 총괄 AI에 보낸 의견입니다. 세 경우에 보냅니다: 위 예산 의견이 "줄여서 권장"·"권하지 않음"일 때, 캠페인 결과의 효율(좋음·보통·나쁨)이 정해지거나 바뀔 때, 아래 재무 인사이트가 새로 생길 때. 총괄 AI 화면의 "재무가 낸 의견" 칸에 그대로 보입니다. 콘텐츠 제작비 재배분 의견에는 기획 AI가 쓸 내용(카테고리·조회당 제작비 근거)까지 다 실어 보내고, 총괄 AI가 그걸 기획 AI에 넘겨 기획 제안이 됩니다. 못 보내면 전달 대기로 남고 자동으로 다시 보냅니다.<Wiring from={[W.marketing, W.dataPerf]} to={[W.orchestrator, W.planning]} /></>}>
        <ResourceBody res={view}>
          {(v) => v.opinions.length === 0 ? <Empty>아직 낸 의견이 없습니다. 마케팅이 캠페인 예산을 요청하거나 결과를 보내면 생깁니다.</Empty> : (
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
                  <div className="mt-2"><AppliedRules rules={item.applied_rules} plain /></div>
                </li>
              ))}
            </ul>
          )}
        </ResourceBody>
      </Panel>
    </div>
  );
}
