import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { Empty, Waiting } from "@/components/finance/bits";
import type { SkillState } from "@/components/data/useSkill";
import { CautionChip } from "@/components/data/ShortsCard";
import { Sources, videoUrl, when } from "@/components/data/Sources";
import { categoryName } from "@/components/data/TrendPopular";
import {
  DATA_SERVER, dataAi, kpiLive,
  type ExplorerLength, type ExplorerPeriod, type ExplorerSort, type ExplorerVideo, type VideoExplorerOutput,
} from "@/lib/data";

// 트렌드 1단계(감지) 영상 탐색 (video_explorer, 계약 VideoExplorerOutput). 서버가 미리 계산한 결과를 받아 그대로 그린다 (시안 detect-v3).
// - 컨트롤 한 줄: 왼쪽 기간(1일·3일·7일) | 정렬(조회순·구독자 대비·좋아요순), 오른쪽 끝 "영상 [숏폼 | 롱폼]"
//   길이·기간·정렬은 서버에 그대로 보낸다(정렬은 서버가 한다). 카테고리를 비워 보내 모든 칸을 한 번에 받는다
// - 카테고리 칩 한 줄: 서버 categories 순서, 개수 = 그 칸 count. 서버가 "7일 이전"(before_period) 영상을 섞어 주면 "(7일 이전 포함)"
// - 숏폼 카드: 너비 228px 고정, 세로 썸네일(9:16, 최대 높이 284px), 한 줄 5개를 좌우 끝까지(space-between), 줄 간격 18px
//   롱폼 카드: 가로 썸네일(16:9), 한 줄 5개
// - 카드: 제목 2줄 · 채널·올린 날 · 조회수 크게 · 좋아요·구독자 대비 한 줄 · 칩 하나(서버 ad_usable·caution_types).
//   값이 없는 칸은 글자 자체를 숨긴다. "7일 이전" 영상은 작은 회색 표시. 누르면 서버 video_url 새 탭
// - 그 칸 영상 수(count)가 서버 min_per_cell보다 적으면 그리드 아래 서버 이유(shortage_reason) 한 줄
// - 고른 조건은 주소에 남긴다: /data/trend#detect?len=short&p=7d&sort=views&cat=<카테고리>

const LENGTHS: { key: ExplorerLength; label: string }[] = [{ key: "short", label: "숏폼" }, { key: "long", label: "롱폼" }];
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

/** 주소 해시(#detect?len=…&p=…&sort=…&cat=…)에서 고른 조건. 없거나 모르는 값이면 기본(숏폼·7일·조회순·첫 칸) */
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

function Thumb({ src, alt, wide, rank }: { src: string | null; alt: string; wide: boolean; rank: number }) {
  const [broken, setBroken] = useState(false);
  const ok = !!src && /^https:\/\//.test(src) && !broken;
  return (
    <span className={`relative block w-full overflow-hidden rounded-lg bg-ink-200 ${wide ? "aspect-video" : "h-[284px]"}`}>
      {ok && <img src={src} alt={alt} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setBroken(true)}
        className="absolute inset-0 h-full w-full object-cover" />}
      <span className="absolute left-1.5 top-1.5 min-w-[20px] rounded bg-ink-900 px-1 py-0.5 text-center text-[11px] font-bold leading-none text-white">{rank}</span>
    </span>
  );
}

function VideoCard({ v, rank, wide }: { v: ExplorerVideo; rank: number; wide: boolean }) {
  const title = v.title ?? "";
  const meta = [v.channel_title, ago(v.published_at)].filter(Boolean).join(" · ");
  const facts = [
    typeof v.likes === "number" ? `좋아요 ${COMPACT.format(v.likes)}` : null,
    typeof v.views_per_subscriber === "number" ? `구독자 대비 ${v.views_per_subscriber.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}배` : null,
  ].filter(Boolean).join(" · ");
  return (
    <li className="min-w-0">
      <a href={v.video_url || videoUrl(v.video_id)} target="_blank" rel="noreferrer noopener"
        className="flex flex-col gap-1 rounded-lg transition hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500"
        aria-label={`${rank}위 ${title || v.video_id} · YouTube에서 보기`}>
        <Thumb src={v.thumbnail_url} alt={title} wide={wide} rank={rank} />
        {title && <span className="mt-1 line-clamp-2 text-[13px] font-semibold leading-snug text-ink-900">{title}</span>}
        {(meta || v.before_period) && (
          <span className="truncate text-xs text-ink-500">
            {meta}{v.before_period && <span className="ml-1 text-[11px] text-ink-400">· 7일 이전</span>}
          </span>
        )}
        {typeof v.views === "number" && <span className="text-lg font-bold tabular-nums text-ink-900" title={`조회 ${num(v.views)}회`}>{COMPACT.format(v.views)}</span>}
        {facts && <span className="text-xs text-ink-700">{facts}</span>}
        <span className="empty:hidden"><CautionChip v={v} className="text-[11px]" /></span>
      </a>
    </li>
  );
}

