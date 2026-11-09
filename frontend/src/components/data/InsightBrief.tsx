import { useState, type ReactNode } from "react";
import { Chip, Empty, TONE, Waiting } from "@/components/finance/bits";
import { okEvidence, type SkillState } from "@/components/data/useSkill";
import { Sources, when } from "@/components/data/Sources";
import { OpportunityCards } from "@/components/data/OpportunityCards";
import { AgeBars } from "@/components/data/AgeBars";
import { useCompany } from "@/demo/company";
import { CopyId, FoldRow, RestFacts, ShortsCard, TileRow, factLinks, shortsThumbs, shortsTiles, verdictOf } from "@/components/data/ShortsCard";
import { DATA_SERVER, kpiLive, type DataFact, type InsightPastResult, type LlmStatus, type TrendInsight, type TrendInsightBriefOutput } from "@/lib/data";

// 트렌드 2단계(인사이트): 기획팀이 가져갈 인사이트 브리프 (v2 시안 ② 보드). trend_insight_brief(계약 TrendInsightBriefOutput) 응답 그대로.
// - 카드 틀은 ShortsCard: 판정 칩 · 제목 · 한 문장(서버 line) · 숫자 타일 · 연결 상품 · 오른쪽 근거 숏츠 썸네일(최대 3) · "근거 전체 보기" 접힘 하나
// - 인사이트 카드: 판정 칩 = 서버 shorts_fit(high → "숏츠로 해볼 만함", 그 밖·표본 부족 unknown → "아직 불확실", 칸 없는 예전 응답은 칩 없음).
//   숫자 타일 = 숏츠 반응(shorts_fit) · 가장 오른 연령(top_age_band) · 목표 참고값(target_views_reference), 서버 값이 있는 것만 (#678)
//   근거 요약 = 서버 evidence_highlights("평소 vs 지금" 막대 + 배수 + 서버 문장 + 출처 배지, #710)
//   근거 숏츠 = evidence_shorts(제목·조회수·광고 칩, #710) → evidence.video_links(썸네일·광고 재료 칩) → evidence.links + 브리프 facts 중 이 인사이트(about·fact_ids)의 YouTube 주소
//   접힘 안: 광고 재료 판정·회사 관련 이유·연결 상품 이유·시즌 근거·연령대 막대(가장 오른 연령대 top_age_band 강조)·숏츠 형식·데이터 근거
//   ·근거 숫자(이 카드 facts 중 근거 요약에 없는 나머지 전부)·한계·예시 아이디어·insight_id 복사
// - 순서: 회사 관련·기회(기회 가설 카드 → 회사 관련 인사이트) → 상품이 연결된 그 밖의 인사이트 → 맨 아래 접힘 "상품 연결 없는 화제 N개"
//   → 참고용 예시(기존 기획안 카드) 접힘
// - 응답에 없는 칸(null·빈 목록)은 그리지 않는다. 이유가 오면 접힘 안에 이유만

const NO_REASON = "서버가 이유를 주지 않았어요";
const num = (v: number, digits = 0) => v.toLocaleString("ko-KR", { maximumFractionDigits: digits });
const times = (v: number) => `평소의 ${num(v, 2)}배`;
const FORMAT_SOURCE = { topic: "이 주제 숏츠", category: "같은 카테고리 숏츠 통계", all: "전체 숏츠 통계" } as const;
const COMPACT_VIEWS = new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 });

function material(i: TrendInsight): { label: string; tone: string } {
  if (i.ad_material === true && i.real_entity) return { label: "광고 재료 가능 · 이름 주의", tone: TONE.warn };
  if (i.ad_material === true) return { label: "광고 재료 가능", tone: TONE.ok };
  if (i.ad_material === false) return { label: "광고 재료 아님", tone: TONE.muted };
  return { label: "광고 재료 판단 못 함", tone: TONE.muted };
}

