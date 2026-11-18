import { ErrorBox, Panel, Waiting } from "@/components/finance/bits";
import { when } from "@/components/data/Sources";
import { useSkill } from "@/components/data/useSkill";
import { AD, times } from "@/components/data/TrendMemes";
import { safeLink } from "@/components/data/InstagramAudio";
import { Thumb } from "@/components/data/VideoExplorer";
import { dataAi, type InstagramRadarGroup, type InstagramRadarOutput, type InstagramRadarPost } from "@/lib/data";
import { type GridItem, gridItems, kindLabel, showGrid, thumbSrc } from "@/lib/igRadarGrid";

// 트렌드 1 감지 "인스타" 칩의 인스타 레이더 (instagram_radar, Business Discovery). 서버가 미리 계산한 결과를 읽기만 한다.
// - 위: "갑자기 뜬 게시물" 썸네일 그리드 (모든 묶음 대표 게시물, media_id 중복 제거, 배수 순 최대 12개).
//   썸네일은 서버가 걸러 준 인스타 CDN https 주소만(약 4일 뒤 만료), 없거나 깨지면 회색 칸. 카드 전체가 게시물 링크(safeLink 통과만)
// - 아래: 터진 게시물을 해시태그·구절로 묶은 표. 숫자·배수·광고 재료 판정은 서버 값
// - 묶음마다 접힘으로 대표 게시물: 계정 · 지표 · 배수 · 게시물 링크(서버 permalink, https만) · 캡션 일부(외부 글, 텍스트로만)
// - 모르면 0이 아니라 서버 이유를 보여 준다. 첫 바퀴 진행 중(판정 전)이면 그리드는 숨긴다

const METRIC: Record<InstagramRadarPost["metric"], string> = { view_count: "조회", like_count: "좋아요" };
const n = (v: number) => v.toLocaleString("ko-KR");
const COMPACT = new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 });

/** 올린 지 얼마 */
function ago(iso: string | null): string | null {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const min = Math.floor(ms / 60_000);
  if (min < 60) return `${Math.max(min, 1)}분 전`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h}시간 전` : `${Math.floor(h / 24)}일 전`;
}

function PostCard({ item, rank }: { item: GridItem; rank: number }) {
  const { post: p, group: g } = item;
  const link = safeLink(p.permalink);
  const kind = kindLabel(p);
  const ad = AD[g.ad_material];
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
      <span><span className={`chip text-[11px] ${ad.tone}`}>광고 재료 {ad.label}</span></span>
    </>
  );
  const cls = "flex flex-col gap-1 rounded-lg";
  return (
    <li className="min-w-0">
      {link ? (
        <a href={link} target="_blank" rel="noreferrer noopener"
          className={`${cls} transition hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500`}
          aria-label={`${rank}위 ${p.account} 게시물 · 평소의 ${times(p.ratio)} · 인스타그램에서 보기`}>{body}</a>
      ) : <div className={cls}>{body}</div>}
    </li>
  );
}

function PostGrid({ out }: { out: InstagramRadarOutput }) {
  if (!showGrid(out)) return null;
  const items = gridItems(out.groups);
  return (
    <section aria-label="갑자기 뜬 게시물">
      <h3 className="mb-1.5 text-sm font-semibold text-ink-900">갑자기 뜬 게시물 <small className="font-normal text-ink-500">평소 대비 배수 순 · {n(items.length)}개</small></h3>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {items.map((item, i) => <PostCard key={item.post.media_id} item={item} rank={i + 1} />)}
      </ul>
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

function GroupRows({ g }: { g: InstagramRadarGroup }) {
  const ad = AD[g.ad_material];
  return (
    <>
      <tr className="align-top">
        <td className="min-w-[10rem] px-1 pt-2">
          <b className="text-ink-900">{g.name}</b>
          <small className="block text-xs text-ink-500">{g.description ?? g.description_reason ?? `게시물 ${n(g.posts)}개`}</small>
        </td>
        <td className="whitespace-nowrap px-1 pt-2 text-right tabular-nums">{n(g.accounts)}곳</td>
        <td className="whitespace-nowrap px-1 pt-2 text-right font-semibold tabular-nums">{times(g.best_ratio)}</td>
        <td className="min-w-[8rem] px-1 pt-2"><small className="text-xs text-ink-500">{g.members.length ? g.members.join(" · ") : "-"}</small></td>
        <td className="min-w-[7rem] px-1 pt-2">
          <span className={`chip text-xs ${ad.tone}`}>{ad.label}</span>
          {g.ad_material_reason && <small className="mt-0.5 block text-[11px] text-ink-500">{g.ad_material_reason}</small>}
        </td>
      </tr>
      <tr className="border-b border-ink-100">
        <td colSpan={5} className="px-1 pb-2">
          <details className="text-xs">
            <summary className="cursor-pointer select-none text-ink-500">게시물 {n(g.top_posts.length)}개 보기</summary>
            <Posts list={g.top_posts} />
          </details>
        </td>
      </tr>
    </>
  );
}

function RadarBody({ out }: { out: InstagramRadarOutput }) {
  return (
    <div className="space-y-2">
      {out.status === "unknown" && <p className="text-sm text-ink-700">{out.reason ?? "레이더 결과를 모르는 상태예요."}</p>}
      <PostGrid out={out} />
      {out.groups.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-ink-100 text-left text-xs text-ink-500">
                <th scope="col" className="whitespace-nowrap px-1 py-1.5 font-medium">묶음</th>
                <th scope="col" className="whitespace-nowrap px-1 py-1.5 text-right font-medium">계정</th>
                <th scope="col" className="whitespace-nowrap px-1 py-1.5 text-right font-medium">평소 대비</th>
                <th scope="col" className="whitespace-nowrap px-1 py-1.5 font-medium">공통점</th>
                <th scope="col" className="whitespace-nowrap px-1 py-1.5 font-medium">광고 재료</th>
              </tr>
            </thead>
            <tbody>
              {out.groups.map((g) => <GroupRows key={g.name} g={g} />)}
            </tbody>
          </table>
        </div>
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
    ? `벤치마크 계정 ${n(out.accounts_total)}곳 중 평소보다 크게 터진 게시물 묶음 · 오늘 읽은 계정 ${n(out.accounts_read)}곳${out.generated_at ? ` · 계산 ${when(out.generated_at)}` : ""}`
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
