import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { orchestrator } from "@/lib/orchestrator";
import { CHARACTER_SKILL, chatCharacterLine, isVideoRequest, unansweredNotice } from "@/lib/requestCharacter";
import { BrandCharacterAsk, useCharacterNames, useChosenCharacter } from "@/components/office/BrandCharacterAsk";
import { TONE_CLASS, barCells, brandIdOf, creativeSpot, currentIndex, deptTag, futureLine, headPill, nowLine, peekText, shortLabel, stepState, toneOfCurrent, type CardTone } from "@/lib/requestCardView";
import { thumbSrc } from "@/lib/creativeRecords";
import { castLineKo, characterLine } from "@/lib/videoPlan";
import { goalLine } from "@/lib/videoGoal";
import { RequestCreative } from "@/components/office/RequestCreative";
import { RequestPublish } from "@/components/office/RequestPublish";
import { ChatReviewDecision } from "@/components/office/ChatReviewDecision";
import { apiBase, fromBackend, planningApi } from "@/lib/planning";
import { requestVideoLink } from "@/lib/requestVideoView";
import { requestTitle } from "@/lib/requestHandoffs";
import type { WorkRequestRow } from "@/lib/workRequests";
import { closingOfRequest, hiddenRequestLine, infraOfRequest, type InfraResult, madeVideo, needPlanRefetch, perfStep, PLAN_LOOKUP_MIN_MS, PLAN_RETRY_MAX, PLAN_RETRY_MS, planCard, planFound, planLookupDue, proposalOfRequest, requestActionError, requestEditError, reportIdOfRequest, resultStep, showRequestCard, stepRows, type PerfStepView, type PlanCardView, type PlanLookup } from "@/lib/requestCards";
import { dataAi, type RequestVideoPerformance } from "@/lib/data";
import { rollbackCardResult, rollbackDecisionOf, type RollbackCardResult } from "@/lib/rollbackStatus";
import { listLatestDeploys, type LatestDeploy } from "@/lib/toolConnections";
import { finance, krw, type ClosingPeriod, type ClosingReport } from "@/lib/finance";
import { planDocPath } from "@/lib/planDoc";
import { planId } from "@/lib/format";
import { STALE_HOURS, staleHours } from "@/lib/officeRequests";
import { keyEvidence, recommendationItems, reportHash } from "@/lib/trendReportView";
import { requestBasedReport } from "@/lib/chatCards";
import type { TrendReportDoneCard } from "@/lib/planningData";
import type { TrendReport as Report } from "@/lib/data";
import { DoneCard, ProgressCard } from "@/components/office/ChatCards";
import { RequestCoverCell } from "@/components/marketing/CoverPicker";
import { refreshWorkRequests, useWorkRequests } from "@/components/office/useWorkRequests";
import { RequestActions } from "@/components/office/RequestActions";
import { InlinePlan, useExpand } from "@/components/office/InlineDoc";
import { CheckLog, RequestRules } from "@/components/planning/PlanChecks";
import type { VideoPlanView } from "@/lib/videoPlan";

// 대화방 "의뢰 진행" 카드 (총괄에게 맡긴 고객 의뢰 하나, 11/20). 전체 대화방·이슈 방·총괄 1:1에서 의뢰 번호가 처음 나온 총괄 말풍선 아래에 붙는다.
// - 데이터: 의뢰 목록(useWorkRequests, 10초)에서 찾고, 없으면 GET /api/requests/{id}를 한 번. 사람 토큰이 없으면 카드 없이 지금 글만
// - 11/26 크게·한눈에 (재현 님 시안, 계산은 lib/requestCardView.ts): 머리 = [▶/▼] · 제목 19px · 상태 알약 · 번호 · ⋯ 메뉴,
//   의뢰 원문 2줄 · "지금 N단계 / 7 · 할 일" + 진행 막대. 접으면 사진 · 지금 N/7 · 작은 막대 · 설명 2줄 · [펼쳐서 단계 보기]
//   단계 = 번호 원 · 부서 꼬리표 · 단계 이름 16px · 상태 글. 지금 단계는 칠하고 큰 버튼, 남은 단계는 무엇을 할지 한 줄
//   끝나면 머리에 영상 카드([영상 열기] · [이미지 시트][스토리보드]). 카드 안 작은 글씨는 styles.css .req-card가 키운다
// - 데이터 trend_report_create 단계에 보고서(tr_)가 있으면: 진행 중 = 진행 카드(5초 폴링, 끝나면 멈춤), 끝남 = 완료 카드 (ChatCards 그대로)
// - 기획 receive_insight가 끝나면 기획 기록을 읽어(1분에 한 번까지) 칩 "기획서 N"/"제안 N" + 작은 칸(영상 제작안 요약·콘셉트 · [기획서 열기] [PDF로 저장]).
//   제안은 result_ref 또는 기획 기록의 insightId "trend_report:<보고서>". PDF로 저장은 그 화면을 ?print=1로 연다(그린 뒤 한 번 인쇄)
// - 사람 확인 단계: "<다음 단계> 진행"·"여기서 멈추기" → decideRequestStep. 누른 뒤 끄고 결과는 다음 읽기에 반영.
//   실패하면 버튼 아래 한 줄(403 서버 차단·409 이미 처리·404·상태 코드, requestActionError)을 보이고 버튼을 다시 켠다
// - 열린 채 6시간(STALE_HOURS) 넘게 그대로면 "오래 멈춤 · N시간" 회색 칩 (AI 오피스 자리 상태에는 쓰지 않는다)
// - 영상 제작이 끝나면 [영상 열기]·썸네일 고르기 아래 "어디에 올릴까요?" [유튜브][인스타그램] (RequestPublish, 11/26)
// - 재무 손익 정리가 끝나면 그 기간 손익 줄과 설명(ClosingCell, 11/26)
// - 실패·건너뜀: 회색 + 의뢰 note(코드 문장). 고객 글·부서 문장은 옮기지 않는다. 계산은 lib/requestCards.ts

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
  // 따로 읽은 값은 그 번호일 때만 (같은 자리에서 번호가 바뀌어도 앞 의뢰를 보이지 않는다, 11/24 ①)
  return { row: listed ?? (single?.request_id === requestId ? single : null), hasToken, tasks };
}