function Evidence({ e }: { e: TrendInsight["evidence"] }) {
  const rows: { source: string; body: ReactNode }[] = [];
  if (e.youtube_videos !== null) {
    const parts = [`인기 영상 ${num(e.youtube_videos)}개`];
    if (e.youtube_views_per_hour !== null) parts.push(`시간당 조회 ${num(e.youtube_views_per_hour)}회`);
    if (e.youtube_views_per_subscriber !== null) parts.push(`구독자 대비 최대 ${num(e.youtube_views_per_subscriber, 2)}배`);
    if (e.youtube_at) parts.push(`${when(e.youtube_at)} 기준`);
    rows.push({ source: "YouTube", body: parts.join(" · ") });
  }
  if (e.google_rank !== null) {
    rows.push({
      source: "Google 인기 검색어",
      body: (
        <>
          {num(e.google_rank)}위{e.google_at ? ` · ${when(e.google_at)} 기준` : ""}
          {e.google_news.length > 0 && (
            <ul className="mt-0.5 space-y-0.5">
              {e.google_news.map((n, k) => (
                <li key={`${n.url ?? n.title}-${k}`} className="truncate">
                  {n.url ? <a href={n.url} target="_blank" rel="noreferrer noopener" className="text-brand-700 hover:underline">{n.title}</a> : n.title}
                  {n.source && <span className="text-ink-500"> · {n.source}</span>}
                </li>
              ))}
            </ul>
          )}
        </>
      ),
    });
  }
  if (e.naver_ratio !== null) {
    const parts = [times(e.naver_ratio)];
    if (e.naver_status === "rising") parts.push("급상승");
    if (e.naver_rising_started) parts.push(`${e.naver_rising_started}부터 상승`);
    if (e.naver_until) parts.push(`${e.naver_until}까지`);
    rows.push({ source: "네이버 검색", body: parts.join(" · ") });
  }
  const videoLinks = e.links.filter((l) => l.includes("youtube.com/watch"));
  return (
    <div className="space-y-1.5 text-xs text-ink-700">
      {rows.length > 0 && (
        <ul className="space-y-1">
          {rows.map((r) => <li key={r.source} className="min-w-0"><span className="font-semibold text-ink-900">{r.source}</span> · {r.body}</li>)}
        </ul>
      )}
      {videoLinks.length > 0 && (
        <p className="flex flex-wrap gap-x-2">
          {videoLinks.map((l, k) => <a key={l} href={l} target="_blank" rel="noreferrer noopener" className="text-brand-700 hover:underline">▶ 근거 영상 {k + 1}</a>)}
        </p>
      )}
      {e.missing.length > 0 && <p className="text-ink-500">없는 출처 · {e.missing.join(" · ")}</p>}
    </div>
  );
}

/** 시즌 근거 한 줄. 서버가 시즌 값을 주지 않았으면 null */
function seasonText(e: TrendInsight["evidence"]): string | null {
  if (typeof e.season_ratio !== "number") return null;
  const month = e.season_month ? `${e.season_month.slice(0, 7)} ` : "";
  return [e.season_keyword, `${month}기간 평균의 ${num(e.season_ratio, 2)}배`].filter(Boolean).join(" · ");
}

function Products({ i }: { i: TrendInsight }) {
  return i.products.length > 0 ? (
    <ul className="space-y-1">
      {i.products.map((p) => (
        <li key={p.product_id}>
          <span className="font-semibold text-ink-900">{p.name}</span>
          {p.category_name && <span className="text-ink-500"> · {p.category_name}</span>}
          <span className="block text-xs text-ink-500">{[p.reason, p.season].filter(Boolean).join(" · ")}</span>
        </li>
      ))}
    </ul>
  ) : <span className="text-ink-500">{i.products_reason ?? NO_REASON}</span>;
}

const CAUTION_NAME = { real_person: "인물", trademark: "상표", music: "음원" } as const;

const BAND: Record<InsightPastResult["band"], string> = {
  green: "border-emerald-300 bg-emerald-50 text-emerald-900",
  red: "border-red-300 bg-red-50 text-red-900",
  gray: "border-ink-100 bg-ink-50 text-ink-700",
};
const VIEWS_TONE: Record<InsightPastResult["views_verdict"], string> = {
  achieved: TONE.ok, missed: TONE.bad, measuring: TONE.warn, none: TONE.muted,
};
const SALES_TONE: Record<string, string> = { more: TONE.ok, less: TONE.bad };
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" }) : "?");

/** 카드 맨 위 "지난번 결과" 띠: 가장 최근 캠페인 1개 (서버 값·문장 그대로). 없으면 회색 한 줄 */
function PastBand({ list }: { list: InsightPastResult[] | undefined }) {
  const p = list?.[0];
  if (!p) {
    return (
      <div className={`border px-4 py-2 text-sm ${BAND.gray}`}>
        <b>지난번 결과 없음</b> · 이 주제로 한 캠페인이 아직 없습니다
      </div>
    );
  }
  return (
    <div className={`flex flex-col gap-1 border px-4 py-2 text-sm ${BAND[p.band]}`}>
      <div className="flex flex-wrap items-center gap-2">
        <b>지난번 결과</b>
        <span className={`chip text-xs font-semibold ${VIEWS_TONE[p.views_verdict]}`}>{p.views_label}</span>
        <span className={`chip text-xs font-semibold ${SALES_TONE[p.sales_verdict ?? ""] ?? TONE.muted}`}>{p.sales_label}</span>
        <span className="text-xs opacity-80">{p.campaign_id} · {day(p.window_from)}~{day(p.window_to)}</span>
      </div>
      <p>{p.summary_line}</p>
      {p.note_line && <p className="text-xs opacity-80">{p.note_line}</p>}
    </div>
  );
}

