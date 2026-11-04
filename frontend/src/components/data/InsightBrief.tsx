import { useState, type ReactNode } from "react";
import { Chip, Empty, TONE, Waiting } from "@/components/finance/bits";
import { okEvidence, type SkillState } from "@/components/data/useSkill";
import { Sources, when } from "@/components/data/Sources";
import { OpportunityCards } from "@/components/data/OpportunityCards";
import { AgeBars } from "@/components/data/AgeBars";
import { DATA_SERVER, kpiLive, type TrendInsight, type TrendInsightBriefOutput } from "@/lib/data";

// 트렌드 2단계(인사이트): 기획팀이 가져갈 인사이트 브리프. trend_insight_brief(계약 TrendInsightBriefOutput) 응답 그대로.
// - 카드마다: 한 줄 인사이트 · 카테고리 · 광고 재료 가능 여부(초록 가능 / 노랑 실존 이름·상표 주의 / 회색 아님·판단 못 함 + 서버 이유)
//   · 타깃 연령대 + 연령대별 배수 막대 · 데이터 근거(접힘) · 연결 상품(없으면 서버 이유) · 잘 맞는 숏츠 형식 + 표본 수 · 한계
//   · 참고용 예시(접힘) · insight_id(작게, 복사 버튼: 기획팀이 제안에 적는 값)
// - 연령대별 막대: 브리프 age_bands(#556, trend_age 결과 그대로·설정 순서). 따로 부르지 않는다.
//   막대 길이는 그 주제 연령대 배수 중 가장 큰 값에 대한 비율(그리기용). 굵은 글씨 = 타깃 연령대(target_age)
// - 순서: 기회 가설 카드(opportunities, OpportunityCards.tsx) → 회사 관련 인사이트 → 그 밖의 인사이트
// - 회사 관련 인사이트(company_related, #543)는 따로 묶고 연결 상품·시즌 근거를 강조한다. 나머지 인사이트는 그 아래
// - 시즌 근거(evidence.season_*): 작년 같은 달·다음 달 ÷ 기간 평균 중 큰 값 (서버 값)
// - 응답에 없는 칸(null·빈 목록)은 그리지 않는다. 이유가 오면 이유만

const NO_REASON = "서버가 이유를 주지 않았어요";
const num = (v: number, digits = 0) => v.toLocaleString("ko-KR", { maximumFractionDigits: digits });
const times = (v: number) => `평소의 ${num(v, 2)}배`;
const FORMAT_SOURCE = { topic: "이 주제 숏츠", category: "같은 카테고리 숏츠 통계", all: "전체 숏츠 통계" } as const;

function material(i: TrendInsight): { label: string; tone: string } {
  if (i.ad_material === true && i.real_entity) return { label: "광고 재료 가능 · 이름 주의", tone: TONE.warn };
  if (i.ad_material === true) return { label: "광고 재료 가능", tone: TONE.ok };
  if (i.ad_material === false) return { label: "광고 재료 아님", tone: TONE.muted };
  return { label: "광고 재료 판단 못 함", tone: TONE.muted };
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[7.5rem_minmax(0,1fr)]">
      <p className="text-xs font-semibold text-ink-500">{label}</p>
      <div className="min-w-0 text-sm text-ink-700">{children}</div>
    </div>
  );
}

function CopyId({ id }: { id: string }) {
  const [done, setDone] = useState<"idle" | "ok" | "failed">("idle");
  const copy = () => {
    if (!navigator.clipboard) { setDone("failed"); return; }
    navigator.clipboard.writeText(id).then(() => setDone("ok"), () => setDone("failed"));
  };
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-ink-500">
      <code className="rounded bg-ink-50 px-1.5 py-0.5 font-mono">{id}</code>
      <button type="button" className="rounded px-1.5 py-0.5 font-semibold text-brand-700 ring-1 ring-ink-100 hover:bg-brand-50" onClick={copy}
        aria-label={`인사이트 번호 ${id} 복사`}>
        {done === "ok" ? "복사됨" : "복사"}
      </button>
      {done === "failed" && <span className="text-red-700">복사하지 못했어요</span>}
    </span>
  );
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

