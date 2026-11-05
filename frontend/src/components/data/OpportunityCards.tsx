import { TONE } from "@/components/finance/bits";
import { useSkill } from "@/components/data/useSkill";
import { when } from "@/components/data/Sources";
import { AgeBars } from "@/components/data/AgeBars";
import { CopyId, FoldRow, ShortsCard, factLinks, shortsThumbs, type Tile, type Verdict } from "@/components/data/ShortsCard";
import { dataAi, type DataFact, type DomainOpportunity, type OpportunityEvidence, type ShortsFit } from "@/lib/data";

// 트렌드 2단계(인사이트) 맨 위 "기회 가설" 카드. trend_insight_brief의 opportunities(계약 DomainOpportunity, #579) 그대로. v2 틀(ShortsCard)
// - 판정 칩 하나: 숏츠 적합도(shorts_fit.level) high → "숏츠로 해볼 만함", uncertain·low → "아직 불확실". shorts_fit이 없으면 칩 없음
// - 숫자 타일(서버 값이 있는 것만): 숏츠 반응(shorts_fit.ratio·samples) · 가장 오른 연령(age_bands 중 배수가 가장 큰 연령대) · 목표 참고값(target_views_reference)
// - 근거 숏츠: 이 카드 video_links(주소·썸네일) → 브리프 facts 중 이 카드(about = opportunity_id, fact_ids)의 YouTube 근거 주소, 최대 3개
// - 접힘 "근거 전체 보기": 적합도 이유 · 근거(실제 소비·검색 관심: 출처·기간·표본, 키 필요·승인 전은 회색 자리 + 서버 이유)
//   · 연령대 막대 · 잘 되는 형식 · 연결 상품 이유 · 한계 · 기획이 정할 것(표시만) · 카드 번호 복사
// - 근거의 출처 이름은 data_sources 응답에서 source_id로 찾는다. 못 찾으면 id 그대로

const NO_REASON = "서버가 이유를 주지 않았어요";
const num = (v: number, digits = 0) => v.toLocaleString("ko-KR", { maximumFractionDigits: digits });
const COMPACT = new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 });
const VERDICT: Record<ShortsFit["level"], Verdict> = {
  high: { label: "숏츠로 해볼 만함", tone: TONE.ok },
  uncertain: { label: "아직 불확실", tone: TONE.muted },
  low: { label: "아직 불확실", tone: TONE.muted },
};
const LEVEL: Record<ShortsFit["level"], string> = { high: "높음", uncertain: "불확실", low: "낮음" };
const MISSING: Record<Exclude<OpportunityEvidence["status"], "known">, string> = {
  key_required: "키 등록 후 가능",
  not_approved: "수집 승인 전",
  unknown: "값 없음",
};

