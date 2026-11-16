import { Navigate } from "react-router";
import { Loading } from "@/components/admin/bits";
import { PreparingChip } from "@/components/Preparing";
import { FinanceOpinionsCard } from "@/components/admin/FinanceOpinionsCard";
import { SupportOverviewCard } from "@/components/admin/SupportOverviewCard";
import { clock, incidentStatus } from "@/lib/adminRelay";
import { incidentTitle } from "@/lib/agentChat";
import { ago } from "@/lib/format";
import { useIncidents } from "@/lib/incidents";
import { Link, useSearchParams } from "@/lib/nav";
import { CompanyName } from "@/demo/CompanyName";

// 총괄 개요: 사람 승인을 기다리는 일, 종류별 이슈 건수, 장애가 아닌 이슈(문의 처리 결과), 재무가 낸 의견.
// 이슈 목록·상세와 기록은 아카이브 AI의 장애 기록 화면에서 본다. 이 화면은 읽기 전용이다.
const isInquiry = (kind: string) => kind === "support_resolved";

export default function OrchestratorPage() {
  const ctx = useSearchParams().get("ctx");
  const { incidents, error, updatedAt, reload } = useIncidents();

  // 예전 주소(/orchestrator?ctx=…)로 들어오면 장애 기록의 그 이슈로 보낸다
  if (ctx) return <Navigate to={`/archive/incidents?ctx=${encodeURIComponent(ctx)}`} replace />;
  if (!incidents) return <Loading error={error} what="총괄 이슈를 불러오는 중…" />;

  const holds = incidents.reduce((n, i) => n + i.pending_holds.length, 0);
  const faults = incidents.filter((i) => !isInquiry(i.kind));
  const inquiries = incidents.filter((i) => isInquiry(i.kind));
  const tone = holds ? "text-amber-800" : "text-emerald-800";

  return (
    <div className="space-y-8 px-8 pb-12 pt-2">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">총괄</h1>
          <p className="mt-1 text-sm text-ink-500"><CompanyName />에서 문제가 생기면 총괄 AI가 이슈 하나로 묶고, 부서에 일을 나눠 맡깁니다. 사람 승인이 필요한 일만 사람에게 올립니다. 이 화면은 보기만 할 수 있습니다.</p>
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs">
          <button className="btn-ghost" onClick={() => void reload()}>새로고침{updatedAt ? ` · ${clock(updatedAt.toISOString())}` : ""}</button>
        </div>
      </header>

      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">{error}</p>}

      <section aria-labelledby="orch-summary" className={`rounded-[28px] p-6 ring-1 ${holds ? "bg-amber-50 ring-amber-100" : "bg-emerald-50 ring-emerald-100"}`}>
        <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
          <div>
            <h2 id="orch-summary" className={`text-sm font-semibold ${tone}`}>사람 승인을 기다리는 일</h2>
            <p className={`mt-1 text-4xl font-bold tabular-nums ${tone}`}>{holds ? `${holds}건` : "없음"}</p>
          </div>
          <dl className={`flex gap-8 pb-1 text-sm ${tone}`}>
            <div><dt className="text-xs opacity-80">장애 이슈</dt><dd className="text-lg font-semibold tabular-nums">{faults.length}건 (진행 중 {faults.filter((i) => i.status === "open").length}건)</dd></div>
            <div><dt className="text-xs opacity-80">문의 처리 결과</dt><dd className="text-lg font-semibold tabular-nums">{inquiries.length}건</dd></div>
          </dl>
          <p className={`ml-auto max-w-sm pb-1 text-xs ${tone}`}>승인·거절은 사람이 승인 대기함에서 직접 합니다. 이슈의 단계·타임라인·해결 내용은 아카이브 AI의 장애 기록에서 봅니다.</p>
        </div>
        <Link href="/archive/incidents" className="mt-4 inline-block text-sm font-semibold text-brand-700 underline underline-offset-2">장애 기록에서 이슈 자세히 보기 →</Link>
      </section>

      <SupportOverviewCard />

      <section className="card space-y-3 p-6" aria-labelledby="orch-inquiry">
        <h2 id="orch-inquiry" className="text-lg font-semibold">장애가 아닌 이슈 (문의 처리 결과)</h2>
        {inquiries.length === 0 ? <p className="text-sm text-ink-500">아직 없습니다.</p> : (
          <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
            {[...inquiries].reverse().slice(0, 10).map((i) => (
              <li key={i.context_id}>
                <Link href={`/archive/incidents?ctx=${encodeURIComponent(i.context_id)}`} className="flex flex-wrap items-center gap-2 px-4 py-2.5 text-sm hover:bg-ink-50">
                  <span className={`chip ${incidentStatus(i.status).tone}`}>{incidentStatus(i.status).label}</span>
                  <span className="font-medium" title={i.title}>{incidentTitle(i)}</span>
                  <span className="ml-auto text-xs text-ink-500">{ago(i.opened_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <FinanceOpinionsCard tick={updatedAt?.getTime() ?? 0} />

      <section className="card space-y-2 p-6" aria-labelledby="orch-eval">
        <div className="flex items-center gap-2"><h2 id="orch-eval" className="text-lg font-semibold">에이전트 평가 진단</h2><PreparingChip /></div>
        <p className="text-sm text-ink-500">인사 AI의 에이전트 평가 결과를 총괄이 받아 부서별 문제를 진단하는 자리입니다. 아직 연결되지 않았습니다.</p>
      </section>
    </div>
  );
}
