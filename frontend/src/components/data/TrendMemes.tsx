import { useState, type ReactNode } from "react";
import { ErrorBox, Panel, TONE, Waiting } from "@/components/finance/bits";
import { useSkill, type SkillState } from "@/components/data/useSkill";
import { safeLink } from "@/lib/safeLink";
import { judgedByShare, shareLine } from "@/lib/memeView";
import { SummaryBody, SummaryCard } from "@/components/data/KeywordTrend";
import { Thumb } from "@/components/data/VideoExplorer";
import { thumbSrc } from "@/lib/igRadarGrid";
import { perSubscriberLabel, shortsNote, splitShorts } from "@/lib/memeShorts";
import { daysAgoLabel, layerChips, megaNote, megaSorted, platformLabel, viewsLabel } from "@/lib/radarMeta";
import { dataAi, type MegaViewItem, type MegaViews, type MemeShortsSearch, type MemeShortsVideo, type MemeWindow, type TrendMeme, type TrendMemesOutput } from "@/lib/data";
import type { DetectView } from "@/lib/detectView";

// 트렌드 1 감지 하위 탭의 "요즘 유행·밈" (trend_memes, 오늘 가장 큰 화제 한 줄 바로 아래 플랫폼 칩 안). 영상 하나가 아니라 여러 채널에서 같이 늘어난 해시태그·구절 묶음.
// - 숫자·순위·판정은 서버 값 그대로. 설명 한 줄만 서버 LLM(숫자 없음)
// - 이번 주(7일 vs 직전 7일) / 오늘(어제 vs 지난 4주 같은 요일) 토글. 서버가 미리 계산한 결과를 읽는다
// - 대표 영상은 링크만 (썸네일을 걸지 않는다)
// - 배수 칸: 서버가 몫으로 판정했으면(judged_by share) "이번 주 몫 · 지난주 몫 (×몫 배수)", 영상 수 판정(count·예전 응답)이면 영상 수 배수 (lib/memeView.ts)

const WINDOWS: { key: MemeWindow; label: string; before: string; span: string }[] = [
  { key: "week", label: "이번 주", before: "지난주", span: "이번 주" },
  { key: "day", label: "오늘", before: "지난 4주 같은 요일", span: "어제" },
];

export const AD: Record<TrendMeme["ad_material"], { label: string; tone: string }> = {
  ok: { label: "가능", tone: TONE.ok },
  caution: { label: "주의", tone: TONE.warn },
  excluded: { label: "뺌", tone: TONE.bad },
  unknown: { label: "분류 대기", tone: TONE.muted },
};

export const times = (n: number) => `${n.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}배`;

/** 인스타 칸 (m.instagram, Instagram 해시태그 검색). 모르면 0이 아니라 서버 이유 */
function Instagram({ m }: { m: TrendMeme }) {
  const ig = m.instagram ?? null;
  if (ig?.status === "known" && ig.posts !== null) {
    return (
      <>
        24h {ig.posts.toLocaleString("ko-KR")}개{ig.posts_24h_capped ? "+" : ""}
        {ig.top_like_median != null && <small className="block text-[11px] text-ink-500">좋아요 중앙 {ig.top_like_median.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}</small>}
      </>
    );
  }
  if (ig?.status === "not_hashtag") return <span className="text-ink-500">해시태그 아님</span>;
  return ig?.reason ? <small className="block max-w-[9rem] whitespace-normal text-[11px] text-ink-500">{ig.reason}</small> : <span className="text-ink-500">-</span>;
}

/** "3곳 동시": 쇼츠·검색·인스타가 모두 오를 때.
 *  - 쇼츠: 배수를 알고 2배 이상이거나 새로 등장
 *  - 검색: 서버 판정이 급상승(rising)
 *  - 인스타: 서버가 직전 기간 기준값을 주지 않으므로 "최근 24시간에 게시물이 있음(status known, posts > 0)"을 오름으로 본다 */
export function risingEverywhere(m: TrendMeme): boolean {
  const shorts = (m.ratio_status === "known" && m.ratio !== null && m.ratio >= 2) || m.ratio_status === "new";
  const search = m.search.status === "rising";
  const ig = m.instagram ?? null;
  const insta = ig?.status === "known" && ig.posts !== null && ig.posts > 0;
  return shorts && search && insta;
}

