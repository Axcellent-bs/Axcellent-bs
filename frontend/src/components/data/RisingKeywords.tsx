import { useState } from "react";
import { useNavigate } from "react-router";
import { TONE } from "@/components/finance/bits";
import { safeLink } from "@/lib/safeLink";
import type { KeywordTrendOutput, KeywordWindow, RisingKeyword, RisingLevel } from "@/lib/data";
import type { TrendNavState } from "@/lib/chatCards";
import {
  RISING_BASIS, candidateChips, fadedChip, levelOf, pickFirst, requestFormForKeyword, sourceShort, sparkline, splitRising,
} from "@/lib/keywordTrendView";
import { orbLayout } from "@/lib/wordOrb";

// 감지 → 검색 "급상승 검색어" 패널 본문 (keyword_trend, 11/20 v3). 서버가 하루 한 번 판정해 저장한 결과를 읽기만 한다.
// - 후보 출처 칩(candidate_counts, 0은 숨김) · 직전 판정 재사용이면 노란 띠(stale_reason)
// - 동그란 단어 구름(lib/wordOrb: 배수 큰 말이 가운데, 원 안, 최대 24개, 색 = level). 단어를 누르면 그 카드가 자기 칸 맨 위에 펼쳐진다
// - 급상승 카드(level rising): 배수 · 우리 상품 · 출처 · 월 검색·증가·블로그·카페 · 왜(why_line) · 작은 그래프(points·baseline) ·
//   "이 키워드로 분석 의뢰"(분석 의뢰 탭 직접 정하기에 키워드) · 뉴스 접힘
// - 오르는 중(warming) 작은 카드 · 나머지(normal·unknown) 접힌 표. 판정이 하나도 없으면 "오늘 판정 못 함" + 월 검색 큰 순 표만
// - 외부 글(뉴스 제목·why_line)은 글자로만, 링크는 safeLink만. 예전 응답(level 없음)은 status로 칸을 정한다

const ORB_SIZE = 440;
const LEVEL_TEXT: Record<RisingLevel, string> = {
  rising: "font-black text-brand-700",
  warming: "font-bold text-amber-700",
  normal: "font-medium text-ink-500",
  unknown: "font-medium text-ink-400",
};

const compact = (v: number): string => {
  const a = Math.abs(v);
  const one = (x: number) => (Math.round(x * 10) / 10).toLocaleString("ko-KR", { maximumFractionDigits: 1 });
  const sign = v < 0 ? "-" : "";
  if (a >= 1e8) return `${sign}${one(a / 1e8)}억`;
  if (a >= 1e4) return `${sign}${one(a / 1e4)}만`;
  if (a >= 1e3) return `${sign}${one(a / 1e3)}천`;
  return `${sign}${a.toLocaleString("ko-KR")}`;
};
const times = (n: number) => `${n.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}배`;

function Orb({ rows, onPick }: { rows: RisingKeyword[]; onPick: (keyword: string) => void }) {
  const placed = orbLayout(rows.map((r) => ({ text: r.keyword, weight: r.ratio ?? 0, level: levelOf(r) })), ORB_SIZE);
  if (placed.length === 0) return null;
  return (
    // 정사각형 틀(최대 440px, 가운데). 목록 높이에 맞춰 늘어나지 않게 grid 밖에 두고 self-start. 글자 크기는 틀 폭 비례(cqw)
    <div className="relative mx-auto aspect-square w-full max-w-[440px] self-start rounded-full ring-1 ring-ink-100"
      style={{ containerType: "inline-size", background: "radial-gradient(circle, rgba(91,91,214,0.10) 0 30%, rgba(91,91,214,0.02) 70%)" }}>
      {placed.map((p) => (
        <button key={p.text} type="button" onClick={() => onPick(p.text)} title={`${p.text} 카드 보기`}
          className={`absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap leading-none hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${LEVEL_TEXT[p.level]}`}
          style={{ left: `${(p.x / ORB_SIZE) * 100}%`, top: `${(p.y / ORB_SIZE) * 100}%`, fontSize: `${((p.fontPx / ORB_SIZE) * 100).toFixed(2)}cqw` }}>
          {p.text}
        </button>
      ))}
    </div>
  );
}

