import type { ReactNode } from "react";
import { Chip, TONE } from "@/components/finance/bits";
import { useSkill } from "@/components/data/useSkill";
import { when } from "@/components/data/Sources";
import { AgeBars } from "@/components/data/AgeBars";
import { dataAi, type DomainOpportunity, type OpportunityEvidence, type ShortsFit } from "@/lib/data";

// 트렌드 2단계(인사이트) 맨 위 "기회 가설" 카드. trend_insight_brief의 opportunities(계약 DomainOpportunity, #579) 그대로.
// - 카드: 가설 한 줄 → 숏츠 적합도 칩(높음 초록 / 불확실 노랑 / 낮음 회색 + 서버 이유) → 근거 목록(출처·기간·표본, 접힘)
//   → 연령대 막대("연령대끼리 양 비교 아님") → 잘 되는 형식 → 목표 참고값 → 연결 상품 → 한계 → "기획이 정할 것"(표시만)
// - 근거 status가 key_required·not_approved·unknown이면 값 없이 회색 자리만 + 서버 이유 (어떤 근거가 빠졌는지 보이게)
// - 근거의 출처 이름은 data_sources 응답에서 source_id로 찾는다. 못 찾으면 id 그대로
// - 응답에 없는 칸(null·빈 목록)은 그리지 않는다

const NO_REASON = "서버가 이유를 주지 않았어요";
const num = (v: number, digits = 0) => v.toLocaleString("ko-KR", { maximumFractionDigits: digits });
const FIT: Record<ShortsFit["level"], { label: string; tone: string }> = {
  high: { label: "숏츠 적합도 높음", tone: TONE.ok },
  uncertain: { label: "숏츠 적합도 불확실", tone: TONE.warn },
  low: { label: "숏츠 적합도 낮음", tone: TONE.muted },
};
const MISSING: Record<Exclude<OpportunityEvidence["status"], "known">, string> = {
  key_required: "키 등록 후 가능",
  not_approved: "수집 승인 전",
  unknown: "값 없음",
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[7.5rem_minmax(0,1fr)]">
      <p className="text-xs font-semibold text-ink-500">{label}</p>
      <div className="min-w-0 text-sm text-ink-700">{children}</div>
    </div>
  );
}

function EvidenceItem({ e, name }: { e: OpportunityEvidence; name: string }) {
  const period = e.period_start || e.period_end ? [e.period_start, e.period_end].filter(Boolean).join(" ~ ") : null;
  if (e.status !== "known") {
    return (
      <li className="rounded-lg border border-dashed border-ink-200 bg-ink-50 px-2.5 py-1.5 text-xs text-ink-500">
        <span className="font-semibold">{name}</span> · {e.label} · <span className="font-semibold">{MISSING[e.status]}</span>
        {e.reason && <span> · {e.reason}</span>}
      </li>
    );
  }
  const value = typeof e.value === "number" ? `${num(e.value, 2)}${e.unit ?? ""}` : null;
  return (
    <li className="px-2.5 py-1 text-xs text-ink-700">
      <span className="font-semibold text-ink-900">{name}</span> · {e.label}
      {value && <span className="font-semibold text-ink-900"> {value}</span>}
      {[period, typeof e.samples === "number" ? `표본 ${num(e.samples)}` : null, e.reason].filter(Boolean).map((x) => <span key={x} className="text-ink-500"> · {x}</span>)}
    </li>
  );
}

function EvidenceGroup({ title, list, nameOf }: { title: string; list: OpportunityEvidence[]; nameOf: (id: string) => string }) {
  if (list.length === 0) return null;
  return (
    <div>
      <p className="text-xs font-semibold text-ink-700">{title}</p>
      <ul className="mt-1 space-y-1">{list.map((e, k) => <EvidenceItem key={`${e.source_id}-${e.label}-${k}`} e={e} name={nameOf(e.source_id)} />)}</ul>
    </div>
  );
}

