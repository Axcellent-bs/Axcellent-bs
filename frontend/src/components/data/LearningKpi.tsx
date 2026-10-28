import { Bars, Stat } from "@/components/admin/bits";
import { ChartWithTable, RingMeter, TableView } from "@/components/charts";
import { Chip, Empty, ErrorBox, Panel, TONE, Waiting, useResource } from "@/components/finance/bits";
import { humanId, shortDateTime } from "@/lib/format";
import {
  BLOCK_KO, DATA_SERVER, GRADE_KO, UNKNOWN, dataAi, duration, evidenceLines, kpiLive, minutes, num,
  type A2AResult, type KpiSummaryOutput,
} from "@/lib/data";

// 학습 효과·KPI (SCN-L): 회사 기억을 참조한 2차 사건이 1차보다 얼마나 빨리 복구됐는지, 자동 처리율·등급·차단·이관.
// 데이터 AI의 kpi_summary 결과를 그대로 보여 준다. 화면에서는 초 → 분 단위만 바꾼다.

/** 값이 0이거나 비었을 때 근거의 "확인할 수 없음" 사유를 같이 보여 준다 */
function Notes({ notes }: { notes: string[] }) {
  if (!notes.length) return null;
  return (
    <ul className="mt-2 space-y-1">
      {notes.map((n, i) => <li key={i} className="rounded-md bg-ink-100 px-2 py-1 text-[11px] leading-relaxed text-ink-500">{n}</li>)}
    </ul>
  );
}

function sum(r: Record<string, number> | null | undefined) {
  return Object.values(r ?? {}).reduce((a, b) => a + (num(b) ?? 0), 0);
}

