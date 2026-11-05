import { useState } from "react";
import { Empty, Panel, Waiting } from "@/components/finance/bits";
import { okEvidence, type SkillState } from "@/components/data/useSkill";
import { CautionChip, ThumbImage } from "@/components/data/ShortsCard";
import { Sources, videoUrl } from "@/components/data/Sources";
import { FilterButton, LongVideos, SORTS, categoryName, longVideoCount, tagsOf, type SortKey } from "@/components/data/TrendPopular";
import { DATA_SERVER, type PromoConceptsOutput, type ShortsStatsOutput, type ShortsVideo, type TrendPopularOutput } from "@/lib/data";

// 트렌드 1단계(감지) 숏츠 그리드 (v2 시안 ① 보드). shorts_stats(계약 ShortsStatsOutput) by_category[].videos 그대로.
// 숏츠인지는 서버가 정한다(길이 기준 max_seconds). 화면에서 길이를 따로 판단하지 않는다
// - 카테고리 칩: 서버 by_category 순서, 화면 카드가 있는 칸만, 개수 = 그 칸 카드 수
// - 정렬: 조회수 / 시간당 조회 / 구독자 대비 배수. 받은 값으로만, 값이 없는 숏츠는 뒤로(서버 순서 유지)
// - 많이 붙는 태그: trend_popular 같은 이름 칸의 YouTube 태그 주제 (tagsOf)
// - 카드: 썸네일(서버 thumbnail_url, 세로·cover, alt = 제목, 없거나 깨지면 회색 자리) · 순위 · 제목 2줄 · 채널 · 큰 조회수
//   · 시간당·구독자 대비 한 줄 · 칩 하나(서버 ad_usable·caution_types). 누르면 서버 video_url 새 탭
// - 맨 아래 접힘 "긴 영상 N개": trend_popular 영상 중 서버 is_short가 true가 아닌 것 (LongVideos)

const num = (v: number) => v.toLocaleString("ko-KR", { maximumFractionDigits: 0 });
const COMPACT = new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 });
const NO_REASON = "서버가 이유를 주지 않았어요";

/** 받은 값으로만 정렬. 값이 없으면 뒤로(서버 순서 유지) */
function sorted(list: ShortsVideo[], key: SortKey): ShortsVideo[] {
  return list.map((v, i) => ({ v, i })).sort((a, b) => {
    const x = a.v[key] ?? null, y = b.v[key] ?? null;
    if (x === null && y === null) return a.i - b.i;
    if (x === null) return 1;
    if (y === null) return -1;
    return y - x || a.i - b.i;
  }).map((x) => x.v);
}

function ShortCard({ v, rank }: { v: ShortsVideo; rank: number }) {
  const facts = [
    typeof v.views_per_hour === "number" ? `시간당 +${num(v.views_per_hour)}` : null,
    typeof v.views_per_subscriber === "number" ? `구독자 대비 ${v.views_per_subscriber.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}배` : null,
  ].filter(Boolean);
  const title = v.title ?? "제목 없음";
  return (
    <li className="min-w-0">
      <a href={v.video_url || videoUrl(v.video_id)} target="_blank" rel="noreferrer noopener"
        className="card flex h-full gap-3 p-3 transition hover:ring-brand-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
        aria-label={`${rank}위 ${title} · YouTube에서 보기`}>
        <ThumbImage src={v.thumbnail_url} alt={title} className="w-24 shrink-0">
          <span className="absolute left-1.5 top-1.5 rounded-md bg-ink-900 px-1.5 py-0.5 text-[11px] font-bold text-white">{rank}위</span>
        </ThumbImage>
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="line-clamp-2 text-sm font-semibold leading-snug text-ink-900">{title}</span>
          {v.channel_title && <span className="truncate text-xs text-ink-500">{v.channel_title}</span>}
          {v.views != null && <span className="mt-1 text-xl font-bold tabular-nums text-ink-900" title={`조회 ${num(v.views)}회`}>{COMPACT.format(v.views)}</span>}
          {facts.length > 0 && <span className="text-xs text-ink-500">{facts.join(" · ")}</span>}
          <span className="mt-auto pt-1 empty:hidden"><CautionChip v={v} /></span>
        </span>
      </a>
    </li>
  );
}

export function ShortsGrid({ shorts, popular, promo, onReload }: {
  shorts: SkillState<ShortsStatsOutput>;
  popular: SkillState<TrendPopularOutput>;
  promo: PromoConceptsOutput | null;
  onReload: () => void;
}) {
  const out = shorts.kind === "ok" ? shorts.out : null;
  const popularOut = popular.kind === "ok" ? popular.out : null;
  const groups = (out?.by_category ?? []).filter((g) => (g.videos?.length ?? 0) > 0);
  const [picked, setPicked] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("views");
  const current = groups.find((g) => g.name === picked) ?? groups[0] ?? null;
  const cards = current ? sorted(current.videos ?? [], sort) : [];
  const tags = current ? tagsOf(popularOut, current.name) : [];
  const longs = longVideoCount(popularOut);
  return (
    <Panel id="data-trend-popular" title="지금 뜨는 숏츠" titleHidden
      tools={<button type="button" className="btn-ghost" disabled={shorts.kind === "loading"} onClick={onReload}>다시 보기</button>}>
      <div className="space-y-4">
        {shorts.kind === "loading" ? <Waiting what="숏츠를 받는 중…" />
          : shorts.kind === "failed" ? <Empty>숏츠를 받지 못했어요. {shorts.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
          : !current ? <Empty>{out?.status === "unknown" ? (out.reason ?? NO_REASON) : "화면에 보여 줄 숏츠가 없어요. 서버가 카테고리별 숏츠 카드(videos)를 주지 않았어요."}</Empty> : (
            <>
              <div className="space-y-2.5 rounded-2xl bg-ink-50 p-3">
                <div className="flex flex-wrap items-center gap-2" role="group" aria-label="카테고리">
                  <span className="w-16 text-xs font-semibold text-ink-500">카테고리</span>
                  {groups.map((g) => (
                    <FilterButton key={g.name} on={g.name === current.name} onClick={() => setPicked(g.name)}>
                      {categoryName(g.name)} <span className="font-normal opacity-75">{g.videos?.length ?? 0}</span>
                    </FilterButton>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-2" role="group" aria-label="정렬">
                  <span className="w-16 text-xs font-semibold text-ink-500">정렬</span>
                  {SORTS.map((s) => <FilterButton key={s.key} on={sort === s.key} onClick={() => setSort(s.key)}>{s.label}</FilterButton>)}
                </div>
              </div>
              {tags.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-semibold text-ink-500">많이 붙는 태그</span>
                  {tags.map((t) => <span key={t} className="chip bg-surface text-sm text-ink-700 ring-1 ring-ink-100">#{t}</span>)}
                </div>
              )}
              <ol className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label={`${categoryName(current.name)} 숏츠`}>
                {cards.map((v, i) => <ShortCard key={v.video_id} v={v} rank={i + 1} />)}
              </ol>
              <Sources evidence={okEvidence(shorts)} />
            </>
          )}
        {popularOut && longs > 0 && (
          <details className="card p-4 sm:p-5">
            <summary className="cursor-pointer text-sm font-semibold text-ink-700">긴 영상 {longs}개 <span className="font-normal text-ink-500">· 지금 인기 영상 중 서버가 숏츠로 표시하지 않은 것</span></summary>
            <div className="mt-3"><LongVideos out={popularOut} promo={promo} /></div>
          </details>
        )}
      </div>
    </Panel>
  );
}