const pill = (on: boolean, tone: "brand" | "ink" = "brand") =>
  `min-h-[38px] rounded-lg px-3.5 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${
    on ? (tone === "brand" ? "bg-brand-600 text-white" : "bg-ink-900 text-white") : "bg-surface text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50"}`;

export function VideoExplorer() {
  const location = useLocation();
  const navigate = useNavigate();
  const [q, setQ] = useState<ExplorerQuery>(() => explorerQueryFromHash(location.hash));
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
  }, [period, length, sort]);

  /** 조건을 바꾸고 주소에 남긴다 (새로고침해도 유지) */
  const change = (patch: Partial<ExplorerQuery>) => {
    const next = { ...q, ...patch };
    setQ(next);
    navigate({ pathname: location.pathname, search: location.search, hash: hashOf(next) }, { replace: true });
  };

  const out = state.kind === "ok" ? state.out : null;
  const order = out?.categories.length ? out.categories : (out?.cells ?? []).map((c) => c.category);
  const cells = order.map((name) => out?.cells.find((c) => c.category === name)).filter((c) => c !== undefined);
  const current = cells.find((c) => c.category === q.category) ?? cells[0] ?? null;
  const wide = length === "long";
  const short = current && out ? current.count < out.min_per_cell : false;

  return (
    <section id="data-trend-popular" aria-label="영상 탐색" className="space-y-3">
      {/* 컨트롤 한 줄: 기간 | 정렬 ……… 영상 [숏폼 | 롱폼] */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2" role="group" aria-label="기간">
          {PERIODS.map((p) => <button key={p.key} type="button" aria-pressed={period === p.key} onClick={() => change({ period: p.key })} className={pill(period === p.key)}>{p.label}</button>)}
        </div>
        <span aria-hidden className="mx-1.5 h-6 w-px bg-ink-200" />
        <div className="flex items-center gap-2" role="group" aria-label="정렬">
          {SORTS.map((s) => <button key={s.key} type="button" aria-pressed={sort === s.key} onClick={() => change({ sort: s.key })} className={pill(sort === s.key, "ink")}>{s.label}</button>)}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-sm font-semibold text-ink-700">영상</span>
          <div className="inline-flex overflow-hidden rounded-lg ring-1 ring-ink-200" role="group" aria-label="영상 길이">
            {LENGTHS.map((l) => (
              <button key={l.key} type="button" aria-pressed={length === l.key} onClick={() => change({ length: l.key })}
                className={`min-h-[38px] px-6 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${length === l.key ? "bg-brand-600 text-white" : "bg-surface text-ink-700 hover:bg-ink-50"}`}>
                {l.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 카테고리 칩 한 줄 (개수 = 서버 count) */}
      {cells.length > 0 && (
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="카테고리">
          {cells.map((c) => {
            const on = c.category === current?.category;
            const old = c.videos.some((v) => v.before_period);
            return (
              <button key={c.category} type="button" aria-pressed={on} onClick={() => change({ category: c.category })}
                className={`min-h-[34px] rounded-full px-3.5 text-sm transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${on ? "bg-brand-50 font-semibold text-brand-700 ring-1 ring-brand-200" : "bg-surface text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50"}`}>
                {categoryName(c.category)} {num(c.count)}{old && <span className="ml-1 text-ink-500">(7일 이전 포함)</span>}
              </button>
            );
          })}
        </div>
      )}

      {state.kind === "loading" ? <Waiting what="영상을 받는 중…" />
        : state.kind === "failed" ? <Empty>영상을 받지 못했어요. {state.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
        : out?.status === "unknown" ? <Empty>{out.reason ?? NO_REASON}</Empty>
        : !current ? <Empty>서버가 카테고리 칸(cells)을 주지 않았어요.</Empty> : (
          <>
            {current.videos.length > 0 && (
              <ol className="grid" aria-label={`${categoryName(current.category)} ${wide ? "롱폼" : "숏폼"} 영상`}
                style={wide
                  ? { gridTemplateColumns: "repeat(5, minmax(0, 1fr))", columnGap: 24, rowGap: 18 }
                  : { gridTemplateColumns: "repeat(5, 228px)", justifyContent: "space-between", rowGap: 18 }}>
                {current.videos.map((v, i) => <VideoCard key={v.video_id} v={v} rank={i + 1} wide={wide} />)}
              </ol>
            )}
            {short && <p className="rounded-lg bg-ink-50 px-3 py-2 text-sm text-ink-700">{current.shortage_reason ?? NO_REASON}</p>}
            <div className="space-y-1 text-xs text-ink-500">
              {out?.computed_at && <p>서버가 {when(out.computed_at)}에 미리 모은 영상이에요.</p>}
              <Sources evidence={state.kind === "ok" ? state.evidence : null} />
            </div>
          </>
        )}
    </section>
  );
}