function InsightCard({ i }: { i: TrendInsight }) {
  const m = material(i);
  const company = i.company_related === true;
  const season = seasonText(i.evidence);
  return (
    <li className={`card space-y-3 p-4 sm:p-5 ${company ? "ring-2 ring-emerald-200" : ""}`}>
      <div className="flex flex-wrap items-start gap-2">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-ink-900 text-xs font-bold text-white">{i.rank}</span>
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-bold text-ink-900">{i.topic}</h3>
          {i.aliases.length > 0 && <p className="text-xs text-ink-500">같은 주제 · {i.aliases.join(", ")}</p>}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {i.category && i.category !== "unknown" && <Chip label={i.category} tone="bg-ink-100 text-ink-700" />}
          {i.classification_pending && <Chip label="분류 대기" tone={TONE.muted} />}
          <Chip label={m.label} tone={m.tone} />
        </div>
      </div>
      {company && (
        <div className="space-y-2 rounded-xl bg-emerald-50 px-3 py-2.5 text-sm text-emerald-900">
          {i.company_reason && <p className="text-xs">{i.company_reason}</p>}
          <div>
            <p className="text-xs font-semibold">연결 상품</p>
            <div className="mt-0.5"><Products i={i} /></div>
          </div>
          {season && (
            <div>
              <p className="text-xs font-semibold">시즌 근거</p>
              <p className="mt-0.5 font-semibold">{season}</p>
            </div>
          )}
        </div>
      )}
      {i.line && (
        <p className="rounded-xl bg-brand-50 px-3 py-2 text-sm font-semibold leading-relaxed text-brand-900">
          {i.line}
          {i.line_status === "fixed" && i.line_reason && <span className="mt-0.5 block text-xs font-normal text-ink-500">고정 문장 · {i.line_reason}</span>}
        </p>
      )}
      <div className="space-y-2.5">
        {(i.ad_material_reason || (i.real_entity && i.entity_names.length > 0)) && (
          <Row label="광고 재료">
            {[i.ad_material_reason, i.real_entity && i.entity_names.length ? `이름 · ${i.entity_names.join(", ")}` : null].filter(Boolean).join(" · ")}
          </Row>
        )}
        <Row label="타깃 연령대">
          {i.target_age ? (
            <>
              <span className="font-semibold text-ink-900">{i.target_age}</span>
              {i.target_age_ratio !== null && <span> · {times(i.target_age_ratio)}</span>}
              {i.target_age_status === "rising" && <span> · 급상승</span>}
              {(i.age_bands?.length ?? 0) > 0 && <AgeBars bands={i.age_bands ?? []} top={i.target_age} />}
            </>
          ) : <span className="text-ink-500">{i.target_age_reason ?? NO_REASON}</span>}
        </Row>
        {!company && <Row label="연결 상품"><Products i={i} /></Row>}
        {!company && season && <Row label="시즌 근거">{season}</Row>}
        <Row label="잘 맞는 숏츠 형식">
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
        </Row>
        {i.limits.length > 0 && (
          <Row label="한계">
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-ink-500">{i.limits.map((l) => <li key={l}>{l}</li>)}</ul>
          </Row>
        )}
      </div>
      <details className="rounded-xl bg-ink-50 px-3 py-2">
        <summary className="cursor-pointer text-xs font-semibold text-ink-700">데이터 근거 보기</summary>
        <div className="mt-2"><Evidence e={i.evidence} /></div>
      </details>
      {i.example_ideas.length > 0 && (
        <details className="rounded-xl px-3 py-2 ring-1 ring-ink-100">
          <summary className="cursor-pointer text-xs font-semibold text-ink-700">참고용 예시 {i.example_ideas.length}개</summary>
          <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-sm text-ink-700">{i.example_ideas.map((x) => <li key={x}>{x}</li>)}</ul>
        </details>
      )}
      <div className="border-t border-ink-100 pt-2"><CopyId id={i.insight_id} /></div>
    </li>
  );
}

