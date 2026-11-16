import { Loading } from "@/components/admin/bits";
import { SupportOverviewCard } from "@/components/admin/SupportOverviewCard";
import { IncidentExplorer } from "@/components/incident/IncidentExplorer";
import { clock } from "@/lib/adminRelay";
import { useIncidents } from "@/lib/incidents";
import { useSearchParams } from "@/lib/nav";
import { CompanyName } from "@/demo/CompanyName";

// 총괄 화면 (재무 AI 화면과 같은 짜임): 머리말 → 요약 카드 → 이슈 목록과 상세.
// 고객사에서 문제가 터지면 총괄이 이슈 하나로 묶고 부서에 나눠 맡긴다. 이 화면은 읽기 전용이다.
// 이슈 목록·상세는 아카이브 AI의 장애 기록 화면과 같은 부품(components/incident)을 쓴다.
export default function OrchestratorPage() {
  const ctxParam = useSearchParams().get("ctx"); // 결정 기록 화면에서 "총괄 이슈에서 보기"로 들어올 때
  const { incidents, error, updatedAt, reload } = useIncidents();

  if (!incidents) return <Loading error={error} what="총괄 이슈를 불러오는 중…" />;

  return (
    <div className="space-y-8 px-8 pb-12 pt-2">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">총괄·이슈</h1>
          <p className="mt-1 text-sm text-ink-500"><CompanyName />에서 문제가 생기면 총괄 AI가 이슈 하나로 묶고, 7개 부서에 일을 나눠 맡깁니다. 사람 승인이 필요한 일만 사람에게 올립니다. 이 화면은 보기만 할 수 있습니다.</p>
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs">
          <button className="btn-ghost" onClick={() => void reload()}>새로고침{updatedAt ? ` · ${clock(updatedAt.toISOString())}` : ""}</button>
        </div>
      </header>

      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">{error}</p>}

      <IncidentExplorer incidents={incidents} initialCtx={ctxParam}>
        <SupportOverviewCard />
      </IncidentExplorer>
    </div>
  );
}
