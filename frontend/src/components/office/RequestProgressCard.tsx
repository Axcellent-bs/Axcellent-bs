import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { speaker } from "@/lib/agentChat";
import { orchestrator } from "@/lib/orchestrator";
import { apiBase, fromBackend, planningApi } from "@/lib/planning";
import { requestTitle } from "@/lib/requestHandoffs";
import { REQUEST_STATUS, type WorkRequestRow } from "@/lib/workRequests";
import { proposalOfRequest, reportIdOfRequest, showRequestCard, stepRows, type StepDot } from "@/lib/requestCards";
import { keyEvidence, reportHash, sortBeats } from "@/lib/trendReportView";
import type { TrendReportDoneCard } from "@/lib/planningData";
import type { TrendReport as Report } from "@/lib/data";
import { DoneCard, ProgressCard } from "@/components/office/ChatCards";
import { refreshWorkRequests, useWorkRequests } from "@/components/office/useWorkRequests";

// 대화방 "의뢰 진행" 카드 (총괄에게 맡긴 고객 의뢰 하나, 11/20). 전체 대화방·이슈 방·총괄 1:1에서 의뢰 번호가 처음 나온 총괄 말풍선 아래에 붙는다.
// - 데이터: 의뢰 목록(useWorkRequests, 10초)에서 찾고, 없으면 GET /api/requests/{id}를 한 번. 사람 토큰이 없으면 카드 없이 지금 글만
// - 머리: 일 이름 · 의뢰 상태 칩 · 번호. 단계: plan 순서로 번호 점 · 부서 · 단계 이름 · 상태 칩
// - 데이터 trend_report_create 단계에 보고서(tr_)가 있으면: 진행 중 = 진행 카드(5초 폴링, 끝나면 멈춤), 끝남 = 완료 카드 (ChatCards 그대로)
// - 기획 receive_insight가 끝나면 "기획 제안 열기"(result_ref 또는 기획 기록의 insightId "trend_report:<보고서>")
// - 사람 확인 단계: "<다음 단계> 진행"·"여기서 멈추기" → decideRequestStep. 누른 뒤 끄고 결과는 다음 읽기에 반영
// - 실패·건너뜀: 회색 + 의뢰 note(코드 문장). 고객 글·부서 문장은 옮기지 않는다. 계산은 lib/requestCards.ts

const DOT: Record<StepDot, string> = {
  ok: "bg-emerald-600 text-white", run: "bg-brand-600 text-white", warn: "bg-amber-500 text-white", gray: "bg-ink-100 text-ink-500",
};

/** 의뢰 하나 읽기: 목록에 있으면 그것, 없으면 한 번 따로 */
function useRequest(requestId: string): { row: WorkRequestRow | null; hasToken: boolean; tasks: { name: string; label: string }[] } {
  const { hasToken, rows, tasks } = useWorkRequests();
  const listed = rows?.find((r) => r.request_id === requestId) ?? null;
  const [single, setSingle] = useState<WorkRequestRow | null>(null);
  const [asked, setAsked] = useState(false);
  useEffect(() => {
    if (!hasToken || listed || asked || rows === null) return;
    setAsked(true);
    orchestrator.workRequest(requestId).then(setSingle).catch(() => { /* 못 읽으면 카드 없이 글만 */ });
  }, [hasToken, listed, asked, rows, requestId]);
  return { row: listed ?? single, hasToken, tasks };
}

/** 보고서 칸: 진행 중이면 진행 카드, 끝나면 완료 카드 (보고서에서 요약·근거 3개) */
function ReportCell({ reportId }: { reportId: string }) {
  const [done, setDone] = useState<TrendReportDoneCard | null>(null);
  if (done) return <DoneCard card={done} />;
  const finish = (r: Report) => setDone({
    kind: "trend_report_done", report_id: r.report_id, status: r.status, summary_line: r.summary_line,
    top_evidence: keyEvidence(sortBeats(r.sections.recommendation.beats)).map((k) => k.short),
    report_path: `/data/trend${reportHash(r.report_id)}`,
  });
  return <ProgressCard card={{ kind: "trend_report_progress", report_id: reportId }} onDone={finish} />;
}