/** 응답은 트렌드 화면(TrendScan)이 한 번 받는다. examples = 참고용 예시(기존 기획안 카드), examplesCount = 그 개수 */
export function InsightBrief({ state, examples, examplesCount }: {
  state: SkillState<TrendInsightBriefOutput>; examples: ReactNode; examplesCount: number | null;
}) {
  const out = state.kind === "ok" ? state.out : null;
  if (!kpiLive) return null;
  // 회사 관련 인사이트는 맨 위에 따로 묶는다 (서버 순서 그대로)
  const companyOnes = (out?.insights ?? []).filter((i) => i.company_related === true);
  const others = (out?.insights ?? []).filter((i) => i.company_related !== true);
  const meta = out ? [out.day ? `${out.day} 브리프` : null, out.generated_at ? `만든 시각 ${when(out.generated_at)}` : null, out.cached ? "오늘 만든 브리프" : null].filter(Boolean).join(" · ") : null;
  return (
    <section id="data-trend-insight" aria-labelledby="data-trend-insight-title" className="space-y-4">
      <div className="card space-y-2 p-4 sm:p-5">
        <h2 id="data-trend-insight-title" className="text-lg font-bold text-ink-900">인사이트 브리프</h2>
        <p className="text-xs text-ink-500">{meta || "기획팀이 이 인사이트를 가져가 제안을 만들어요."}</p>
        {out?.limits && out.limits.length > 0 && (
          <details className="text-xs text-ink-500">
            <summary className="cursor-pointer font-semibold text-ink-700">브리프 한계 {out.limits.length}개</summary>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">{out.limits.map((l) => <li key={l}>{l}</li>)}</ul>
          </details>
        )}
        <Sources evidence={okEvidence(state)} label="근거 · 브리프가 불러온 곳" />
      </div>
      {/* 맨 위 기회 가설 카드 (opportunities, #579) → 회사 관련 인사이트 → 그 밖의 인사이트 */}
      {out && <OpportunityCards list={out.opportunities} reason={out.opportunities_reason} />}
      {state.kind === "loading" ? <Waiting what="인사이트를 묶는 중…" />
        : state.kind === "failed" ? <Empty>인사이트를 받지 못했어요. {state.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
        : out && (out.status === "unknown" || out.insights.length === 0)
          ? <Empty>{out.reason ?? "오늘 인사이트가 없어요."}</Empty>
          : out && (
            <>
              {companyOnes.length > 0 && (
                <section aria-labelledby="data-trend-insight-company" className="space-y-3">
                  <h3 id="data-trend-insight-company" className="text-base font-bold text-ink-900">
                    회사 관련 인사이트 <span className="text-sm font-normal text-ink-500">· {companyOnes.length}개</span>
                  </h3>
                  <ul className="space-y-4">{companyOnes.map((i) => <InsightCard key={i.insight_id} i={i} />)}</ul>
                </section>
              )}
              {others.length > 0 && (
                <section aria-labelledby={companyOnes.length > 0 ? "data-trend-insight-others" : undefined} className="space-y-3">
                  {companyOnes.length > 0 && <h3 id="data-trend-insight-others" className="text-base font-bold text-ink-900">그 밖의 인사이트</h3>}
                  <ul className="space-y-4">{others.map((i) => <InsightCard key={i.insight_id} i={i} />)}</ul>
                </section>
              )}
            </>
          )}
      <details className="card p-4">
        <summary className="cursor-pointer text-sm font-semibold text-ink-900">
          참고용 예시{examplesCount !== null ? ` ${examplesCount}개` : ""} <span className="font-normal text-ink-500">· 숏츠 광고 예시 아이디어</span>
        </summary>
        <div className="mt-4">{examples}</div>
      </details>
    </section>
  );
}
