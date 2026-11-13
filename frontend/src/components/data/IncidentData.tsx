import { Bars, Stat } from "@/components/admin/bits";
import { ChartWithTable, TableView } from "@/components/charts";
import { Chip, Empty, ErrorBox, Panel, RefList, TONE, Waiting, type Resource } from "@/components/finance/bits";
import { incidentTitle, type OrchIncident } from "@/lib/agentChat";
import { krw } from "@/lib/finance";
import { humanId, shortDateTime } from "@/lib/format";
import {
  CATEGORY_KO, CONFIDENCE_KO, FACT_KIND_KO, FACT_SOURCE_KO, FLOW, INCIDENT_STATUS_KO, ORCH_SERVER, STEP_STATUS_KO, UNKNOWN, bool, clock, count,
  detectorName, duration, impactOf, num, recoveryOf, rootCauseOf, skipReason, stepOf, type CauseFact,
} from "@/lib/data";

// 장애 사건 데이터 (결제 장애). 감지는 쇼핑몰 모니터·Zabbix가 하고, 총괄이 사건을 연 뒤 데이터 AI에 일을 맡긴다:
// 영향 분석 → 원인 후보 → 복구 판정 지원 → 처리 시간 요약.
// 숫자는 총괄 사건(GET /orch/api/incidents)에 담긴 데이터 AI 결과 그대로. 없으면 "확인할 수 없음".

const NO_RECORD = "기록 없음";

/** 감지 → 사건 열림 → 요청 받음 → 복구 확인. 시각이 없는 칸만 "기록 없음" */
function DetectionLine({ inc }: { inc: OrchIncident }) {
  const by = inc.detected_by;
  const cells: { label: string; who: string | null; at: string | null }[] = [
    { label: "감지", who: by?.source ? detectorName(by.source) : null, at: clock(by?.at) },
    { label: "사건 열림", who: "총괄", at: clock(inc.opened_at) },
    { label: "요청 받음", who: "데이터 AI", at: clock(stepOf(inc, "impact")?.started_at) },
    { label: "복구 확인", who: null, at: clock(inc.timeline?.recovered_at) },
  ];
  return (
    <ol aria-label="감지부터 복구까지" className="flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-2xl bg-surface-2 px-4 py-3 text-xs">
      {cells.map((c, i) => (
        <li key={c.label} className="flex items-center gap-2">
          {i > 0 && <span aria-hidden="true" className="text-ink-300">→</span>}
          <span className="font-semibold text-ink-900">{c.label}</span>
          {c.label === "감지" && !c.who ? (
            <span className="text-ink-500">({NO_RECORD})</span>
          ) : (
            <span className="tabular-nums text-ink-500">({[c.who, c.at ?? NO_RECORD].filter(Boolean).join(" · ")})</span>
          )}
        </li>
      ))}
    </ol>
  );
}

const RECENT = 6;
/** 원인 후보 근거로 모으는 사실 종류 (이상 구간·확인하지 못한 출처 제외) */
const CAUSE_KINDS = ["deploy", "notice", "inquiry_surge", "past_incident"] as const;

