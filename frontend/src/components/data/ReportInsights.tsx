import { createContext, useContext, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { TONE, Waiting } from "@/components/finance/bits";
import { PreparingChip } from "@/components/Preparing";
import { when } from "@/components/data/Sources";
import { safeLink } from "@/lib/safeLink";
import type { Proposal } from "@/lib/proposals";
import { sectionState, type SectionState } from "@/lib/segmentView";
import { reportHash } from "@/lib/trendReportView";
import {
  HANDOFF_GATED_TEXT, deliveryOf, handoffChip, handoffGated, insightReports, proposalFor, reportInsightId, type ChipTone, type DeliveryChip,
} from "@/lib/handoffView";
import {
  FIRST_ROWS, cardHead, directionChips, evidenceFacts, factFlags, insightSourceLabel, orderFacts, planResult, reportStatusLabel, themeCandidates, weakWarning,
} from "@/lib/insightView";
import { dataAi, type ReceiveInsightFact, type ReceiveInsightInput, type TrendReportHandoffOutput, type TrendReportListItem } from "@/lib/data";

// 인사이트 탭 맨 위 "분석 의뢰 보고서" 인사이트 카드 (11/20 v3). 보고서 화면 맨 아래도 같은 카드를 쓴다.
// - 목록: trend_report_list(10) 중 완료·부분 완료이고 has_insight가 false 아닌 것. 카드마다 trend_report_insight(처음 한 번, 동시에 최대 3개)
// - 11/26 카드 = "기획에 넘긴 것" (lib/insightView.ts): 머리 "TR-xxxx → 기획 PROP-n"(없으면 "아직 기획에 안 감") · 완료/일부 완료 · 시각,
//   기획에 넘긴 것 상자(결론 · 방향 칩 · 주제 후보 · 형식), 근거 표 처음 3줄(약하지 않은 근거 먼저, 출처는 한국어 섹션 이름) + "근거 N개 모두 ▸",
//   처음 3줄이 모두 약하면 경고 한 줄, 기획 결과 한 줄(AI 제작안·규칙 초안·사람이 씀 + 상태), 보고서 열기 · PROP-n 열기 · 기획에 보내기
// - 보여 줄 것(옛): "분석 의뢰 · TR-xxxx" · 만든 시각 · 전달 상태 칩, 제목·요약, "보고서 열기"·"기획에 보내기" (외부 링크는 safeLink)
// - 기획에 보내기: trend_report_handoff. 누르는 동안·보낸 뒤 버튼을 끈다. 이미 기획 기록에 있거나 목록 handoff가 created·duplicate면 칩만.
//   게이트웨이에 막히면(403·404·거부) "기획에 보내기 준비 중 (허용 목록 추가 대기)"
// - 전달 상태: 기획 기록(proposals from data, insightId = "trend_report:" + report_id) → 보낸 결과 → "아직 기획에 안 감" (lib/handoffView.ts)
// - 외부 글(요약·근거)은 글자로만

/** 기획 AI 기록 (TrendScan이 30초마다 받아 내려 준다). 못 읽었으면 null */
export const PlanningProposals = createContext<Proposal[] | null>(null);
export const usePlanningRecord = () => useContext(PlanningProposals);

// 보고서 인사이트는 동시에 최대 3개만 부른다 (카드가 많아도 서버를 한꺼번에 두드리지 않게)
let running = 0;
const waiting: (() => void)[] = [];
function limited<T>(job: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const start = () => {
      running += 1;
      job().then(resolve, reject).finally(() => {
        running -= 1;
        waiting.shift()?.();
      });
    };
    if (running < 3) start();
    else waiting.push(start);
  });
}

const TONE_OF: Record<ChipTone, string> = { ok: TONE.ok, warn: TONE.warn, muted: TONE.muted, bad: TONE.bad };
export function DeliveryBadge({ chip }: { chip: DeliveryChip }) {
  return <span className={`chip text-[12.5px] ${TONE_OF[chip.tone]}`}>{chip.text}</span>;
}

type SendState = { kind: "idle" } | { kind: "busy" } | { kind: "sent"; out: TrendReportHandoffOutput } | { kind: "gated" } | { kind: "failed"; message: string };

