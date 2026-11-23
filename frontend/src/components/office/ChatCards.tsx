import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { TONE } from "@/components/finance/bits";
import { dataAi, type TrendReport } from "@/lib/data";
import type { ChatCard, TrendReportDoneCard, TrendReportProgressCard, TrendRequestCard } from "@/lib/planningData";
import { sectionState, type SectionState } from "@/lib/segmentView";
import {
  cardInput, doneView, formFromCard, progressKeepPolling, progressPct, reportPath, requestCardRows, submitState, submitView,
  type SubmitState, type TrendNavState,
} from "@/lib/chatCards";
import { REPORT_POLL_MS, REPORT_STATUS, reportHash, reportLabel } from "@/lib/trendReportView";
import { CreativeChatCard } from "./CreativeChatCard";

// 데이터분석 1:1 대화(DirectChat, 데이터분석 AI만)의 메시지 카드 3종 (공통 계약 lib/planningData.ts ChatCard).
// - 분석 의뢰 카드: 칸 요약(프리셋 꼬리표) · 이유 · 예상 호출 · [분석 의뢰 넣기](trend_report_create, 한 번만) · [분석 의뢰 탭에서 고치기] · [아니에요](접기)
// - 진행 카드: 대화가 열려 있는 동안 5초마다 trend_report_get으로 % (끝난 섹션 ÷ 5), 끝나면 멈춘다
// - 완료 카드: 결론 한 줄 · 핵심 근거 최대 3개 · [보고서 열기] · [PDF로 저장](보고서를 열고 인쇄)
// 서버 글(의뢰문·결론·근거)은 글자로만 넣는다. 계산은 lib/chatCards.ts

/** 접수 결과: 대화 목록을 다시 읽어 카드가 다시 그려져도 같은 메시지로 두 번 접수하지 않게 (이 탭이 열려 있는 동안) */
const submitted = new Map<string, SubmitState>();

export function ChatCardView({ card, messageId, threadId }: { card: ChatCard; messageId: string; threadId: string | null }) {
  if (card.kind === "creative") return <CreativeChatCard card={card} />;
  if (card.kind === "trend_request") return <RequestCard card={card} messageId={messageId} threadId={threadId} />;
  if (card.kind === "trend_report_progress") return <ProgressCard card={card} />;
  return <DoneCard card={card} />;
}

function RequestCard({ card, messageId, threadId }: { card: TrendRequestCard; messageId: string; threadId: string | null }) {
  const navigate = useNavigate();
  // 서버가 accepted_report_id를 주면 그것이 먼저(새로고침해도 접수됨), 없으면 이 탭이 기억한 상태
  const [local, setLocal] = useState<SubmitState | null>(() => submitted.get(messageId) ?? null);
  const state = submitState(card, local);
  const [folded, setFolded] = useState(false);
  const set = (s: SubmitState) => { submitted.set(messageId, s); setLocal(s); };
  const view = submitView(state);
  const done = state.kind === "done";

  const submit = () => {
    if (state.kind !== "idle" && !(state.kind === "error" && !state.missing)) return;
    set({ kind: "busy" });
    dataAi.trendReportCreate(cardInput(card, threadId))
      .then((r) => set({ kind: "done", reportId: r.output.report_id, reused: r.output.reused }))
      .catch((e: Error) => {
        const s: SectionState = sectionState(e.message);
        set(s.kind === "missing" ? { kind: "error", missing: true, message: "" } : { kind: "error", missing: false, message: s.message });
      });
  };
  const edit = () => navigate("/data/trend#request", { state: { trendRequestFill: formFromCard(card) } satisfies TrendNavState });

  if (folded) {
    return (
      <div className="mt-2 flex w-full max-w-[80%] items-center gap-2 text-xs text-ink-500">
        분석 의뢰 카드를 접었어요
        <button type="button" className="text-brand-700 hover:underline" onClick={() => setFolded(false)}>다시 보기</button>
      </div>
    );
  }
  return (
    <section aria-label="분석 의뢰 카드" className="mt-2 w-full max-w-[80%] space-y-2 rounded-xl border border-ink-200 bg-surface px-3.5 py-3 text-sm">
      <h4 className="text-[13px] font-semibold text-ink-900">분석 의뢰 카드</h4>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
        {requestCardRows(card).map((r) => (
          <div key={r.key} className="contents">
            <dt className="text-xs text-ink-500">{r.label}</dt>
            <dd className="min-w-0 break-words text-ink-900">
              {r.value}
              {r.preset && <span className={`chip ml-1.5 text-[10px] ${TONE.muted}`}>프리셋</span>}
            </dd>
          </div>
        ))}
      </dl>
      {card.brief_reason && <p className="text-xs text-ink-500">{card.brief_reason}</p>}
      {card.cost_note && <p className="text-[11px] text-ink-400">예상 호출 · {card.cost_note}</p>}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        {view.button && (
          <button type="button" className="btn-primary btn-card" disabled={view.disabled} onClick={submit}>{view.button}</button>
        )}
        {view.note && (
          <span role="status" className={`text-xs ${done ? "font-semibold text-emerald-700" : state.kind === "error" ? "text-red-700" : "text-ink-500"}`}>
            {view.note}
            {view.openReport && state.kind === "done" && (
              <> · <button type="button" className="text-brand-700 hover:underline" onClick={() => navigate(`/data/trend${reportHash(state.reportId)}`)}>보고서 열기</button></>
            )}
          </span>
        )}
        {!done && (
          <>
            <button type="button" className="btn-ghost btn-card" onClick={edit}>분석 의뢰 탭에서 고치기</button>
            <button type="button" className="btn-ghost btn-card" onClick={() => setFolded(true)}>아니에요</button>
          </>
        )}
      </div>
    </section>
  );
}

