import { useState, type ReactNode } from "react";
import { Empty, TONE } from "@/components/finance/bits";
import type { SkillState } from "@/components/data/useSkill";
import { CautionChip, ThumbImage, hasCaution } from "@/components/data/ShortsCard";
import { videoUrl } from "@/components/data/Sources";
import { type PopularCategory, type PopularTopic, type PopularVideo, type PromoConceptsOutput, type TrendPopularOutput } from "@/lib/data";

// 트렌드 1단계(감지) 보조: 맨 위 한 줄(TrendHeadline)과 맨 아래 접힘 "긴 영상"(LongVideos). trend_popular(계약 TrendPopularOutput) 응답 그대로.
// 숏츠 그리드는 ShortsGrid.tsx(shorts_stats)
// - 맨 위 한 줄: 오늘 가장 큰 화제 = 지금 인기 1위(items[0].keyword) + YouTube 인기 스냅샷 시각
// - 태그 칩(tagsOf): 그 카테고리 주제 중 YouTube 태그에서 나온 것(origins에 youtube_tag)만, 서버 이름 그대로
// - 긴 영상: 카테고리 칩(서버 순서, 개수 = 그 칸 긴 영상 수) · 정렬(조회수 / 시간당 조회 / 구독자 대비 배수, 받은 값으로만, 값이 없는 영상은 뒤로)
//   · 영상 카드(썸네일 · 순위 · 제목 2줄 · 채널 · 큰 조회수 · 시간당·구독자 대비 · 칩 하나). 누르면 서버 video_url 새 탭
// - 긴 영상 = 그 칸 주제들의 영상(videos) + 카테고리 조회수 상위 영상(top_videos) 중 서버 is_short가 true가 아닌 것(모름 포함). 같은 영상은 한 번만.
//   길이는 화면에서 따로 판단하지 않는다
// - 칩: 서버 ad_usable·caution_types가 있으면 그 값(CautionChip, #679), 없으면 주제 판정(materialOf: 기획안 재료에서 뺀 주제·추천 안 함 분류 = 광고에 안 씀,
//   실존 이름·상표 = 주의, 재료로 쓴 주제 = 가능)

const num = (v: number | null | undefined) =>
  typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("ko-KR", { maximumFractionDigits: 0 }) : null;
const COMPACT = new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 });
const TIME = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const when = (iso: string | null) => (iso ? TIME.format(new Date(iso)) : null);
const norm = (s: string) => s.trim().toLowerCase();
export const categoryName = (c: string | null | undefined) => (c && c !== "unknown" ? c : "분류 못 함");

/** 근거 (서버 값이 있는 것만) */
export function evidenceOf(t: PopularTopic): string[] {
  const out: string[] = [];
  if (t.youtube_videos != null) out.push(`YouTube 인기 영상 ${num(t.youtube_videos)}개`);
  if (t.trending_rank != null) out.push(`인기 검색어 ${t.trending_rank}위`);
  if (t.views_per_hour != null) out.push(`지금 시간당 조회 +${num(t.views_per_hour)}`);
  return out;
}

// ------------------------------------------------------------------- 재료 판정 (칩)

type MaterialKind = "ok" | "caution" | "excluded" | "none";
export type Material = { kind: MaterialKind; reason?: string };
/** 영상 카드 칩 (none = 기획안이 보지 않은 주제라 칩을 그리지 않는다) */
const MATERIAL: Record<Exclude<MaterialKind, "none">, { chip: string; label: string }> = {
  ok: { chip: TONE.ok, label: "광고 재료 가능" },
  caution: { chip: TONE.warn, label: "실존 인물·상표 주의" },
  excluded: { chip: TONE.muted, label: "광고에 안 씀" },
};

/** 이름(대표·별칭)이 promo_concepts 재료·뺀 목록, trend_popular 표시와 맞는지로 판정한다 */
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

/** 응답 안 모든 칸의 영상 id → 제목 (근거 링크 글자용) */
export function videoTitles(out: TrendPopularOutput | null): Map<string, string | null> {
  const m = new Map<string, string | null>();
  for (const c of out?.by_category ?? []) {
    for (const v of c.top_videos ?? []) m.set(v.video_id, v.title);
    for (const t of c.items) for (const v of t.videos ?? []) if (v.title) m.set(v.video_id, v.title);
  }
  return m;
}

