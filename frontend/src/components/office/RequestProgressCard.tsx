import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { speaker } from "@/lib/agentChat";
import { orchestrator } from "@/lib/orchestrator";
import { apiBase, fromBackend, planningApi } from "@/lib/planning";
import { requestVideoLink } from "@/lib/requestVideoView";
import { requestTitle } from "@/lib/requestHandoffs";
import { REQUEST_STATUS, type WorkRequestRow } from "@/lib/workRequests";
import { madeVideo, perfStep, PLAN_LOOKUP_MIN_MS, planCard, planLookupDue, proposalOfRequest, requestActionError, reportIdOfRequest, resultStep, showRequestCard, stepRows, type PerfStepView, type PlanCardView, type PlanLookup, type StepDot } from "@/lib/requestCards";
import { dataAi, type RequestVideoPerformance } from "@/lib/data";
import { planDocPath } from "@/lib/planDoc";
import { planId } from "@/lib/format";
import { STALE_HOURS, staleHours } from "@/lib/officeRequests";
import { keyEvidence, reportHash, sortBeats } from "@/lib/trendReportView";
import type { TrendReportDoneCard } from "@/lib/planningData";
import type { TrendReport as Report } from "@/lib/data";
import { DoneCard, ProgressCard } from "@/components/office/ChatCards";
import { refreshWorkRequests, useWorkRequests } from "@/components/office/useWorkRequests";

// 대화방 "의뢰 진행" 카드 (총괄에게 맡긴 고객 의뢰 하나, 11/20). 전체 대화방·이슈 방·총괄 1:1에서 의뢰 번호가 처음 나온 총괄 말풍선 아래에 붙는다.
// - 데이터: 의뢰 목록(useWorkRequests, 10초)에서 찾고, 없으면 GET /api/requests/{id}를 한 번. 사람 토큰이 없으면 카드 없이 지금 글만
// - 머리: 일 이름 · 의뢰 상태 칩 · 번호. 단계: plan 순서로 번호 점 · 부서 · 단계 이름 · 상태 칩
// - 데이터 trend_report_create 단계에 보고서(tr_)가 있으면: 진행 중 = 진행 카드(5초 폴링, 끝나면 멈춤), 끝남 = 완료 카드 (ChatCards 그대로)
// - 기획 receive_insight가 끝나면 기획 기록을 읽어(1분에 한 번까지) 칩 "기획서 N"/"제안 N" + 작은 칸(영상 제작안 요약·콘셉트 · [기획서 열기] [PDF로 저장]).
//   제안은 result_ref 또는 기획 기록의 insightId "trend_report:<보고서>". PDF로 저장은 그 화면을 ?print=1로 연다(그린 뒤 한 번 인쇄)
// - 사람 확인 단계: "<다음 단계> 진행"·"여기서 멈추기" → decideRequestStep. 누른 뒤 끄고 결과는 다음 읽기에 반영.
//   실패하면 버튼 아래 한 줄(403 서버 차단·409 이미 처리·404·상태 코드, requestActionError)을 보이고 버튼을 다시 켠다
// - 열린 채 6시간(STALE_HOURS) 넘게 그대로면 "오래 멈춤 · N시간" 회색 칩 (AI 오피스 자리 상태에는 쓰지 않는다)
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

// 기획 기록 읽기 (카드 여러 개가 같이 쓴다). 1분에 한 번 넘게 다시 읽지 않는다 (lib/requestCards.ts planLookupDue)
let planCache: { at: number | null; list: PlanLookup[] | null; inflight: Promise<PlanLookup[]> | null } = { at: null, list: null, inflight: null };
function loadPlanLookup(): Promise<PlanLookup[]> {
  if (planCache.inflight) return planCache.inflight;
  if (planCache.list && !planLookupDue(planCache.at, Date.now())) return Promise.resolve(planCache.list);
  const inflight = planningApi(apiBase("planning")).state()
    .then((raw) => (fromBackend(raw, []).proposals ?? []).map((p): PlanLookup => ({
      id: p.id, insightId: p.insightId, spec: p.spec, title: p.title, idea: p.idea, videoPlan: p.videoPlan,
      result: p.result ? { direction: p.result.direction, summary: p.result.summary } : undefined,
      campaign: p.handoff?.campaign,
    })))
    .catch(() => planCache.list ?? [])
    .then((list) => { planCache = { at: Date.now(), list, inflight: null }; return list; });
  planCache = { ...planCache, inflight };
  return inflight;
}

/** 기획 receive_insight 단계가 끝나면 기획 기록을 읽는다 (result_ref가 PROP-여도: 기획서·영상 제작안을 보려고).
 *  의뢰가 바뀌면(updated_at) 다시 읽되 1분에 한 번까지 */