function EvidenceItem({ e, name }: { e: OpportunityEvidence; name: string }) {
  const period = e.period_start || e.period_end ? [e.period_start, e.period_end].filter(Boolean).join(" ~ ") : null;
  if (e.status !== "known") {
    return (
      <li className="rounded-lg border border-dashed border-ink-200 bg-surface px-2.5 py-1.5 text-xs text-ink-500">
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
    <FoldRow label={title}>
      <ul className="space-y-1">{list.map((e, k) => <EvidenceItem key={`${e.source_id}-${e.label}-${k}`} e={e} name={nameOf(e.source_id)} />)}</ul>
    </FoldRow>
  );
}

function tilesOf(o: DomainOpportunity): Tile[] {
  const tiles: Tile[] = [];
  const fit = o.shorts_fit;
  if (fit && typeof fit.ratio === "number") {
    tiles.push({ label: "숏츠 반응", value: `${num(fit.ratio, 2)}배`, note: [fit.category, fit.samples ? `표본 ${num(fit.samples)}개` : null].filter(Boolean).join(" · ") || null });
  }
  const top = o.age_bands.filter((b) => typeof b.ratio === "number").sort((a, b) => (b.ratio ?? 0) - (a.ratio ?? 0))[0];
  if (top && typeof top.ratio === "number") tiles.push({ label: "가장 오른 연령", value: top.band, note: `검색 평소의 ${num(top.ratio, 2)}배` });
  if (typeof o.target_views_reference === "number") {
    tiles.push({ label: "목표 참고값", value: `${COMPACT.format(o.target_views_reference)}회`, note: o.target_reference_basis });
  }
  return tiles;
}

function Card({ o, facts, nameOf }: { o: DomainOpportunity; facts: DataFact[] | undefined; nameOf: (id: string) => string }) {
  const fit = o.shorts_fit;
  return (
    <ShortsCard
      verdict={fit ? VERDICT[fit.level] : null}
      kicker={o.axis ?? null}
      title={o.subtopic}
      line={o.line || null}
      lineNote={o.line_status === "fixed" ? "고정 문장" : null}
      tiles={tilesOf(o)}
      products={o.products.map((p) => ({ id: p.product_id, name: p.name }))}
      thumbs={shortsThumbs(o.video_links, factLinks(facts, o.opportunity_id, o.fact_ids))}
      fold={<>
        {o.subtopic_evidence && <FoldRow label="주제 근거">{o.subtopic_evidence}</FoldRow>}
        {fit && (
          <FoldRow label="숏츠 적합도">
            <span className="font-semibold text-ink-900">{LEVEL[fit.level]}</span> · {fit.reason}
            {[
              typeof fit.median_views === "number" ? `중앙값 조회 ${num(fit.median_views)}회` : null,
              typeof fit.overall_median_views === "number" ? `전체 중앙값 ${num(fit.overall_median_views)}회` : null,
              fit.snapshot_at ? `${when(fit.snapshot_at)} 기준` : null,
            ].filter(Boolean).map((x) => <span key={x} className="text-xs text-ink-500"> · {x}</span>)}
          </FoldRow>
        )}
        <EvidenceGroup title="실제 소비" list={o.consumption} nameOf={nameOf} />
        <EvidenceGroup title="검색 관심" list={o.search_interest} nameOf={nameOf} />
        <FoldRow label="연령대">
          {o.age_bands.length > 0 ? <AgeBars bands={o.age_bands} top={null} /> : <span className="text-ink-500">{o.age_reason ?? NO_REASON}</span>}
        </FoldRow>
        {o.formats.length > 0 && (
          <FoldRow label="잘 되는 형식">
            <ul className="space-y-0.5">
              {o.formats.map((f) => (
                <li key={f.name}>
                  <span className="font-semibold text-ink-900">{f.name}</span>
                  <span className="text-ink-500"> · {num(f.count)}개{typeof f.median_views === "number" ? ` · 중앙값 조회 ${num(f.median_views)}회` : ""}</span>
                </li>
              ))}
            </ul>
          </FoldRow>
        )}
        <FoldRow label="연결 상품">
          {o.products.length > 0 ? (
            <ul className="space-y-1">
              {o.products.map((p) => (
                <li key={p.product_id}>
                  <span className="font-semibold text-ink-900">{p.name}</span>
                  <span className="block text-xs text-ink-500">{[p.category_name, p.reason, p.season].filter(Boolean).join(" · ")}</span>
                </li>
              ))}
            </ul>
          ) : <span className="text-ink-500">{o.products_reason ?? NO_REASON}</span>}
        </FoldRow>
        {o.limits.length > 0 && (
          <FoldRow label="한계"><ul className="list-disc space-y-0.5 pl-4 text-xs text-ink-500">{o.limits.map((l) => <li key={l}>{l}</li>)}</ul></FoldRow>
        )}
        {o.planning_decides.length > 0 && (
          <FoldRow label="기획이 정할 것">
            <ul className="space-y-1">
              {o.planning_decides.map((d) => (
                <li key={d} className="flex items-start gap-2">
                  <span aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded border border-ink-300 bg-surface" />
                  <span>{d}</span>
                </li>
              ))}
            </ul>
          </FoldRow>
        )}
        {o.line_status === "fixed" && o.line_reason && <p className="text-xs text-ink-500">고정 문장 · {o.line_reason}</p>}
        <CopyId id={o.opportunity_id} label="카드 번호" />
      </>}
    />
  );
}

/** 기회 가설 카드 <li> 목록 (부모가 <ul>로 감싼다). opportunities가 응답에 없으면(예전 응답) 그리지 않는다. 비었으면 서버 이유 */
export function OpportunityCards({ list, reason, facts }: { list: DomainOpportunity[] | undefined; reason: string | null | undefined; facts: DataFact[] | undefined }) {
  const sources = useSkill(() => dataAi.dataSources());
  if (list === undefined) return null;
  const names = new Map((sources.state.kind === "ok" ? sources.state.out.sources : []).map((s) => [s.source_id, s.name]));
  const nameOf = (id: string) => names.get(id) ?? id;
  if (list.length === 0) return <li className="card p-4 text-sm text-ink-500">기회 가설 카드가 없어요. {reason ?? NO_REASON}</li>;
  return <>{list.map((o) => <Card key={o.opportunity_id} o={o} facts={facts} nameOf={nameOf} />)}</>;
}