// ------------------------------------------------------------------- 맨 위 한 줄

export function TrendHeadline({ popular }: { popular: SkillState<TrendPopularOutput> }) {
  if (popular.kind === "loading") return <p className="text-sm text-ink-500" aria-live="polite">지금 인기 받는 중…</p>;
  if (popular.kind === "failed") return <p className="text-sm text-amber-700">지금 인기를 받지 못했어요. {popular.reason}</p>;
  const out = popular.out;
  const top = out.items[0] ?? null;
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h2 className="text-lg font-bold text-ink-900">
        {top ? <>오늘 가장 큰 화제는 <span className="text-brand-700">{top.keyword}</span></> : (out.empty_reason ?? "지금 인기 주제가 없어요.")}
      </h2>
      {out.youtube_status === "known" && out.snapshot_at && <span className="text-xs text-ink-500">YouTube 인기 {when(out.snapshot_at)} 기준</span>}
    </div>
  );
}

// ------------------------------------------------------------------- 카테고리 칸

/** 화면 칸 하나. 예전 응답(by_category 없음)은 주제 분류별로 묶는다 */
type Cell = { category: string; items: PopularTopic[]; shortage_reason: string | null; top_videos?: PopularVideo[]; top_videos_reason?: string | null };
type Card = { v: PopularVideo; topic: PopularTopic | null };
export type SortKey = "views" | "views_per_hour" | "views_per_subscriber";
export const SORTS: { key: SortKey; label: string }[] = [
  { key: "views", label: "조회수" },
  { key: "views_per_hour", label: "시간당 조회" },
  { key: "views_per_subscriber", label: "구독자 대비 배수" },
];

function cellsOf(out: TrendPopularOutput): Cell[] {
  if (out.by_category?.length) return out.by_category.map((c: PopularCategory) => ({ ...c }));
  const groups = new Map<string, PopularTopic[]>();
  for (const t of out.items) groups.set(categoryName(t.topic_category), [...(groups.get(categoryName(t.topic_category)) ?? []), t]);
  return [...groups.entries()].map(([category, items]) => ({ category, items, shortage_reason: null }));
}

/** 칸의 긴 영상: 주제 영상(주제 순서) → 카테고리 상위 영상. 같은 영상은 처음 것만, 서버 is_short가 true인 것은 뺀다 */
function cardsOf(c: Cell): Card[] {
  const seen = new Set<string>();
  const out: Card[] = [];
  for (const t of c.items) for (const v of t.videos ?? []) if (!seen.has(v.video_id)) { seen.add(v.video_id); out.push({ v, topic: t }); }
  for (const v of c.top_videos ?? []) if (!seen.has(v.video_id)) { seen.add(v.video_id); out.push({ v, topic: null }); }
  return out.filter((c) => c.v.is_short !== true); // 숏츠(서버 is_short)는 위 숏츠 그리드에서 본다
}

/** 받은 값으로만 정렬. 값이 없으면 뒤로(서버 순서 유지) */
function sorted(cards: Card[], key: SortKey): Card[] {
  return cards.map((c, i) => ({ c, i })).sort((a, b) => {
    const x = a.c.v[key], y = b.c.v[key];
    if (x == null && y == null) return a.i - b.i;
    if (x == null) return 1;
    if (y == null) return -1;
    return y - x || a.i - b.i;
  }).map((x) => x.c);
}

