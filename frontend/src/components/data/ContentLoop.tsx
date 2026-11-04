import { Fragment } from "react";
import { Chip, TONE } from "@/components/finance/bits";
import { LOOP_BACK, LOOP_STAGES, PROBE_STATUS, type LoopProbe, type LoopProbeSkill, type LoopStage } from "@/lib/contentLoop";
import { UNKNOWN } from "@/lib/data";

// 콘텐츠 루프 띠 (트렌드 탭 맨 위). 단계 이름·담당·상태는 lib/contentLoop.ts 한 곳에서 온다.
// 1단계만 숫자를 보여 준다: 트렌드 탭이 이미 받은 trend_scan 응답의 판정 개수(다시 부르지 않는다).
// 응답이 없거나 실패하면 숫자 없이 "확인할 수 없음". 2~4단계는 숫자 없이 상태 칩만 보여 준다.
// 1·2·4단계 칩과 테두리는 트렌드 탭이 받은 trend_scan·trend_insight_brief·campaign_effect 응답 결과(probes)로 정한다.
// 테두리·칩·범례는 같은 말을 한다: 동작 중 = 파란 실선, 확인 중 = 회색 실선, 측정 대기·연결 확인 필요·준비 중 = 회색 점선.
// 단계를 누르면 트렌드 탭의 같은 이름 하위 탭으로 간다(onSelect). 지금 보는 단계는 바깥 고리(ring)로 표시한다.

/** 1단계에 보여 줄 판정 개수. 화면이 응답(trend_scan results + trend_candidates candidates)을 센 값이다.
 *  youtube: YouTube 후보 수 (기본 판정에서만, 직접 입력이면 없음). confirmed: 급상승 중 YouTube 근거도 있는 것.
 *  youtubeNote: YouTube 후보를 받지 못했거나 스냅샷이 없을 때 서버 이유 */
export type TrendCounts = {
  rising: number;
  normal: number;
  unknown: number;
  youtube?: number;
  confirmed?: number;
  youtubeNote?: string;
};
export type LoopTrendState =
  | { kind: "loading" }
  | { kind: "counts"; counts: TrendCounts }
  | { kind: "unknown"; reason: string };

function TrendLine({ state }: { state: LoopTrendState }) {
  if (state.kind === "loading") return <p className="text-sm text-ink-500" aria-live="polite">판정하는 중…</p>;
  if (state.kind === "unknown") {
    return <p className="text-sm text-amber-700">{UNKNOWN} · {state.reason}</p>;
  }
  const { rising, normal, unknown, youtube, confirmed, youtubeNote } = state.counts;
  return (
    <div className="space-y-0.5">
      <p className="text-sm text-ink-700">
        이번 판정 급상승 <b className="text-ink-900">{rising}개</b> · 평범 {normal}개 · 판정 불가 {unknown}개
        {youtube != null ? ` · YouTube 후보 ${youtube}개` : ""}
      </p>
      {confirmed != null && confirmed > 0 && (
        <p className="text-xs text-ink-700">두 소스 확인 (검색 급상승 + YouTube 인기 태그) {confirmed}개</p>
      )}
      {youtubeNote && <p className="text-xs text-amber-700">YouTube 후보: {UNKNOWN} · {youtubeNote}</p>}
    </div>
  );
}

export type LoopProbes = Record<LoopProbeSkill, LoopProbe>;

/** 카드 테두리. 범례도 이 표에서 그린다 */
type Frame = "live" | "checking" | "off";
const FRAME: Record<Frame, { box: string; legend: string }> = {
  live: { box: "border-2 border-brand-500 bg-surface", legend: "실제 데이터로 동작 (동작 중)" },
  checking: { box: "border-2 border-ink-300 bg-surface", legend: "서버 응답 확인 중" },
  off: { box: "border border-dashed border-ink-300 bg-ink-50", legend: "측정 대기 · 연결 확인 필요 · 아직 데이터 없음 (숫자를 만들지 않음)" },
};

/** 응답으로 상태를 정하는 단계(probe)는 응답 결과를, 나머지는 live 여부를 따른다 */
function frameOf(stage: LoopStage, probes: LoopProbes): Frame {
  if (!stage.live) return "off";
  if (!stage.probe) return "live";
  const probe = probes[stage.probe];
  return probe === "ok" ? "live" : probe === "checking" ? "checking" : "off";
}