/** 배수 칸: 몫으로 판정한 유행(judged_by share)이면 "이번 주 몫 · 지난주 몫 (×몫 배수)", 영상 수 판정이면 지금처럼 배수 */
function Ratio({ m, span, before }: { m: TrendMeme; span: string; before: string }) {
  const share = shareLine(m, span, before);
  if (share) return <span className="whitespace-normal">{share}</span>;
  if (m.ratio_status === "known" && m.ratio !== null) return <>{times(m.ratio)}</>;
  if (m.ratio_status === "new") return <>새로 등장</>;
  return <span className="text-ink-500">모름<small className="block text-[11px]">{m.ratio_reason ?? "지난 기간 표본 없음"}</small></span>;
}

function Search({ m }: { m: TrendMeme }) {
  const s = m.search;
  if ((s.status === "rising" || s.status === "normal") && s.ratio !== null) {
    return <>{times(s.ratio)}<small className="block text-[11px] text-ink-500">{s.status === "rising" ? "급상승" : "평범"}</small></>;
  }
  const label = s.status === "not_checked" ? "확인 안 함" : "모름";
  return <span className="text-ink-500">{label}{s.reason && <small className="block text-[11px]">{s.reason}</small>}</span>;
}

function Material({ m }: { m: TrendMeme }) {
  const ad = AD[m.ad_material];
  const products = m.products.length ? `상품 연결: ${m.products.slice(0, 2).map((p) => p.name).join(", ")}${m.products.length > 2 ? ` 외 ${m.products.length - 2}` : ""}` : null;
  return (
    <div className="min-w-[8rem]">
      <span className={`chip text-xs ${ad.tone}`}>{ad.label}</span>
      <small className="mt-0.5 block text-[11px] text-ink-500">{m.ad_material === "caution" || m.ad_material === "excluded" ? m.ad_material_reason : products ?? m.products_reason ?? m.ad_material_reason}</small>
    </div>
  );
}

function Videos({ m }: { m: TrendMeme }) {
  if (!m.top_videos.length) return <span className="text-ink-500">없음</span>;
  return (
    <ul className="min-w-[10rem] space-y-1">
      {m.top_videos.map((v) => {
        const link = safeLink(v.video_url); // http(s)만 링크, 아니면 제목 글자만
        return (
          <li key={v.video_id} className="text-xs">
            {link ? <a href={link} target="_blank" rel="noreferrer noopener" className="text-brand-700 underline-offset-2 hover:underline">{v.title || v.video_id}</a> : (v.title || v.video_id)}
            <small className="block text-[11px] text-ink-500">{[v.channel_title, v.views !== null ? `조회 ${v.views.toLocaleString("ko-KR")}` : null].filter(Boolean).join(" · ")}</small>
          </li>
        );
      })}
    </ul>
  );
}

function criteriaLine(out: TrendMemesOutput, span: string, before: string) {
  const c = out.criteria;
  if (!c) return null;
  return `기준: ${span} ${c.min_channels}개 이상 채널 · 영상 ${c.min_videos}개 이상 · ${before}의 ${c.min_ratio}배 이상(${before}에 없었으면 새로 등장). 인물 이름 자체·뉴스·사건은 유행에서 뺍니다(기존 분류기 그대로).`;
}

/** 종합 칩 맨 위 요약 줄의 첫 칸: 1위 유행 하나 (서버 순위 그대로). 받는 중·실패·없음이면 짧은 문장 */
function TopMeme({ s, label, span, before }: { s: SkillState<TrendMemesOutput>; label: string; span: string; before: string }) {
  const m = s.kind === "ok" ? s.out.items[0] ?? null : null;
  const ratio = !m ? null : shareLine(m, span, before)
    ?? (m.ratio_status === "known" && m.ratio !== null ? `${before} 대비 ${times(m.ratio)}` : m.ratio_status === "new" ? "새로 등장" : null);
  return (
    <SummaryCard label={`${label} 유행`}>
      {m ? (
        <>
          <SummaryBody title={m.name} lines={[`채널 ${m.channels}곳`, `영상 ${m.videos}개`, ratio]} />
          {m.description && <p className="mt-0.5 text-xs text-ink-500">{m.description}</p>}
        </>
      ) : (
        <p className="text-sm text-ink-500">{s.kind === "loading" ? "받는 중…" : s.kind === "failed" ? "받지 못했어요" : "기준을 넘은 유행이 없어요."}</p>
      )}
    </SummaryCard>
  );
}