/** "기획에 보내기" 버튼. 이미 보냈으면(sent) 그리지 않는다. 보낸 결과는 onSent로 올려 칩을 바꾼다 */
export function HandoffButton({ reportId, already, onSent, className = "btn-primary" }: {
  reportId: string; already: boolean; onSent: (out: TrendReportHandoffOutput) => void; className?: string;
}) {
  const [s, setS] = useState<SendState>({ kind: "idle" });
  if (already && s.kind !== "failed") return null;
  if (s.kind === "gated") return <span className="inline-flex items-center gap-1.5 text-xs text-violet-800"><PreparingChip />{HANDOFF_GATED_TEXT}</span>;
  if (s.kind === "sent") return null; // 결과 칩이 대신 보인다
  const send = () => {
    if (s.kind === "busy") return;
    setS({ kind: "busy" });
    dataAi.trendReportHandoff(reportId)
      .then((r) => { setS({ kind: "sent", out: r.output }); onSent(r.output); })
      .catch((e: Error & { status?: number }) => {
        if (handoffGated(e)) setS({ kind: "gated" });
        else setS({ kind: "failed", message: sectionState(e.message).kind === "missing" ? HANDOFF_GATED_TEXT : e.message });
      });
  };
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <button type="button" className={className} disabled={s.kind === "busy"} onClick={send}>{s.kind === "busy" ? "보내는 중…" : "기획에 보내기"}</button>
      {s.kind === "failed" && <span role="alert" className="text-xs text-red-700">{s.message}</span>}
    </span>
  );
}

type InsightLoad = { kind: "loading" } | { kind: "ok"; insight: ReceiveInsightInput | null; reason: string | null } | { kind: "error"; state: SectionState };

function useReportInsight(reportId: string): InsightLoad {
  const [load, setLoad] = useState<InsightLoad>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    setLoad({ kind: "loading" });
    limited(() => dataAi.trendReportInsight(reportId))
      .then((r) => { if (live) setLoad({ kind: "ok", insight: r.output.insight, reason: r.output.reason }); })
      .catch((e: Error) => { if (live) setLoad({ kind: "error", state: sectionState(e.message) }); });
    return () => { live = false; };
  }, [reportId]);
  return load;
}

const ROW_TONE: Record<"ok" | "warn" | "bad", ChipTone> = { ok: "ok", warn: "warn", bad: "bad" };

/** 근거 표 한 줄: 근거 · 값(줄어드는 중 ↓·표본 작음 칩) · 출처 */
function FactRow({ f, reportId }: { f: ReceiveInsightFact; reportId: string }) {
  const flags = factFlags(f.value);
  const link = safeLink(f.link);
  return (
    <tr className="border-t border-ink-100 align-top">
      <td className="break-words py-1.5 pr-2 text-ink-900">{f.label}</td>
      <td className="break-words py-1.5 pr-2">
        <span className={`font-bold tabular-nums ${flags.falling ? "text-red-700" : "text-ink-900"}`}>{flags.falling && <span aria-hidden="true">↓ </span>}{f.value}</span>
        {flags.falling && <span className={`chip ml-1 text-[12.5px] ${TONE.bad}`}>줄어드는 중</span>}
        {flags.smallSample && <span className={`chip ml-1 text-[12.5px] ${TONE.warn}`}>표본 작음</span>}
      </td>
      <td className="break-words py-1.5 text-[13.5px] text-ink-500">
        {insightSourceLabel(f, reportId)}
        {link && <> · <a href={link} target="_blank" rel="noreferrer noopener" className="text-brand-700 hover:underline">원문</a></>}
      </td>
    </tr>
  );
}

function FactTable({ rows, reportId, caption }: { rows: ReceiveInsightFact[]; reportId: string; caption: string }) {
  return (
    <table className="w-full table-fixed text-[14.5px]">
      <caption className="sr-only">{caption}</caption>
      <colgroup><col /><col className="w-[34%]" /><col className="w-[28%]" /></colgroup>
      <thead><tr className="text-left text-[12.5px] text-ink-500"><th scope="col" className="pb-1 font-medium">근거</th><th scope="col" className="pb-1 font-medium">값</th><th scope="col" className="pb-1 font-medium">출처</th></tr></thead>
      <tbody>{rows.map((f, k) => <FactRow key={`${f.label}-${k}`} f={f} reportId={reportId} />)}</tbody>
    </table>
  );
}