function IncidentPicker({ list, selected, onSelect }: { list: OrchIncident[]; selected: string | null; onSelect: (id: string) => void }) {
  const recent = list.slice(0, RECENT);
  const older = list.slice(RECENT);
  const olderSelected = older.some((i) => i.context_id === selected);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div role="tablist" aria-label="장애 사건 (최근 먼저)" className="flex min-w-0 flex-wrap gap-1.5">
        {recent.map((i) => {
          const on = i.context_id === selected;
          const st = INCIDENT_STATUS_KO[i.status] ?? { label: i.status, tone: "muted" as const };
          return (
            <button key={i.context_id} type="button" role="tab" aria-selected={on} aria-controls="data-incident-panel" onClick={() => onSelect(i.context_id)}
              className={`inline-flex max-w-full items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${on ? "bg-brand-600 text-white" : "bg-surface text-ink-700 ring-1 ring-ink-100 hover:bg-ink-100"}`}>
              <span className="truncate">{incidentTitle(i)}</span>
              <span className={`shrink-0 text-xs tabular-nums ${on ? "text-white/80" : "text-ink-500"}`}>{shortDateTime(i.opened_at)}</span>
              <span className={`chip shrink-0 ${TONE[st.tone]}`}>{st.label}</span>
            </button>
          );
        })}
      </div>
      {older.length > 0 && (
        <label className="min-w-0 max-w-full">
          <span className="sr-only">이전 사건 고르기</span>
          <select value={olderSelected ? selected ?? "" : ""} onChange={(e) => { if (e.target.value) onSelect(e.target.value); }}
            className={`max-w-full rounded-full border-0 py-2 pl-4 pr-8 text-sm font-medium ring-1 ${olderSelected ? "bg-brand-600 text-white ring-brand-600" : "bg-surface text-ink-700 ring-ink-100"}`}>
            <option value="">이전 사건 {older.length}건</option>
            {older.map((i) => <option key={i.context_id} value={i.context_id}>{incidentTitle(i)} · {shortDateTime(i.opened_at)}</option>)}
          </select>
        </label>
      )}
    </div>
  );
}

