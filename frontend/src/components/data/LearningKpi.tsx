import type { ReactNode } from "react";
import { Bars, Stat } from "@/components/admin/bits";
import { AgentAccuracy } from "@/components/data/AgentAccuracy";
import { ChartWithTable, RingMeter, TableView } from "@/components/charts";
import { Chip, Empty, ErrorBox, Panel, TONE, Waiting, useResource } from "@/components/finance/bits";
import type { OrchIncident } from "@/lib/agentChat";
import { humanId, shortDateTime } from "@/lib/format";
import {
  BLOCK_KO, DATA_SERVER, GRADE_KO, UNKNOWN, compareByType, dataAi, duration, eventTypeName, evidenceLines, faultKindOf, kpiLive, kpiNote,
  mttrUnit, num, type A2AResult, type KpiSummaryOutput,
} from "@/lib/data";

// 운영 지표: 복구 속도(회사 기억을 참조한 2차 사건이 1차보다 얼마나 빨리 복구됐는지) → AI 단독 처리율 → 평상시 지표.
// 데이터 AI(8106)의 kpi_summary(POST /data-a2a/a2a) 결과를 그대로 보여 준다.
// 화면에서는 단위(초·분)와 1차·마지막 비교 비율만 계산한다.

/** 계산에서 빠진 기록("확인할 수 없음" 사유)을 패널 맨 아래 한 곳에 접어 둔다 */
function Notes({ notes }: { notes: string[] }) {
  if (!notes.length) return null;
  return (
    <details className="mt-4 rounded-xl bg-surface-2 px-3 py-2 text-xs">
      <summary className="cursor-pointer font-medium text-ink-700">계산에서 빠진 기록 {notes.length}개 보기</summary>
      <ul className="mt-2 space-y-1">
        {notes.map((n, i) => <li key={i} className="leading-relaxed text-ink-500">{kpiNote(n, humanId)}</li>)}
      </ul>
    </details>
  );
}

function sum(r: Record<string, number> | null | undefined) {
  return Object.values(r ?? {}).reduce((a, b) => a + (num(b) ?? 0), 0);
}

/** 운영 지표 묶음 제목 (평상시 지표 · 복구 속도 · AI 단독 처리율) */
function Group({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={`${id}-group`} className="space-y-3">
      <h2 id={`${id}-group`} className="text-lg font-bold tracking-tight text-ink-900">{title}</h2>
      {children}
    </section>
  );
}

/** 사건 줄의 장애 종류: 장애 스위치·결제사 지연·잘못된 배포, 모르면 지표 종류 */
function kindLabel(inc: OrchIncident | undefined): string {
  const fault = faultKindOf(inc);
  const metric = eventTypeName(inc?.event_type);
  return fault ? `${fault} · ${metric}` : `${metric} · 장애 종류 기록 없음`;
}