/** 보고서 칸: 진행 중이면 진행 카드, 끝나면 완료 카드 (보고서에서 요약·근거 3개) */
function ReportCell({ reportId }: { reportId: string }) {
  const [done, setDone] = useState<TrendReportDoneCard | null>(null);
  if (done) return <DoneCard card={done} />;
  const finish = (r: Report) => setDone({
    kind: "trend_report_done", report_id: r.report_id, status: r.status, summary_line: r.summary_line,
    top_evidence: keyEvidence(recommendationItems(r.sections.recommendation)).map((k) => k.short),
    report_path: `/data/trend${reportHash(r.report_id)}`,
    request_based: requestBasedReport(r),
  });
  return <ProgressCard card={{ kind: "trend_report_progress", report_id: reportId }} onDone={finish} />;
}

// 인프라 의뢰 결과 (11/26 총괄 대화방). 총괄 단계는 짧은 번호만 남긴다: 장애 건수·진단 결과는 그 번호로,
// 배포 상태는 도구 연결 화면과 같은 GET /api/dashboard/deployments/latest로 다시 읽는다(지어내지 않는다)
function DeploysCell() {
  const [rows, setRows] = useState<LatestDeploy[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    listLatestDeploys().then((r) => live && setRows(r), (e: unknown) => live && setError(e instanceof Error ? e.message : "배포 상태를 읽지 못했어요"));
    return () => { live = false; };
  }, []);
  if (error) return <p className="mt-1 text-[13.5px] text-red-700">{error}</p>;
  if (!rows) return <p className="mt-1 animate-pulse text-[13.5px] text-ink-500">배포 상태를 불러오는 중…</p>;
  if (rows.length === 0) return <p className="mt-1 text-[13.5px] text-ink-700">배포 도구에서 받은 배포 기록이 아직 없어요.</p>;
  return (
    <dl className="mt-1 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 rounded-lg border border-ink-100 bg-ink-50 px-2.5 py-2 text-[13.5px]" aria-label="서비스별 최근 배포">
      {rows.map((d) => (
        <div key={d.service} className="contents"><dt className="truncate text-ink-700">{d.service}</dt><dd className="text-right tabular-nums text-ink-900">{d.version} · {d.deployed_at.slice(0, 16).replace("T", " ")}</dd></div>
      ))}
    </dl>
  );
}
// 사건 롤백 상태 (11/26 롤백 요청 2단계, 읽기 전용). 총괄 사건 목록(GET /api/incidents, 로그인 회사 범위)에서
// 열린 사건의 롤백 단계를 읽어 보여 준다. 승인 대기(held)이고 review_id가 있을 때만 결정 영역(ChatReviewDecision, 기존 안전 관문 결정 경로 한 곳)을 붙인다 (3-a)
const KO_TIME = (iso: string | null) => (iso ? new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" }) : "확인 안 됨");
const CONF_KO = { low: "낮음", mid: "보통", high: "높음" } as const;
function RollbackCell() {
  const [res, setRes] = useState<RollbackCardResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0); // 승인·거절 뒤 바로 다시 읽기
  useEffect(() => {
    let live = true;
    const load = () => orchestrator.incidents().then((r) => live && (setRes(rollbackCardResult(r.data)), setError(null)), (e: unknown) => live && setError(e instanceof Error ? e.message : "사건 목록을 읽지 못했어요"));
    load();
    const t = setInterval(load, 5000);
    return () => { live = false; clearInterval(t); };
  }, [tick]);
  if (error) return <p className="mt-1 text-[13.5px] text-red-700">{error}</p>;
  if (!res) return <p className="mt-1 animate-pulse text-[13.5px] text-ink-500">사건 롤백 상태를 불러오는 중…</p>;
  if (res.kind === "none") return <p className="mt-1 rounded-lg border border-ink-100 bg-ink-50 px-2.5 py-2 text-[13.5px] text-ink-700" aria-label="사건 롤백 상태">{res.text}</p>;
  const s = res.status;
  const decision = rollbackDecisionOf(s);
  const rows: [string, string][] = [];
  if (s.service) rows.push(["서비스", s.service]);
  if (s.fromVersion || s.toVersion) rows.push(["버전", `${s.fromVersion ?? "확인 안 됨"} → ${s.toVersion ?? "확인 안 됨"}`]);
  if (s.toVersion) rows.push(["되돌릴 버전 직전 health", s.toVersionHealthy === null ? "확인 안 됨" : s.toVersionHealthy ? "정상" : "정상 아님"]);
  if (s.confidence) rows.push(["원인 확신도", CONF_KO[s.confidence]]);
  if (s.irreversibleCount !== null) rows.push(["되돌릴 수 없는 변경", `${s.irreversibleCount}건`]);
  if (s.priorRollbacks !== null) rows.push(["이 사건의 이전 롤백", `${s.priorRollbacks}회`]);
  rows.push(["사건 시작", KO_TIME(s.openedAt)]);
  if (s.state === "waiting") rows.push(["승인 대기 기한", KO_TIME(s.expiresAt)]);
  return (
    <div className="mt-1 rounded-lg border border-ink-100 bg-ink-50 px-2.5 py-2 text-[13.5px]" aria-label="사건 롤백 상태">
      <p className="font-medium text-ink-900">{s.headline}{res.openCount > 1 ? ` (열린 사건 ${res.openCount}건 중 가장 최근)` : ""}</p>
      <dl className="mt-1 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5">
        {rows.map(([k, v]) => (
          <div key={k} className="contents"><dt className="truncate text-ink-700">{k}</dt><dd className="text-right tabular-nums text-ink-900">{v}</dd></div>
        ))}
      </dl>
      {s.onReject && <p className="mt-1 text-ink-700">{s.onReject}.{decision ? "" : " 승인·거절은 장애 대응 사건에서 해요."}</p>}
      {decision && (
        <div className="mt-1.5">
          <ChatReviewDecision key={decision.reviewId} reviewId={decision.reviewId} what="사건 롤백" confirmName={decision.service} confirmText={decision.confirmText} doneText="승인했어요. 롤백은 사건 흐름이 이어서 진행해요." onDone={() => setTick((n) => n + 1)} />
        </div>
      )}
    </div>
  );
}
function InfraCell({ result }: { result: InfraResult }) {
  if (result.kind === "rollback") return <RollbackCell />;
  if (result.kind === "deploys") return <DeploysCell />;
  const text =
    result.kind === "incidents"
      ? result.count === null ? "이 회사의 Zabbix가 아직 연결되지 않아 장애를 확인하지 못했어요." : result.count === 0 ? "지금 DejaFix에 올라온 장애가 없어요." : `지금 장애가 ${result.count}건 있어요. 장애 대응 탭에서 자세히 볼 수 있어요.`
      : result.status === "suspected" ? "원인 후보로 의심되는 배포가 있어요. 장애 대응 탭에서 확인해 주세요. 되돌리기는 하지 않았어요." : "지금은 원인으로 볼 만한 배포를 찾지 못했어요.";
  return <p className="mt-1 break-words rounded-lg border border-ink-100 bg-ink-50 px-2.5 py-2 text-[13.5px] text-ink-900" aria-label="인프라 확인 결과">{text}</p>;
}