function usePlanLookup(need: boolean, version: string | undefined): PlanLookup[] | null {
  const [list, setList] = useState<PlanLookup[] | null>(planCache.list);
  useEffect(() => {
    if (!need) return;
    let alive = true;
    void loadPlanLookup().then((x) => { if (alive) setList(x); });
    return () => { alive = false; };
  }, [need, version]);
  return list;
}

// 의뢰 영상 성과 읽기 (데이터 video_performance campaign_id = 의뢰 번호). 의뢰마다 1분에 한 번까지 (planLookupDue)
const perfCache = new Map<string, { at: number; out: RequestVideoPerformance | null }>();
function usePerf(requestId: string, need: boolean): { out: RequestVideoPerformance | null; tick: number } {
  const [state, setState] = useState<{ out: RequestVideoPerformance | null; tick: number }>(() => ({ out: perfCache.get(requestId)?.out ?? null, tick: 0 }));
  useEffect(() => {
    if (!need) return;
    let alive = true;
    const load = () => {
      const hit = perfCache.get(requestId);
      if (hit && !planLookupDue(hit.at, Date.now())) { setState((s) => ({ out: hit.out, tick: s.tick })); return; }
      dataAi.requestVideoPerformance(requestId)
        .then((r) => r.output)
        .catch(() => hit?.out ?? null) // 못 읽으면 지금 카드 그대로
        .then((out) => { perfCache.set(requestId, { at: Date.now(), out }); if (alive) setState((s) => ({ out, tick: s.tick + 1 })); });
    };
    load();
    const timer = setInterval(load, PLAN_LOOKUP_MIN_MS);
    return () => { alive = false; clearInterval(timer); };
  }, [requestId, need]);
  return state;
}

/** 데이터 · 성과 측정 칸: 시점 4칸(조회·좋아요, 배수 · 잴 날짜) + 출처 한 줄 */
function PerfCell({ view }: { view: PerfStepView }) {
  return (
    <section aria-label="의뢰 영상 성과" className="mt-1.5 w-full space-y-1.5 rounded-xl border border-ink-100 bg-ink-50 px-3 py-2.5">
      {view.note && <p className="text-xs text-amber-800">{view.note}</p>}
      <ul className="grid grid-cols-2 gap-1.5 text-xs sm:grid-cols-4">
        {view.cells.map((c) => (
          <li key={c.name} className={`min-w-0 rounded-lg border border-ink-100 bg-surface px-2 py-1.5 ${c.wait ? "text-ink-500" : "text-ink-900"}`}>
            <b className="block text-[11px] font-medium text-ink-500">{c.name}</b>
            <span className="block break-words tabular-nums">{c.main}</span>
            {c.sub && <span className="block break-words text-ink-700">{c.sub}</span>}
          </li>
        ))}
      </ul>
      <p className="break-words text-xs text-ink-500">{view.source}</p>
    </section>
  );
}

/** 기획 단계 칸 (보고서 완료 카드와 같은 모양): 영상 제작안 요약 · 콘셉트 한 줄 · [기획서 열기] [PDF로 저장] */
function PlanCell({ card }: { card: PlanCardView }) {
  const navigate = useNavigate();
  return (
    <section aria-label={card.head} className="mt-1.5 w-full space-y-1.5 rounded-xl border border-ink-100 bg-ink-50 px-3 py-2.5">
      <h4 className="text-[12.5px] font-semibold text-ink-900">{card.head}</h4>
      {card.line && <p className="line-clamp-2 break-words text-[12.5px] text-ink-700">{card.line}</p>}
      <div className="flex flex-wrap gap-1.5 pt-0.5">
        <button type="button" className="btn-primary btn-card" onClick={() => navigate(card.openPath)}>기획서 열기</button>
        <button type="button" className="btn-ghost btn-card" onClick={() => navigate(card.printPath)}>PDF로 저장</button>
      </div>
    </section>
  );
}

