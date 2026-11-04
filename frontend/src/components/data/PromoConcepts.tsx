import { Chip, Empty, Waiting } from "@/components/finance/bits";
import type { ReactNode } from "react";
import { okEvidence, okOut, useSkill, type SkillState } from "@/components/data/useSkill";
import { Basis, Sources, VideoLinks } from "@/components/data/Sources";
import { videoTitles } from "@/components/data/TrendPopular";
import {
  DATA_SERVER, dataAi, kpiLive,
  type PopularTopic, type PromoConcept, type PromoConceptsOutput, type PromoTrendKind, type ShortsStatsOutput, type TrendPopularOutput,
} from "@/lib/data";

// 트렌드 2단계(인사이트) 맨 아래 "참고용 예시" 접힘 안: 캣북스 숏츠 광고 예시 아이디어(참고용, 확정 기획은 기획팀). 예시마다 타깃 연령대(trend_age). promo_concepts(계약 PromoConceptsOutput) 응답 그대로 보여 준다.
// - 맨 위: 제목 · 다시 만들기(계약 regenerate, 서버는 하루 캐시) · 재료 한 줄(상품·트렌드·시즌·형식 개수, 못 읽은 출처는 서버 이유)
//   · "뺀 재료 N개 보기"(접힘: 뺀 지금 인기 주제·시즌 확인 키워드·버린 초안과 서버 이유)
// - 왼쪽: 잘 되는 숏츠 형식 막대. 값은 기획안 재료 shorts_formats(서버 대표값)라 기획안 칩의 평균 조회수와 같다.
//   막대 길이는 그중 가장 큰 값에 대한 비율(그리기용). 아래 표본 한계는 shorts_stats의 서버 문장 그대로
// - 오른쪽 카드: 칩(길이·형식·트렌드 출처) → 제목 → "왜 맞나" → 콘티(넓은 화면 가로, 좁은 화면 세로) → 등장 상품·가격 → 주의(노랑)
//   → [기획팀에 보내기] 꺼 둠(승인 절차 등록 전)
// - 트렌드 출처 색: 지금 인기 = 파랑, 시즌 = 보라, 회사 이벤트 = 초록, 트렌드 없음 = 회색
// - 콘티 문장 앞의 "1컷:" 같은 번호는 떼고 화면이 컷 번호를 한 번만 붙인다
// - 응답은 트렌드 화면(TrendScan)이 한 번 받아 1단계 요약 카드와 같이 쓴다
// - 근거(카드 아래): 빌린 트렌드의 출처(지금 인기 = YouTube 인기 차트 영상 링크·Google 인기 검색어 순위, 시즌 = 네이버 데이터랩
//   월별 배율, 이벤트 = 회사 설정), 숏츠 형식의 표본(shorts_stats 형식별 조회수 상위 숏츠 링크), 상품 = shop-api 값.
//   지금 인기·숏츠 통계는 이미 받은 응답(trend_popular·shorts_stats)에서 이름이 같은 것을 찾는다. 맨 위에 부른 API 목록

const TIME = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const when = (iso: string | null) => (iso ? TIME.format(new Date(iso)) : null);
const won = (v: number | null) => (typeof v === "number" ? `${v.toLocaleString("ko-KR")}원` : null);
const count = (v: number) => Math.round(v).toLocaleString("ko-KR");
const NO_REASON = "서버가 이유를 주지 않았어요";
const SEND_BLOCKED = "보내기는 승인 절차 등록 후 열려요";

const KIND: Record<PromoTrendKind, { label: string; tone: string }> = {
  popular: { label: "지금 인기", tone: "bg-brand-50 text-brand-700" },
  season: { label: "시즌", tone: "bg-violet-50 text-violet-700" },
  event: { label: "회사 이벤트", tone: "bg-emerald-50 text-emerald-700" },
};
const NO_TREND = "bg-ink-100 text-ink-500";

