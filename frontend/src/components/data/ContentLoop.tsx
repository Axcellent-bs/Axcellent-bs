import { Fragment } from "react";
import { Chip, TONE } from "@/components/finance/bits";
import { LOOP_BACK, LOOP_STAGES, type LoopStage } from "@/lib/contentLoop";
import { UNKNOWN } from "@/lib/data";

// 콘텐츠 루프 띠 (트렌드 탭 맨 위). 단계 이름·담당·상태는 lib/contentLoop.ts 한 곳에서 온다.
// 1단계만 숫자를 보여 준다: 트렌드 탭이 이미 받은 trend_scan 응답의 판정 개수(다시 부르지 않는다).
// 응답이 없거나 실패하면 숫자 없이 "확인할 수 없음". 2~5단계는 숫자 없이 상태 칩만 보여 준다.

/** 1단계에 보여 줄 trend_scan 판정 개수. 화면이 응답의 results를 센 값이다 */
export type TrendCounts = { rising: number; normal: number; unknown: number };
export type LoopTrendState =
  | { kind: "loading" }
  | { kind: "counts"; counts: TrendCounts }
  | { kind: "unknown"; reason: string };

function TrendLine({ state }: { state: LoopTrendState }) {
  if (state.kind === "loading") return <p className="text-sm text-ink-500" aria-live="polite">판정하는 중…</p>;
  if (state.kind === "unknown") {
    return <p className="text-sm text-amber-700">{UNKNOWN} · {state.reason}</p>;
  }
  const { rising, normal, unknown } = state.counts;
  return (
    <p className="text-sm text-ink-700">
      이번 판정 급상승 <b className="text-ink-900">{rising}개</b> · 평범 {normal}개 · 판정 불가 {unknown}개
    </p>
  );
}

function StageCard({ stage, trend }: { stage: LoopStage; trend: LoopTrendState }) {
  const frame = stage.live
    ? "border-2 border-brand-500 bg-surface"
    : "border border-dashed border-ink-300 bg-ink-50";
  return (
    <li className={`flex min-w-0 flex-1 flex-col gap-2 p-4 ${frame}`} aria-label={`${stage.no}단계 ${stage.title}`}>
      <p className="text-[11px] font-semibold text-ink-500">{stage.no}</p>
      <h3 className="text-base font-bold text-ink-900">{stage.title}</h3>
      <p className="text-xs text-ink-500">{stage.owner}</p>
      {stage.source === "trend_scan" ? <TrendLine state={trend} /> : <p className="text-sm text-ink-700">{stage.description}</p>}
      <div className="mt-auto space-y-1 pt-1">
        <Chip label={stage.status.label} tone={TONE[stage.status.tone]} />
        {stage.note && <p className="text-[11px] leading-relaxed text-ink-500">{stage.note}</p>}
      </div>
    </li>
  );
}

export function ContentLoop({ trend }: { trend: LoopTrendState }) {
  const live = LOOP_STAGES.filter((s) => s.live).map((s) => s.no);
  const first = LOOP_STAGES[0]?.no;
  const last = LOOP_STAGES[LOOP_STAGES.length - 1]?.no;
  return (
    <section aria-labelledby="content-loop-title" className="card space-y-4 p-4 sm:p-5">
      <div>
        <p className="panel-title">콘텐츠 루프</p>
        <h2 id="content-loop-title" className="mt-1 text-base font-bold text-ink-900">
          지금 실제 데이터로 도는 단계는 {live.join("·")}번. 나머지는 만드는 중이거나 준비 중
        </h2>
      </div>
      <ol className="flex flex-col gap-2 lg:flex-row lg:items-stretch">
        {LOOP_STAGES.map((stage, i) => (
          <Fragment key={stage.no}>
            {i > 0 && (
              <li aria-hidden className="flex items-center justify-center text-ink-500">
                <span className="lg:hidden">↓</span>
                <span className="hidden lg:inline">→</span>
              </li>
            )}
            <StageCard stage={stage} trend={trend} />
          </Fragment>
        ))}
      </ol>
      <div className="flex items-center gap-3 text-xs text-ink-500" aria-label={`${last}단계에서 ${first}단계로 돌아감`}>
        <span className="whitespace-nowrap">{last} → {first}</span>
        <span aria-hidden className="h-px min-w-6 flex-1 border-t border-dashed border-ink-300" />
        <span className="text-right">{LOOP_BACK}</span>
      </div>
      <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-500">
        <span className="flex items-center gap-1.5"><span aria-hidden className="inline-block h-3 w-3 border-2 border-brand-500 bg-surface" />실제 데이터로 동작</span>
        <span className="flex items-center gap-1.5"><span aria-hidden className="inline-block h-3 w-3 border border-dashed border-ink-300 bg-ink-50" />아직 데이터 없음 (숫자를 만들지 않음)</span>
      </p>
    </section>
  );
}
