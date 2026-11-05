import { useEffect, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router";
import { Empty, Panel, Waiting } from "@/components/finance/bits";
import type { SkillState } from "@/components/data/useSkill";
import { CautionChip, ThumbImage } from "@/components/data/ShortsCard";
import { Sources, videoUrl, when } from "@/components/data/Sources";
import { FilterButton, categoryName, tagsOf } from "@/components/data/TrendPopular";
import {
  DATA_SERVER, dataAi, kpiLive,
  type ExplorerLength, type ExplorerPeriod, type ExplorerSort, type ExplorerVideo, type TrendPopularOutput, type VideoExplorerOutput,
} from "@/lib/data";

// 트렌드 1단계(감지) 영상 탐색 (video_explorer, 계약 VideoExplorerOutput). 서버가 미리 계산한 결과를 받아 그대로 그린다.
// - 컨트롤 한 줄: [숏 | 롱] · 카테고리 칩(서버 categories 순서, 개수 = 그 칸 count) · 기간 칩(1일·3일·7일) · 정렬(조회순·구독자 대비·좋아요순)
//   길이·기간·정렬은 서버에 그대로 보낸다(정렬은 서버가 한다). 카테고리를 비워 보내 모든 칸을 한 번에 받고, 칩은 받은 칸 중에서 고른다
// - 숏은 세로 썸네일(9:16) 그리드, 롱은 가로 썸네일(16:9) 그리드. 숏·롱은 서버가 나눈다(화면에서 길이를 판단하지 않는다)
// - 카드: 썸네일 · 제목 2줄 · 채널 · 큰 조회수 · 작게 좋아요·구독자 대비·올린 지 얼마 · 칩(서버 ad_usable·caution_types). 누르면 서버 video_url 새 탭
// - 그 칸 영상 수(count)가 서버 min_per_cell보다 적으면 카드 자리에 서버 이유(shortage_reason) 한 줄
// - 고른 조건은 주소에 남긴다: /data/trend#detect?len=short&p=7d&sort=views&cat=<카테고리>. 새로고침해도 유지
// - 많이 붙는 태그: trend_popular 같은 이름 칸의 YouTube 태그 주제 (tagsOf)

const LENGTHS: { key: ExplorerLength; label: string }[] = [{ key: "short", label: "숏" }, { key: "long", label: "롱" }];
const PERIODS: { key: ExplorerPeriod; label: string }[] = [{ key: "1d", label: "1일" }, { key: "3d", label: "3일" }, { key: "7d", label: "7일" }];
const SORTS: { key: ExplorerSort; label: string }[] = [
  { key: "views", label: "조회순" },
  { key: "views_per_subscriber", label: "구독자 대비" },
  { key: "likes", label: "좋아요순" },
];
const NO_REASON = "서버가 이유를 주지 않았어요";
const COMPACT = new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 });
const num = (v: number) => v.toLocaleString("ko-KR", { maximumFractionDigits: 0 });

export type ExplorerQuery = { length: ExplorerLength; period: ExplorerPeriod; sort: ExplorerSort; category: string | null };

const pick = <T extends string>(v: string | null, list: { key: T }[], fallback: T): T => list.find((x) => x.key === v)?.key ?? fallback;

/** 주소 해시(#detect?len=…&p=…&sort=…&cat=…)에서 고른 조건. 없거나 모르는 값이면 기본(숏·7일·조회순·첫 칸) */
export function explorerQueryFromHash(hash: string): ExplorerQuery {
  const q = hash.indexOf("?");
  const params = new URLSearchParams(q >= 0 ? hash.slice(q + 1) : "");
  return {
    length: pick(params.get("len"), LENGTHS, "short"),
    period: pick(params.get("p"), PERIODS, "7d"),
    sort: pick(params.get("sort"), SORTS, "views"),
    category: params.get("cat") || null,
  };
}

function hashOf(q: ExplorerQuery): string {
  const params = new URLSearchParams({ len: q.length, p: q.period, sort: q.sort });
  if (q.category) params.set("cat", q.category);
  return `#detect?${params.toString()}`;
}