/** "기획에 넘긴 것" 상자: 결론 · 방향 칩 · 주제 후보 · 형식 (없는 줄은 생략) */
function Handed({ insight }: { insight: ReceiveInsightInput }) {
  const dirs = directionChips(insight.facts);
  const themes = themeCandidates(insight.facts);
  return (
    <div className="space-y-2 rounded-xl bg-ink-50 px-3.5 py-3">
      <p className="text-[12.5px] font-semibold text-ink-500">기획에 넘긴 것</p>
      <p className="whitespace-pre-line break-words text-[14.5px] text-ink-900"><b>결론</b> {insight.summary}</p>
      {dirs.length > 0 && (
        <p className="flex flex-wrap items-center gap-1.5 text-[14.5px] text-ink-700"><b className="text-ink-900">방향</b>
          {dirs.map((d) => <span key={d} className={`chip text-[12.5px] ${TONE.info}`}>{d}</span>)}
        </p>
      )}
      {themes.length > 0 && <p className="break-words text-[14.5px] text-ink-700"><b className="text-ink-900">주제 후보</b> {themes.join(" · ")}</p>}
      {insight.shorts_format && <p className="break-words text-[14.5px] text-ink-700"><b className="text-ink-900">형식</b> {insight.shorts_format}</p>}
    </div>
  );
}

/** 보고서 인사이트 카드 하나 = 기획에 넘긴 것 (11/26). listHandoff: 목록이 준 지난 전달 결과 (보고서 화면에서는 없음) */
export function ReportInsightCard({ reportId, createdAt, status = null, listHandoff = null, sent, onSent }: {
  reportId: string; createdAt: string | null; listHandoff?: TrendReportHandoffOutput | null;
  /** 보고서 상태(done·partial) → 완료·일부 완료 칩. 모르면 칩 없음 */
  status?: string | null;
  /** 바깥(보고서 머리 버튼)과 결과를 같이 쓸 때 */
  sent?: TrendReportHandoffOutput | null; onSent?: (out: TrendReportHandoffOutput) => void;
}) {
  const navigate = useNavigate();
  const planning = usePlanningRecord();
  const load = useReportInsight(reportId);
  const [mine, setMine] = useState<TrendReportHandoffOutput | null>(null);
  const result = sent ?? mine;
  const proposal = proposalFor(planning, reportInsightId(reportId));
  const chip = result && !proposal ? handoffChip(result) : deliveryOf(proposal, listHandoff);
  const handle = (out: TrendReportHandoffOutput) => { setMine(out); onSent?.(out); };
  const open = () => navigate(`/data/trend${reportHash(reportId)}`);
  const insight = load.kind === "ok" ? load.insight : null;
  const facts = insight ? orderFacts(evidenceFacts(insight.facts)) : [];
  const warn = weakWarning(facts);
  const plan = planResult(proposal);
  const statusText = reportStatusLabel(status);

  return (
    <li className="card space-y-3 border-l-4 border-l-brand-500 p-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <b className="text-[15px] text-ink-900">{cardHead(reportId, proposal)}</b>
        {statusText && <span className={`chip text-[12.5px] ${status === "done" ? TONE.ok : TONE.warn}`}>{statusText}</span>}
        {createdAt && <span className={`chip text-[12.5px] ${TONE.muted}`}>{when(createdAt)}</span>}
        {!proposal && <DeliveryBadge chip={chip} />}
      </div>
      {warn && <p role="note" className="rounded-lg bg-amber-50 px-3 py-2 text-[14.5px] font-semibold text-amber-800">⚠ {warn}</p>}
      {load.kind === "loading" ? <Waiting what="보고서 인사이트를 읽는 중…" />
        : load.kind === "error" ? (
          <p className="text-[14.5px] text-ink-500">{load.state.kind === "missing" ? "보고서 인사이트는 데이터 AI에 붙이는 중이에요 (준비 중)" : load.state.message}</p>
        ) : !insight ? (
          <p className="text-[14.5px] text-ink-500">{load.reason ?? "이 보고서에서는 기획에 보낼 인사이트를 뽑지 못했어요"}</p>
        ) : (
          <>
            <h4 className="break-words text-base font-bold text-ink-900">{insight.title}</h4>
            <Handed insight={insight} />
            {facts.length > 0 && (
              <div className="space-y-1">
                <FactTable rows={facts.slice(0, FIRST_ROWS)} reportId={reportId} caption="기획이 먼저 볼 근거" />
                {facts.length > FIRST_ROWS && (
                  <details className="group">
                    <summary className="cursor-pointer select-none text-[14.5px] font-semibold text-brand-700 hover:underline">근거 {facts.length}개 모두 <span aria-hidden="true" className="inline-block group-open:rotate-90">▸</span></summary>
                    <div className="pt-1"><FactTable rows={facts.slice(FIRST_ROWS)} reportId={reportId} caption="나머지 근거" /></div>
                  </details>
                )}
              </div>
            )}
          </>
        )}
      {plan && (
        <p className="flex flex-wrap items-center gap-1.5 text-[14.5px] text-ink-700">
          <b className="text-ink-900">기획 결과</b> {plan.who} · <span className="min-w-0 break-words">{plan.title}</span>
          <DeliveryBadge chip={{ text: plan.status, tone: ROW_TONE[plan.tone], sent: true }} />
        </p>
      )}
      <div className="flex flex-wrap items-center gap-1.5 print-hide">
        <button type="button" className="btn-ghost px-2.5 py-1 text-[14px]" onClick={open}>보고서 열기</button>
        {proposal && <button type="button" className="btn-ghost px-2.5 py-1 text-[14px]" onClick={() => navigate(`/planning/proposals/${encodeURIComponent(proposal.id)}`)}>{proposal.id} 열기</button>}
        {insight && <HandoffButton reportId={reportId} already={chip.sent || !!result} onSent={handle} className="btn-primary px-2.5 py-1 text-[14px]" />}
      </div>
    </li>
  );
}