function KpiBody({ res }: { res: A2AResult<KpiSummaryOutput> }) {
  const o = res.output;
  const { headline, notes } = evidenceLines(res.evidence?.data_summary);
  // 감지 시각 순서대로 1차, 2차 … (서버 값의 순서만 맞춘다)
  const incs = [...(o.incidents ?? [])].sort((a, b) => (a.detected_at ?? "").localeCompare(b.detected_at ?? ""));
  const name = (i: number, id: string) => `${i + 1}차 · ${humanId(id)}`;
  const bars: Record<string, number> = {};
  incs.forEach((x, i) => { const s = num(x.mttr_seconds); if (s != null) bars[name(i, x.context_id)] = minutes(s); });
  const rate = num(o.auto_handled_rate);
  const grades = o.grade_counts ?? {};
  const blocked = o.blocked_counts ?? {};
  const meta = { mock: res.fallback, collected_at: res.evidence?.collected_at ?? undefined };

  return (
    <div className="space-y-5">
      <Panel id="data-learning" title="사건별 복구 시간 비교" meta={meta}
        hint="같은 종류의 장애가 다시 났을 때, 회사 기억(지난 사건 기록)을 참조해 더 빨리 복구했는지 봅니다.">
        {headline && <p className="mb-4 rounded-2xl bg-brand-50 px-4 py-3 text-sm font-medium text-brand-700">{headline}</p>}
        {incs.length === 0 ? (
          <><Empty>비교할 사건이 없습니다 ({UNKNOWN}).</Empty><Notes notes={notes} /></>
        ) : (
          <div className="grid gap-5 lg:grid-cols-[3fr_2fr]">
            <div>
              <p className="mb-2 text-xs font-medium text-ink-700">복구까지 걸린 시간 (분)</p>
              <ChartWithTable
                chart={<Bars data={bars} />}
                table={<TableView columns={["사건", "감지", "복구", "복구까지 걸린 시간", "회사 기억"]}
                  rows={incs.map((x, i) => [name(i, x.context_id), shortDateTime(x.detected_at), shortDateTime(x.recovered_at), duration(x.mttr_seconds), x.used_memory === true ? "참조함" : x.used_memory === false ? "참조 안 함" : UNKNOWN])} />}
              />
            </div>
            <ul className="space-y-2">
              {incs.map((x, i) => (
                <li key={x.context_id} className="flex flex-wrap items-center gap-2 rounded-2xl bg-surface-2 px-4 py-3">
                  <span className="text-sm font-semibold text-ink-900">{i + 1}차</span>
                  <span className="text-xs text-ink-500">{humanId(x.context_id)}</span>
                  <span className="ml-auto text-sm font-semibold tabular-nums">{duration(x.mttr_seconds)}</span>
                  <Chip label={x.used_memory === true ? "회사 기억 참조" : x.used_memory === false ? "회사 기억 참조 안 함" : `회사 기억: ${UNKNOWN}`}
                    tone={x.used_memory === true ? TONE.info : TONE.muted} />
                </li>
              ))}
            </ul>
          </div>
        )}
      </Panel>

      <Panel id="data-kpi" title="자동 처리·안전 지표" meta={meta} hint="AI가 사람 없이 끝낸 비율, 안전 등급별 처리 건수, 안전 장치가 막은 요청, 사람에게 넘긴 건수입니다.">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-2xl bg-surface-2 px-4 py-3">
            <p className="text-xs font-medium text-ink-700">자동 처리율</p>
            <div className="mt-2 flex items-center gap-3">
              <RingMeter value={rate} size={72}><span className="text-sm font-semibold tabular-nums">{rate == null ? "–" : `${Math.round(rate * 100)}%`}</span></RingMeter>
              <p className="text-xs text-ink-500">{rate == null ? UNKNOWN : "사람 승인 없이 AI가 끝낸 비율"}</p>
            </div>
            {!rate && <Notes notes={notes} />}
          </div>
          <div className="rounded-2xl bg-surface-2 px-4 py-3">
            <p className="mb-2 text-xs font-medium text-ink-700">등급별 처리 (건)</p>
            {sum(grades) ? <Bars data={grades} labels={GRADE_KO} /> : <><p className="text-xs text-ink-500">0건</p><Notes notes={notes} /></>}
          </div>
          <div className="rounded-2xl bg-surface-2 px-4 py-3">
            <p className="mb-2 text-xs font-medium text-ink-700">차단 사유별 (건)</p>
            {sum(blocked) ? <Bars data={blocked} labels={BLOCK_KO} color="bg-red-500" /> : <><p className="text-xs text-ink-500">0건</p><Notes notes={notes} /></>}
          </div>
          <div className="flex flex-col">
            <Stat label="사람에게 넘긴 건수 (이관)" value={num(o.escalation_count) == null ? UNKNOWN : `${o.escalation_count}건`} sub="AI가 스스로 끝내지 않고 사람에게 넘긴 일" />
            {!o.escalation_count && <Notes notes={notes} />}
          </div>
        </div>
      </Panel>
    </div>
  );
}

function LiveKpi({ tick }: { tick: number }) {
  const res = useResource("kpi", () => dataAi.kpiSummary().then((r) => ({ data: r, meta: { mock: r.fallback } })), tick);
  if (!res.env) return res.error ? <ErrorBox message={`${res.error}. ${DATA_SERVER}가 켜져 있는지 확인하세요.`} onRetry={res.reload} /> : <Waiting />;
  return (
    <>
      {res.error && <div className="mb-3"><ErrorBox message={`새로 받지 못해 이전 값을 보여 줍니다. ${res.error}`} onRetry={res.reload} /></div>}
      <KpiBody res={res.env.data} />
    </>
  );
}

export function LearningKpi({ tick }: { tick: number }) {
  if (!kpiLive) {
    return (
      <Panel id="data-learning" title="학습 효과·KPI">
        <Empty>실시간 조회 꺼짐 — 운영에서는 dashboard-api 중계 예정</Empty>
      </Panel>
    );
  }
  return <LiveKpi tick={tick} />;
}
