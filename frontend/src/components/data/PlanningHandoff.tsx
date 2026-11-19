import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { Panel, TONE, Waiting } from "@/components/finance/bits";
import { apiBase, fromBackend, planningApi } from "@/lib/planning";
import type { Proposal } from "@/lib/proposals";
import { chainOf, dataProposals, handoffSummary, sourceChip, type CellState } from "@/lib/handoffView";

// 트렌드&콘텐츠 "기획 전달" 탭 (#handoff, 11/20 v3). 기획 AI가 받은 데이터 인사이트와 진행 상태를 기획 기록에서 읽는다.
// - 출처: 기획 AI 상태(GET /state, lib/planning fromBackend)의 proposals 중 from "data" (lib/navCounts.ts와 같은 방식). 처음 열 때 + 30초마다
// - 요약 숫자 4개(기획이 받음 · 진행 중 · 마케팅 전달 · 성과 확인) + 줄마다 사슬(데이터 보냄 → 기획 PROP-n → 기획안/확정 → 마케팅 → 성과)
// - 줄을 누르면 기획 제안 화면(/planning/proposals/<id>). 상태 → 칸 매핑은 lib/handoffView.ts (proposalStage 재사용)
// - 같은 기록으로 인사이트 카드의 전달 상태 칩·탭 옆 배지 숫자를 센다 (TrendScan이 한 번 받아 나눠 쓴다)

export const PLANNING_POLL_MS = 30_000;

export type PlanLoad =
  | { kind: "loading" }
  | { kind: "ok"; all: Proposal[]; at: Date }
  | { kind: "failed"; reason: string; all: Proposal[] | null };

/** 기획 기록(proposals 전부)을 받아 30초마다 다시 받는다. 실패해도 직전 목록은 남긴다 */
export function usePlanningProposals(): { load: PlanLoad; reload: () => void } {
  const [load, setLoad] = useState<PlanLoad>({ kind: "loading" });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((x) => x + 1), []);
  useEffect(() => {
    let live = true;
    planningApi(apiBase("planning")).state()
      .then((raw) => { if (live) setLoad({ kind: "ok", all: fromBackend(raw, []).proposals ?? [], at: new Date() }); })
      .catch((e: unknown) => {
        if (!live) return;
        const reason = e instanceof Error ? e.message : "연결하지 못했어요";
        setLoad((prev) => ({ kind: "failed", reason, all: prev.kind === "ok" ? prev.all : prev.kind === "failed" ? prev.all : null }));
      });
    const t = setTimeout(() => setTick((x) => x + 1), PLANNING_POLL_MS);
    return () => { live = false; clearTimeout(t); };
  }, [tick]);
  return { load, reload };
}

/** 받은 기획 기록 (못 읽었으면 직전 것, 그것도 없으면 null) */
export const proposalsOf = (load: PlanLoad): Proposal[] | null => (load.kind === "ok" ? load.all : load.kind === "failed" ? load.all : null);

const CELL: Record<CellState, string> = {
  done: "border-emerald-300 bg-emerald-50 text-emerald-800",
  current: "border-brand-500 bg-brand-50 font-bold text-brand-700",
  future: "border-ink-100 bg-ink-50 text-ink-500 opacity-50",
};

function Row({ p }: { p: Proposal }) {
  const navigate = useNavigate();
  const c = chainOf(p);
  return (
    <li>
      <button type="button" onClick={() => navigate(`/planning/proposals/${encodeURIComponent(p.id)}`)}
        className="flex w-full flex-col gap-1.5 rounded-xl px-3 py-2.5 text-left ring-1 ring-ink-100 transition hover:bg-ink-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
        aria-label={`${p.title} · 기획 제안 ${p.id} 열기`}>
        <span className="flex flex-wrap items-center gap-1.5 text-sm">
          <span className={`chip text-[11px] ${TONE.info}`}>{sourceChip(p)}</span>
          <b className="min-w-0 break-keep text-ink-900">{p.title}</b>
        </span>
        <span className="flex flex-wrap items-center gap-1 text-xs">
          {c.cells.map((cell, i) => (
            <span key={cell.key} className="flex items-center gap-1">
              {i > 0 && <span aria-hidden="true" className="text-ink-400">→</span>}
              <span className={`rounded-md border px-1.5 py-0.5 ${CELL[cell.state]}`}>{cell.label}</span>
            </span>
          ))}
          {c.rejected && (
            <>
              <span aria-hidden="true" className="text-ink-400">→</span>
              <span className={`chip text-[11px] ${TONE.bad}`}>폐기{c.note ? ` · "${c.note}"` : ""}</span>
            </>
          )}
        </span>
        {c.warn && <span className="text-xs text-amber-700">{c.warn}</span>}
      </button>
    </li>
  );
}

const hhmm = (d: Date) => d.toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

export function PlanningHandoff({ load, onRetry }: { load: PlanLoad; onRetry: () => void }) {
  const all = proposalsOf(load);
  const list = dataProposals(all);
  const s = handoffSummary(list);
  return (
    <Panel id="data-trend-handoff" title="기획 전달 · 기획이 가져간 데이터 인사이트"
      hint={load.kind === "ok" ? `기획 기록 ${hhmm(load.at)} 기준 · 30초마다 다시 읽어요` : undefined}>
      <div className="space-y-3">
        {load.kind === "failed" && (
          <div role="alert" className="flex flex-wrap items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">
            <span className="min-w-0 flex-1">기획 AI 기록을 읽을 수 없어요 · {load.reason}</span>
            <button type="button" className="btn-ghost" onClick={onRetry}>다시 시도</button>
          </div>
        )}
        {all === null ? (load.kind === "loading" ? <Waiting what="기획 기록을 읽는 중…" /> : null) : list.length === 0 ? (
          <p className="text-sm text-ink-500">
            아직 기획이 가져간 데이터 인사이트가 없어요. 기획 화면의 &apos;받아오기&apos;나 인사이트 탭의 &apos;기획에 보내기&apos;로 보낼 수 있어요
          </p>
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {([["기획이 받음", s.received], ["진행 중 제안", s.active], ["마케팅 전달", s.marketing], ["성과 확인", s.measured]] as const).map(([label, n]) => (
                <div key={label} className="rounded-lg bg-ink-50 px-3 py-2">
                  <dd className="text-xl font-bold tabular-nums text-ink-900">{n.toLocaleString("ko-KR")}</dd>
                  <dt className="text-xs text-ink-500">{label}</dt>
                </div>
              ))}
            </dl>
            <ul className="space-y-2">{list.map((p) => <Row key={p.id} p={p} />)}</ul>
          </>
        )}
      </div>
    </Panel>
  );
}