/** 올린 지 얼마 (서버 published_at 기준) */
function ago(iso: string | null): string | null {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const min = Math.floor(ms / 60_000);
  if (min < 60) return `${Math.max(min, 1)}분 전`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h}시간 전` : `${Math.floor(h / 24)}일 전`;
}

function VideoCard({ v, rank, wide }: { v: ExplorerVideo; rank: number; wide: boolean }) {
  const title = v.title ?? "제목 없음";
  const facts = [
    typeof v.likes === "number" ? `좋아요 ${COMPACT.format(v.likes)}` : null,
    typeof v.views_per_subscriber === "number" ? `구독자 대비 ${v.views_per_subscriber.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}배` : null,
    ago(v.published_at),
  ].filter(Boolean);
  return (
    <li className="min-w-0">
      <a href={v.video_url || videoUrl(v.video_id)} target="_blank" rel="noreferrer noopener"
        className="card flex h-full flex-col gap-2 p-2.5 transition hover:ring-brand-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
        aria-label={`${rank}위 ${title} · YouTube에서 보기`}>
        <ThumbImage src={v.thumbnail_url} alt={title} wide={wide}>
          <span className="absolute left-1.5 top-1.5 rounded-md bg-ink-900 px-1.5 py-0.5 text-[11px] font-bold text-white">{rank}위</span>
        </ThumbImage>
        <span className="flex min-w-0 flex-1 flex-col gap-1 px-0.5">
          <span className="line-clamp-2 text-sm font-semibold leading-snug text-ink-900">{title}</span>
          {v.channel_title && <span className="truncate text-xs text-ink-500">{v.channel_title}</span>}
          {typeof v.views === "number"
            ? <span className="text-xl font-bold tabular-nums text-ink-900" title={`조회 ${num(v.views)}회`}>{COMPACT.format(v.views)}</span>
            : v.views_reason && <span className="text-xs text-ink-500">{v.views_reason}</span>}
          {facts.length > 0 && <span className="text-xs text-ink-500">{facts.join(" · ")}</span>}
          <span className="mt-auto pt-1 empty:hidden"><CautionChip v={v} /></span>
        </span>
      </a>
    </li>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={label}>
      <span className="w-16 text-xs font-semibold text-ink-500">{label}</span>
      {children}
    </div>
  );
}

export function VideoExplorer({ popular }: { popular: SkillState<TrendPopularOutput> }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [q, setQ] = useState<ExplorerQuery>(() => explorerQueryFromHash(location.hash));
  const [run, setRun] = useState(0);
  const [state, setState] = useState<SkillState<VideoExplorerOutput>>({ kind: "loading" });
  const { length, period, sort } = q;
  useEffect(() => {
    if (!kpiLive) return;
    let live = true;
    setState({ kind: "loading" });
    dataAi.videoExplorer({ period, length, sort })
      .then((r) => live && setState({ kind: "ok", out: r.output, evidence: r.evidence ?? null }))
      .catch((e: Error) => live && setState({ kind: "failed", reason: e.message }));
    return () => { live = false; };
  }, [period, length, sort, run]);

  /** 조건을 바꾸고 주소에 남긴다 (새로고침해도 유지) */
  const change = (patch: Partial<ExplorerQuery>) => {
    const next = { ...q, ...patch };
    setQ(next);
    navigate({ pathname: location.pathname, search: location.search, hash: hashOf(next) }, { replace: true });
  };

  const out = state.kind === "ok" ? state.out : null;
  const popularOut = popular.kind === "ok" ? popular.out : null;
  const order = out?.categories.length ? out.categories : (out?.cells ?? []).map((c) => c.category);
  const cells = order.map((name) => out?.cells.find((c) => c.category === name)).filter((c) => c !== undefined);
  const current = cells.find((c) => c.category === q.category) ?? cells[0] ?? null;
  const wide = length === "long";
  const tags = current ? tagsOf(popularOut, current.category) : [];
  const short = current && out ? current.count < out.min_per_cell : false;

  return (
    <Panel id="data-trend-popular" title="영상 탐색" titleHidden
      tools={<button type="button" className="btn-ghost" disabled={state.kind === "loading"} onClick={() => setRun((n) => n + 1)}>다시 보기</button>}>
      <div className="space-y-4">
        <div className="space-y-2.5 rounded-2xl bg-ink-50 p-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2.5">
            <div className="inline-flex rounded-full bg-surface p-1 ring-1 ring-ink-100" role="group" aria-label="영상 길이">
              {LENGTHS.map((l) => (
                <button key={l.key} type="button" aria-pressed={length === l.key} onClick={() => change({ length: l.key })}
                  className={`min-h-[36px] rounded-full px-4 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${length === l.key ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-50"}`}>
                  {l.label}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="기간">
              {PERIODS.map((p) => <FilterButton key={p.key} on={period === p.key} onClick={() => change({ period: p.key })}>{p.label}</FilterButton>)}
            </div>
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="정렬">
              {SORTS.map((s) => <FilterButton key={s.key} on={sort === s.key} onClick={() => change({ sort: s.key })}>{s.label}</FilterButton>)}
            </div>
          </div>
          {cells.length > 0 && (
            <Row label="카테고리">
              {cells.map((c) => (
                <FilterButton key={c.category} on={c.category === current?.category} onClick={() => change({ category: c.category })}>
                  {categoryName(c.category)} <span className="font-normal opacity-75">{num(c.count)}</span>
                </FilterButton>
              ))}
            </Row>
          )}
        </div>

        {state.kind === "loading" ? <Waiting what="영상을 받는 중…" />
          : state.kind === "failed" ? <Empty>영상을 받지 못했어요. {state.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
          : out?.status === "unknown" ? <Empty>{out.reason ?? NO_REASON}</Empty>
          : !current ? <Empty>서버가 카테고리 칸(cells)을 주지 않았어요.</Empty> : (
            <>
              {tags.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-semibold text-ink-500">많이 붙는 태그</span>
                  {tags.map((t) => <span key={t} className="chip bg-surface text-sm text-ink-700 ring-1 ring-ink-100">#{t}</span>)}
                </div>
              )}
              <ol className={`grid gap-3 ${wide ? "sm:grid-cols-2 xl:grid-cols-3" : "grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5"}`}
                aria-label={`${categoryName(current.category)} ${wide ? "롱" : "숏"} 영상`}>
                {current.videos.map((v, i) => <VideoCard key={v.video_id} v={v} rank={i + 1} wide={wide} />)}
                {short && (
                  <li className="col-span-full rounded-xl bg-ink-50 px-3 py-2.5 text-sm text-ink-700">
                    {current.shortage_reason ?? NO_REASON}
                  </li>
                )}
              </ol>
              {out?.computed_at && <p className="text-xs text-ink-500">서버가 {when(out.computed_at)}에 미리 모은 영상이에요.</p>}
              <Sources evidence={state.kind === "ok" ? state.evidence : null} />
            </>
          )}
      </div>
    </Panel>
  );
}