// 재무 손익 정리 결과 (11/26). 총괄 단계는 번호("<기간>:<날짜>")만 남기고, 숫자는 재무 화면과 같은
// GET /api/finance/closing으로 다시 읽는다(코드 계산 값 그대로, 새로 계산하지 않는다)
const CLOSING_LINES = 6;
const PERIOD_KO: Record<ClosingPeriod, string> = { day: "하루", week: "한 주", month: "한 달" };
function ClosingCell({ period, date }: { period: ClosingPeriod; date: string }) {
  const [report, setReport] = useState<ClosingReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    finance.closing(period, date).then((r) => live && setReport(r.data), (e: unknown) => live && setError(e instanceof Error ? e.message : "손익을 읽지 못했어요"));
    return () => { live = false; };
  }, [period, date]);
  if (error) return <p className="mt-1 text-[13.5px] text-red-700">{error}</p>;
  if (!report) return <p className="mt-1 animate-pulse text-[13.5px] text-ink-500">손익을 불러오는 중…</p>;
  return (
    <div className="mt-1 space-y-1 rounded-lg border border-ink-100 bg-ink-50 px-2.5 py-2 text-[13.5px]" aria-label="손익 정리 결과">
      <p className="text-ink-700">{report.from} ~ {report.to} ({PERIOD_KO[period]}) · {report.status === "confirmed" ? "확정" : "확정 전"}</p>
      <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5">
        {report.lines.slice(0, CLOSING_LINES).map((ln) => (
          <div key={ln.code} className="contents"><dt className="truncate text-ink-700">{ln.label}</dt><dd className="text-right tabular-nums text-ink-900">{krw(ln.amount)}</dd></div>
        ))}
      </dl>
      {report.commentary && <p className="break-words text-ink-700">{report.commentary}</p>}
      <a className="btn-ghost btn-card inline-flex items-center" href="/finance#closing">재무 화면에서 전체 보기</a>
    </div>
  );
}

