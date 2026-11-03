import { useState, type ReactNode } from "react";
import { Chip, Empty, Panel, TONE, Waiting } from "@/components/finance/bits";
import type { SkillState } from "@/components/data/useSkill";
import { DATA_SERVER, type PopularCategory, type PopularTopic, type PopularVideo, type PromoConceptsOutput, type TrendPopularOutput } from "@/lib/data";

// 트렌드 1단계(감지) 위쪽: 요약 카드 3개 + 카테고리 버튼.
// - 지금 인기는 trend_popular(계약 TrendPopularOutput), 재료·시즌은 promo_concepts(계약 PromoConceptsOutput) 응답 그대로다.
//   두 응답은 트렌드 화면(TrendScan)이 한 번 받아 2단계 기획안과 같이 쓴다
// - 요약 카드: 오늘 가장 큰 화제(지금 인기 1위와 근거) · 기획안에 쓸 수 있는 트렌드 수(뺀 수와 서버 이유) · 다가오는 시즌(시즌 재료).
//   값이 없으면 그 카드에 서버 이유
// - 카테고리는 버튼으로 하나만 펼친다(서버가 준 순서). 고른 칸만 그리고, 다른 칸은 버튼을 누를 때 그린다
// - 고른 칸: 뜨는 주제 칩 + 조회수 높은 영상(순위·썸네일 자리·제목·채널·조회수). 썸네일은 계약에 없어 자리만 둔다
// - 주제 칩 색: 기획안 재료로 쓴 주제 = 초록, 상표·실존 이름 주의 = 노랑, 재료에서 뺀 주제 = 회색 + 서버 이유,
//   기획안이 보지 않은 주제 = 흰색. 숫자는 서버 값만, 없으면(null) 그 줄을 뺀다

const num = (v: number | null | undefined) =>
  typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("ko-KR", { maximumFractionDigits: 0 }) : null;
const TIME = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const when = (iso: string | null) => (iso ? TIME.format(new Date(iso)) : null);
const NO_REASON = "서버가 이유를 주지 않았어요";
const norm = (s: string) => s.trim().toLowerCase();
const categoryName = (c: string | null | undefined) => (c && c !== "unknown" ? c : "분류 못 함");

/** 근거 (서버 값이 있는 것만) */
export function evidenceOf(t: PopularTopic): string[] {
  const out: string[] = [];
  if (t.youtube_videos != null) out.push(`YouTube 인기 영상 ${num(t.youtube_videos)}개`);
  if (t.trending_rank != null) out.push(`인기 검색어 ${t.trending_rank}위`);
  if (t.views_per_hour != null) out.push(`지금 시간당 조회 +${num(t.views_per_hour)}`);
  return out;
}

// ------------------------------------------------------------------- 재료 판정 (칩 색)

type MaterialKind = "ok" | "caution" | "excluded" | "none";
export type Material = { kind: MaterialKind; reason?: string };
const MATERIAL: Record<MaterialKind, { chip: string; legend: string }> = {
  ok: { chip: TONE.ok, legend: "기획안 재료" },
  caution: { chip: TONE.warn, legend: "상표·실존 이름 주의" },
  excluded: { chip: TONE.muted, legend: "재료에서 뺌 (이유)" },
  none: { chip: "bg-surface text-ink-700 ring-1 ring-ink-100", legend: "기획안이 보지 않은 주제" },
};

/** 이름(대표·별칭)이 promo_concepts 재료·뺀 목록, trend_popular 표시와 맞는지로 칩 색을 정한다 */
export function materialOf(names: string[], promo: PromoConceptsOutput | null, topic?: PopularTopic | null): Material {
  const keys = names.map(norm);
  const skipped = promo?.skipped_trends?.find((s) => keys.includes(norm(s.name)));
  if (skipped) return { kind: "excluded", reason: skipped.reason };
  if (topic?.brand_safe === false) return { kind: "excluded", reason: `${categoryName(topic.topic_category)} 분류는 추천하지 않아요` };
  const used = promo?.trends.find((t) => t.kind === "popular" && keys.includes(norm(t.name)));
  const entities = [...new Set([...(used?.entity_names ?? []), ...(topic?.entity_names ?? [])])];
  if ((used?.ip_risk || topic?.real_entity === true)) return { kind: "caution", reason: entities.length ? entities.join(", ") : undefined };
  if (used) return { kind: "ok" };
  return { kind: "none" };
}

