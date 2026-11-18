import { AgentDiagnosisInbox } from "@/components/admin/AgentDiagnosisInbox";
import { PageSkeleton } from "@/components/admin/bits";
import { IncidentExplorer } from "@/components/incident/IncidentExplorer";
import { clock } from "@/lib/adminRelay";
import { useIncidents } from "@/lib/incidents";
import { useSearchParams } from "@/lib/nav";
import { CompanyName } from "@/demo/CompanyName";

// 아카이브 AI → 장애 기록. 장애가 났을 때 총괄 AI가 처리한 이슈와 그 기록을 한곳에서 본다.
// 이슈 목록·상세는 총괄 화면과 같은 부품(components/incident)과 같은 데이터(lib/incidents.ts)를 쓴다.
// 이 화면은 읽기 전용이다.
export default function ArchiveIncidentsPage() {
  const ctx = useSearchParams().get("ctx"); // 다른 화면에서 이슈 번호를 들고 넘어올 때
  const { incidents, error, updatedAt, reload } = useIncidents();

  if (!incidents) return <PageSkeleton error={error} what="이슈를 불러오는 중…" />;

  return (
    <div className="space-y-8 px-8 pb-12 pt-2">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">장애 기록</h1>
          <p className="mt-1 text-sm text-ink-500"><CompanyName />에서 문제가 생기면 총괄 AI가 이슈 하나로 묶어 부서에 맡기고, 끝난 일은 아카이브 AI가 기록으로 남깁니다. 이 화면은 보기만 할 수 있습니다.</p>
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs">
          <button className="btn-ghost" onClick={() => void reload()}>새로고침{updatedAt ? ` · ${clock(updatedAt.toISOString())}` : ""}</button>
        </div>
      </header>

      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">{error}</p>}

      <IncidentExplorer incidents={incidents} initialCtx={ctx} />

      {/* 총괄 AI가 보낸 진단: 아카이브 AI의 단계(비슷한 과거 찾기·이슈 기록·해결 기록)가 반복해 느릴 때만 보인다 */}
      <AgentDiagnosisInbox agent="archive" />
    </div>
  );
}