/** "1컷: 문장", "1컷 | 문장", "1. 문장" → "문장" (번호는 화면이 붙인다) */
const cutText = (s: string) => s.replace(/^\s*(\d+\s*컷|컷\s*\d+|\d+)\s*[:|.)\-–·]?\s*/, "");

function TrendChip({ c }: { c: PromoConcept }) {
  if (!c.borrowed_trend) return <Chip label="트렌드 없음" tone={NO_TREND} />;
  const k = c.trend_kind ? KIND[c.trend_kind] : null;
  return <Chip label={k ? `${k.label} · ${c.borrowed_trend}` : c.borrowed_trend} tone={k?.tone ?? NO_TREND} />;
}

const ratioText = (v: number | null) => (typeof v === "number" ? `${v.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}배` : "–");

/** 지금 인기 응답에서 이름(대표·별칭)이 같은 주제 */
function topicOf(popular: TrendPopularOutput | null, name: string): PopularTopic | null {
  const key = name.trim().toLowerCase();
  const all = [...(popular?.items ?? []), ...(popular?.by_category ?? []).flatMap((c) => c.items)];
  return all.find((t) => [t.keyword, ...t.aliases].some((n) => n.trim().toLowerCase() === key)) ?? null;
}

type Ctx = { out: PromoConceptsOutput; popular: TrendPopularOutput | null; shorts: ShortsStatsOutput | null };

/** 초안 하나의 근거 줄: 트렌드 출처 · 숏츠 형식 표본 · 상품 */
function conceptBasis(c: PromoConcept, { out, popular, shorts }: Ctx) {
  const rows: { source: string; body: ReactNode }[] = [];
  const trend = out.trends.find((t) => t.name === c.borrowed_trend) ?? null;
  if (trend?.kind === "popular") {
    const topic = topicOf(popular, trend.name);
    const parts = [trend.detail];
    if (topic?.youtube_videos != null) parts.push(`YouTube 인기 차트 영상 ${topic.youtube_videos}개`);
    if (topic?.trending_rank != null) parts.push(`Google 인기 검색어 ${topic.trending_rank}위`);
    rows.push({
      source: "트렌드 · 지금 인기",
      body: <>{parts.join(" · ")} {topic && topic.video_ids.length > 0 && <VideoLinks ids={topic.video_ids} titles={videoTitles(popular)} max={2} />}</>,
    });
  } else if (trend?.kind === "season") {
    const check = (out.season_checks ?? []).find((s) => trend.name.startsWith(`${s.keyword} `));
    rows.push({
      source: "트렌드 · 네이버 데이터랩 월별 검색",
      body: check?.status === "known"
        ? `'${check.keyword}' 작년 같은 달(${check.same_month ?? "?"}) ${ratioText(check.same_month_ratio)} · 다음 달(${check.next_month ?? "?"}) ${ratioText(check.next_month_ratio)} (기간 평균 대비)`
        : trend.detail,
    });
  } else if (trend?.kind === "event") {
    rows.push({ source: "트렌드 · 회사 이벤트 달력", body: `${trend.detail} (회사 설정 [promo] events)` });
  }
  if (c.shorts_format) {
    const group = shorts?.by_format.find((g) => g.name === c.shorts_format) ?? null;
    const fmt = out.shorts_formats?.find((f) => f.name === c.shorts_format) ?? null;
    rows.push({
      source: "형식 · YouTube 숏츠 표본",
      body: <>
        {fmt ? `인기 숏츠 ${fmt.count.toLocaleString("ko-KR")}개 평균 조회수 ${count(fmt.avg_views)}회` : "표본 값을 받지 못했어요"}
        {shorts?.snapshot_at && ` (${when(shorts.snapshot_at)} 스냅샷)`}{" "}
        {group && group.top.length > 0 && <VideoLinks ids={group.top.map((v) => v.video_id)} titles={new Map(group.top.map((v) => [v.video_id, v.title]))} max={2} />}
      </>,
    });
  }
  if (c.products.length > 0) rows.push({ source: "상품 · 캣북스 상품 목록 (shop-api)", body: "이름·가격은 상품 목록 값 그대로" });
  return rows;
}