// 기획 기록 읽기 (카드 여러 개가 같이 쓴다). 1분에 한 번 넘게 다시 읽지 않는다 (lib/requestCards.ts planLookupDue)
// force: 1분 제한 없이 읽는다 (결과 번호가 목록에 없을 때·못 찾아 다시 읽을 때. 부르는 쪽이 10초·15초 간격을 지킨다)
let planCache: { at: number | null; list: PlanLookup[] | null; inflight: Promise<PlanLookup[]> | null } = { at: null, list: null, inflight: null };
function loadPlanLookup(force = false): Promise<PlanLookup[]> {
  if (planCache.inflight) return planCache.inflight;
  if (!force && planCache.list && !planLookupDue(planCache.at, Date.now())) return Promise.resolve(planCache.list);
  const inflight = planningApi(apiBase("planning")).state()
    .then((raw) => (fromBackend(raw, []).proposals ?? []).map((p): PlanLookup => ({
      id: p.id, insightId: p.insightId, spec: p.spec, title: p.title, idea: p.idea, videoPlan: p.videoPlan, fullPlan: p.videoPlan,
      result: p.result ? { direction: p.result.direction, summary: p.result.summary } : undefined,
      campaign: p.handoff?.campaign,
    })))
    .catch(() => planCache.list ?? [])
    .then((list) => { planCache = { at: Date.now(), list, inflight: null }; return list; });
  planCache = { ...planCache, inflight };
  return inflight;
}

// 결과 번호(PROP-)마다 마지막으로 다시 읽은 때 (카드 여러 개가 같이 쓴다. 같은 번호는 10초에 한 번까지)
const refetchAt = new Map<string, number>();

/** 기획 receive_insight 단계가 끝나면 기획 기록을 읽는다 (result_ref가 PROP-여도: 기획서·영상 제작안을 보려고).
 *  의뢰가 바뀌면(updated_at) 다시 읽되 1분에 한 번까지. 11/23: 결과 번호가 목록에 없으면 바로(번호마다 10초에 한 번까지),
 *  그래도 못 찾으면 15초 간격으로 최대 8번 더 읽는다(카드가 사라지면 멈춤) */