function Legend() {
  const dot = (cls: string) => <i className={`mr-1 inline-block h-2 w-2 rounded-full align-middle ${cls}`} />;
  return (
    <p className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-xs text-ink-500">
      <span>{dot("bg-brand-600")}급상승 2배↑</span>
      <span>{dot("bg-amber-500")}오르는 중 1.5배↑</span>
      <span>{dot("bg-ink-300")}평범</span>
      <span>가운데일수록·클수록 평소보다 많이 오른 말 · 단어를 누르면 그 카드를 보여 줘요</span>
    </p>
  );
}

function Spark({ r }: { r: RisingKeyword }) {
  const W = 128;
  const H = 46;
  const s = sparkline(r.points, r.baseline, W, H - 8);
  if (!s) return null;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-[46px] w-32 shrink-0" aria-hidden="true">
      <path d={s.area} className="fill-brand-50" />
      {s.baseY !== null && <line x1={0} x2={W} y1={s.baseY} y2={s.baseY} className="stroke-ink-400" strokeDasharray="3 3" strokeWidth={1} />}
      <polyline points={s.line} fill="none" className="stroke-brand-600" strokeWidth={1.8} />
      <text x={0} y={H - 1} className="fill-ink-400 text-[8px]">{r.points?.length ?? 0}일 전</text>
      <text x={W} y={H - 1} textAnchor="end" className="fill-ink-400 text-[8px]">어제</text>
    </svg>
  );
}

/** 급상승 카드 하나 (rank는 rising·warming만) */
function KeywordCard({ r, rank, win }: { r: RisingKeyword; rank: number | null; win: KeywordWindow }) {
  const navigate = useNavigate();
  const sp = r.spread?.status === "known" ? r.spread : null;
  const meta = [
    r.monthly_volume !== null ? `월 검색 ${compact(r.monthly_volume)}` : null,
    r.increase !== null && r.increase > 0 ? `${win === "week" ? "이번 주" : "어제"} +${compact(r.increase)}(추정)` : null,
    sp?.blog_7d != null ? `블로그 7일 ${sp.blog_7d.toLocaleString("ko-KR")}${sp.blog_7d_capped ? "+" : ""}` : null,
    sp?.cafe_total != null ? `카페 ${compact(sp.cafe_total)}` : null,
  ].filter(Boolean).join(" · ");
  const ask = () => navigate("/data/trend#request", { state: { trendRequestFill: requestFormForKeyword(r.keyword) } satisfies TrendNavState });
  const faded = fadedChip(r);
  const rising = levelOf(r) === "rising" && !faded;
  return (
    <li className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-2.5 gap-y-1 rounded-xl px-3 py-2.5 ring-1 ring-ink-100 sm:grid-cols-[1.5rem_minmax(0,1fr)_8rem]">
      <span className="text-[15px] font-black leading-snug text-brand-700">{rank ?? ""}</span>
      <div className="min-w-0 space-y-0.5">
        <p className="flex flex-wrap items-center gap-1.5">
          <b className="break-keep text-[15px] text-ink-900">{r.keyword}</b>
          {r.ratio !== null && <span className={`text-[13px] font-black ${faded ? "text-ink-500" : "text-emerald-700"}`}>평소 {times(r.ratio)}</span>}
          {faded && <span className={`chip text-[11px] ${TONE.warn}`} title="정점이 지나고 지금 값은 평소 범위 안이에요">{faded}</span>}
          {r.products !== null && r.products > 0 && (
            <span className={`chip text-[11px] ${TONE.ok}`} title={r.product_names.join(", ") || undefined}>우리 상품 {r.products}</span>
          )}
          {sourceShort(r.candidate_sources).map((s) => <span key={s} className={`chip text-[11px] ${TONE.muted}`}>{s}</span>)}
        </p>
        {meta && <p className="text-xs text-ink-500">{meta}</p>}
        {r.why_line && <p className="text-xs text-ink-700"><span className="text-ink-400">왜:</span> {r.why_line}</p>}
      </div>
      <div className="col-start-2 sm:col-start-3 sm:row-start-1"><Spark r={r} /></div>
      <div className="col-start-2 flex flex-wrap items-center gap-1.5 sm:col-span-2">
        <button type="button" className={rising ? "btn-primary px-2.5 py-1 text-xs" : "btn-ghost px-2.5 py-1 text-xs"} onClick={ask}>이 키워드로 분석 의뢰</button>
        {r.news.length > 0 && (
          <details className="text-xs text-ink-500">
            <summary className="cursor-pointer select-none rounded-md px-2 py-1 ring-1 ring-ink-100">뉴스 {r.news.length}건</summary>
            <ul className="mt-1 space-y-0.5">
              {r.news.map((n, i) => {
                const link = safeLink(n.url);
                return (
                  <li key={`${n.url ?? n.title}-${i}`}>
                    {link ? <a href={link} target="_blank" rel="noreferrer noopener" className="text-brand-700 underline-offset-2 hover:underline">{n.title}</a> : n.title}
                    {n.source && <span className="text-ink-500"> · {n.source}</span>}
                  </li>
                );
              })}
            </ul>
          </details>
        )}
      </div>
    </li>
  );
}

