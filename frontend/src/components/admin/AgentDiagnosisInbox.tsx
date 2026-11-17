import { useEffect, useState } from "react";
import { canSeeDiagnosisInbox, type ReceivedDiagnosis } from "@/lib/agentDiagnosis";
import { shortDateTime } from "@/lib/format";
import { hr } from "@/lib/hr";
import { Link } from "@/lib/nav";
import { kindOf, useSession } from "@/demo/session";

// 에이전트 화면 안의 "총괄 AI가 보낸 진단" 칸. 에이전트 평가에서 같은 단계가 반복해 느리면 총괄 AI가 진단하고,
// 해당 에이전트가 그 결과를 받아 저장해 둔다. 이 칸은 에이전트가 받아 둔 진단을 읽어 보여 줄 뿐이다.
//
// 쓰는 법: 에이전트 화면(담당자가 고치는 파일)의 알맞은 자리에 한 줄만 넣는다.
//   <AgentDiagnosisInbox agent="finance" />
// - agent: 진단을 받는 에이전트(부서) 이름. infra·data·dev·support·marketing·finance·archive
// - showService: 한 에이전트에 서비스가 여럿일 때(예: 고객지원) 받은 서비스 이름을 같이 보인다
//
// 읽기 전용이다. 철회는 총괄 화면에서만 한다. 받은 진단이 없거나, 불러오지 못했거나, 일반회원이면 아무것도 그리지 않는다.

export function AgentDiagnosisInbox({ agent, showService = false }: { agent: string; showService?: boolean }) {
  const { account } = useSession();
  const allowed = canSeeDiagnosisInbox(account ? kindOf(account) : null);
  const [rows, setRows] = useState<ReceivedDiagnosis[] | null>(null);

  useEffect(() => {
    if (!allowed) return;
    let live = true;
    hr.receivedDiagnoses(agent)
      .then((r) => { if (live) setRows(r); })
      .catch(() => { if (live) setRows([]); }); // 못 불러오면 칸을 보이지 않는다(화면의 다른 부분은 그대로)
    return () => { live = false; };
  }, [agent, allowed]);

  if (!allowed || !rows || rows.length === 0) return null;

  return (
    <section className="card space-y-3 p-6" aria-labelledby={`diag-inbox-${agent}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id={`diag-inbox-${agent}`} className="text-lg font-semibold">총괄 AI가 보낸 진단</h2>
        <span className="text-sm tabular-nums text-ink-500">받은 진단 {rows.length}건</span>
        <Link href="/orchestrator#orch-eval" className="ml-auto text-sm font-medium text-brand-700 underline underline-offset-2">총괄 화면에서 보기 →</Link>
      </div>
      <p className="text-sm text-ink-500">
        같은 단계가 반복해 오래 걸려 총괄 AI가 진단했고, 이 에이전트가 결과를 받았어요. 알림이라 에이전트의 동작을 바꾸지 않고, 틀릴 수 있어요.
      </p>
      <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
        {rows.map((r) => (
          <li key={r.note_id} className="space-y-1.5 px-4 py-3 text-sm">
            <p className="flex flex-wrap items-center gap-2">
              <b className="font-medium text-ink-900">{r.step_label}</b>
              <span className={`chip ${r.cause_known ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-800"}`}>
                {r.cause_known ? "원인 확정" : "원인 미확정"}
              </span>
              {showService && r.service_name && <span className="chip bg-ink-100 text-ink-700">{r.service_name}</span>}
              <span className="ml-auto tabular-nums text-ink-500">{shortDateTime(r.received_at)} 받음</span>
            </p>
            <p className="text-ink-700">{r.summary}</p>
            {r.evidence_contexts.length > 0 && (
              <p className="flex flex-wrap gap-x-2 gap-y-1 text-xs text-ink-600">
                <span className="text-ink-500">근거 이슈</span>
                {r.evidence_contexts.map((ctx, i) => (
                  <Link key={ctx} href={`/archive/incidents?ctx=${encodeURIComponent(ctx)}`} className="underline underline-offset-2">이슈 {i + 1}</Link>
                ))}
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