function VideoCard({ card, rank, promo }: { card: Card; rank: number; promo: PromoConceptsOutput | null }) {
  const { v, topic } = card;
  const server = hasCaution(v);
  const m = !server && topic ? materialOf([topic.keyword, ...topic.aliases], promo, topic) : null;
  const chip = m && m.kind !== "none" ? MATERIAL[m.kind] : null;
  const facts = [
    v.views_per_hour != null ? `시간당 +${num(v.views_per_hour)}` : null,
    v.views_per_subscriber != null ? `구독자 대비 ${v.views_per_subscriber.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}배` : null,
  ].filter(Boolean);
  return (
    <li className="min-w-0">
      <a href={v.video_url || videoUrl(v.video_id)} target="_blank" rel="noreferrer noopener"
        className="card flex h-full gap-3 p-3 transition hover:ring-brand-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
        aria-label={`${rank}위 ${v.title ?? "제목 없음"} · YouTube에서 보기`}>
        <ThumbImage src={v.thumbnail_url} alt={v.title ?? "제목 없음"} className="w-24 shrink-0">
          <span className="absolute left-1.5 top-1.5 rounded-md bg-ink-900 px-1.5 py-0.5 text-[11px] font-bold text-white">{rank}위</span>
        </ThumbImage>
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="line-clamp-2 text-sm font-semibold leading-snug text-ink-900">{v.title ?? "제목 없음"}</span>
          {v.channel_title && <span className="truncate text-xs text-ink-500">{v.channel_title}</span>}
          {v.views != null && <span className="mt-1 text-xl font-bold tabular-nums text-ink-900" title={`조회 ${num(v.views)}회`}>{COMPACT.format(v.views)}</span>}
          {facts.length > 0 && <span className="text-xs text-ink-500">{facts.join(" · ")}</span>}
          {server ? <span className="mt-auto pt-1 empty:hidden"><CautionChip v={v} /></span> : chip && (
            <span className="mt-auto pt-1">
              <span className={`chip text-xs ${chip.chip}`} title={m?.reason}>{chip.label}</span>
            </span>
          )}
        </span>
      </a>
    </li>
  );
}

export function FilterButton({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" aria-pressed={on} onClick={onClick}
      className={`min-h-[40px] rounded-full px-3.5 py-1.5 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${on ? "bg-brand-600 text-white" : "bg-surface text-ink-700 ring-1 ring-ink-100 hover:bg-ink-50"}`}>
      {children}
    </button>
  );
}

/** 카테고리(서버 이름) 칸의 태그 칩: 그 칸 주제 중 YouTube 태그에서 나온 것 (서버 이름 그대로) */
export function tagsOf(out: TrendPopularOutput | null, category: string): string[] {
  const cell = out ? cellsOf(out).find((c) => c.category === category) : undefined;
  return cell ? cell.items.filter((t) => t.origins.includes("youtube_tag")).map((t) => t.keyword) : [];
}

/** 긴 영상 수 (모든 칸, 같은 영상은 칸마다 한 번) */
export function longVideoCount(out: TrendPopularOutput | null): number {
  return out ? cellsOf(out).reduce((n, c) => n + cardsOf(c).length, 0) : 0;
}

/** 맨 아래 접힘 안 "긴 영상" 그리드 (지난 v2 그리드, 칸·정렬 따로) */
export function LongVideos({ out, promo }: { out: TrendPopularOutput; promo: PromoConceptsOutput | null }) {
  const cells = cellsOf(out).filter((c) => cardsOf(c).length > 0);
  const [picked, setPicked] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("views");
  const current = cells.find((c) => c.category === picked) ?? cells[0] ?? null;
  if (!current) return <Empty>{out.empty_reason ?? "긴 영상이 없어요."}</Empty>;
  const cards = sorted(cardsOf(current), sort);
  return (
    <div className="space-y-4">
      <div className="space-y-2.5 rounded-2xl bg-ink-50 p-3">
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="긴 영상 카테고리">
          <span className="w-16 text-xs font-semibold text-ink-500">카테고리</span>
          {cells.map((c) => (
            <FilterButton key={c.category} on={c.category === current.category} onClick={() => setPicked(c.category)}>
              {categoryName(c.category)} <span className="font-normal opacity-75">{cardsOf(c).length}</span>
            </FilterButton>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="긴 영상 정렬">
          <span className="w-16 text-xs font-semibold text-ink-500">정렬</span>
          {SORTS.map((s) => <FilterButton key={s.key} on={sort === s.key} onClick={() => setSort(s.key)}>{s.label}</FilterButton>)}
        </div>
      </div>
      <ol className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label={`${categoryName(current.category)} 긴 영상`}>
        {cards.map((c, i) => <VideoCard key={c.v.video_id} card={c} rank={i + 1} promo={promo} />)}
      </ol>
    </div>
  );
}
