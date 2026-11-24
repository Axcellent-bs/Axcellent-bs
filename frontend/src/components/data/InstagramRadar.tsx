import { useState } from "react";
import { ErrorBox, Panel, Waiting } from "@/components/finance/bits";
import { when } from "@/components/data/Sources";
import { useSkill } from "@/components/data/useSkill";
import { AD, times } from "@/components/data/TrendMemes";
import { safeLink } from "@/lib/safeLink";
import { ageLabels, genderLabels } from "@/lib/radarMeta";
import { Thumb } from "@/components/data/VideoExplorer";
import { IgWhyFacts } from "@/components/data/IgWhyFacts";
import { dataAi, type InstagramRadarGroup, type InstagramRadarOutput, type InstagramRadarPost } from "@/lib/data";
import { type GridFilter, type GridItem, type GridSort, engagementLabel, gridView, kindLabel, showChips, showGrid, thumbSrc } from "@/lib/igRadarGrid";

// 트렌드 1 감지 "인스타" 칩의 인스타 레이더 (instagram_radar, Business Discovery). 서버가 미리 계산한 결과를 읽기만 한다.
// - 맨 위: "갑자기 뜬 게시물" 썸네일 그리드 (모든 묶음 대표 게시물, media_id 중복 제거, 배수 순 최대 12개).
//   썸네일은 서버가 걸러 준 인스타 CDN https 주소만(약 4일 뒤 만료), 없거나 깨지면 회색 칸. 카드 전체가 게시물 링크(safeLink 통과만).
//   카드 아래 초록 한 줄 = 그 게시물이 계정 평소와 가장 다른 점(standout, 서버 코드 템플릿)
// - 아래: "왜 떴나" 묶음 접힘(첫 묶음만 펼침). 펼치면 AI 한 줄 결론(why_line, 숫자 없음) → 근거 숫자 표(why_facts, 코드 계산)
//   → 공통점·광고 재료 이유 → 대표 게시물(계정 · 지표 · 배수 · 링크 · 캡션 일부, 외부 글이라 텍스트로만). 결론이 없으면 이유를 회색으로
// - 숫자·배수·광고 재료 판정은 서버 값. 모르면 0이 아니라 서버 이유를 보여 준다. 첫 바퀴 진행 중(판정 전)이면 그리드는 숨긴다

export const METRIC: Record<InstagramRadarPost["metric"], string> = { view_count: "조회", like_count: "좋아요" };
const n = (v: number) => v.toLocaleString("ko-KR");
export const COMPACT = new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 });

