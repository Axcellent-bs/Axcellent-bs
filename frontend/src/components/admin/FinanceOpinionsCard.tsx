import { useCallback, useEffect, useState } from "react";
import { krw, signedKrw } from "@/lib/finance";
import { shortDateTime } from "@/lib/format";
import { Link } from "@/lib/nav";
import { orchestrator, type FinanceOpinionRow } from "@/lib/orchestrator";

// 총괄 화면의 "재무가 낸 의견": 재무 AI가 A2A(receive_finance_opinion)로 보낸 의견을 총괄이 받은 그대로 보여 준다.
// 총괄은 보관하고, 기획이 알아야 하는 의견(콘텐츠 제작비 재배분)만 기획에 그대로 넘긴다(relay). 숫자는 재무가 보낸 값 그대로다.
// 원본과 자세한 근거는 재무 화면 캠페인 예산에 있다.

const KIND: Record<FinanceOpinionRow["opinion"]["kind"], { label: string; tone: string }> = {
  campaign_result: { label: "캠페인 손익", tone: "bg-sky-50 text-sky-800" },
  budget_decision: { label: "예산 의견", tone: "bg-amber-50 text-amber-800" },
  insight: { label: "재무 인사이트", tone: "bg-violet-50 text-violet-800" },
};
const VERDICT_KO: Record<string, string> = { good: "효율 좋음", neutral: "효율 보통", bad: "효율 나쁨" };
const SHOWN = 10;

export function FinanceOpinionsCard({ tick = 0 }: { tick?: number }) {
  const [rows, setRows] = useState<FinanceOpinionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setRows(await orchestrator.financeOpinions());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "재무 의견을 불러오지 못했습니다");
    }
  }, []);
  useEffect(() => { void load(); }, [load, tick]);

  return (
    <section className="card space-y-3 p-6" aria-labelledby="orch-finance">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="orch-finance" className="text-lg font-semibold">재무가 낸 의견</h2>
        {rows && <span className="text-sm tabular-nums text-ink-500">받은 의견 {rows.length}건</span>}
        <Link href="/finance/daily#campaigns" className="ml-auto text-sm font-medium text-brand-700 underline underline-offset-2">재무 화면 캠페인 예산에서 근거 보기 →</Link>
      </div>
      <p className="text-sm text-ink-500">재무 AI가 캠페인 결과, 줄이거나 권하지 않은 예산, 놓치기 쉬운 패턴을 총괄 AI에 알립니다. 콘텐츠 제작비 재배분처럼 기획이 알아야 하는 의견은 총괄 AI가 <Link href="/planning/proposals" className="font-medium text-brand-700 underline">기획 AI 기획 제안</Link>으로 넘깁니다. 집행 여부는 사람과 안전 규칙이 정합니다.</p>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {rows === null && !error ? <p className="text-sm text-ink-500">불러오는 중…</p> : rows && rows.length === 0 ? (
        <p className="text-sm text-ink-500">아직 받은 의견이 없습니다. 마케팅이 캠페인 예산을 묻거나 결과를 저장하면 재무가 의견을 보냅니다.</p>
      ) : rows && (
        <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
          {rows.slice(0, SHOWN).map(({ opinion: o, received_at, relay }) => (
            <li key={o.opinion_id} className="space-y-1 px-4 py-3 text-sm">
              <p className="flex flex-wrap items-center gap-2">
                <span className={`chip ${KIND[o.kind].tone}`}>{KIND[o.kind].label}</span>
                <b className="font-medium text-ink-900">{o.campaign_name ?? (o.kind === "insight" ? "재무 인사이트" : o.campaign_id ?? "-")}</b>
                {o.channel && <span className="text-ink-500">{o.channel}</span>}
                {o.verdict && o.kind !== "insight" && <span className="text-ink-700">{VERDICT_KO[o.verdict] ?? o.verdict}</span>}
                <span className="ml-auto tabular-nums text-ink-500">{shortDateTime(received_at ?? o.submitted_at)}</span>
              </p>
              {(o.spend_krw !== null || o.profit_krw !== null) && (
                <p className="tabular-nums text-ink-700">
                  {o.spend_krw !== null && <>{o.kind === "budget_decision" ? "요청" : "광고비"} {krw(o.spend_krw)}</>}
                  {o.profit_krw !== null && <> · 손익 <b className={`font-medium ${o.profit_krw < 0 ? "text-red-700" : "text-ink-900"}`}>{signedKrw(o.profit_krw)}</b></>}
                  {o.roi !== null && <> · ROI {(o.roi * 100).toFixed(1)}%</>}
                </p>
              )}
              <p className="text-ink-700">{o.recommendation}</p>
              {relay && (
                <p className="text-ink-500">
                  {relay.status === "delivered"
                    ? <>기획 AI에 넘김{relay.proposal_id && <> · <Link href="/planning/proposals" className="font-medium text-brand-700 underline">기획 제안 {relay.proposal_id}</Link>{relay.outcome === "duplicate" ? " (이미 받음)" : ""}</>}</>
                    : relay.status === "pending"
                      ? <>기획 AI에 넘기는 중{relay.problem ? ` · 아직 못 넘김: ${relay.problem} (다시 보냅니다)` : ""}</>
                      : <>기획 AI에 넘기지 못함: {relay.problem}</>}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      {rows && rows.length > SHOWN && <p className="text-sm text-ink-500">최근 {SHOWN}건만 보입니다.</p>}
    </section>
  );
}