function Card({ o, nameOf }: { o: DomainOpportunity; nameOf: (id: string) => string }) {
  const fit = o.shorts_fit;
  const evidence = [...o.consumption, ...o.search_interest];
  const missing = evidence.filter((e) => e.status !== "known").length;
  return (
    <li className="card space-y-3 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-bold text-ink-900">{o.subtopic}</h3>
          {o.subtopic_evidence && <p className="text-xs text-ink-500">근거 · {o.subtopic_evidence}</p>}
        </div>
        {fit && <Chip label={FIT[fit.level].label} tone={FIT[fit.level].tone} />}
      </div>
      {o.line && (
        <p className="rounded-xl bg-brand-50 px-3 py-2 text-sm font-semibold leading-relaxed text-brand-900">
          {o.line}
          {o.line_status === "fixed" && o.line_reason && <span className="mt-0.5 block text-xs font-normal text-ink-500">고정 문장 · {o.line_reason}</span>}
        </p>
      )}
      <div className="space-y-2.5">
        {fit && (
          <Row label="숏츠 적합도">
            {fit.reason}
            {[
              fit.category ? `분류 ${fit.category}` : null,
              typeof fit.median_views === "number" ? `중앙값 조회 ${num(fit.median_views)}회` : null,
              typeof fit.overall_median_views === "number" ? `전체 중앙값 ${num(fit.overall_median_views)}회` : null,
              fit.samples ? `표본 ${num(fit.samples)}개` : null,
              fit.snapshot_at ? `${when(fit.snapshot_at)} 기준` : null,
            ].filter(Boolean).map((x) => <span key={x} className="text-xs text-ink-500"> · {x}</span>)}
          </Row>
        )}
        <Row label="연령대">
          {o.age_bands.length > 0 ? <AgeBars bands={o.age_bands} top={null} /> : <span className="text-ink-500">{o.age_reason ?? NO_REASON}</span>}
        </Row>
        {o.formats.length > 0 && (
          <Row label="잘 되는 형식">
            <ul className="space-y-0.5">
              {o.formats.map((f) => (
                <li key={f.name}>
                  <span className="font-semibold text-ink-900">{f.name}</span>
                  <span className="text-ink-500"> · {num(f.count)}개{typeof f.median_views === "number" ? ` · 중앙값 조회 ${num(f.median_views)}회` : ""}</span>
                </li>
              ))}
            </ul>
          </Row>
        )}
        {typeof o.target_views_reference === "number" && (
          <Row label="목표 참고값">
            <span className="font-semibold text-ink-900">조회 {num(o.target_views_reference)}회</span>
            {o.target_reference_basis && <span className="text-ink-500"> · {o.target_reference_basis}</span>}
          </Row>
        )}
        <Row label="연결 상품">
          {o.products.length > 0 ? (
            <ul className="space-y-1">
              {o.products.map((p) => (
                <li key={p.product_id}>
                  <span className="font-semibold text-ink-900">{p.name}</span>
                  {p.category_name && <span className="text-ink-500"> · {p.category_name}</span>}
                  <span className="block text-xs text-ink-500">{[p.reason, p.season].filter(Boolean).join(" · ")}</span>
                </li>
              ))}
            </ul>
          ) : <span className="text-ink-500">{o.products_reason ?? NO_REASON}</span>}
        </Row>
        {o.limits.length > 0 && (
          <Row label="한계">
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-ink-500">{o.limits.map((l) => <li key={l}>{l}</li>)}</ul>
          </Row>
        )}
      </div>
      {evidence.length > 0 && (
        <details className="rounded-xl bg-ink-50 px-3 py-2">
          <summary className="cursor-pointer text-xs font-semibold text-ink-700">
            근거 {evidence.length}개 보기{missing > 0 ? ` · 빠진 근거 ${missing}개` : ""}
          </summary>
          <div className="mt-2 space-y-2">
            <EvidenceGroup title="실제 소비" list={o.consumption} nameOf={nameOf} />
            <EvidenceGroup title="검색 관심" list={o.search_interest} nameOf={nameOf} />
          </div>
        </details>
      )}
      {o.planning_decides.length > 0 && (
        <div className="rounded-xl px-3 py-2 ring-1 ring-ink-100">
          <p className="text-xs font-semibold text-ink-700">기획이 정할 것</p>
          <ul className="mt-1 space-y-1 text-sm text-ink-700">
            {o.planning_decides.map((d) => (
              <li key={d} className="flex items-start gap-2">
                <span aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded border border-ink-300 bg-surface" />
                <span>{d}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

/** opportunities가 응답에 없으면(예전 응답) 그리지 않는다. 비었으면 서버 이유 */
export function OpportunityCards({ list, reason }: { list: DomainOpportunity[] | undefined; reason: string | null | undefined }) {
  const sources = useSkill(() => dataAi.dataSources());
  if (list === undefined) return null;
  const names = new Map((sources.state.kind === "ok" ? sources.state.out.sources : []).map((s) => [s.source_id, s.name]));
  const nameOf = (id: string) => names.get(id) ?? id;
  return (
    <section aria-labelledby="data-trend-opportunity" className="space-y-3">
      <h3 id="data-trend-opportunity" className="text-base font-bold text-ink-900">
        기회 가설{list.length > 0 && <span className="text-sm font-normal text-ink-500"> · {list.length}개</span>}
      </h3>
      {list.length === 0
        ? <p className="card p-4 text-sm text-ink-500">{reason ?? NO_REASON}</p>
        : <ul className="space-y-4">{list.map((o) => <Card key={o.opportunity_id} o={o} nameOf={nameOf} />)}</ul>}
    </section>
  );
}