type ListLoad = { kind: "loading" } | { kind: "ok"; items: TrendReportListItem[] } | { kind: "error"; state: SectionState };

/** 인사이트 탭의 보고서 목록 (최근 10개 중 인사이트를 뽑을 수 있는 것). 개수는 필터 칩이 쓴다 */
export function useInsightReports(): ListLoad {
  const [load, setLoad] = useState<ListLoad>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    dataAi.trendReportList(10)
      .then((r) => { if (live) setLoad({ kind: "ok", items: insightReports(r.output.items) }); })
      .catch((e: Error) => { if (live) setLoad({ kind: "error", state: sectionState(e.message) }); });
    return () => { live = false; };
  }, []);
  return load;
}

export function ReportInsights({ load }: { load: ListLoad }) {
  if (load.kind === "loading") return <Waiting what="분석 의뢰 보고서를 읽는 중…" />;
  if (load.kind === "error") {
    return load.state.kind === "missing"
      ? <p role="status" className="flex flex-wrap items-center gap-2 rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-800"><PreparingChip /> 분석 의뢰 보고서 인사이트를 붙이는 중이에요.</p>
      : <p className="text-sm text-ink-500">분석 의뢰 보고서를 읽지 못했어요 · {load.state.message}</p>;
  }
  if (load.items.length === 0) return <p className="text-sm text-ink-500">인사이트를 뽑을 분석 의뢰 보고서가 아직 없어요. 분석 의뢰 탭에서 보고서를 만들면 여기에 나와요.</p>;
  return (
    <ul className="space-y-3" aria-label="분석 의뢰 보고서 인사이트">
      {load.items.map((i) => <ReportInsightCard key={i.report_id} reportId={i.report_id} createdAt={i.created_at} status={i.status} listHandoff={i.handoff ?? null} />)}
    </ul>
  );
}