/** 단계 카드. onSelect가 있으면 누르면 그 단계의 하위 탭으로 간다. active면 지금 보고 있는 하위 탭 */
function StageCard({ stage, trend, probes, active, onSelect }: {
  stage: LoopStage; trend: LoopTrendState; probes: LoopProbes; active: boolean; onSelect?: (stage: LoopStage) => void;
}) {
  const status = stage.probe ? PROBE_STATUS[probes[stage.probe]] : stage.status;
  const frame = FRAME[frameOf(stage, probes)].box;
  const body = (
    <>
      <p className="text-[11px] font-semibold text-ink-500">{stage.no}</p>
      <h3 className="text-base font-bold text-ink-900">{stage.title}</h3>
      <p className="text-xs text-ink-500">{stage.owner}</p>
      {stage.source === "trend_scan" ? <TrendLine state={trend} /> : <p className="text-sm text-ink-700">{stage.description}</p>}
      <div className="mt-auto space-y-1 pt-1">
        <Chip label={status.label} tone={TONE[status.tone]} />
        {stage.note && <p className="text-[11px] leading-relaxed text-ink-500">{stage.note}</p>}
      </div>
    </>
  );
  const box = `flex min-w-0 flex-col gap-2 p-4 text-left ${frame} ${active ? "ring-2 ring-brand-600 ring-offset-2" : ""}`;
  if (!onSelect) {
    return <li className={`flex-1 ${box}`} aria-label={`${stage.no}단계 ${stage.title}`}>{body}</li>;
  }
  return (
    <li className="flex min-w-0 flex-1">
      <button type="button" onClick={() => onSelect(stage)} aria-current={active ? "step" : undefined}
        aria-label={`${stage.no}단계 ${stage.title} 보기`}
        className={`w-full transition hover:bg-ink-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${box}`}>
        {body}
      </button>
    </li>
  );
}

export function ContentLoop({ trend, probes, active, onSelect }: {
  trend: LoopTrendState;
  probes: LoopProbes;
  /** 지금 보고 있는 하위 탭의 단계 번호 */
  active?: number;
  /** 단계를 누르면 그 하위 탭으로 */
  onSelect?: (stage: LoopStage) => void;
}) {
  const live = LOOP_STAGES.filter((s) => frameOf(s, probes) === "live").map((s) => s.no);
  const failed = LOOP_STAGES.filter((s) => s.live && s.probe && probes[s.probe] === "failed").map((s) => s.no);
  const checking = LOOP_STAGES.filter((s) => frameOf(s, probes) === "checking").map((s) => s.no);
  const idle = LOOP_STAGES.filter((s) => s.live && s.probe && probes[s.probe] === "idle").map((s) => s.no);
  const first = LOOP_STAGES[0]?.no;
  const last = LOOP_STAGES[LOOP_STAGES.length - 1]?.no;
  return (
    <section aria-labelledby="content-loop-title" className="card space-y-4 p-4 sm:p-5">
      <div>
        <p className="panel-title">콘텐츠 루프</p>
        <h2 id="content-loop-title" className="mt-1 text-base font-bold text-ink-900">
          {live.length ? `지금 실제 데이터로 도는 단계는 ${live.join("·")}번.` : "지금 실제 데이터로 도는 단계가 없어요."}
          {checking.length ? ` ${checking.join("·")}번은 확인 중.` : ""}
          {idle.length ? ` ${idle.join("·")}번은 측정 대기.` : ""}
          {failed.length ? ` ${failed.join("·")}번은 연결 확인 필요.` : ""}
          {" "}나머지는 만드는 중이거나 준비 중
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
            <StageCard stage={stage} trend={trend} probes={probes} active={stage.no === active} onSelect={onSelect} />
          </Fragment>
        ))}
      </ol>
      <div className="flex items-center gap-3 text-xs text-ink-500" aria-label={`${last}단계에서 ${first}단계로 돌아감`}>
        <span className="whitespace-nowrap">{last} → {first}</span>
        <span aria-hidden className="h-px min-w-6 flex-1 border-t border-dashed border-ink-300" />
        <span className="text-right">{LOOP_BACK}</span>
      </div>
      <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-500">
        {(Object.keys(FRAME) as Frame[]).map((f) => (
          <span key={f} className="flex items-center gap-1.5"><span aria-hidden className={`inline-block h-3 w-3 ${FRAME[f].box}`} />{FRAME[f].legend}</span>
        ))}
        {onSelect && <span>단계를 누르면 아래에서 그 단계를 봐요 (바깥 고리 = 지금 보는 단계)</span>}
      </p>
    </section>
  );
}