/** 기획 기록에서 insightId로 제안 찾기 (단계 result_ref가 없을 때만 한 번 읽는다) */
function useProposalLookup(need: boolean): { id: string; insightId?: string }[] | null {
  const [list, setList] = useState<{ id: string; insightId?: string }[] | null>(null);
  useEffect(() => {
    if (!need || list) return;
    planningApi(apiBase("planning")).state()
      .then((raw) => setList((fromBackend(raw, []).proposals ?? []).map((p) => ({ id: p.id, insightId: p.insightId }))))
      .catch(() => setList([]));
  }, [need, list]);
  return list;
}

export function RequestProgressCard({ requestId }: { requestId: string }) {
  const navigate = useNavigate();
  const { row, hasToken, tasks } = useRequest(requestId);
  const [deciding, setDeciding] = useState<string | null>(null);
  const [decided, setDecided] = useState<Set<string>>(() => new Set());
  const rows = useMemo(() => (row ? stepRows(row) : []), [row]);
  const insightStep = row?.plan.find((p) => p.skill === "receive_insight");
  const insightDone = !!(insightStep && row?.steps[insightStep.step_id]?.status === "completed");
  const needLookup = insightDone && !(row?.steps[insightStep!.step_id]?.result_ref ?? "").startsWith("PROP-");
  const proposals = useProposalLookup(needLookup);
  if (!showRequestCard(hasToken, requestId) || !row) return null;
  const proposal = proposalOfRequest(row, proposals);
  const st = REQUEST_STATUS[row.status];

  const decide = (stepId: string, approved: boolean) => {
    setDeciding(stepId);
    orchestrator.decideRequestStep(row.request_id, stepId, approved)
      .then(() => { setDecided((s) => new Set(s).add(stepId)); void refreshWorkRequests(); })
      .catch(() => { /* 다음 읽기에 상태가 그대로면 다시 누를 수 있다 */ })
      .finally(() => setDeciding(null));
  };

  return (
    <section aria-label={`의뢰 진행 ${row.request_id}`} className="mt-2 w-full max-w-xl space-y-2.5 rounded-xl border-[1.5px] border-brand-500 bg-surface px-3.5 py-3 text-[13px]">
      <p className="flex flex-wrap items-center gap-1.5">
        <b className="text-[13.5px] text-ink-900">의뢰 진행 · {requestTitle(row, tasks)}</b>
        <span className={`chip text-[11px] ${st.tone}`}>{st.label}</span>
        <span className="chip bg-ink-100 text-[11px] tabular-nums text-ink-500">{row.request_id}</span>
      </p>
      <ol className="space-y-2">
        {rows.map((s, i) => {
          const report = s.skill === "trend_report_create" ? reportIdOfRequest(row, s.stepId) : null;
          const isInsight = s.skill === "receive_insight";
          return (
            <li key={s.stepId} className="grid grid-cols-[1.375rem_minmax(0,1fr)] gap-2">
              <span className={`mt-0.5 grid h-5 w-5 place-items-center rounded-full text-[11px] font-bold ${DOT[s.dot]}`} aria-hidden="true">{i + 1}</span>
              <div className={`min-w-0 ${s.muted ? "text-ink-500" : "text-ink-900"}`}>
                <p className="flex flex-wrap items-center gap-1.5">
                  <b className="font-semibold">{s.dept ? `${speaker(s.dept).replace(/\s*AI$/, "")} · ` : ""}{s.label}</b>
                  <span className={`chip text-[11px] ${s.chip.tone}`}>{isInsight && proposal ? proposal : s.chip.label}</span>
                </p>
                {s.muted && <p className="text-xs text-ink-500">{s.muted}</p>}
                {report && <ReportCell reportId={report} />}
                {isInsight && proposal && (
                  <button type="button" className="btn-ghost mt-1 px-2.5 py-1 text-xs" onClick={() => navigate(`/planning/proposals/${encodeURIComponent(proposal)}`)}>기획 제안 열기</button>
                )}
                {s.decide && !decided.has(s.stepId) && (
                  <span className="mt-1 flex flex-wrap gap-1.5">
                    <button type="button" className="btn-primary px-2.5 py-1 text-xs" disabled={deciding !== null} onClick={() => decide(s.stepId, true)}>{s.decide.approve}</button>
                    <button type="button" className="btn-ghost px-2.5 py-1 text-xs" disabled={deciding !== null} onClick={() => decide(s.stepId, false)}>{s.decide.stop}</button>
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