export function RequestProgressCard({ requestId }: { requestId: string }) {
  const navigate = useNavigate();
  const { row, hasToken, tasks } = useRequest(requestId);
  const [deciding, setDeciding] = useState<string | null>(null);
  const [decided, setDecided] = useState<Set<string>>(() => new Set());
  // 결정 실패: 단계 id → 버튼 아래 한 줄 (lib/requestCards.ts requestActionError). 버튼은 다시 켠다
  const [failed, setFailed] = useState<Record<string, string>>({});
  const rows = useMemo(() => (row ? stepRows(row) : []), [row]);
  const insightStep = row?.plan.find((p) => p.skill === "receive_insight");
  const insightDone = !!(insightStep && row?.steps[insightStep.step_id]?.status === "completed");
  const made = !!row && madeVideo(row);
  const perf = usePerf(requestId, hasToken && made);
  const proposals = usePlanLookup(insightDone, `${row?.updated_at ?? ""}|${perf.tick}`);
  if (!showRequestCard(hasToken, requestId) || !row) return null;
  const proposal = proposalOfRequest(row, proposals);
  const found = proposal ? proposals?.find((p) => p.id === proposal) : undefined;
  const plan = found ? planCard(found) : null;
  const perfView = made ? perfStep(perf.out, Date.now()) : null;
  const result = perfView ? resultStep(row.request_id, found, perfView) : null;
  const st = REQUEST_STATUS[row.status];
  const videoLink = requestVideoLink(row);
  const stale = staleHours(row, Date.now());

  const decide = (stepId: string, approved: boolean) => {
    setDeciding(stepId);
    setFailed(({ [stepId]: _gone, ...rest }) => rest);
    orchestrator.decideRequestStep(row.request_id, stepId, approved)
      .then(() => { setDecided((s) => new Set(s).add(stepId)); void refreshWorkRequests(); })
      .catch((e: unknown) => {
        const err = requestActionError(e);
        setFailed((f) => ({ ...f, [stepId]: err.text }));
        if (err.reload) void refreshWorkRequests();
      })
      .finally(() => setDeciding(null));
  };

  return (
    <section aria-label={`의뢰 진행 ${row.request_id}`} className="mt-2 w-full max-w-xl space-y-2.5 rounded-xl border-[1.5px] border-brand-500 bg-surface px-3.5 py-3 text-[13px]">
      <p className="flex flex-wrap items-center gap-1.5">
        <b className="text-[13.5px] text-ink-900">의뢰 진행 · {requestTitle(row, tasks)}</b>
        <span className={`chip text-[11px] ${st.tone}`}>{st.label}</span>
        <span className="chip bg-ink-100 text-[11px] tabular-nums text-ink-500">{row.request_id}</span>
        {stale !== null && <span className="chip bg-ink-100 text-[11px] text-ink-500" title={`${STALE_HOURS}시간 넘게 바뀌지 않아 AI 오피스 자리 상태에는 쓰지 않아요`}>오래 멈춤 · {stale}시간</span>}
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
                  <span className={`chip text-[11px] ${s.chip.tone}`} title={isInsight && proposal ? (found?.spec ? `${proposal} · ${found.spec}` : proposal) : undefined}>
                    {isInsight && proposal ? (plan?.chip ?? planId(proposal)) : s.chip.label}
                  </span>
                </p>
                {s.muted && <p className="text-xs text-ink-500">{s.muted}</p>}
                {videoLink && s.skill === "generate_media" && (
                  <button type="button" className="btn-primary btn-card mt-1" onClick={() => navigate(videoLink)}>영상 열기</button>
                )}
                {report && <ReportCell reportId={report} />}
                {isInsight && plan && <PlanCell card={plan} />}
                {isInsight && proposal && !plan && (
                  <button type="button" className="btn-primary btn-card mt-1" onClick={() => navigate(planDocPath({ id: proposal }))}>기획 제안 열기</button>
                )}
                {s.decide && !decided.has(s.stepId) && (
                  <span className="mt-1 flex flex-wrap gap-1.5">
                    <button type="button" className="btn-primary btn-card" disabled={deciding !== null} onClick={() => decide(s.stepId, true)}>{s.decide.approve}</button>
                    <button type="button" className="btn-ghost btn-card" disabled={deciding !== null} onClick={() => decide(s.stepId, false)}>{s.decide.stop}</button>
                  </span>
                )}
                {s.decide && failed[s.stepId] && <p role="alert" className="mt-1 text-xs text-red-700">{failed[s.stepId]}</p>}
              </div>
            </li>
          );
        })}
        {perfView && (
          <li className="grid grid-cols-[1.375rem_minmax(0,1fr)] gap-2">
            <span className={`mt-0.5 grid h-5 w-5 place-items-center rounded-full text-[11px] font-bold ${DOT[perfView.done ? "ok" : "run"]}`} aria-hidden="true">{rows.length + 1}</span>
            <div className="min-w-0 text-ink-900">
              <p className="flex flex-wrap items-center gap-1.5">
                <b className="font-semibold">데이터 · 성과 측정</b>
                <span className={`chip text-[11px] ${perfView.chip.tone}`}>{perfView.chip.label}</span>
              </p>
              <PerfCell view={perfView} />
            </div>
          </li>
        )}
        {perfView && result && (
          <li className="grid grid-cols-[1.375rem_minmax(0,1fr)] gap-2">
            <span className={`mt-0.5 grid h-5 w-5 place-items-center rounded-full text-[11px] font-bold ${DOT[result.chip.label === "결과 받음" ? "ok" : "gray"]}`} aria-hidden="true">{rows.length + 2}</span>
            <div className="min-w-0 text-ink-900">
              <p className="flex flex-wrap items-center gap-1.5">
                <b className="font-semibold">기획 · 결과 받기</b>
                <span className={`chip text-[11px] ${result.chip.tone}`}>{result.chip.label}</span>
              </p>
              <p className="break-words text-xs text-ink-500">{result.line}</p>
            </div>
          </li>
        )}
      </ol>
    </section>
  );
}