function InsightCard({ i, facts }: { i: TrendInsight; facts: DataFact[] | undefined }) {
  const m = material(i);
  const season = seasonText(i.evidence);
  const kicker = [i.category && i.category !== "unknown" ? i.category : null, i.company_related ? "회사 관련" : null, i.classification_pending ? "분류 대기" : null]
    .filter(Boolean).join(" · ");
  // 형식 빌리기(format, 서버 company_relevance): "형식 빌리기" 칩 · 주의 칩(서버 cautions) · 빌릴 형식 타일 · 이어 붙일 책 · 지금 뜨는 영상
  const borrow = i.company_relevance === "format";
  const bf = i.borrow_format;
  const formatTile = borrow && bf ? [{
    label: "빌릴 형식", value: bf.name,
    note: typeof bf.median_views === "number" ? `숏츠 형식 ${bf.name} 중앙값 ${COMPACT_VIEWS.format(bf.median_views)}회` : null,
  }] : [];
  const cautions = i.cautions ?? [];
  return (
    <ShortsCard
      verdict={borrow ? { label: "형식 빌리기", tone: "bg-brand-50 text-brand-700", title: i.company_relevance_reason ?? null }
        : verdictOf(i.shorts_fit, i.shorts_fit_status, i.shorts_fit_reason)}
      kicker={kicker || null}
      badges={borrow && cautions.length > 0 ? (
        <span className={`chip text-xs ${TONE.warn}`} title={i.caution_note ?? undefined}>{cautions.map((c) => CAUTION_NAME[c]).join("·")} 주의</span>
      ) : null}
      title={i.topic}
      line={i.line || null}
      lineNote={i.line_status === "fixed" ? "고정 문장" : null}
      highlights={i.evidence_highlights}
      extraTiles={formatTile}
      products={(borrow ? (i.borrow_products ?? []) : i.products).map((p) => ({ id: p.product_id, name: p.name }))}
      productsLabel={borrow ? "이어 붙일 책" : "연결 상품"}
      thumbs={shortsThumbs([...(i.evidence_shorts ?? []), ...(i.evidence.video_links ?? [])], [...i.evidence.links, ...factLinks(facts, i.insight_id, i.fact_ids)])}
      thumbsLabel={borrow ? "지금 뜨는 영상" : "이런 숏츠가 잘 돼요"}
      highlight={i.company_related === true}
      topBand={<PastBand list={i.past_results} />}
      fold={<>
        <TileRow tiles={shortsTiles(i)} />
        {borrow && i.caution_note && <FoldRow label="주의">{i.caution_note}</FoldRow>}
        <FoldRow label="광고 재료">
          <Chip label={m.label} tone={m.tone} />
          {[i.ad_material_reason, i.real_entity && i.entity_names.length ? `이름 · ${i.entity_names.join(", ")}` : null].filter(Boolean).map((x) => <span key={x} className="ml-1 text-xs text-ink-500">{x}</span>)}
        </FoldRow>
        {(i.shorts_fit || i.shorts_fit_reason) && (
          <FoldRow label="숏츠 적합도">{i.shorts_fit ? i.shorts_fit.reason : i.shorts_fit_reason}</FoldRow>
        )}
        {!i.top_age_band && i.top_age_band_reason && <FoldRow label="가장 오른 연령">{i.top_age_band_reason}</FoldRow>}
        {i.company_reason && <FoldRow label="회사 관련">{i.company_reason}</FoldRow>}
        {i.company_relevance_reason && <FoldRow label="회사 연결">{i.company_relevance_reason}</FoldRow>}
        <FoldRow label="연결 상품"><Products i={i} /></FoldRow>
        {season && <FoldRow label="시즌 근거">{season}</FoldRow>}
        <FoldRow label="연령대">
          {i.target_age ? (
            <>
              <span className="font-semibold text-ink-900">{i.target_age}</span>
              {i.target_age_ratio !== null && <span> · {times(i.target_age_ratio)}</span>}
              {i.target_age_status === "rising" && <span> · 급상승</span>}
              {(i.age_bands?.length ?? 0) > 0 && <AgeBars bands={i.age_bands ?? []} top={i.top_age_band ?? i.target_age} />}
            </>
          ) : <span className="text-ink-500">{i.target_age_reason ?? NO_REASON}</span>}
        </FoldRow>
        <FoldRow label="숏츠 형식">
          {i.shorts_format ? (
            <>
              <span className="font-semibold text-ink-900">{i.shorts_format}</span>
              {i.shorts_format_samples !== null && <span className="text-ink-500"> · 표본 {num(i.shorts_format_samples)}개</span>}
              {i.shorts_format_source && (
                <span className="text-ink-500"> · {FORMAT_SOURCE[i.shorts_format_source]}에서 고름
                  {typeof i.shorts_format_source_count === "number" && typeof i.shorts_format_source_total === "number"
                    ? ` (${num(i.shorts_format_source_total)}개 중 ${num(i.shorts_format_source_count)}개)` : ""}</span>
              )}
              {typeof i.shorts_format_median_views === "number" && <span className="text-ink-500"> · 중앙값 조회수 {num(i.shorts_format_median_views)}회</span>}
              {i.shorts_format_at && <span className="text-ink-500"> · {when(i.shorts_format_at)} 기준</span>}
            </>
          ) : <span className="text-ink-500">{i.shorts_format_reason ?? NO_REASON}</span>}
        </FoldRow>
        <FoldRow label="데이터 근거"><Evidence e={i.evidence} /></FoldRow>
        <RestFacts facts={facts} about={i.insight_id} ids={i.fact_ids} shown={i.evidence_highlights} />
        {i.limits.length > 0 && (
          <FoldRow label="한계"><ul className="list-disc space-y-0.5 pl-4 text-xs text-ink-500">{i.limits.map((l) => <li key={l}>{l}</li>)}</ul></FoldRow>
        )}
        {i.example_ideas.length > 0 && (
          <FoldRow label={`예시 아이디어 ${i.example_ideas.length}개`}><ul className="list-disc space-y-0.5 pl-4">{i.example_ideas.map((x) => <li key={x}>{x}</li>)}</ul></FoldRow>
        )}
        {i.line_status === "fixed" && i.line_reason && <p className="text-xs text-ink-500">고정 문장 · {i.line_reason}</p>}
        <CopyId id={i.insight_id} />
      </>}
    />
  );
}