function KpiBody({ res, accuracy, incidents }: { res: A2AResult<KpiSummaryOutput>; accuracy: ReactNode; incidents: OrchIncident[] | null }) {
  const o = res.output;
  const { headline, notes } = evidenceLines(res.evidence?.data_summary);
  // 감지 시각 순서대로 1차, 2차 … (서버 값의 순서만 맞춘다)
  const incs = [...(o.incidents ?? [])].sort((a, b) => (a.detected_at ?? "").localeCompare(b.detected_at ?? ""));
  const order = new Map(incs.map((x, i) => [x.context_id, i]));
  const name = (id: string) => `${(order.get(id) ?? 0) + 1}차 · ${humanId(id)}`;
  // 장애 종류는 총괄 사건 기록에서 (데이터 AI 요약에는 없다)
  const byId = new Map((incidents ?? []).map((i) => [i.context_id, i]));
  // 복구 시간이 없는 사건은 막대·목록에서 뺀다 (막대와 목록이 어긋나지 않게)
  const timed = incs.filter((x) => num(x.mttr_seconds) != null);
  const secs = timed.map((x) => x.mttr_seconds as number);
  // 같은 지표 종류끼리만 1차와 마지막을 비교한다 (종류가 하나뿐인 사건은 목록에만)
  const lines = compareByType(timed, (id) => byId.get(id)?.event_type);
  const unit = mttrUnit(secs);
  const bars: Record<string, number> = {};
  for (const x of timed) bars[name(x.context_id)] = unit.value(x.mttr_seconds as number);
  const untimed = incs.length - timed.length;
  const rate = num(o.auto_handled_rate);
  const grades = o.grade_counts ?? {};
  const blocked = o.blocked_counts ?? {};
  const meta = { mock: false, collected_at: res.evidence?.collected_at ?? undefined };
  const basis = headline && <span className="text-xs text-ink-500">{kpiNote(headline, humanId)}</span>;

  return (
    <div className="space-y-8">
      <Group id="data-mttr" title="복구 속도">
      <Panel id="data-learning" title="사건별 복구 시간 비교" meta={meta} tools={basis}
        hint="같은 종류의 장애가 다시 났을 때, 회사 기억(지난 사건 기록)을 참조해 더 빨리 복구했는지 봅니다.">
        {incs.length === 0 ? (
          <Empty>아직 비교할 사건이 없어요. 장애 사건이 해결되면 사건별 복구 시간이 여기에 쌓입니다.</Empty>
        ) : timed.length === 0 ? (
          <p className="rounded-2xl bg-surface-2 px-4 py-3 text-sm text-ink-500">복구 시간이 기록된 사건이 아직 없어요</p>
        ) : (
          <div className="space-y-3">
            {lines.length > 0 ? (
              <ul className="space-y-1 text-sm font-medium text-ink-900">{lines.map((l) => <li key={l}>{l}</li>)}</ul>
            ) : (
              <p className="text-sm font-medium text-ink-900">같은 종류 사건이 한 번 더 해결되면 비교가 나와요</p>
            )}
            <p className="text-xs text-ink-500">비교는 같은 지표 종류(결제 오류·결제 응답 지연)끼리만 해요. 종류가 다른 장애는 복구 방법이 달라 빠르기를 견주지 않아요.</p>
            <div className="grid gap-5 lg:grid-cols-[3fr_2fr]">
              <div>
                <p className="mb-2 text-xs font-medium text-ink-700">복구까지 걸린 시간 ({unit.unit})</p>
                <ChartWithTable
                  chart={<Bars data={bars} />}
                  table={<TableView columns={["사건", "장애 종류", "감지", "복구", "복구까지 걸린 시간", "회사 기억"]}
                    rows={timed.map((x) => [name(x.context_id), kindLabel(byId.get(x.context_id)), shortDateTime(x.detected_at), shortDateTime(x.recovered_at), duration(x.mttr_seconds), x.used_memory === true ? "참조함" : x.used_memory === false ? "참조 안 함" : UNKNOWN])} />}
                />
              </div>
              <ul className="space-y-2">
                {timed.map((x) => (
                  <li key={x.context_id} className="flex flex-wrap items-center gap-2 rounded-2xl bg-surface-2 px-4 py-3">
                    <span className="text-sm font-semibold text-ink-900">{(order.get(x.context_id) ?? 0) + 1}차</span>
                    <span className="text-xs text-ink-500">{humanId(x.context_id)}</span>
                    <Chip label={kindLabel(byId.get(x.context_id))} tone={TONE.muted} />
                    <span className="ml-auto text-sm font-semibold tabular-nums">{duration(x.mttr_seconds)}</span>
                    <Chip label={x.used_memory === true ? "회사 기억 참조" : x.used_memory === false ? "회사 기억 참조 안 함" : `회사 기억: ${UNKNOWN}`}
                      tone={x.used_memory === true ? TONE.info : TONE.muted} />
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
        {untimed > 0 && timed.length > 0 && <p className="mt-3 text-xs text-ink-500">복구 시간이 없는 사건 {untimed}건은 비교에서 뺐어요</p>}
      </Panel>
      </Group>

      <Group id="data-auto" title="AI 단독 처리율">
      <Panel id="data-kpi" title="자동 처리·안전 지표" meta={meta} hint="AI가 사람 없이 끝낸 사건 비율과, 사건 안에서 처리한 작업을 안전 등급·차단 사유별로 센 건수, 사람에게 넘긴 건수입니다.">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-2xl bg-surface-2 px-4 py-3">
            <p className="text-xs font-medium text-ink-700">자동 처리율 (사건)</p>
            <div className="mt-2 flex items-center gap-3">
              <RingMeter value={rate} size={72}><span className="text-sm font-semibold tabular-nums">{rate == null ? "–" : `${Math.round(rate * 100)}%`}</span></RingMeter>
              <p className="text-xs text-ink-500">{rate == null ? UNKNOWN : "사람 승인 없이 AI가 끝낸 비율"}</p>
            </div>
            {rate != null && <p className="mt-2 text-xs tabular-nums text-ink-700">사건 {incs.length}건 중 {Math.round(rate * incs.length)}건</p>}
          </div>
          <div className="rounded-2xl bg-surface-2 px-4 py-3">
            <p className="mb-2 text-xs font-medium text-ink-700">등급별 처리 (작업 건)</p>
            {sum(grades) ? <Bars data={grades} labels={GRADE_KO} /> : <p className="text-xs text-ink-500">0건</p>}
          </div>
          <div className="rounded-2xl bg-surface-2 px-4 py-3">
            <p className="mb-2 text-xs font-medium text-ink-700">차단 사유별 (작업 건)</p>
            {sum(blocked) ? <Bars data={blocked} labels={BLOCK_KO} color="bg-red-500" /> : <p className="text-xs text-ink-500">0건</p>}
          </div>
          <div className="flex flex-col">
            <Stat label="사람에게 넘긴 건수 (이관)" value={num(o.escalation_count) == null ? UNKNOWN : `${o.escalation_count}건`} sub="AI가 스스로 끝내지 않고 사람에게 넘긴 일" />
          </div>
        </div>
        <Notes notes={notes} />
      </Panel>
      </Group>

      <Group id="data-daily" title="평상시 지표">{accuracy}</Group>
    </div>
  );
}

function LiveKpi({ tick, incidents }: { tick: number; incidents: OrchIncident[] | null }) {
  const res = useResource("kpi", () => dataAi.kpiSummary().then((r) => ({ data: r, meta: { mock: false } })), tick);
  if (!res.env) {
    if (!res.error) return <Waiting />;
    return (
      <div className="space-y-5">
        <ErrorBox message={`${res.error}. ${DATA_SERVER}가 켜져 있는지 확인해 주세요.`} onRetry={res.reload} />
        <AgentAccuracy tick={tick} feedback={undefined} feedbackError={`운영 중 반응 비율: ${UNKNOWN} (KPI를 받지 못함)`} />
      </div>
    );
  }
  return (
    <>
      {res.error && <div className="mb-3"><ErrorBox message={`새로 받지 못해 이전 값을 보여 줍니다. ${res.error}`} onRetry={res.reload} /></div>}
      <KpiBody res={res.env.data} incidents={incidents} accuracy={<AgentAccuracy tick={tick} feedback={res.env.data.output.human_feedback ?? null} />} />
    </>
  );
}

/** `incidents`: 총괄 사건 목록(장애 종류를 붙이는 데 쓴다). 못 받았으면 null이고 비교 문장은 나오지 않는다 */
export function LearningKpi({ tick, incidents = null }: { tick: number; incidents?: OrchIncident[] | null }) {
  if (!kpiLive) {
    return (
      <div className="space-y-5">
        <Panel id="data-learning" title="운영 지표">
          <Empty>데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 평상시 지표를 받지 않아요. backend/의 scripts/run_web.sh로 화면을 켜면 토큰이 들어갑니다.</Empty>
        </Panel>
        <AgentAccuracy tick={tick} feedback={null} />
      </div>
    );
  }
  return <LiveKpi tick={tick} incidents={incidents} />;
}