function ConceptCard({ c, ctx }: { c: PromoConcept; ctx: Ctx }) {
  const cautions = [...c.warnings, ...c.cautions];
  const cuts = c.storyboard.map(cutText).filter(Boolean);
  return (
    <li className="card space-y-3 p-4">
      <div className="flex flex-wrap gap-1.5">
        {typeof c.duration_seconds === "number" ? <Chip label={`${c.duration_seconds}초`} tone="bg-ink-100 text-ink-700" />
          : c.format && <Chip label={c.format} tone="bg-ink-100 text-ink-700" />}
        {c.shorts_format && (
          <Chip label={[`형식 · ${c.shorts_format}`, typeof c.shorts_format_avg_views === "number" ? `평균 조회수 ${count(c.shorts_format_avg_views)}회` : null].filter(Boolean).join(" · ")}
            tone="bg-ink-100 text-ink-700" />
        )}
        <TrendChip c={c} />
        {c.target_age
          ? <Chip label={`타깃 ${c.target_age} · 평소의 ${ratioText(c.target_age_ratio ?? null)}${c.target_age_status === "rising" ? " 급상승" : ""}`} tone="bg-violet-50 text-violet-700" />
          : c.target_age_reason && <Chip label={`타깃 미정 · ${c.target_age_reason}`} tone="bg-ink-100 text-ink-500" />}
      </div>
      <div>
        <h3 className="text-lg font-bold text-ink-900">{c.title}</h3>
        {c.concept && <p className="mt-0.5 text-sm text-ink-700">{c.concept}</p>}
      </div>
      {c.fit_reason && (
        <div className="rounded-xl bg-brand-50 px-3 py-2 text-sm text-ink-900">
          <p className="text-xs font-semibold text-brand-700">왜 맞나</p>
          <p className="mt-0.5">{c.fit_reason}</p>
        </div>
      )}
      {cuts.length > 0 && (
        <ol aria-label="콘티" className="flex flex-col gap-2 sm:flex-row">
          {cuts.map((cut, i) => (
            <li key={i} className="min-w-0 flex-1 rounded-xl bg-ink-50 px-3 py-2 text-sm leading-relaxed text-ink-700">
              <span className="mb-1 block text-xs font-semibold text-ink-500">{i + 1}컷</span>
              {cut}
            </li>
          ))}
        </ol>
      )}
      {c.products.length > 0 && (
        <div className="text-sm">
          <p className="mb-1 text-xs font-semibold text-ink-500">등장 상품</p>
          <ul className="flex flex-wrap gap-1.5">
            {c.products.map((p) => (
              <li key={p.product_id} className="chip bg-surface text-sm text-ink-900 ring-1 ring-ink-100">
                {p.name}{won(p.price) && <b className="ml-1.5">{won(p.price)}</b>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {cautions.length > 0 && (
        <div className="rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <p className="text-xs font-semibold text-amber-700">주의</p>
          <ul className="mt-0.5 list-disc space-y-0.5 pl-4">{cautions.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}
      <Basis rows={conceptBasis(c, ctx)} />
      <div className="flex flex-wrap items-center gap-2 border-t border-ink-100 pt-3">
        <button type="button" className="btn-primary" disabled aria-describedby={`send-${c.concept_id}`}>기획팀에 보내기</button>
        <span id={`send-${c.concept_id}`} className="text-xs text-ink-500">{SEND_BLOCKED}</span>
      </div>
    </li>
  );
}

/** 재료 한 줄: 개수, 못 읽은 출처는 서버 이유 */
function Ingredients({ out }: { out: PromoConceptsOutput }) {
  const trends = out.trends.filter((t) => t.kind === "popular").length;
  const seasons = out.trends.filter((t) => t.kind === "season").length;
  const parts = [
    out.catalog_status === "known" ? `상품 ${out.products.toLocaleString("ko-KR")}개` : null,
    `트렌드 ${trends}개`,
    `시즌 ${seasons}개`,
    out.shorts_status === "known" ? `형식 ${out.shorts_formats?.length ?? 0}개` : null,
  ].filter(Boolean);
  const warns = [
    out.catalog_status === "unknown" ? `상품: ${out.catalog_reason ?? NO_REASON}` : null,
    out.popular_status === "unknown" ? `지금 인기: ${out.popular_reason ?? NO_REASON}` : null,
    out.season_status === "unknown" ? `시즌: ${out.season_reason ?? NO_REASON}` : null,
    out.shorts_status !== "known" ? `숏츠 형식: ${out.shorts_reason ?? NO_REASON}` : null,
    out.popular_trend_note ? `지금 인기 트렌드를 쓴 초안 없음: ${out.popular_trend_note}` : null,
  ].filter((x): x is string => Boolean(x));
  const skipped = out.skipped_trends ?? [];
  const checks = out.season_checks ?? [];
  const ratio = (v: number | null) => (typeof v === "number" ? `${v.toLocaleString("ko-KR")}배` : "–");
  return (
    <div className="space-y-1 text-sm">
      <p className="text-ink-700"><span className="font-semibold text-ink-900">재료</span> · {parts.join(" · ")}</p>
      {warns.map((w) => <p key={w} className="text-xs text-amber-700">{w}</p>)}
      {(skipped.length > 0 || checks.length > 0 || out.dropped.length > 0) && (
        <details className="text-xs text-ink-700">
          <summary className="cursor-pointer font-semibold text-ink-900">
            {skipped.length > 0 ? `뺀 재료 ${skipped.length}개 보기` : "뺀 재료 없음 · 시즌 확인·버린 초안 보기"}
          </summary>
          <div className="mt-2 space-y-2 rounded-xl bg-ink-50 px-3 py-2">
            {skipped.length > 0 && (
              <ul className="space-y-0.5">{skipped.map((t) => <li key={t.name}><b>{t.name}</b> · {t.reason}</li>)}</ul>
            )}
            {checks.length > 0 && (
              <div>
                <p className="font-semibold text-ink-900">시즌 확인한 키워드 {checks.length}개 (작년 같은 달·다음 달 검색 ÷ 기간 평균)</p>
                <ul className="mt-0.5 space-y-0.5">
                  {checks.map((s) => (
                    <li key={s.keyword}>
                      <b>{s.keyword}</b> · {s.status === "known" ? `같은 달 ${ratio(s.same_month_ratio)} · 다음 달 ${ratio(s.next_month_ratio)}` : (s.reason ?? NO_REASON)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {out.dropped.length > 0 && (
              <div>
                <p className="font-semibold text-ink-900">검사에서 버린 초안 {out.dropped.length}개</p>
                <ul className="mt-0.5 space-y-0.5">{out.dropped.map((d, i) => <li key={`${d.title}-${i}`}><b>{d.title}</b> · {d.reason}</li>)}</ul>
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

/** 형식 하나의 표본 숏츠 링크 (shorts_stats 형식별 조회수 상위) */
function FormatSample({ top }: { top: ShortsStatsOutput["by_format"][number]["top"] }) {
  if (top.length === 0) return null;
  return <p className="mt-0.5 text-xs text-ink-700">표본 · <VideoLinks ids={top.map((v) => v.video_id)} titles={new Map(top.map((v) => [v.video_id, v.title]))} max={2} /></p>;
}

/** 왼쪽: 잘 되는 숏츠 형식 막대(기획안 재료 값) + 표본 한계(shorts_stats 서버 문장) */
function FormatBars({ out, s }: { out: PromoConceptsOutput; s: SkillState<ShortsStatsOutput> }) {
  const formats = out.shorts_formats ?? [];
  const max = Math.max(0, ...formats.map((f) => f.avg_views));
  const groups = s.kind === "ok" ? s.out.by_format : [];
  return (
    <section aria-labelledby="promo-formats-title" className="card space-y-3 p-4">
      <h3 id="promo-formats-title" className="text-base font-semibold text-ink-900">잘 되는 숏츠 형식</h3>
      {formats.length === 0 ? (
        <p className="text-sm text-ink-500">{out.shorts_reason ?? "기획안에 쓴 숏츠 형식이 없어요."}</p>
      ) : (
        <ul className="space-y-3">
          {formats.map((f) => (
            <li key={f.name}>
              <p className="flex items-baseline justify-between gap-2 text-sm">
                <span className="font-semibold text-ink-900">{f.name}</span>
                <span className="tabular-nums text-ink-700">평균 조회수 {count(f.avg_views)}회</span>
              </p>
              <div className="mt-1 h-2.5 rounded-full bg-ink-100" aria-hidden>
                <div className="h-2.5 rounded-full bg-brand-500" style={{ width: `${max > 0 ? Math.max(2, (f.avg_views / max) * 100) : 0}%` }} />
              </div>
              <p className="mt-0.5 text-xs text-ink-500">숏츠 {f.count.toLocaleString("ko-KR")}개</p>
              <FormatSample top={groups.find((g) => g.name === f.name)?.top ?? []} />
            </li>
          ))}
        </ul>
      )}
      <div className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">
        <p className="font-semibold">표본 한계</p>
        {s.kind === "loading" ? <p>받는 중…</p>
          : s.kind === "failed" ? <p>받지 못했어요. {s.reason}</p>
          : <p>{s.out.status === "unknown" ? (s.out.reason ?? NO_REASON) : (s.out.limitation || NO_REASON)}</p>}
      </div>
      <Sources evidence={okEvidence(s)} label="근거 · 숏츠 통계가 불러온 곳" />
    </section>
  );
}

/** 응답은 트렌드 화면(TrendScan)이 한 번 받는다. onRegenerate = "다시 만들기". popular = 이미 받은 지금 인기(근거 영상 찾기용) */
export function PromoConcepts({ state, popular = null, onRegenerate }: {
  state: SkillState<PromoConceptsOutput>; popular?: TrendPopularOutput | null; onRegenerate: () => void;
}) {
  const shorts = useSkill(() => dataAi.shortsStats());
  if (!kpiLive) return null;
  const out = state.kind === "ok" ? state.out : null;
  const ctx: Ctx | null = out ? { out, popular, shorts: okOut(shorts.state) } : null;
  const meta = out ? [out.generated_at ? `만든 시각 ${when(out.generated_at)}` : null, out.cached ? "오늘 만든 초안" : null].filter(Boolean).join(" · ") : null;
  return (
    <section id="data-trend-promo" aria-labelledby="data-trend-promo-title" className="space-y-4">
      <div className="card space-y-2 p-4 sm:p-5">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <h2 id="data-trend-promo-title" className="text-lg font-bold text-ink-900">캣북스 숏츠 광고 예시 아이디어 <span className="chip ml-1 bg-ink-100 align-middle text-xs font-semibold text-ink-700">참고용</span></h2>
            <p className="text-xs text-ink-500">{meta || "참고용 예시예요. 확정 기획은 기획팀, 제작·게시는 마케팅이 해요."}</p>
          </div>
          <button type="button" className="btn-ghost" disabled={state.kind === "loading"} onClick={onRegenerate}>다시 만들기</button>
        </div>
        {out && <Ingredients out={out} />}
        <Sources evidence={okEvidence(state)} label="근거 · 기획안이 불러온 곳" />
      </div>
      {state.kind === "loading" ? <Waiting what="예시 아이디어를 받는 중…" />
        : state.kind === "failed" ? <Empty>예시 아이디어를 받지 못했어요. {state.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
        : out && (
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2.4fr)]">
            <FormatBars out={out} s={shorts.state} />
            {out.concepts.length === 0
              ? <Empty>{out.empty_reason ?? "예시 아이디어가 없어요."}</Empty>
              : ctx && <ul className="space-y-4">{out.concepts.map((c) => <ConceptCard key={c.concept_id} c={c} ctx={ctx} />)}</ul>}
          </div>
        )}
    </section>
  );
}