type ProgressLoad = { kind: "loading" } | { kind: "ok"; report: TrendReport } | { kind: "error"; state: SectionState };

/** 보고서 진행 카드. onDone: 보고서가 끝나면(폴링을 멈출 때) 그 보고서를 넘긴다 (대화방 의뢰 진행 카드가 완료 카드로 바꿀 때) */
export function ProgressCard({ card, onDone }: { card: TrendReportProgressCard; onDone?: (report: TrendReport) => void }) {
  const navigate = useNavigate();
  const [load, setLoad] = useState<ProgressLoad>({ kind: "loading" });
  const [tick, setTick] = useState(0);
  const doneRef = useRef(onDone);
  useEffect(() => { doneRef.current = onDone; });
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    dataAi.trendReportGet(card.report_id)
      .then((r) => {
        if (!live) return;
        setLoad({ kind: "ok", report: r.output });
        if (progressKeepPolling(r.output.status)) timer = setTimeout(() => setTick((x) => x + 1), REPORT_POLL_MS);
        else doneRef.current?.(r.output);
      })
      .catch((e: Error) => { if (live) setLoad({ kind: "error", state: sectionState(e.message) }); });
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [card.report_id, tick]);

  const code = reportLabel(card.report_id);
  const pct = load.kind === "ok" ? progressPct(load.report) : 0;
  const st = load.kind === "ok" ? REPORT_STATUS[load.report.status] : null;
  const running = load.kind === "ok" && progressKeepPolling(load.report.status);
  return (
    <section aria-label="보고서 진행" className="mt-2 w-full max-w-[80%] space-y-1.5 rounded-xl border border-ink-200 bg-surface px-3.5 py-2.5 text-sm">
      <p className="flex flex-wrap items-center gap-2 text-xs text-ink-700">
        <b className="text-ink-900">보고서 {code}</b>
        {load.kind === "loading" && <span className="text-ink-500">진행 확인 중…</span>}
        {load.kind === "error" && <span className="text-ink-500">{load.state.kind === "missing" ? "진행 보기는 준비 중이에요" : load.state.message}</span>}
        {st && <span className={`chip text-[11px] ${TONE[st.tone]}`}>{st.label}</span>}
        {load.kind === "ok" && <span className="tabular-nums text-ink-500">{pct}%</span>}
        {load.kind === "ok" && !running && (
          <button type="button" className="text-brand-700 hover:underline" onClick={() => navigate(`/data/trend${reportHash(card.report_id)}`)}>보고서 열기</button>
        )}
      </p>
      <span className="block h-1.5 w-full rounded-full bg-ink-100" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={`보고서 ${code} 진행`}>
        <span className={`block h-1.5 rounded-full transition-all ${running ? "bg-brand-500" : "bg-emerald-500"}`} style={{ width: `${pct}%` }} />
      </span>
    </section>
  );
}

const DONE_TONE = {
  ok: { border: "border-emerald-400", title: "text-emerald-800" },
  warn: { border: "border-amber-400", title: "text-amber-800" },
  bad: { border: "border-red-400", title: "text-red-800" },
} as const;

export function DoneCard({ card }: { card: TrendReportDoneCard }) {
  const navigate = useNavigate();
  const v = doneView(card);
  const tone = DONE_TONE[v.tone];
  const path = reportPath(card.report_path, card.report_id);
  return (
    <section aria-label="보고서 완료" className={`mt-2 w-full max-w-[80%] space-y-2 rounded-xl border-2 ${tone.border} bg-surface px-3.5 py-3 text-sm`}>
      <h4 className={`text-[13px] font-semibold ${tone.title}`}>{v.title}</h4>
      {card.summary_line && <p className="whitespace-pre-line break-words font-medium text-ink-900">{card.summary_line}</p>}
      {v.evidence.length > 0 && (
        <ul className="space-y-0.5">{v.evidence.map((e, i) => <li key={i} className="text-xs text-ink-700">· {e}</li>)}</ul>
      )}
      <div className="flex flex-wrap gap-2 pt-1">
        <button type="button" className="btn-primary btn-card" onClick={() => navigate(path)}>보고서 열기</button>
        <button type="button" className="btn-ghost btn-card" onClick={() => navigate(path, { state: { trendReportPrint: true } satisfies TrendNavState })}>PDF로 저장</button>
      </div>
    </section>
  );
}
