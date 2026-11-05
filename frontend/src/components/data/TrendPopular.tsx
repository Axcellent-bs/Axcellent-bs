import type { ReactNode } from "react";
import type { SkillState } from "@/components/data/useSkill";
import { type PopularCategory, type PopularTopic, type PromoConceptsOutput, type TrendPopularOutput } from "@/lib/data";

// 트렌드 1단계(감지) 보조: 맨 위 한 줄(TrendHeadline)과 카테고리 태그 칩(tagsOf). trend_popular(계약 TrendPopularOutput) 응답 그대로.
// 영상 그리드는 VideoExplorer.tsx(video_explorer)
// - 맨 위 한 줄: 오늘 가장 큰 화제 = 지금 인기 1위(items[0].keyword) + YouTube 인기 스냅샷 시각
// - 태그 칩(tagsOf): 그 카테고리 주제 중 YouTube 태그에서 나온 것(origins에 youtube_tag)만, 서버 이름 그대로
// - 재료 판정(materialOf): 기획안 재료에서 뺀 주제·추천 안 함 분류 = 광고에 안 씀, 실존 이름·상표 = 주의, 재료로 쓴 주제 = 가능

const num = (v: number | null | undefined) =>
  typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("ko-KR", { maximumFractionDigits: 0 }) : null;
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
type Cell = { category: string; items: PopularTopic[] };

function cellsOf(out: TrendPopularOutput): Cell[] {
  if (out.by_category?.length) return out.by_category.map((c: PopularCategory) => ({ category: c.category, items: c.items }));
  const groups = new Map<string, PopularTopic[]>();
  for (const t of out.items) groups.set(categoryName(t.topic_category), [...(groups.get(categoryName(t.topic_category)) ?? []), t]);
  return [...groups.entries()].map(([category, items]) => ({ category, items }));
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