/** 나머지(평범·판정 못 함) 표: 키워드 · 평소 대비(막대 + 배수, 없으면 서버 이유) · 월 검색(없으면 서버 이유 그대로, 둘 다 없으면 빈칸) · 후보 출처 */
function RestTable({ rows }: { rows: RisingKeyword[] }) {
  const max = Math.max(1, ...rows.map((r) => r.ratio ?? 0));
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-ink-100 text-left text-ink-500">
            <th className="px-1 py-1.5 font-medium">키워드</th>
            <th className="px-1 py-1.5 font-medium">평소 대비</th>
            <th className="px-1 py-1.5 text-right font-medium">월 검색</th>
            <th className="px-1 py-1.5 font-medium">후보 출처</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.keyword} className="border-b border-ink-100 align-top">
              <td className="whitespace-nowrap px-1 py-1.5 text-ink-900">{r.keyword}</td>
              <td className="px-1 py-1.5">
                {r.ratio !== null ? (
                  <span className="inline-flex items-center gap-1.5 whitespace-nowrap tabular-nums">
                    <span className="inline-block h-1.5 rounded-full bg-ink-300" style={{ width: `${Math.max(4, (r.ratio / max) * 48)}px` }} aria-hidden="true" />
                    {times(r.ratio)}
                  </span>
                ) : <span className="text-ink-500">{r.reason ?? ""}</span>}
              </td>
              <td className="px-1 py-1.5 text-right tabular-nums">
                {r.monthly_volume !== null ? compact(r.monthly_volume) : <span className="text-[11px] text-ink-500">{r.increase_reason ?? r.volume_reason ?? ""}</span>}
              </td>
              <td className="whitespace-nowrap px-1 py-1.5 text-ink-500">{sourceShort(r.candidate_sources).join(" · ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RisingKeywords({ out, win }: { out: KeywordTrendOutput; win: KeywordWindow }) {
  const [picked, setPicked] = useState<string | null>(null);
  const [restOpen, setRestOpen] = useState(false);
  if (out.rising.length === 0) {
    return <p className="text-sm text-ink-700">급상승 검색어가 없어요.{out.reason ? ` ${out.reason}` : ""}</p>;
  }
  const split = splitRising(out.rising);
  const chips = candidateChips(out.candidate_counts);
  // 검색광고·확산 키가 없으면 줄마다 반복하지 않고 패널 위에 한 줄씩
  const keyNotes = [
    out.searchad_status !== "connected" ? `월 검색 · ${out.searchad_reason ?? "검색광고 키가 없어 비워 둬요"}` : null,
    out.spread_status !== "connected" && out.spread_reason ? `블로그·카페 · ${out.spread_reason}` : null,
  ].filter((x): x is string => !!x);
  const pick = (k: string) => {
    setPicked(k);
    const row = out.rising.find((r) => r.keyword === k);
    if (row && (levelOf(row) === "normal" || levelOf(row) === "unknown")) setRestOpen(true);
  };
  const pickedRow = picked ? out.rising.find((r) => r.keyword === picked) ?? null : null;
  const pickedLevel = pickedRow ? levelOf(pickedRow) : null;

  return (
    <div className="space-y-3">
      {chips.length > 0 && (
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-ink-500">
          <span>후보 {out.rising.length}개 =</span>
          {chips.map((c) => <span key={c.key} className="rounded-full bg-ink-50 px-2 py-0.5 ring-1 ring-ink-100">{c.label} <b className="text-ink-900">{c.count}</b></span>)}
        </p>
      )}
      {out.stale_reason && <p role="status" className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">{out.stale_reason}</p>}
      {keyNotes.map((n) => <p key={n} className="text-xs text-ink-500">{n}</p>)}

      {split.noJudgment ? (
        <>
          <p className="text-sm text-ink-700">오늘 판정 못 함 · {out.reason ?? split.rest.find((r) => r.reason)?.reason ?? "이유를 받지 못했어요"}</p>
          <RestTable rows={split.rest} />
        </>
      ) : (
        <>
          <Orb rows={out.rising} onPick={pick} />
          <Legend />

          {split.rising.length > 0 && (
            <section aria-label="급상승" className="space-y-1.5">
              <p className="text-xs font-bold text-ink-500">급상승 · 평소의 2배 이상 {split.rising.length}개{pickedLevel === "rising" ? " (구름에서 고른 단어가 맨 위)" : ""}</p>
              <ol className="space-y-2">
                {pickFirst(split.rising, pickedLevel === "rising" ? picked : null).map((r) => (
                  <KeywordCard key={r.keyword} r={r} rank={split.rising.indexOf(r) + 1} win={win} />
                ))}
              </ol>
            </section>
          )}

          {split.warming.length > 0 && (
            <section aria-label="오르는 중" className="space-y-1.5">
              <p className="text-xs font-bold text-ink-500">오르는 중 · 평소의 1.5~2배 {split.warming.length}개</p>
              {pickedRow && pickedLevel === "warming" && (
                <ol><KeywordCard r={pickedRow} rank={split.warming.indexOf(pickedRow) + 1} win={win} /></ol>
              )}
              <ul className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-1.5">
                {split.warming.map((r) => (
                  <li key={r.keyword}>
                    <button type="button" onClick={() => setPicked(r.keyword)} aria-pressed={picked === r.keyword}
                      className="flex w-full justify-between gap-1.5 rounded-lg border border-dashed border-ink-200 px-2.5 py-1.5 text-left text-[13px] hover:bg-ink-50">
                      <b className="truncate text-ink-900">{r.keyword}</b>
                      {r.ratio !== null && <span className="shrink-0 text-xs font-bold text-amber-700">{times(r.ratio)}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {split.rest.length > 0 && (
            <details open={restOpen} onToggle={(e) => setRestOpen((e.target as HTMLDetailsElement).open)} className="text-xs text-ink-500">
              <summary className="cursor-pointer select-none font-bold text-brand-700">
                나머지 {split.rest.length}개 보기 (평범 {split.rest.filter((r) => levelOf(r) === "normal").length} · 판정 못 함 {split.rest.filter((r) => levelOf(r) === "unknown").length})
              </summary>
              <div className="mt-2 space-y-2">
                {pickedRow && (pickedLevel === "normal" || pickedLevel === "unknown") && (
                  <ol><KeywordCard r={pickedRow} rank={null} win={win} /></ol>
                )}
                <RestTable rows={split.rest} />
              </div>
            </details>
          )}
        </>
      )}
      <p className="border-t border-ink-100 pt-2 text-[11px] text-ink-400">기준: {RISING_BASIS}</p>
    </div>
  );
}