/** 올린 지 얼마 (보고서 작은 게시물 카드도 쓴다) */
export function ago(iso: string | null): string | null {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const min = Math.floor(ms / 60_000);
  if (min < 60) return `${Math.max(min, 1)}분 전`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h}시간 전` : `${Math.floor(h / 24)}일 전`;
}

/** "갑자기 뜬 게시물" 카드 하나. 트렌드 분석 보고서(TrendReportView) 콘텐츠 섹션도 같이 쓴다.
 *  adMaterial이 없으면(묶음 판정이 없는 게시물) 광고 재료 칩을 그리지 않는다 */
export function PostCard({ post: p, rank, adMaterial, domId }: { post: InstagramRadarPost; rank: number; adMaterial?: InstagramRadarGroup["ad_material"]; domId?: string }) {
  const link = safeLink(p.permalink);
  const kind = kindLabel(p);
  const ad = adMaterial ? AD[adMaterial] : null;
  const engagement = engagementLabel(p);
  const layers = [...ageLabels(p.audience_ages), ...genderLabels(p.audience_genders)];
  const body = (
    <>
      <Thumb src={thumbSrc(p.thumbnail_url)} alt={p.caption_excerpt ?? `${p.account} 게시물`} wide={false} rank={rank} tall>
        <span className="absolute bottom-1.5 right-1.5 rounded bg-ink-900/80 px-1 py-0.5 text-[11px] font-bold leading-none text-white tabular-nums">×{p.ratio.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}</span>
        {kind && <span className="absolute bottom-1.5 left-1.5 rounded bg-ink-900/80 px-1 py-0.5 text-[11px] leading-none text-white">{kind}</span>}
      </Thumb>
      <span className="mt-1 truncate text-[13px] font-semibold text-ink-900">{p.account}</span>
      <span className="truncate text-xs text-ink-700">
        {METRIC[p.metric]} {COMPACT.format(p.value)}{ago(p.posted_at) && <span className="text-ink-500"> · {ago(p.posted_at)}</span>}
      </span>
      {engagement && <span className="text-xs text-ink-700"><span className="chip mr-1 text-[11px]">밈</span>{engagement}</span>}
      {p.standout && <span className="text-xs font-medium text-emerald-700">{p.standout}</span>}
      {layers.length > 0 && <span className="flex flex-wrap gap-1">{layers.map((c) => <span key={c} className="chip text-[11px]">{c}</span>)}</span>}
      {ad &&<span><span className={`chip text-[11px] ${ad.tone}`}>광고 재료 {ad.label}</span></span>}
    </>
  );
  const cls = "flex flex-col gap-1 rounded-lg";
  return (
    <li id={domId} className="min-w-0">
      {link ? (
        <a href={link} target="_blank" rel="noreferrer noopener"
          className={`${cls} transition hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500`}
          aria-label={`${rank}위 ${p.account} 게시물 · 평소의 ${times(p.ratio)} · 인스타그램에서 보기`}>{body}</a>
      ) : <div className={cls}>{body}</div>}
    </li>
  );
}

const FILTERS: { key: GridFilter; label: string }[] = [
  { key: "all", label: "전체" },
  { key: "benchmark", label: "벤치마크" },
  { key: "meme", label: "밈·크리에이터" },
];
const SORTS: { key: GridSort; label: string }[] = [
  { key: "ratio", label: "평소 대비 배수" },
  { key: "engagement", label: "팔로워 대비 반응" },
];
const btn = (on: boolean) =>
  `rounded-md px-2.5 py-1 text-xs ring-1 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${on ? "bg-brand-50 font-bold text-brand-700 ring-brand-500" : "text-ink-500 ring-ink-100 hover:bg-ink-50"}`;

function PostGrid({ out }: { out: InstagramRadarOutput }) {
  const [filter, setFilter] = useState<GridFilter>("all");
  const [sort, setSort] = useState<GridSort>("ratio");
  if (!showGrid(out)) return null;
  const chips = showChips(out);
  const f = chips ? filter : "all";
  const items = gridView(out.groups, out.meme_posts, f, sort);
  const order = f === "meme" && sort === "engagement" ? "팔로워 대비 반응 순" : "평소 대비 배수 순";
  return (
    <section aria-label="갑자기 뜬 게시물">
      <h3 className="mb-1.5 text-sm font-semibold text-ink-900">갑자기 뜬 게시물 <small className="font-normal text-ink-500">{order} · {n(items.length)}개</small></h3>
      {chips && (
        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <div role="group" aria-label="계정 종류" className="flex gap-1">
            {FILTERS.map((x) => <button key={x.key} type="button" aria-pressed={f === x.key} onClick={() => setFilter(x.key)} className={btn(f === x.key)}>{x.label}</button>)}
          </div>
          {f === "meme" && (
            <div role="group" aria-label="정렬" className="flex gap-1">
              {SORTS.map((x) => <button key={x.key} type="button" aria-pressed={sort === x.key} onClick={() => setSort(x.key)} className={btn(sort === x.key)}>{x.label}</button>)}
            </div>
          )}
        </div>
      )}
      {items.length === 0 ? <p className="text-xs text-ink-500">이 종류에서는 갑자기 뜬 게시물이 없어요.</p> : (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {items.map((item: GridItem, i) => <PostCard key={item.post.media_id} post={item.post} adMaterial={item.group?.ad_material} rank={i + 1} />)}
        </ul>
      )}
    </section>
  );
}

function Posts({ list }: { list: InstagramRadarPost[] }) {
  if (list.length === 0) return <p className="text-xs text-ink-500">대표 게시물이 없어요.</p>;
  return (
    <ul className="mt-1 space-y-1.5">
      {list.map((p) => {
        const link = safeLink(p.permalink);
        return (
          <li key={p.media_id} className="text-xs text-ink-700">
            <span className="font-semibold text-ink-900">{p.account}</span>
            {" · "}{METRIC[p.metric]} {n(p.value)}
            {" · "}평소 {times(p.ratio)}
            {p.posted_at && <span className="text-ink-500"> · {when(p.posted_at)}</span>}
            {link && <> · <a href={link} target="_blank" rel="noreferrer noopener" className="text-brand-700 underline-offset-2 hover:underline">게시물 열기</a></>}
            {p.caption_excerpt && <span className="block whitespace-pre-line break-words text-ink-500">{p.caption_excerpt}</span>}
          </li>
        );
      })}
    </ul>
  );
}

/** 묶음 하나 = 접힘. 펼치면 AI 한 줄 결론(파란 띠) → 근거 숫자 표 → 대표 게시물 */
function GroupFold({ g, open }: { g: InstagramRadarGroup; open: boolean }) {
  const ad = AD[g.ad_material];
  return (
    <details open={open} className="group rounded-lg border border-ink-100">
      <summary className="flex cursor-pointer select-none flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-2">
        <b className="text-ink-900">{g.name}</b>
        <span className="text-xs tabular-nums text-ink-700">{n(g.accounts)}곳 · 최대 평소의 <b className="text-ink-900">{times(g.best_ratio)}</b></span>
        <span className={`chip text-xs ${ad.tone}`}>광고 재료 {ad.label}</span>
        <small className="w-full text-xs text-ink-500">{g.description ?? g.description_reason ?? `게시물 ${n(g.posts)}개`}</small>
      </summary>
      <div className="space-y-2 border-t border-ink-100 px-3 py-2">
        {g.why_line ? (
          <div className="rounded-md bg-brand-50 px-3 py-2">
            <p className="text-sm font-semibold text-brand-700">{g.why_line}</p>
            <small className="block text-[11px] text-brand-700/80">AI 한 줄 결론 · 아래 근거 숫자만 사용</small>
          </div>
        ) : (
          <p className="text-xs text-ink-500">AI 한 줄 결론 없음 · {g.why_line_reason ?? "이유를 받지 못했어요"}</p>
        )}
        <IgWhyFacts facts={g.why_facts ?? []} />
        {g.members.length > 0 && <p className="text-xs text-ink-500">공통점 · {g.members.join(" · ")}</p>}
        {g.ad_material_reason && <p className="text-[11px] text-ink-500">광고 재료 · {g.ad_material_reason}</p>}
        <div className="text-xs">
          <p className="text-ink-500">대표 게시물 {n(g.top_posts.length)}개</p>
          <Posts list={g.top_posts} />
        </div>
      </div>
    </details>
  );
}

function RadarBody({ out }: { out: InstagramRadarOutput }) {
  return (
    <div className="space-y-2">
      {out.status === "unknown" && <p className="text-sm text-ink-700">{out.reason ?? "레이더 결과를 모르는 상태예요."}</p>}
      <PostGrid out={out} />
      {out.groups.length > 0 ? (
        <section aria-label="레이더 묶음" className="space-y-2">
          <h3 className="text-sm font-semibold text-ink-900">왜 떴나 <small className="font-normal text-ink-500">여러 계정이 같이 터진 묶음 · 평소와 다른 점</small></h3>
          {out.groups.map((g, i) => <GroupFold key={g.name} g={g} open={i === 0} />)}
        </section>
      ) : out.status === "known" ? (
        <p className="text-sm text-ink-700">평소보다 크게 터진 게시물이 없어요.</p>
      ) : null}
      {out.accounts_failed.length > 0 && <p className="text-xs text-ink-500">못 읽은 계정 · {out.accounts_failed.join(", ")}</p>}
      {out.criteria && <p className="text-xs text-ink-500">{out.criteria}</p>}
      {out.limits.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-500">
          {out.limits.map((l, i) => <li key={`${i}-${l}`}>{l}</li>)}
        </ul>
      )}
    </div>
  );
}

export function InstagramRadar() {
  const res = useSkill(() => dataAi.instagramRadar());
  const s = res.state;
  const out = s.kind === "ok" ? s.out : null;
  // 계정 수는 서버가 셌을 때만 (모르면 0이라고 쓰지 않는다)
  const counted = out && (out.status === "known" || out.accounts_total > 0);
  const hint = counted
    ? `${(out.meme_accounts_total ?? 0) > 0 ? `계정 ${n(out.accounts_total)}곳(벤치마크 ${n(out.accounts_total - (out.meme_accounts_total ?? 0))}곳 + 밈·크리에이터 ${n(out.meme_accounts_total ?? 0)}곳)` : `벤치마크 계정 ${n(out.accounts_total)}곳`} 중 평소보다 크게 터진 게시물 묶음 · 오늘 읽은 계정 ${n(out.accounts_read)}곳${out.generated_at ? ` · 계산 ${when(out.generated_at)}` : ""}`
    : "벤치마크 계정 중 평소보다 크게 터진 게시물 묶음";
  return (
    <Panel id="data-trend-ig-radar" title="인스타 레이더" hint={hint}
      tools={<span className="text-xs text-ink-500">Business Discovery</span>}>
      {s.kind === "loading" ? <Waiting what="레이더를 받는 중…" />
        : s.kind === "failed" ? <ErrorBox message={s.reason} onRetry={res.reload} />
        : <RadarBody out={s.out} />}
    </Panel>
  );
}