function usePlanLookup(row: WorkRequestRow | null, need: boolean, version: string | undefined): PlanLookup[] | null {
  const [list, setList] = useState<PlanLookup[] | null>(planCache.list);
  useEffect(() => {
    if (!need) return;
    let alive = true;
    void loadPlanLookup().then((x) => { if (alive) setList(x); });
    return () => { alive = false; };
  }, [need, version]);
  // 결과 번호가 기억해 둔 목록에 없다: 1분 제한 없이 바로 다시
  const ref = row ? planRefOf(row) : null;
  useEffect(() => {
    if (!need || !row || !ref) return;
    const now = Date.now();
    if (!needPlanRefetch(row, list, refetchAt.get(ref) ?? null, now)) return;
    refetchAt.set(ref, now);
    let alive = true;
    void loadPlanLookup(true).then((x) => { if (alive) setList(x); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [need, ref, list]);
  // 끝났는데 제안을 못 찾았다: 찾을 때까지 15초 간격으로 최대 8번
  const found = !!row && planFound(row, list);
  useEffect(() => {
    if (!need || !row || found) return;
    let tries = 0;
    let alive = true;
    const timer = setInterval(() => {
      tries += 1;
      if (tries > PLAN_RETRY_MAX) { clearInterval(timer); return; }
      void loadPlanLookup(true).then((x) => { if (alive) setList(x); });
    }, PLAN_RETRY_MS);
    return () => { alive = false; clearInterval(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [need, found, row?.request_id]);
  return list;
}

/** 기획 단계 결과 번호(PROP-)만 (다시 읽기 판정의 열쇠) */
function planRefOf(row: WorkRequestRow): string | null {
  const p = row.plan.find((x) => x.skill === "receive_insight");
  const ref = p ? row.steps[p.step_id]?.result_ref : null;
  return ref && /^PROP-/.test(ref) ? ref : null;
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
    <section aria-label="의뢰 영상 성과" className="w-full space-y-1.5 border border-ink-100 bg-ink-50 px-3 py-2.5 text-[13.5px]">
      {view.note && <p className="text-amber-800">{view.note}</p>}
      <ul className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {view.cells.map((c) => (
          <li key={c.name} className={`min-w-0 border border-ink-100 bg-surface px-2 py-1.5 ${c.wait ? "text-ink-500" : "text-ink-900"}`}>
            <b className="block text-[13.5px] font-medium text-ink-500">{c.name}</b>
            <span className="block break-words tabular-nums">{c.main}</span>
            {c.sub && <span className="block break-words text-ink-700">{c.sub}</span>}
          </li>
        ))}
      </ul>
      <p className="break-words text-ink-500">{view.source}</p>
    </section>
  );
}

/** 기획 단계 칸 (보고서 완료 카드와 같은 모양): 영상 제작안 요약 · 콘셉트 한 줄 · [기획서 열기] [PDF로 저장] */
function PlanCell({ card, plan }: { card: PlanCardView; plan?: VideoPlanView }) {
  const navigate = useNavigate();
  const expand = useExpand(); // 11/26 카드 안에서 기획서 펼쳐 보기 (콘셉트·장면 카드·3D 콘티)
  return (
    <section aria-label={card.head} className="w-full space-y-1.5 border border-ink-100 bg-ink-50 px-3 py-2.5">
      <h4 className="text-[13.5px] font-semibold text-ink-500">{card.head}</h4>
      {card.line && <p className="line-clamp-2 break-words text-[15px] text-ink-900">{card.line}</p>}
      <div className="flex flex-wrap gap-1.5 pt-0.5">
        {expand.button}
        <button type="button" className="btn-primary btn-card" onClick={() => navigate(card.openPath)}>기획서 열기</button>
        <button type="button" className="btn-ghost btn-card" onClick={() => navigate(card.printPath)}>PDF로 저장</button>
      </div>
      {expand.opened && <InlinePlan plan={plan} open={expand.open} />}
    </section>
  );
}

/** 삭제한 의뢰의 접힌 한 줄. [되돌리기] → unhideRequest → 목록 다시 읽기. 실패하면 줄 아래에 이유 */
function HiddenRequestLine({ requestId, line }: { requestId: string; line: { text: string; canUndo: boolean } }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const undo = () => {
    setBusy(true);
    setError(null);
    orchestrator.unhideRequest(requestId)
      .then(() => refreshWorkRequests())
      .catch((e: unknown) => {
        const err = requestEditError(e);
        setError(err.text);
        if (err.reload) void refreshWorkRequests();
      })
      .finally(() => setBusy(false));
  };
  return (
    <div className="mt-2 text-[13.5px] text-ink-500" aria-label={`삭제한 의뢰 ${requestId}`}>
      <p>
        {line.text}
        {line.canUndo && <> · <button type="button" className="underline hover:text-ink-700 disabled:opacity-50" disabled={busy} onClick={undo}>되돌리기</button></>}
      </p>
      {error && <p role="alert" className="mt-1 text-red-700">{error}</p>}
    </div>
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
  const proposals = usePlanLookup(row, insightDone, `${row?.updated_at ?? ""}|${perf.tick}`);
  // 펼침이 기본(지금처럼). 기억하지 않는다 · 의뢰 원문 2줄 말줄임
  const [open, setOpen] = useState(true);
  const [askFull, setAskFull] = useState(false);
  const chosen = useChosenCharacter(row ? brandIdOf(row) : null);
  const castNames = useCharacterNames(!!row && (((row as { cast?: unknown[] | null }).cast?.length ?? 0) >= 2));
  if (!showRequestCard(hasToken, requestId) || !row) return null;
  // 삭제한 의뢰: 카드 대신 회색 한 줄 (되돌리면 카드 전체가 다시 보인다)
  const hiddenLine = hiddenRequestLine(row, requestTitle(row, tasks), hasToken);
  if (hiddenLine) return <HiddenRequestLine requestId={row.request_id} line={hiddenLine} />;
  const proposal = proposalOfRequest(row, proposals);
  const found = proposal ? proposals?.find((p) => p.id === proposal) : undefined;
  const plan = found ? planCard(found, row.request_id) : null;
  const perfView = made ? perfStep(perf.out, Date.now()) : null;
  const result = perfView ? resultStep(row.request_id, found, perfView) : null;
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

  const title = requestTitle(row, tasks);
  const cur = currentIndex(rows, row.status);
  const now = nowLine(rows.map((s) => ({ ...s, reasonCode: row.steps[s.stepId]?.reason_code ?? null })), row.status, isVideoRequest(row));
  const pill = headPill(rows, row.status);
  const cells = barCells(rows, row.status);
  const charLine = chatCharacterLine(row, chosen?.name, castNames); // 11/27 여럿이면 "등장 캐릭터 2 · …"
  const photo = chosen ? thumbSrc(chosen.thumbnail_base64) : null;
  const peek = peekText(charLine?.text, plan?.line, row.text);
  const spot = creativeSpot(rows, row.status, !!videoLink);
  const charStep = rows.some((s) => s.skill === CHARACTER_SKILL);
  // 사람 확인을 기다리는 동안은 검사 결과를 그 단계(버튼 위)에만 둔다 (두 번 보이지 않게)
  const humanDeciding = rows.some((s) => s.skill === "human_check" && !!s.decide && !decided.has(s.stepId));
  // 11/27 등장 캐릭터가 여럿이면 "등장 캐릭터: 주인공 · … / 동료 · …"
  const castKo = found?.fullPlan?.cast?.length ? found.fullPlan.cast.map(castLineKo).join(" / ") : "";
  const lead = castKo ? "" : found?.fullPlan ? characterLine(found.fullPlan) : "";
  // 11/23 홍보 영상: [이미지 시트][스토리보드] 대화방 안 패널 · 영상 승인 대기면 [승인 화면 열기] · 기술 검사 (자리만 creativeSpot)
  const creative = <RequestCreative row={row} plan={found?.videoPlan ?? null} />;
  // 끝났으면 머리 영상 카드에, 아니면 영상 단계 줄에 (한 곳에만)
  const videoButton = videoLink ? <button type="button" className="btn-primary btn-card" onClick={() => navigate(videoLink)}>영상 열기</button> : null;
  const toggle = () => setOpen((o) => !o);

  return (
    <section aria-label={`의뢰 진행 ${row.request_id}`}
      className={`req-card mt-2 w-full max-w-xl space-y-3 border-[1.5px] bg-surface px-4 py-3.5 text-[15px] leading-relaxed text-ink-900 ${row.status === "done" ? "border-ink-100" : "border-brand-500"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="grid h-7 w-7 shrink-0 place-items-center bg-brand-50 text-[13px] text-brand-700 hover:bg-brand-100" aria-expanded={open}
          aria-label={open ? "의뢰 진행 접기" : "의뢰 진행 펼치기"} onClick={toggle}><span aria-hidden="true">{open ? "▼" : "▶"}</span></button>
        {open
          ? <h3 className="min-w-0 break-words text-[19px] font-bold leading-snug">{title}</h3>
          : <b className="min-w-0 truncate text-base font-bold">의뢰 진행 · {title}</b>}
        <span className={`chip whitespace-nowrap text-[13px] font-bold ${TONE_CLASS[pill.tone].pill}`}>{pill.label}</span>
        <span className="text-[12.5px] tabular-nums text-ink-500">{row.request_id}</span>
        {stale !== null && <span className="chip bg-ink-100 text-[12.5px] text-ink-500" title={`${STALE_HOURS}시간 넘게 바뀌지 않아 AI 오피스 자리 상태에는 쓰지 않아요`}>오래 멈춤 · {stale}시간</span>}
        {/* 11/23 의뢰 정리 메뉴 ⋯ (고객 의뢰 넘김 카드와 같은 함수). 메뉴·수정 칸을 열면 한 줄을 다 쓴다 */}
        <div className="ml-auto min-w-0 has-[[role=group]]:basis-full has-[[role=alertdialog]]:basis-full">
          <RequestActions row={row} title={title} />
        </div>
      </div>

      {/* ⓪ 접힌 카드: 사진 · 지금 N/7 · 작은 막대 · 설명 2줄 · 펼치기 */}
      {!open && (
        <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-3 min-[481px]:pl-9">
          {photo
            ? <img src={photo} alt="회사 캐릭터" className="aspect-[3/4] w-[60px] border border-ink-100 bg-ink-50 object-cover min-[481px]:w-[72px]" />
            : <span className="hidden" aria-hidden="true" />}
          <div className={`min-w-0 space-y-1.5 ${photo ? "" : "col-span-2"}`}>
            <p className={`break-words text-[15px] font-bold ${TONE_CLASS[now.tone].text}`}>{now.short}</p>
            <MiniBar cells={cells} />
            {peek && <p className="line-clamp-2 break-words text-[14.5px] text-ink-700">{peek}</p>}
            <button type="button" className="text-[14px] text-brand-700 hover:underline" onClick={toggle}>펼쳐서 단계 보기 ▾</button>
          </div>
        </div>
      )}

      {open && (
        <>
          {row.text.trim() && (
            <div className="bg-ink-50 px-3 py-2 text-[14.5px] text-ink-700">
              <p className={`break-words ${askFull ? "whitespace-pre-line" : "line-clamp-2"}`}><b className="text-ink-900">의뢰</b> · {row.text}</p>
              {row.text.length > ASK_FOLD_CHARS && (
                <button type="button" className="text-[13px] text-brand-700 hover:underline" aria-expanded={askFull} onClick={() => setAskFull((v) => !v)}>{askFull ? "줄이기" : "의뢰 전체 보기"}</button>
              )}
            </div>
          )}

          {/* ② 끝남: 머리 영상 카드 (영상 한 줄 요약 · [영상 열기] · [이미지 시트][스토리보드]) */}
          {spot === "head" && (
            <div aria-label="만든 영상" className="space-y-2 border border-ink-100 bg-surface-2 p-3">
              {plan && <p className="break-words font-bold">{plan.line} <span className="font-normal text-ink-500">· {plan.head}</span></p>}
              <span className="req-now flex flex-wrap gap-2">{videoButton}</span>
              {creative}
            </div>
          )}

          <div className="space-y-1.5">
            <p className={`break-words text-[15.5px] font-bold ${TONE_CLASS[now.tone].text}`}>{now.text}</p>
            <Bar cells={cells} labels={rows.map(shortLabel)} />
          </div>

          {/* 11/24 옛 의뢰(캐릭터 단계가 없는 계획): 회사 캐릭터 질문·한 줄은 단계 목록 위 그대로 */}
          {!charStep && <BrandCharacterAsk row={row} variant="chat" />}

          <ol className="divide-y divide-ink-100 border-t border-ink-100">
            {rows.map((s, i) => {
              const report = s.skill === "trend_report_create" ? reportIdOfRequest(row, s.stepId) : null;
          const closing = s.skill === "closing_report" ? closingOfRequest(row, s.stepId) : null;
          const infraResult = s.dept === "infra" || s.dept === "orchestrator" ? infraOfRequest(row, s.stepId) : null;
              const isInsight = s.skill === "receive_insight";
              const isCur = i === cur;
              const state = stepState(s, isCur);
              const numTone = isCur ? toneOfCurrent(s) : state.tone;
              const future = !isCur && (s.status === "pending");
              const dept = deptTag(s.dept);
              const deciding_ = !!s.decide && !decided.has(s.stepId);
              return (
                <li key={s.stepId} className={`grid grid-cols-[2rem_minmax(0,1fr)] gap-3 px-2 py-3 min-[481px]:grid-cols-[2.5rem_minmax(0,1fr)] ${isCur ? TONE_CLASS[numTone].row : ""}`}>
                  <span className={`grid h-7 w-7 place-items-center text-[13px] font-bold min-[481px]:h-[34px] min-[481px]:w-[34px] min-[481px]:text-[15px] ${TONE_CLASS[numTone].num}`} aria-hidden="true">
                    {state.tone === "ok" ? "✓" : i + 1}
                  </span>
                  <div className={`min-w-0 space-y-1.5 ${future ? "text-ink-500" : ""}`}>
                    <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className={`border border-current px-2 py-px text-[12.5px] font-bold ${dept.cls}`}>{dept.label}</span>
                      <b className="text-base font-bold">{s.label}</b>
                      <span className={`text-[13px] font-bold ${TONE_CLASS[numTone].text}`}>{state.text}</span>
                      {isInsight && proposal && (
                        <span className="text-[12.5px] tabular-nums text-ink-500" title={found?.spec ? `${proposal} · ${found.spec}` : proposal}>{plan?.chip ?? planId(proposal)}</span>
                      )}
                    </p>
                    {isCur && now.todo && <p className="break-words">{now.todo}</p>}
                    {future && futureLine(s.skill) && <p className="break-words text-[13.5px] text-ink-500">{futureLine(s.skill)}</p>}
                    {s.muted && <p className="break-words text-[13.5px] text-ink-500">{s.muted}</p>}
                    {s.skill === CHARACTER_SKILL && (
                      <div className="flex items-start gap-2.5">
                        {photo && <img src={photo} alt="" className="h-11 w-11 shrink-0 border border-ink-100 bg-ink-50 object-cover" />}
                        <div className="min-w-0 flex-1"><BrandCharacterAsk row={row} variant="chat" /></div>
                      </div>
                    )}
                    {videoLink && s.skill === "generate_media" && (
                      <>
                        {spot !== "head" && <span className="req-now flex flex-wrap gap-2">{videoButton}</span>}
                        <RequestCoverCell requestId={row.request_id} />
                        <RequestPublish requestId={row.request_id} />
                      </>
                    )}
                    {report && <ReportCell reportId={report} />}
                {closing && <ClosingCell period={closing.period} date={closing.date} />}
                    {infraResult && <InfraCell result={infraResult} />}
                    {isInsight && plan && <PlanCell card={plan} plan={found?.fullPlan} />}
                    {/* 끝난 제작안: 주인공 · 의뢰 지시 N/N · 제작 검사 (사람 확인 중이면 그 단계 버튼 위에만) */}
                    {isInsight && found?.fullPlan && !humanDeciding && (
                      <div className="space-y-1.5">
                        {goalLine(found.fullPlan) && <p className="break-words text-[14px] text-ink-700">{goalLine(found.fullPlan)}</p>}
                        {lead && <p className="break-words text-[14px] text-emerald-800">✓ 주인공: {lead}</p>}
                        <RequestRules v={found.fullPlan} />
                        <CheckLog v={found.fullPlan} />
                        {castKo && <p className="break-words text-[14px] text-emerald-800">✓ 등장 캐릭터: {castKo}</p>}
                      </div>
                    )}
                    {isInsight && proposal && !plan && (
                      <button type="button" className="btn-primary btn-card" onClick={() => navigate(planDocPath({ id: proposal, request: row.request_id }))}>기획 제안 열기</button>
                    )}
                    {spot === s.stepId && creative}
                    {/* 11/26 사람 확인: 제작안 검사 결과(의뢰 지시 ✓/✕)를 버튼 위에 */}
                    {deciding_ && s.skill === "human_check" && found?.fullPlan && (
                      <div className="space-y-1.5">
                        {goalLine(found.fullPlan) && <p className="break-words text-[14px] text-ink-700">{goalLine(found.fullPlan)}</p>}
                        <RequestRules v={found.fullPlan} /><CheckLog v={found.fullPlan} />
                      </div>
                    )}
                    {deciding_ && s.notice && <p className="text-[13.5px] text-amber-800">{s.notice}</p>}
                    {deciding_ && unansweredNotice(row, s.stepId) && (
                      <p className="text-[13.5px] text-amber-800">{unansweredNotice(row, s.stepId)}</p>
                    )}
                    {s.decide && !decided.has(s.stepId) && (
                      <span className="req-now flex flex-wrap gap-2 pt-0.5">
                        <button type="button" className="btn-primary btn-card" disabled={deciding !== null} onClick={() => decide(s.stepId, true)}>{s.decide.approve}</button>
                        <button type="button" className="btn-ghost btn-card" disabled={deciding !== null} onClick={() => decide(s.stepId, false)}>{s.decide.stop}</button>
                      </span>
                    )}
                    {s.decide && failed[s.stepId] && <p role="alert" className="text-[13.5px] text-red-700">{failed[s.stepId]}</p>}
                  </div>
                </li>
              );
            })}
            {perfView && (
              <ExtraStep n={rows.length + 1} tone={perfView.done ? "ok" : "run"} dept="data" label="성과 측정" chip={perfView.chip}>
                <PerfCell view={perfView} />
              </ExtraStep>
            )}
            {perfView && result && (
              <ExtraStep n={rows.length + 2} tone={result.chip.label === "결과 받음" ? "ok" : "todo"} dept="planning" label="결과 받기" chip={result.chip}>
                <p className="break-words text-[13.5px] text-ink-500">{result.line}</p>
              </ExtraStep>
            )}
          </ol>
          {spot === "bottom" && creative}
        </>
      )}
    </section>
  );
}

/** 의뢰 원문이 이보다 길면 [의뢰 전체 보기] (2줄 말줄임) */
const ASK_FOLD_CHARS = 80;

/** 진행 막대: 단계 수만큼 칸 + 아래 짧은 단계 이름(좁으면 숨김). 글자 "지금 N단계 / 7"이 대신 읽힌다 */
function Bar({ cells, labels }: { cells: CardTone[]; labels: string[] }) {
  const cols = { gridTemplateColumns: `repeat(${Math.max(cells.length, 1)}, minmax(0, 1fr))` };
  return (
    <div aria-hidden="true" className="space-y-1">
      <div className="grid gap-1" style={cols}>{cells.map((t, i) => <i key={i} className={`block h-2 ${TONE_CLASS[t].bar}`} />)}</div>
      <div className="hidden gap-1 text-center text-[12.5px] text-ink-500 min-[481px]:grid" style={cols}>{labels.map((l, i) => <span key={i} className="truncate">{l}</span>)}</div>
    </div>
  );
}

/** 접힌 카드의 작은 칸 막대 */
function MiniBar({ cells }: { cells: CardTone[] }) {
  return <div aria-hidden="true" className="flex gap-1">{cells.map((t, i) => <i key={i} className={`block h-1.5 max-w-[22px] flex-1 ${TONE_CLASS[t].bar}`} />)}</div>;
}

/** 계획 밖 줄(성과 측정 · 결과 받기): 단계 줄과 같은 모양 */
function ExtraStep({ n, tone, dept, label, chip, children }: { n: number; tone: CardTone; dept: string; label: string; chip: { label: string; tone: string }; children: ReactNode }) {
  const tag = deptTag(dept);
  return (
    <li className="grid grid-cols-[2rem_minmax(0,1fr)] gap-3 px-2 py-3 min-[481px]:grid-cols-[2.5rem_minmax(0,1fr)]">
      <span className={`grid h-7 w-7 place-items-center text-[13px] font-bold min-[481px]:h-[34px] min-[481px]:w-[34px] min-[481px]:text-[15px] ${TONE_CLASS[tone].num}`} aria-hidden="true">{tone === "ok" ? "✓" : n}</span>
      <div className="min-w-0 space-y-1.5">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className={`border border-current px-2 py-px text-[12.5px] font-bold ${tag.cls}`}>{tag.label}</span>
          <b className="text-base font-bold">{label}</b>
          <span className={`chip text-[13px] ${chip.tone}`}>{chip.label}</span>
        </p>
        {children}
      </div>
    </li>
  );
}