/** 응답은 트렌드 화면(TrendScan)이 한 번 받는다. examples = 참고용 예시(기존 기획안 카드), examplesCount = 그 개수 */
const LLM_TONE: Record<string, string> = {
  red: "border-red-300 bg-red-50 text-red-800",
  yellow: "border-amber-300 bg-amber-50 text-amber-800",
  blue: "border-sky-300 bg-sky-50 text-sky-800",
};

/** data LLM이 멈췄을 때 원인 (서버 문구 그대로). 정상이면 아무것도 안 보인다 */
function LlmStatusLine({ s }: { s: LlmStatus | null | undefined }) {
  if (!s || s.state === "ok" || !s.title) return null;
  const where = [s.model && `모델 ${s.model}`, s.base_host && `주소 ${s.base_host}`].filter(Boolean).join(" · ");
  return (
    <div role="status" className={`border px-4 py-3 text-sm ${LLM_TONE[s.tone] ?? LLM_TONE.blue}`}>
      <p className="font-bold">{s.title}</p>
      {s.detail && <p>{s.detail}</p>}
      {s.error_message && <p className="text-xs opacity-80">공급자 메시지{s.request ? ` (${s.request})` : ""}: {s.error_message}</p>}
      {where && <p className="text-xs opacity-80">{where}</p>}
    </div>
  );
}