/** youtube = 쇼츠 칸 중심 (네이버 검색·광고 재료 칸은 종합에서만) */
function Table({ out, span, before, youtube }: { out: TrendMemesOutput; span: string; before: string; youtube: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-ink-100 text-left text-xs text-ink-500">
            <th className="whitespace-nowrap px-1 py-1.5 font-medium">유행</th>
            <th className="whitespace-nowrap px-1 py-1.5 text-right font-medium">퍼진 정도</th>
            <th className="whitespace-nowrap px-1 py-1.5 text-right font-medium">{before} 대비</th>
            {!youtube && <th className="whitespace-nowrap px-1 py-1.5 text-right font-medium">네이버 검색</th>}
            {!youtube && <th className="whitespace-nowrap px-1 py-1.5 text-right font-medium">인스타</th>}
            {!youtube && <th className="whitespace-nowrap px-1 py-1.5 font-medium">광고 재료</th>}
            <th className="whitespace-nowrap px-1 py-1.5 font-medium">{youtube ? "대표 쇼츠" : "대표 영상"}</th>
          </tr>
        </thead>
        <tbody>
          {out.items.map((m) => (
            <tr key={m.name} className="border-b border-ink-100 align-top">
              <td className="min-w-[12rem] px-1 py-2">
                <b className="text-ink-900">{m.name}</b>
                {risingEverywhere(m) && <span className={`chip ml-1.5 align-middle text-xs ${TONE.info}`} title="쇼츠·네이버 검색·인스타에서 같이 오름">3곳 동시</span>}
                <small className="block text-xs text-ink-500">{m.description ?? m.description_reason ?? ""}</small>
              </td>
              <td className="whitespace-nowrap px-1 py-2 text-right tabular-nums">채널 {m.channels}곳<small className="block text-[11px] text-ink-500">영상 {m.videos}개</small></td>
              <td className={`${judgedByShare(m) ? "min-w-[11rem]" : "whitespace-nowrap"} px-1 py-2 text-right tabular-nums`}><Ratio m={m} span={span} before={before} /></td>
              {!youtube && <td className="whitespace-nowrap px-1 py-2 text-right tabular-nums"><Search m={m} /></td>}
              {!youtube && <td className="whitespace-nowrap px-1 py-2 text-right tabular-nums"><Instagram m={m} /></td>}
              {!youtube && <td className="px-1 py-2"><Material m={m} /></td>}
              <td className="px-1 py-2"><Videos m={m} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ShortsCard({ v, rank }: { v: MemeShortsVideo; rank: number }) {
  const link = safeLink(v.video_url);
  const rel = perSubscriberLabel(v);
  const body = (
    <>
      <Thumb src={thumbSrc(v.thumbnail_url)} alt={v.title ?? `${v.channel_title ?? "쇼츠"} 영상`} wide={false} rank={rank} tall />
      <span className="mt-1 line-clamp-2 text-[13px] font-semibold text-ink-900">{v.title || v.video_id}</span>
      <span className="truncate text-xs text-ink-700">
        {[v.channel_title, v.views !== null ? `조회 ${v.views.toLocaleString("ko-KR")}` : null].filter(Boolean).join(" · ")}
      </span>
      {rel && <span className="text-xs font-medium text-emerald-700">{rel}</span>}
      <span><span className="chip text-[11px]">{v.keyword}</span></span>
    </>
  );
  const cls = "flex flex-col gap-1 rounded-lg";
  return (
    <li className="min-w-0">
      {link ? (
        <a href={link} target="_blank" rel="noreferrer noopener" className={`${cls} transition hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500`}>{body}</a>
      ) : <div className={cls}>{body}</div>}
    </li>
  );
}

function ShortsList({ label, list }: { label: string; list: MemeShortsVideo[] }) {
  if (list.length === 0) return null;
  return (
    <div>
      <h4 className="mb-1.5 text-xs font-semibold text-ink-700">{label} <small className="font-normal text-ink-500">{list.length}개</small></h4>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {list.map((v, i) => <ShortsCard key={v.video_id} v={v} rank={i + 1} />)}
      </ul>
    </div>
  );
}

/** 영상 줄 하나: 계정 · 플랫폼 · 조회수 · 며칠 전 · 층 칩. 제목은 유튜브만(텍스트로만), 썸네일은 https만 */
export function MegaRow({ x }: { x: MegaViewItem }) {
  const link = safeLink(x.link);
  const thumb = thumbSrc(x.thumbnail_url);
  const when = daysAgoLabel(x.posted_at);
  const chips = layerChips(x);
  const body = (
    <>
      {thumb && <img src={thumb} alt="" loading="lazy" referrerPolicy="no-referrer" className="h-[54px] w-[40px] shrink-0 object-cover" />}
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate text-[13px] font-semibold text-ink-900">{x.account}{x.title ? ` · ${x.title}` : ""}</span>
        <span className="text-xs text-ink-700">{[platformLabel(x.platform), viewsLabel(x.views), when].filter(Boolean).join(" · ")}</span>
        {chips.length > 0 && <span className="flex flex-wrap gap-1">{chips.map((c) => <span key={c} className="chip text-[11px]">{c}</span>)}</span>}
      </span>
    </>
  );
  const cls = "flex min-w-0 gap-2 border border-ink-100 p-1.5";
  return (
    <li className="min-w-0">
      {link ? <a href={link} target="_blank" rel="noreferrer noopener" className={`${cls} hover:bg-ink-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500`}>{body}</a> : <div className={cls}>{body}</div>}
    </li>
  );
}

/** 조회 천만 이상 영상 (mega_views). 조회수 내림차순, 모르면 서버 이유 */
function MegaViewsPanel({ m }: { m: MegaViews }) {
  const note = megaNote(m);
  return (
    <section aria-label="조회 천만 이상" className="space-y-2 border-t border-ink-100 pt-3">
      <h3 className="text-sm font-semibold text-ink-900">조회 천만 이상 <small className="font-normal text-ink-500">최근 {m.days}일 · 조회수 순</small></h3>
      {note ? <p className="text-sm text-ink-700">{note}</p> : (
        <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">{megaSorted(m.items).map((x) => <MegaRow key={`${x.platform}:${x.item_id}`} x={x} />)}</ul>
      )}
    </section>
  );
}

/** 회사 밈 키워드로 하루 1번 찾은 최근 7일 쇼츠 (shorts_search). 구독자 10만 이상 채널은 따로 */
function ShortsSearch({ s }: { s: MemeShortsSearch | null | undefined }) {
  if (!s) return null;
  const note = shortsNote(s);
  const { small, big } = splitShorts(s.items);
  const removed = s.removed ?? [];
  const limits = s.limits ?? [];
  return (
    <section aria-label="쇼츠 검색" className="space-y-2 border-t border-ink-100 pt-3">
      <h3 className="text-sm font-semibold text-ink-900">쇼츠 검색 <small className="font-normal text-ink-500">
        {s.status === "known" ? `${s.searched.length ? `키워드 ${s.searched.join(", ")} · ` : ""}최근 7일 · 구독자 대비 조회수 순` : "회사 밈 키워드"}</small></h3>
      {note && <p className="text-sm text-ink-700">{note}</p>}
      <ShortsList label="구독자 10만 미만 채널" list={small} />
      <ShortsList label="구독자 10만 이상 채널" list={big} />
      {removed.length > 0 && (
        <details className="text-xs text-ink-500">
          <summary className="cursor-pointer select-none">실존 인물·사건이라 뺀 영상 {removed.length}개</summary>
          <ul className="mt-1 space-y-0.5">
            {removed.map((k, i) => <li key={`${i}-${k.name}`}><b className="font-medium text-ink-700">{k.name}</b> · {k.reason}</li>)}
          </ul>
        </details>
      )}
      {limits.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-500">
          {limits.map((l, i) => <li key={`${i}-${l}`}>{l}</li>)}
        </ul>
      )}
    </section>
  );
}

/** focus: 감지 플랫폼 칩 (all = 종합: 요약 줄 + 전체 칸(네이버 검색·인스타·광고 재료), youtube = 쇼츠 칸 중심). 기획 넘기기 버튼은 그리지 않는다.
 *  유행 이름 옆 "3곳 동시" 칩 = 쇼츠·검색·인스타가 모두 오름 (risingEverywhere)
 *  summaryCards: 종합 요약 줄에서 1위 유행 칸 뒤에 붙일 칸 (검색 급상승 1위·이달 시즌 1위 = 부모가 받은 keyword_trend, 인스타 인기 오디오 1위 = instagram_audio) */
export function TrendMemes({ focus = "all", summaryCards }: { focus?: DetectView; summaryCards?: ReactNode }) {
  const youtube = focus === "youtube";
  const [win, setWin] = useState<MemeWindow>("week");
  const [showMega, setShowMega] = useState(false);
  const res = useSkill(() => dataAi.trendMemes(win));
  const meta = WINDOWS.find((w) => w.key === win)!;
  const pick = (key: MemeWindow) => {
    if (key === win) return;
    setWin(key);
    res.reload();
  };
  const tools = (
    <div role="group" aria-label="유행 기간" className="flex gap-1">
      {WINDOWS.map((w) => (
        <button key={w.key} type="button" aria-pressed={win === w.key} onClick={() => pick(w.key)}
          className={`rounded-md px-2.5 py-1 text-xs ring-1 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${win === w.key ? "bg-brand-50 font-bold text-brand-700 ring-brand-500" : "text-ink-500 ring-ink-100 hover:bg-ink-50"}`}>
          {w.label}
        </button>
      ))}
    </div>
  );
  const s = res.state;
  return (
    <Panel id="data-trend-memes" title="요즘 유행·밈" tools={tools}
      hint={'영상 하나가 아니라 "여러 채널에서 같이 늘어난 해시태그·구절"을 하나의 유행으로 묶었어요. 검색으로도 오르는지 같이 봅니다.'}>
      {!youtube && (
        <div className="mb-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
          <TopMeme s={s} label={meta.label} span={meta.span} before={meta.before} />
          {summaryCards}
        </div>
      )}
      {s.kind === "loading" ? <Waiting what="유행을 받는 중…" />
        : s.kind === "failed" ? <ErrorBox message={s.reason} onRetry={res.reload} />
        : (
          <div className="space-y-2">
            {s.out.items.length ? <Table out={s.out} span={meta.span} before={meta.before} youtube={youtube} /> : (
              <p className="text-sm text-ink-700">기준을 넘은 유행이 없어요.{s.out.status === "unknown" && s.out.reason ? ` ${s.out.reason}` : ""}</p>
            )}
            <p className="text-xs text-ink-500">{criteriaLine(s.out, meta.span, meta.before)}</p>
            <p className="text-xs text-ink-500">
              {`숏츠 표본 ${s.out.sample_videos}개 중 ${meta.span} ${s.out.current_videos}개를 봤어요`}
              {s.out.unknown_published > 0 && ` · 게시 시각을 몰라 뺀 영상 ${s.out.unknown_published}개`}
              {s.out.skipped.length > 0 && ` · 뺀 단위 ${s.out.skipped.length}개`}
            </p>
            {s.out.skipped.length > 0 && (
              <details className="text-xs text-ink-500">
                <summary className="cursor-pointer select-none">뺀 단위와 이유</summary>
                <ul className="mt-1 space-y-0.5">
                  {s.out.skipped.map((k) => <li key={`${k.kind}-${k.name}`}><b className="font-medium text-ink-700">{k.name}</b> · {k.reason}</li>)}
                </ul>
              </details>
            )}
            {s.out.mega_views && (
              <>
                <button type="button" aria-pressed={showMega} onClick={() => setShowMega((v) => !v)}
                  className={`rounded-md px-2.5 py-1 text-xs ring-1 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${showMega ? "bg-brand-50 font-bold text-brand-700 ring-brand-500" : "text-ink-500 ring-ink-100 hover:bg-ink-50"}`}>
                  조회 천만 이상
                </button>
                {showMega && <MegaViewsPanel m={s.out.mega_views} />}
              </>
            )}
            <ShortsSearch s={s.out.shorts_search} />
          </div>
        )}
    </Panel>
  );
}