/** 4칸 가로 처리 흐름 */
function Flow({ inc }: { inc: OrchIncident }) {
  return (
    <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {FLOW.map((f, idx) => {
        const step = stepOf(inc, f.stepId);
        // 처리 시간 요약: 단계는 끝났어도 요약할 사건 기록(incidents)이 비어 있으면 결과는 확인할 수 없다
        const kpiEmpty = f.key === "kpi" && step?.status === "completed" && !(Array.isArray(step.output?.incidents) && step.output.incidents.length > 0);
        const st = !step ? null : kpiEmpty ? { label: UNKNOWN, tone: "muted" as const } : STEP_STATUS_KO[step.status] ?? { label: step.status, tone: "muted" as const };
        const at = clock(step?.finished_at ?? step?.started_at);
        return (
          <li key={f.key} className={`relative rounded-2xl px-4 py-3 ring-1 ${step ? "bg-surface-2 ring-ink-100" : "border border-dashed border-ink-300 ring-transparent"}`}>
            <div className="flex items-center gap-2">
              <span className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${step?.status === "completed" ? "bg-emerald-500 text-white" : step ? "bg-ink-300 text-white" : "bg-ink-100 text-ink-500"}`}>{idx + 1}</span>
              <span className="text-sm font-semibold text-ink-900">{f.label}</span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
              {st ? <Chip label={st.label} tone={TONE[st.tone]} /> : <Chip label="이번 사건에는 없음" tone={TONE.muted} />}
              {step && <span className="tabular-nums text-ink-500">{at ?? "시각 모름"}</span>}
            </div>
            {step?.status === "skipped" && <p className="mt-1.5 text-[11px] text-ink-500">{skipReason(step)}</p>}
            {kpiEmpty && <p className="mt-1.5 text-[11px] text-ink-500">단계는 끝났지만 요약할 사건 기록이 없습니다</p>}
          </li>
        );
      })}
    </ol>
  );
}

function ImpactCard({ inc }: { inc: OrchIncident }) {
  const step = stepOf(inc, "impact");
  const impact = impactOf(inc);
  const cats = (impact?.by_category ?? []).filter((c) => c && typeof c.category === "string");
  const name = (c: string) => CATEGORY_KO[c] ?? c;
  const bars: Record<string, number> = {};
  for (const c of cats) { const v = num(c.failed_order_count); if (v != null) bars[name(c.category)] = v; }
  return (
    <Panel id="data-impact" title="영향 분석" hint="장애 구간에 결제가 실패한 주문과 영향을 받은 고객, 결제 실패(미결제) 금액입니다. 고객 돈이 빠져나가지 않은 받지 못한 매출입니다. 데이터 AI가 센 숫자를 그대로 보여 줍니다.">
      {!step ? <Empty>이번 사건에는 영향 분석 단계가 없습니다.</Empty> : !impact || Object.keys(impact).length === 0 ? <Empty>영향 분석 결과가 아직 없습니다 ({UNKNOWN}).</Empty> : (
        <div className="space-y-4">
          <p className="rounded-2xl bg-surface-2 px-4 py-3 text-sm text-ink-900">{impact.summary || UNKNOWN}</p>
          <div>
            <p className="mb-2 text-xs font-medium text-ink-700">카테고리별 실패 주문 (건)</p>
            {cats.length === 0 ? <p className="text-xs text-ink-500">카테고리별 값: {UNKNOWN}</p> : (
              <ChartWithTable
                chart={<Bars data={bars} color="bg-accent" />}
                table={<TableView columns={["카테고리", "실패 주문", "미결제 금액"]} rows={cats.map((c) => [name(c.category), count(c.failed_order_count, "건"), krw(num(c.damage_amount))])} />}
              />
            )}
          </div>
          <RefList refs={impact.affected_customer_ids ?? []} label="고객 번호 보기" />
        </div>
      )}
    </Panel>
  );
}

function OkChip({ ok, yes, no, label }: { ok: boolean | null; yes: string; no: string; label: string }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-surface-2 px-4 py-3">
      <span className="text-sm text-ink-700">{label}</span>
      <Chip label={ok === true ? yes : ok === false ? no : UNKNOWN} tone={ok === true ? TONE.ok : ok === false ? TONE.bad : TONE.muted} />
    </div>
  );
}

function RecoveryCard({ inc }: { inc: OrchIncident }) {
  const step = stepOf(inc, "recovery");
  const r = recoveryOf(inc);
  const signal = clock(inc.timeline?.recovered_at);
  const allEmpty = bool(r?.recovered) == null && bool(r?.infra_metrics_ok) == null && bool(r?.business_metrics_ok) == null;
  return (
    <Panel id="data-recovery" title="복구 확인" hint="인프라 AI가 데이터 AI의 이상 탐지를 불러 복구됐는지 판정합니다. 서버 지표와 업무 지표를 따로 봅니다.">
      {!step ? <Empty>이번 사건에는 복구 확인 단계가 없습니다.</Empty> : step.status === "skipped" && signal ? (
        <div className="flex flex-wrap items-center gap-2 rounded-2xl bg-surface-2 px-4 py-3 text-sm">
          <Chip label="복구됨" tone={TONE.ok} />
          <span className="tabular-nums text-ink-700">쇼핑몰 정상 복귀 신호 {signal}</span>
        </div>
      ) : allEmpty ? (
        <p className="rounded-2xl bg-surface-2 px-4 py-3 text-sm text-ink-500">인프라 AI의 복구 확인 기록이 아직 없어요</p>
      ) : (
        <div className="space-y-2">
          <OkChip label="복구 판정" ok={bool(r?.recovered)} yes="복구됨" no="복구 안 됨" />
          <OkChip label="서버 지표 (오류율·응답 시간)" ok={bool(r?.infra_metrics_ok)} yes="정상" no="이상" />
          <OkChip label="업무 지표 (결제 실패율)" ok={bool(r?.business_metrics_ok)} yes="정상" no="이상" />
        </div>
      )}
    </Panel>
  );
}

/** 근거 사실 하나. 누르면 시각·출처가 펼쳐진다 */
function FactItem({ fact }: { fact: CauseFact }) {
  return (
    <li>
      <details className="group rounded-xl bg-surface-2 px-3 py-2 text-xs">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-ink-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
          <span className="chip bg-ink-100 text-ink-700">{FACT_KIND_KO[fact.kind] ?? fact.kind}</span>
          <span className="min-w-0 flex-1 truncate">{fact.summary}</span>
          <span aria-hidden="true" className="text-ink-500 transition group-open:rotate-90">›</span>
        </summary>
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-ink-700">
          <dt className="text-ink-500">시각</dt><dd className="tabular-nums">{fact.at ? shortDateTime(fact.at) : UNKNOWN}</dd>
          <dt className="text-ink-500">출처</dt><dd>{FACT_SOURCE_KO[fact.source] ?? fact.source}</dd>
          {fact.ref && (<><dt className="text-ink-500">참조</dt><dd className="break-all">{humanId(fact.ref)}</dd></>)}
          <dt className="text-ink-500">내용</dt><dd className="break-words">{fact.summary}</dd>
        </dl>
      </details>
    </li>
  );
}

function RootCauseCard({ inc }: { inc: OrchIncident }) {
  const step = stepOf(inc, "root_cause");
  const check = stepOf(inc, "cause_check");
  const out = rootCauseOf(inc);
  const facts = (out?.facts ?? []).filter((f) => f && typeof f.fact_id === "string");
  const byId = new Map(facts.map((f) => [f.fact_id, f]));
  const hypotheses = (out?.hypotheses ?? []).filter((h) => h && Array.isArray(h.fact_ids));
  const unknown = facts.filter((f) => f.kind === "unavailable");
  const checkSt = check ? STEP_STATUS_KO[check.status] ?? { label: check.status, tone: "muted" as const } : null;
  // 후보가 없을 때 비어 있던 근거 종류 (모은 사실에 그 종류가 하나도 없음)
  const missing = CAUSE_KINDS.filter((k) => !facts.some((f) => f.kind === k)).map((k) => FACT_KIND_KO[k]);
  const action = inc.resolution_draft?.action;
  return (
    <Panel id="data-root-cause" title="원인 후보" hint="배포·공지·문의 급증·지난 사건을 코드가 모으고, AI는 그 사실만 근거로 후보를 고릅니다. 근거 사실을 누르면 시각과 출처가 보입니다.">
      {!step ? <Empty>이번 사건에는 원인 추정 단계가 없습니다.</Empty> : !out || Object.keys(out).length === 0 ? (
        <Empty>원인 후보가 아직 없어요 ({step.status === "skipped" ? skipReason(step) : UNKNOWN}).</Empty>
      ) : (
        <div className="space-y-4">
          {hypotheses.length === 0 ? (
            <p className="rounded-2xl bg-surface-2 px-4 py-3 text-sm text-ink-700">
              고를 수 있는 원인 후보가 없어요{missing.length > 0 && ` (${missing.join("·")} 기록 없음)`}
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <p className="min-w-0 flex-1 rounded-2xl bg-surface-2 px-4 py-3 text-sm text-ink-900">{out.summary || UNKNOWN}</p>
              {out.needs_confirmation !== false && <Chip label="사람 확인 필요" tone={TONE.warn} />}
            </div>
          )}
          {hypotheses.length > 0 && (
            <ol className="space-y-3">
              {hypotheses.map((h, i) => {
                const conf = CONFIDENCE_KO[h.confidence] ?? { label: h.confidence, tone: "muted" as const };
                const cited = h.fact_ids.map((id) => byId.get(id)).filter((f): f is CauseFact => !!f);
                return (
                  <li key={`${i}-${h.fact_ids.join(",")}`} className="rounded-2xl px-4 py-3 ring-1 ring-ink-100">
                    <div className="flex flex-wrap items-start gap-2">
                      <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-bold text-brand-700">{i + 1}</span>
                      <p className="min-w-0 flex-1 text-sm text-ink-900">{h.explanation}</p>
                      <Chip label={conf.label} tone={TONE[conf.tone]} />
                    </div>
                    <p className="mb-1.5 mt-3 text-xs font-medium text-ink-700">근거 사실 {cited.length}개</p>
                    <ul className="space-y-1.5">{cited.map((f) => <FactItem key={f.fact_id} fact={f} />)}</ul>
                  </li>
                );
              })}
            </ol>
          )}
          {unknown.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-ink-700">확인하지 못한 출처 {unknown.length}곳</p>
              <ul className="space-y-1.5">{unknown.map((f) => <FactItem key={f.fact_id} fact={f} />)}</ul>
            </div>
          )}
          {action && (
            <p className="rounded-2xl bg-brand-50 px-4 py-3 text-sm text-brand-700"><span className="font-semibold">다음 조치</span> · {action}</p>
          )}
          {check && checkSt && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-surface-2 px-4 py-3 text-sm">
              <span className="text-ink-700">원인 확인 요청 (기획)</span>
              <span className="flex flex-wrap items-center gap-2">
                <Chip label={checkSt.label} tone={TONE[checkSt.tone]} />
                {check.handoff_task_id && <span className="text-xs text-ink-500">받은 요청 {humanId(check.handoff_task_id)}</span>}
                {typeof check.output?.request_id === "string" && <span className="text-xs text-ink-500">{humanId(check.output.request_id)}</span>}
              </span>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

export function IncidentData({ list, detail, selected, onSelect }: {
  list: Resource<OrchIncident[]>; detail: Resource<OrchIncident>; selected: string | null; onSelect: (id: string) => void;
}) {
  if (!list.env) return list.error ? <ErrorBox message={`${ORCH_SERVER}에서 사건 목록을 받지 못했어요. ${list.error}`} onRetry={list.reload} /> : <Waiting />;
  const incidents = list.env.data;
  if (incidents.length === 0) {
    return (
      <Empty>
        아직 장애 사건이 없어요. 쇼핑몰에서 결제 장애가 감지되면 총괄이 사건을 열고, 데이터 AI가 센 실패 주문·영향 고객·미결제 금액이 여기에 나타납니다.
      </Empty>
    );
  }
  // 상세가 아직 오지 않았으면 목록의 값(같은 사건 모양)을 쓴다
  const inc = detail.env?.data.context_id === selected ? detail.env.data : incidents.find((i) => i.context_id === selected) ?? null;
  const impact = impactOf(inc);
  return (
    <div className="space-y-5">
      <IncidentPicker list={incidents} selected={selected} onSelect={onSelect} />
      {inc && <DetectionLine inc={inc} />}
      {detail.error && <ErrorBox message={`사건 상세를 새로 받지 못했습니다. ${detail.error}`} onRetry={detail.reload} />}
      {!inc ? <Empty>고른 사건을 찾을 수 없습니다.</Empty> : (
        <div id="data-incident-panel" role="tabpanel" className="space-y-5">
          <div className="flex flex-wrap gap-3">
            <Stat label="실패 주문" value={count(impact?.failed_order_count, "건")} tone={num(impact?.failed_order_count) == null ? "text-ink-500" : ""} />
            <Stat label="영향 고객" value={count(impact?.affected_customer_count, "명")} tone={num(impact?.affected_customer_count) == null ? "text-ink-500" : ""} />
            <Stat label="결제 실패(미결제)" value={krw(num(impact?.damage_amount))} tone={num(impact?.damage_amount) == null ? "text-ink-500" : "text-accent"} />
            <Stat label="복구까지 걸린 시간" value={duration(inc.metrics?.mttr_seconds)} sub="감지 → 정상 복귀" tone={num(inc.metrics?.mttr_seconds) == null ? "text-ink-500" : ""} />
          </div>
          <Panel id="data-flow" title="데이터 AI 처리 흐름" hint={`${incidentTitle(inc)} · ${shortDateTime(inc.opened_at)} 시작. 감지는 쇼핑몰 모니터·Zabbix가 하고, 데이터 AI는 총괄 요청을 받아 일합니다.`}>
            <Flow inc={inc} />
          </Panel>
          <RootCauseCard inc={inc} />
          <div className="grid gap-5 lg:grid-cols-[3fr_2fr]">
            <ImpactCard inc={inc} />
            <RecoveryCard inc={inc} />
          </div>
        </div>
      )}
    </div>
  );
}