function TopicChip({ t, promo }: { t: PopularTopic; promo: PromoConceptsOutput | null }) {
  const m = materialOf([t.keyword, ...t.aliases], promo, t);
  const ev = evidenceOf(t);
  return (
    <li className={`chip max-w-full text-sm ${MATERIAL[m.kind].chip}`} title={[MATERIAL[m.kind].legend, m.reason, ...ev].filter(Boolean).join(" · ")}>
      <span className="font-semibold">{t.keyword}</span>
      {/* 주의 이름(실존 인물·팀·상표)은 칩에 마우스를 올리면 보인다 */}
      {m.kind === "caution" && <span className="ml-1 text-xs">· 주의</span>}
      {m.kind === "excluded" && m.reason && <span className="ml-1 truncate text-xs">· {m.reason}</span>}
    </li>
  );
}

// ------------------------------------------------------------------- 요약 카드 3개

function Card({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section aria-label={label} className="card flex min-w-0 flex-col gap-2 p-4">
      <p className="panel-title">{label}</p>
      {children}
    </section>
  );
}

function Pending<T>({ s, what }: { s: SkillState<T>; what: string }) {
  if (s.kind === "loading") return <p className="text-sm text-ink-500" aria-live="polite">{what} 받는 중…</p>;
  if (s.kind === "failed") return <p className="text-sm text-amber-700">{what}: 받지 못했어요. {s.reason}</p>;
  return null;
}

/** 서버 이유를 묶어 개수와 함께 (같은 문장끼리) */
function reasonCounts(list: { reason: string }[]): { reason: string; count: number }[] {
  const m = new Map<string, number>();
  for (const x of list) m.set(x.reason, (m.get(x.reason) ?? 0) + 1);
  return [...m.entries()].map(([reason, count]) => ({ reason, count })).sort((a, b) => b.count - a.count);
}

