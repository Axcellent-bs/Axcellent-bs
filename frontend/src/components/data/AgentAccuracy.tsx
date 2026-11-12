import { Stat } from "@/components/admin/bits";
import { Chip, Empty, ErrorBox, Panel, TONE, Waiting, useResource } from "@/components/finance/bits";
import { shortDateTime } from "@/lib/format";
import {
  ACCURACY_METRICS, DATA_SERVER, UNKNOWN, dataAi, formatMetric, kpiLive, percent,
  type AccuracyReportOutput, type HumanFeedback,
} from "@/lib/data";

// 에이전트 정확도: 정답지로 잰 최근 평가 결과(accuracy_report)와 운영 중 사람 반응 비율(kpi_summary).
// 숫자는 데이터 AI가 준 값 그대로다. 값이 없으면 "확인할 수 없음".

const SOURCE_KO: Record<AccuracyReportOutput["source"], { label: string; tone: string }> = {
  latest: { label: "최근 평가", tone: TONE.ok },
  baseline: { label: "기준값 (커밋된 평가)", tone: TONE.info },
  none: { label: UNKNOWN, tone: TONE.muted },
};

function EvalResult({ report }: { report: AccuracyReportOutput }) {
  const source = SOURCE_KO[report.source] ?? SOURCE_KO.none;
  if (report.source === "none") return <Empty>정확도 평가 결과가 없습니다 ({UNKNOWN}).</Empty>;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
        <Chip label={source.label} tone={source.tone} />
        <span className="tabular-nums">{report.generated_at ? shortDateTime(report.generated_at) : "시각 모름"}</span>
        <span>시나리오 {report.case_count ?? UNKNOWN}{report.case_count != null ? "개" : ""}</span>
      </div>
      <MetricList report={report} metrics={MAIN} />
      {(REST.length > 0 || report.misses.length > 0) && (
        <details className="rounded-xl bg-surface-2 px-3 py-2 text-xs">
          <summary className="cursor-pointer font-medium text-ink-700">
            다른 지표 {REST.length}개{report.misses.length > 0 && ` · 틀린 케이스 ${report.misses.length}개`} 보기
          </summary>
          <div className="mt-3 space-y-3">
            <MetricList report={report} metrics={REST} />
            {report.misses.length > 0 && (
              <div>
                <p className="mb-1 font-medium text-ink-700">틀린 케이스 {report.misses.length}개</p>
                <ul className="space-y-1 text-ink-700">
                  {report.misses.map((m, i) => (
                    <li key={`${m.case_id}-${i}`} className="break-words"><span className="font-medium">[{m.area}]</span> {m.case_id}: {m.what}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

/** 기본으로 보이는 평가 지표 4개 (ACCURACY_METRICS의 key): 이상 탐지 맞춤 · 원인 1순위 적중 · 근거 없는 원인 후보 · 지어낸 숫자 */
const MAIN_KEYS = ["anomaly.detection_rate", "root_cause.top1_rate", "root_cause.ungrounded_survived_rate", "llm.fabricated_number_rate"];
const MAIN = MAIN_KEYS.map((k) => ACCURACY_METRICS.find((m) => m.key === k)).filter((m): m is (typeof ACCURACY_METRICS)[number] => !!m);
const REST = ACCURACY_METRICS.filter((m) => !MAIN_KEYS.includes(m.key));

function MetricList({ report, metrics }: { report: AccuracyReportOutput; metrics: typeof ACCURACY_METRICS }) {
  return (
    <dl className="grid gap-2 sm:grid-cols-2">
      {metrics.map((m) => {
        const value = report.metrics[m.key];
        return (
          <div key={m.key} className="flex items-center justify-between gap-3 rounded-xl bg-surface-2 px-3 py-2 ring-1 ring-ink-100">
            <dt className="min-w-0 text-xs text-ink-700">{m.label}</dt>
            <dd className={`shrink-0 text-sm font-semibold tabular-nums ${value == null ? "text-ink-500" : "text-ink-900"}`}>{formatMetric(value, m.unit)}</dd>
          </div>
        );
      })}
    </dl>
  );
}

function Feedback({ fb }: { fb: HumanFeedback | null | undefined }) {
  if (!fb) return <Empty>공통 로그가 없어 운영 중 반응 비율을 셀 수 없습니다 ({UNKNOWN}).</Empty>;
  return (
    <div className="grid gap-3 md:grid-cols-3">
      <Stat label="받은 요청 반려율" value={percent(fb.handoff_reject_rate)} sub={`사람이 끝낸 일 ${fb.handoffs_closed}건 중 반려 ${fb.handoffs_rejected}건`} tone={fb.handoff_reject_rate == null ? "text-ink-500" : ""} />
      <Stat label="승인 거절률" value={percent(fb.review_reject_rate)} sub={`사람이 결정한 보류 ${fb.reviews_decided}건 중 거절 ${fb.reviews_rejected}건`} tone={fb.review_reject_rate == null ? "text-ink-500" : ""} />
      <Stat label="확인할 수 없음 비율" value={percent(fb.unknown_rate)} sub={`에이전트 응답 ${fb.results_total}건 중 ${fb.results_unknown}건`} tone={fb.unknown_rate == null ? "text-ink-500" : ""} />
    </div>
  );
}

function LiveEval({ tick }: { tick: number }) {
  const report = useResource("accuracy", () => dataAi.accuracyReport().then((r) => ({ data: r.output, meta: { mock: false } })), tick);
  if (report.env) return <EvalResult report={report.env.data} />;
  if (report.error) return <ErrorBox message={`${report.error}. ${DATA_SERVER}가 켜져 있는지 확인해 주세요.`} onRetry={report.reload} />;
  return <Waiting />;
}

/** `feedback`: kpi_summary의 human_feedback (평상시 지표가 이미 받은 값) */
export function AgentAccuracy({ tick, feedback, feedbackError }: { tick: number; feedback: HumanFeedback | null | undefined; feedbackError?: string | null }) {
  return (
    <Panel id="data-accuracy" title="에이전트 정확도" hint="정답지 시나리오로 잰 평가 결과와, 운영 중 사람이 에이전트 일을 반려·거절한 비율입니다.">
      <div className="space-y-5">
        <section aria-labelledby="data-accuracy-eval">
          <h3 id="data-accuracy-eval" className="mb-2 text-sm font-semibold text-ink-900">평가 결과</h3>
          {kpiLive ? <LiveEval tick={tick} /> : <Empty>데이터 AI 토큰이 없어 평가 결과를 받지 않아요 ({UNKNOWN}).</Empty>}
        </section>
        <section aria-labelledby="data-accuracy-live">
          <h3 id="data-accuracy-live" className="mb-2 text-sm font-semibold text-ink-900">운영 중 사람 반응</h3>
          {feedbackError ? <ErrorBox message={feedbackError} /> : <Feedback fb={feedback} />}
        </section>
      </div>
    </Panel>
  );
}