export function InsightBrief({ state, examples, examplesCount }: {
  state: SkillState<TrendInsightBriefOutput>; examples: ReactNode; examplesCount: number | null;
}) {
  const [examplesOpened, setExamplesOpened] = useState(false);
  const company = useCompany();
  const out = state.kind === "ok" ? state.out : null;
  if (!kpiLive) return null;
  const all = out?.insights ?? [];
  // 두 묶음 (서버 company_relevance·reference_only 그대로, 서버 순서):
  // ① 책에서 뜨는 주제 = 기회 가설 카드 + direct·domain 인사이트 ② 빌려 쓸 요즘 유행 = format
  // 맨 아래 접힘: 참고용(reference_only — 연결할 책 없음·광고 재료 아님·분류 대기). 기획에 넘기지 않는다
  const passed = (i: TrendInsight) => !i.reference_only && i.classification_pending !== true;
  const bookOnes = all.filter((i) => passed(i) && i.company_relevance !== "format");
  const borrowed = all.filter((i) => passed(i) && i.company_relevance === "format");
  const unlinked = all.filter((i) => !passed(i));
  const name = company?.name ?? "회사";
  // 형식 빌리기가 0개면 서버가 limits에 남긴 이유 (첫 줄: 예시 아이디어가 고른 트렌드)
  const noBorrow = out?.limits.find((l) => l.startsWith("형식 빌리기 0개")) ?? null;
  return (
    <section id="data-trend-insight" aria-labelledby="data-trend-insight-title" className="space-y-4">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
        <div className="min-w-0 space-y-1">
          <h2 id="data-trend-insight-title" className="text-2xl font-bold text-ink-900">기획팀에 넘길 인사이트</h2>
          <p className="text-sm text-ink-500">두 가지예요. {name} 책에서 뜨는 주제, 그리고 책 숏츠에 빌려 쓸 수 있는 요즘 유행. 숫자는 모두 데이터 근거예요.</p>
        </div>
        {out?.generated_at && <span className="ml-auto text-xs text-ink-500">{when(out.generated_at)} 기준</span>}
      </div>
      {state.kind === "loading" ? <Waiting what="인사이트를 묶는 중…" />
        : state.kind === "failed" ? <Empty>인사이트를 받지 못했어요. {state.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
        : out && (
          <>
            <LlmStatusLine s={out.llm_status} />
            {(out.status === "unknown" || all.length === 0) && <Empty>{out.reason ?? "오늘 인사이트가 없어요."}</Empty>}
            <section aria-labelledby="data-trend-insight-books" className="space-y-3">
              <h3 id="data-trend-insight-books" className="text-lg font-bold text-ink-900">{name} 책에서 뜨는 주제</h3>
              <ul className="space-y-4">
                <OpportunityCards list={out.opportunities} reason={out.opportunities_reason} facts={out.facts} />
                {bookOnes.map((i) => <InsightCard key={i.insight_id} i={i} facts={out.facts} />)}
              </ul>
            </section>
            <section aria-labelledby="data-trend-insight-borrow" className="space-y-3">
              <h3 id="data-trend-insight-borrow" className="text-lg font-bold text-ink-900">
                책 숏츠에 빌려 쓸 요즘 유행 <span className="text-sm font-normal text-ink-500">이름·로고는 안 쓰고 형식만 빌려요</span>
              </h3>
              {borrowed.length > 0
                ? <ul className="space-y-4">{borrowed.map((i) => <InsightCard key={i.insight_id} i={i} facts={out.facts} />)}</ul>
                : <p className="card p-4 text-sm text-ink-500">지금 빌려 쓸 유행이 없어요.{noBorrow ? ` ${noBorrow}` : ""}</p>}
            </section>
            {unlinked.length > 0 && (
              <details className="card p-4">
                <summary className="cursor-pointer text-sm font-semibold text-ink-900">
                  연결할 책이 없는 화제 (참고용) {unlinked.length}개
                </summary>
                <ul className="mt-4 space-y-4">{unlinked.map((i) => <InsightCard key={i.insight_id} i={i} facts={out.facts} />)}</ul>
              </details>
            )}
            <details className="card p-4">
              <summary className="cursor-pointer text-sm font-semibold text-ink-900">브리프 한계·불러온 곳</summary>
              <div className="mt-2 space-y-2 text-xs text-ink-500">
                {out.limits.length > 0 && <ul className="list-disc space-y-0.5 pl-4">{out.limits.map((l) => <li key={l}>{l}</li>)}</ul>}
                <Sources evidence={okEvidence(state)} label="근거 · 브리프가 불러온 곳" />
              </div>
            </details>
          </>
        )}
      {/* 예시(와 그 안의 숏츠 통계 요청)는 처음 펼칠 때 그린다. 그 뒤에는 접어도 남긴다 */}
      <details className="card p-4" onToggle={(e) => e.currentTarget.open && setExamplesOpened(true)}>
        <summary className="cursor-pointer text-sm font-semibold text-ink-900">
          참고용 예시{examplesCount !== null ? ` ${examplesCount}개` : ""} <span className="font-normal text-ink-500">· 숏츠 광고 예시 아이디어</span>
        </summary>
        {examplesOpened && <div className="mt-4">{examples}</div>}
      </details>
    </section>
  );
}