export function TrendSummary({ popular, promo }: { popular: SkillState<TrendPopularOutput>; promo: SkillState<PromoConceptsOutput> }) {
  const pop = popular.kind === "ok" ? popular.out : null;
  const pro = promo.kind === "ok" ? promo.out : null;
  const top = pop?.items[0] ?? null;
  const usable = pro?.trends.filter((t) => t.kind === "popular") ?? [];
  const seasons = pro?.trends.filter((t) => t.kind === "season") ?? [];
  const skipped = pro?.skipped_trends ?? [];
  const reasons = reasonCounts(skipped);
  return (
    <div className="grid gap-3 md:grid-cols-3">
      <Card label="오늘 가장 큰 화제">
        <Pending s={popular} what="지금 인기" />
        {pop && (top ? (
          <>
            <p className="flex flex-wrap items-center gap-2 text-xl font-bold text-ink-900">
              {top.keyword}
              <Chip label={categoryName(top.topic_category)} tone={TONE.info} />
            </p>
            <p className="text-sm text-ink-700">지금 인기 1위 · {evidenceOf(top).join(" · ") || "근거 숫자를 받지 못했어요"}</p>
            {top.real_entity === true && <p className="text-xs text-amber-700">실존 이름이 든 주제예요{top.entity_names?.length ? ` (${top.entity_names.join(", ")})` : ""}</p>}
          </>
        ) : <p className="text-sm text-ink-500">{pop.empty_reason ?? NO_REASON}</p>)}
      </Card>

      <Card label="기획안에 쓸 수 있는 트렌드">
        <Pending s={promo} what="기획안 재료" />
        {pro && (pro.popular_status === "unknown" && usable.length === 0 ? (
          <p className="text-sm text-amber-700">{pro.popular_reason ?? NO_REASON}</p>
        ) : (
          <>
            <p className="text-xl font-bold text-ink-900">{usable.length}개 <span className="text-sm font-normal text-ink-500">· 뺀 주제 {skipped.length}개</span></p>
            {usable.length > 0 && (
              <ul className="flex flex-wrap gap-1.5">
                {usable.map((t) => <li key={t.name} className={`chip text-sm ${t.ip_risk ? TONE.warn : TONE.ok}`}>{t.name}</li>)}
              </ul>
            )}
            {reasons.length > 0 && (
              <ul className="space-y-0.5 text-xs text-ink-500">
                {reasons.slice(0, 2).map((r) => <li key={r.reason} className="truncate" title={r.reason}>뺀 이유 · {r.reason} ({r.count}개)</li>)}
                {reasons.length > 2 && <li>그 밖의 이유 {reasons.length - 2}가지</li>}
              </ul>
            )}
          </>
        ))}
      </Card>

      <Card label="다가오는 시즌">
        <Pending s={promo} what="시즌 재료" />
        {pro && (seasons.length > 0 ? (
          <ul className="space-y-1">
            {seasons.map((t) => (
              <li key={t.name}>
                <span className="chip bg-violet-50 text-sm font-semibold text-violet-700">{t.name}</span>
                {t.detail && <p className="mt-0.5 text-xs text-ink-700">{t.detail}</p>}
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-ink-500">시즌 재료가 없어요. {pro.season_reason ?? NO_REASON}</p>)}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------- 카테고리 버튼 + 고른 칸

/** 화면 칸 하나. legacy = 예전 응답을 화면이 주제 분류별로 묶은 칸(순위는 전체 순위) */
type Cell = { category: string; items: PopularTopic[]; shortage_reason: string | null; top_videos?: PopularVideo[]; top_videos_reason?: string | null; legacy?: boolean };

function cellsOf(out: TrendPopularOutput): Cell[] {
  if (out.by_category?.length) return out.by_category.map((c: PopularCategory) => ({ ...c }));
  const groups = new Map<string, PopularTopic[]>();
  for (const t of out.items) groups.set(categoryName(t.topic_category), [...(groups.get(categoryName(t.topic_category)) ?? []), t]);
  return [...groups.entries()].map(([category, items]) => ({ category, items, shortage_reason: null, legacy: true }));
}

function VideoRow({ v, rank }: { v: PopularVideo; rank: number }) {
  return (
    <li className="flex items-center gap-3 py-2">
      <span className="w-5 shrink-0 text-right text-sm font-semibold text-ink-500">{rank}</span>
      {/* 썸네일 자리 (계약에 썸네일 주소가 없다) */}
      <span aria-hidden className="grid aspect-video w-20 shrink-0 place-items-center rounded-lg bg-ink-100 text-ink-300">▶</span>
      <div className="min-w-0 flex-1">
        <a className="block truncate font-semibold text-ink-900 hover:underline" href={`https://www.youtube.com/watch?v=${encodeURIComponent(v.video_id)}`} target="_blank" rel="noreferrer noopener">
          {v.title ?? "제목 없음"}
        </a>
        <p className="truncate text-xs text-ink-500">{v.channel_title ?? "채널 모름"}</p>
      </div>
      <span className="shrink-0 text-sm tabular-nums text-ink-700">{v.views != null ? `조회 ${v.views.toLocaleString("ko-KR")}회` : "조회수 모름"}</span>
    </li>
  );
}

function CellView({ c, promo }: { c: Cell; promo: PromoConceptsOutput | null }) {
  const videos = c.top_videos ?? [];
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <section aria-label="뜨는 주제" className="min-w-0 space-y-2">
        <p className="panel-title">뜨는 주제</p>
        {c.items.length > 0 ? (
          <ul className="flex flex-wrap gap-1.5">{c.items.map((t) => <TopicChip key={t.keyword} t={t} promo={promo} />)}</ul>
        ) : null}
        {c.shortage_reason && <p className="text-xs text-ink-500">{c.shortage_reason}</p>}
        {c.items.length === 0 && !c.shortage_reason && <p className="text-xs text-ink-500">이 칸에 들어온 주제가 없어요. {NO_REASON}</p>}
      </section>
      <section aria-label="조회수 높은 영상" className="min-w-0 space-y-1">
        <p className="panel-title">조회수 높은 영상</p>
        {videos.length > 0 ? (
          <ol className="divide-y divide-ink-100">{videos.map((v, i) => <VideoRow key={v.video_id} v={v} rank={i + 1} />)}</ol>
        ) : <p className="text-xs text-ink-500">{c.top_videos_reason ?? "이 칸의 영상을 받지 못했어요. " + NO_REASON}</p>}
        {videos.length > 0 && c.top_videos_reason && <p className="text-xs text-ink-500">{c.top_videos_reason}</p>}
      </section>
    </div>
  );
}

/** 출처 줄: 시각, 또는 못 읽은 이유 그대로 */
function sourceLines(out: TrendPopularOutput): { text: string; warn: boolean }[] {
  return [
    out.youtube_status === "known"
      ? { text: `YouTube 인기 ${when(out.snapshot_at) ?? "-"}`, warn: false }
      : { text: `YouTube: ${out.youtube_reason ?? NO_REASON}`, warn: true },
    out.trending_status === "known"
      ? { text: `인기 검색어 ${when(out.trending_at) ?? "-"}`, warn: false }
      : { text: `인기 검색어: ${out.trending_reason ?? NO_REASON}`, warn: true },
  ];
}

export function TrendPopular({ popular, promo, onReload }: {
  popular: SkillState<TrendPopularOutput>;
  promo: PromoConceptsOutput | null;
  onReload: () => void;
}) {
  const out = popular.kind === "ok" ? popular.out : null;
  const cells = out ? cellsOf(out) : [];
  const [picked, setPicked] = useState<string | null>(null);
  const current = cells.find((c) => c.category === picked) ?? cells[0] ?? null;
  return (
    <Panel id="data-trend-popular" title="카테고리별 지금 인기"
      hint={out ? (
        <span className="flex flex-wrap gap-x-3">
          {sourceLines(out).map((l) => <span key={l.text} className={l.warn ? "text-amber-700" : undefined}>{l.text}</span>)}
        </span>
      ) : "카테고리를 하나 고르면 뜨는 주제와 조회수 높은 영상을 보여 줘요."}
      tools={<button type="button" className="btn-ghost" disabled={popular.kind === "loading"} onClick={onReload}>다시 보기</button>}>
      {popular.kind === "loading" ? <Waiting what="지금 인기를 받는 중…" />
        : popular.kind === "failed" ? <Empty>지금 인기를 받지 못했어요. {popular.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
        : !current ? <Empty>{out?.empty_reason ?? "지금 인기 주제가 없어요."}</Empty> : (
          <div className="space-y-4">
            <div role="tablist" aria-label="카테고리" className="flex flex-wrap gap-1.5">
              {cells.map((c) => {
                const on = c.category === current.category;
                return (
                  <button key={c.category} type="button" role="tab" aria-selected={on} aria-controls="trend-popular-cell"
                    onClick={() => setPicked(c.category)}
                    className={`rounded-full px-3 py-1.5 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${on ? "bg-brand-600 text-white" : "bg-ink-50 text-ink-700 ring-1 ring-ink-100 hover:bg-ink-100"}`}>
                    {categoryName(c.category)}
                  </button>
                );
              })}
            </div>
            {/* 고른 칸만 그린다 */}
            <div id="trend-popular-cell" role="tabpanel" aria-label={categoryName(current.category)}>
              <CellView c={current} promo={promo} />
            </div>
            <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-500" aria-label="주제 칩 색">
              {(Object.keys(MATERIAL) as MaterialKind[]).map((k) => (
                <span key={k} className="flex items-center gap-1"><span aria-hidden className={`inline-block h-3 w-3 rounded-full ${MATERIAL[k].chip}`} />{MATERIAL[k].legend}</span>
              ))}
            </p>
          </div>
        )}
    </Panel>
  );
}
