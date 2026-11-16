import { useState } from "react";
import { clock, incidentStatus } from "@/lib/adminRelay";
import { incidentTitle, type OrchIncident } from "@/lib/agentChat";
import { ago } from "@/lib/format";
import { CompanyName } from "@/demo/CompanyName";
import { IncidentDetail } from "./IncidentDetail";

// 사람 승인을 기다리는 일 요약 + 이슈 목록(왼쪽) + 고른 이슈의 상세(오른쪽).
// 총괄 화면과 아카이브 AI의 장애 기록 화면이 같이 쓴다. 읽기 전용이다.
export function IncidentExplorer({ incidents, initialCtx = null, children }: {
  incidents: OrchIncident[];
  /** 다른 화면에서 이슈 번호를 들고 넘어오면 그 이슈를 먼저 보여 준다 */
  initialCtx?: string | null;
  /** 요약 카드 아래, 목록 위에 끼울 내용 */
  children?: React.ReactNode;
}) {
  const [picked, setPicked] = useState<string | null>(initialCtx);
  const list = [...incidents].reverse();
  const current = list.find((i) => i.context_id === picked) ?? list[0] ?? null;
  const open = incidents.filter((i) => i.status === "open").length;
  const holds = incidents.reduce((n, i) => n + i.pending_holds.length, 0);
  const tone = holds ? "text-amber-800" : "text-emerald-800";

  return (
    <>
      {/* 요약: 화면에서 가장 중요한 곳 */}
      <section aria-labelledby="orch-summary" className={`rounded-[28px] p-6 ring-1 ${holds ? "bg-amber-50 ring-amber-100" : "bg-emerald-50 ring-emerald-100"}`}>
        <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
          <div>
            <h2 id="orch-summary" className={`text-sm font-semibold ${tone}`}>사람 승인을 기다리는 일</h2>
            <p className={`mt-1 text-4xl font-bold tabular-nums ${tone}`}>{holds ? `${holds}건` : "없음"}</p>
          </div>
          <dl className={`flex gap-8 pb-1 text-sm ${tone}`}>
            <div><dt className="text-xs opacity-80">전체 이슈</dt><dd className="text-lg font-semibold tabular-nums">{incidents.length}건</dd></div>
            <div><dt className="text-xs opacity-80">진행 중</dt><dd className="text-lg font-semibold tabular-nums">{open}건</dd></div>
          </dl>
          <p className={`ml-auto max-w-sm pb-1 text-xs ${tone}`}>승인·거절은 사람이 승인 대기함에서 직접 합니다. 나머지 단계는 총괄 AI가 정해진 규칙대로 부서에 맡겨 끝냅니다.</p>
        </div>
      </section>

      {children}

      {list.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-ink-300 px-5 py-8 text-center text-sm text-ink-500">
          아직 이슈가 없습니다. <CompanyName />에 장애가 생기면 총괄 AI가 이슈를 열고 여기에 나타납니다.
        </p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
          <ul className="space-y-2" aria-label="이슈 목록">
            {list.map((i) => (
              <li key={i.context_id}>
                <button onClick={() => setPicked(i.context_id)}
                  className={`w-full rounded-2xl border p-3 text-left text-sm transition ${i.context_id === current?.context_id ? "border-brand-500 bg-brand-50" : "border-ink-100 bg-surface hover:border-brand-500"}`}>
                  <span className="flex items-center justify-between gap-2">
                    <span className={`chip ${incidentStatus(i.status).tone}`}>{incidentStatus(i.status).label}</span>
                    <span className="text-xs text-ink-500">{ago(i.opened_at)}</span>
                  </span>
                  <span className="mt-1.5 block truncate font-semibold" title={i.title}>{incidentTitle(i)}</span>
                  <span className="block truncate text-xs text-ink-500" title={`이슈 번호 ${i.context_id}`}>{clock(i.opened_at)} 시작</span>
                </button>
              </li>
            ))}
          </ul>
          {current && <IncidentDetail incident={current} />}
        </div>
      )}
    </>
  );
}
