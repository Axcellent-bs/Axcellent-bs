import { useState, type ReactNode } from "react";
import { ErrorBox, Panel, TONE, Waiting } from "@/components/finance/bits";
import { when } from "@/components/data/Sources";
import { safeLink } from "@/lib/safeLink";
import type { SkillState } from "@/components/data/useSkill";
import type { KeywordTrendOutput, KeywordWindow, NewKeyword, NewKeywordDay, RisingKeyword, SeasonKeyword } from "@/lib/data";

// 트렌드 1 감지 하위 탭 "검색" 칩의 검색 트렌드 · 도서 분야 (keyword_trend). 서버가 미리 계산한 결과를 읽기만 한다.
// - 블록 4개: 급상승 검색어(단어 구름 + 순위 목록) · AI 요약 · 새로 나타난 키워드(최근 4일) · 이달 시즌 키워드(분류 탭)
// - 숫자·판정은 서버 값 그대로. 검색광고 키가 없으면 검색량·증가 칸은 서버 이유(없으면 "키 등록 후 가능"), 배수·판정은 그대로 보여 준다
// - 받기(useSkill)는 부모(PlatformViews)가 한다. 종합 칩 요약 칸(KeywordSummaryCards)도 같은 응답을 쓴다
// - 단어 구름은 글자 크기(CSS)만 쓴다. 라이브러리 없음

const KEY_LATER = "키 등록 후 가능";

const WINDOWS: { key: KeywordWindow; label: string }[] = [
  { key: "day", label: "일간" },
  { key: "week", label: "주간" },
];

const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];

/** 큰 수를 한국식으로 줄인다: 18000 → "1.8만", 9600 → "9.6천", 120 → "120" */
export function compactKo(v: number): string {
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  const one = (x: number) => Math.round(x * 10) / 10;
  const fmt = (x: number) => x.toLocaleString("ko-KR", { maximumFractionDigits: 1 });
  if (one(a / 1e4) >= 10_000) return `${sign}${fmt(one(a / 1e8))}억`;
  if (one(a / 1e3) >= 10) return `${sign}${fmt(one(a / 1e4))}만`;
  if (a >= 1e3) return `${sign}${fmt(one(a / 1e3))}천`;
  return `${sign}${a.toLocaleString("ko-KR")}`;
}

/** 배수 "1.8배" */
export const timesKo = (n: number) => `${n.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}배`;

/** "2026-11-17" → "11-17 화" (요일은 날짜 자체로 계산, 시간대 영향 없음) */
function dayLabel(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return day;
  return `${day.slice(5)} ${WEEKDAY[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]}`;
}

/** 종합 칩 요약 칸 하나 (이번 주 유행·검색 급상승 1위·이달 시즌 1위가 같이 쓴다) */
export function SummaryCard({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 rounded-xl bg-brand-50 px-4 py-3">
      <p className="text-xs font-semibold text-brand-700">{label}</p>
      <div className="mt-0.5 min-w-0">{children}</div>
    </div>
  );
}

/** 요약 칸 안 큰 이름 + 작은 줄 */
export function SummaryBody({ title, lines }: { title: string; lines: (string | null | undefined)[] }) {
  const rest = lines.filter(Boolean);
  return (
    <>
      <p className="truncate text-lg font-bold text-ink-900" title={title}>{title}</p>
      {rest.length > 0 && <p className="text-sm text-ink-700">{rest.join(" · ")}</p>}
    </>
  );
}

const SummaryNote = ({ children }: { children: ReactNode }) => <p className="text-sm text-ink-500">{children}</p>;

/** 종합 칩 요약 줄의 검색 칸 2개: 검색 급상승 1위 · 이달 시즌 1위. 받는 중·실패면 짧은 문장 */
export function KeywordSummaryCards({ state }: { state: SkillState<KeywordTrendOutput> }) {
  if (state.kind !== "ok") {
    const note = state.kind === "loading" ? "받는 중…" : "받지 못했어요";
    return (
      <>
        <SummaryCard label="검색 급상승 1위"><SummaryNote>{note}</SummaryNote></SummaryCard>
        <SummaryCard label="이달 시즌 1위"><SummaryNote>{note}</SummaryNote></SummaryCard>
      </>
    );
  }
  const out = state.out;
  const top = out.rising.find((r) => r.status === "rising") ?? null;
  const season = out.season[0] ?? null;
  return (
    <>
      <SummaryCard label="검색 급상승 1위">
        {top ? (
          <SummaryBody title={top.keyword} lines={[
            top.ratio !== null ? `평소 ${timesKo(top.ratio)}` : null,
            top.products !== null ? (top.products > 0 ? `상품 ${top.products}종` : "상품 없음") : null,
          ]} />
        ) : <SummaryNote>급상승한 검색어가 없어요.{out.reason ? ` ${out.reason}` : ""}</SummaryNote>}
      </SummaryCard>
      <SummaryCard label={out.season_month ? `${out.season_month}월 시즌 1위` : "이달 시즌 1위"}>
        {season ? (
          <SummaryBody title={season.keyword} lines={[
            season.peak_from ? `${season.peak_from}부터 몰림` : null,
            timesKo(season.ratio),
          ]} />
        ) : <SummaryNote>{out.season_reason ?? "시즌 키워드가 없어요."}</SummaryNote>}
      </SummaryCard>
    </>
  );
}

// ------------------------------------------------------------------- 1 급상승 검색어

/** 단어 구름: 배수가 클수록 크게. 급상승은 강조색 굵게, 나머지는 옅게. 순위 목록과 같은 내용이라 읽기 프로그램에는 숨긴다 */
function WordCloud({ list }: { list: RisingKeyword[] }) {
  const max = Math.max(1, ...list.map((r) => r.ratio ?? 0));
  return (
    <div aria-hidden="true" className="flex min-h-[8rem] flex-wrap content-center items-center justify-center gap-x-3 gap-y-1 rounded-xl bg-ink-50 px-3 py-4">
      {list.map((r) => {
        const size = 0.8 + 1.2 * Math.min(1, (r.ratio ?? 0) / max);
        const rising = r.status === "rising";
        return (
          <span key={r.keyword} title={r.ratio !== null ? `평소 ${timesKo(r.ratio)}` : undefined} style={{ fontSize: `${size.toFixed(2)}rem` }}
            className={`max-w-full break-keep leading-tight ${rising ? "font-bold text-accent" : "text-ink-500"}`}>
            {r.keyword}
          </span>
        );
      })}
    </div>
  );
}

function ProductChip({ r }: { r: RisingKeyword }) {
  if (r.products !== null && r.products > 0) {
    return <span className={`chip text-xs ${TONE.info}`} title={r.product_names.join(", ") || undefined}>상품 {r.products}</span>;
  }
  return <span className={`chip text-xs ${TONE.muted}`} title={r.products_reason ?? undefined}>{r.products === 0 ? "상품 없음" : "상품 모름"}</span>;
}

function RisingItem({ r }: { r: RisingKeyword }) {
  const rising = r.status === "rising";
  const ratio = r.ratio !== null ? `평소 ${timesKo(r.ratio)}` : `배수 모름${r.reason ? ` (${r.reason})` : ""}`;
  const line = [
    r.news_count !== null ? `뉴스 ${r.news_count.toLocaleString("ko-KR")}` : null,
    ratio,
    r.monthly_volume !== null ? `월 검색 ${compactKo(r.monthly_volume)}` : null,
    rising ? null : r.status === "normal" ? "평범" : "판정 못 함",
  ].filter(Boolean).join(" · ");
  const sp = r.spread?.status === "known" ? r.spread : null;
  const spread = sp ? [
    sp.blog_7d !== null ? `블로그 7일 ${sp.blog_7d.toLocaleString("ko-KR")}${sp.blog_7d_capped ? "+" : ""}` : null,
    sp.cafe_total !== null ? `카페 전체 ${compactKo(sp.cafe_total)}` : null,
  ].filter(Boolean).join(" · ") : "";
  return (
    <li className="flex gap-3 py-2">
      <span className={`w-5 shrink-0 text-sm font-semibold tabular-nums ${rising ? "text-ink-900" : "text-ink-500"}`}>{r.rank}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={`break-keep ${rising ? "font-semibold text-ink-900" : "text-ink-700"}`}>{r.keyword}</span>
          <ProductChip r={r} />
        </div>
        <p className="text-xs text-ink-500">{line}</p>
        {spread && <p className="text-xs text-ink-500">{spread}</p>}
        {r.news.length > 0 && (
          <details className="text-xs text-ink-500">
            <summary className="cursor-pointer select-none">뉴스 제목 보기</summary>
            <ul className="mt-1 space-y-0.5">
              {r.news.slice(0, 3).map((n, i) => {
                const link = safeLink(n.url); // 피드 주소: http(s)만 링크, 아니면 제목 글자만
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
      <div className="shrink-0 text-right">
        {r.increase !== null ? (
          <span className={`text-sm font-semibold tabular-nums ${r.increase > 0 ? "text-emerald-700" : "text-ink-500"}`}>
            {r.increase > 0 ? "+" : ""}{compactKo(r.increase)}
          </span>
        ) : (
          <span className="block max-w-[8rem] text-[11px] text-ink-500">{r.increase_reason ?? r.volume_reason ?? KEY_LATER}</span>
        )}
      </div>
    </li>
  );
}

function RisingBlock({ out }: { out: KeywordTrendOutput }) {
  if (out.rising.length === 0) {
    return <p className="text-sm text-ink-700">급상승 검색어가 없어요.{out.reason ? ` ${out.reason}` : ""}</p>;
  }
  const spreadNote = out.spread_status !== "connected" ? (out.spread_reason ?? null) : null;
  return (
    <div className="space-y-2">
      <div className="grid gap-4 md:grid-cols-2">
        <WordCloud list={out.rising} />
        <ol className="divide-y divide-ink-100" aria-label="급상승 검색어 순위">
          {out.rising.map((r) => <RisingItem key={r.keyword} r={r} />)}
        </ol>
      </div>
      {spreadNote && <p className="text-xs text-ink-500">블로그·카페 · {spreadNote}</p>}
    </div>
  );
}

// ------------------------------------------------------------------- 3 새로 나타난 키워드

const volumeOf = (k: NewKeyword) => (k.monthly_volume !== null ? compactKo(k.monthly_volume) : k.volume_below_10 ? "10 미만" : "-");

function NewDay({ d }: { d: NewKeywordDay }) {
  return (
    <div className="min-w-0 rounded-xl p-3 ring-1 ring-ink-100">
      <p className="text-xs font-semibold text-ink-500">{dayLabel(d.day)}</p>
      {d.status === "pending" ? (
        <p className="mt-1 text-sm text-ink-500">{d.reason ?? "아직 집계 전이에요"}</p>
      ) : d.items.length === 0 ? (
        <p className="mt-1 text-sm text-ink-500">{d.reason ?? "새 키워드가 없어요."}</p>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {d.items.map((k) => (
            <li key={k.keyword} className="flex items-baseline gap-2 text-sm">
              <span className="min-w-0 flex-1 truncate text-ink-900" title={k.keyword}>{k.keyword}</span>
              <span className="shrink-0 text-xs tabular-nums text-ink-500">{volumeOf(k)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- 4 시즌 키워드

function SeasonProducts({ s }: { s: SeasonKeyword }) {
  if (s.products !== null && s.products > 0) return <span title={s.product_names.join(", ") || undefined}>{s.products}종</span>;
  if (s.products === 0) return <span className="text-ink-500">없음 · 들여올 후보</span>;
  return <span className="text-[11px] text-ink-500">{s.products_reason ?? "-"}</span>;
}

function SeasonBlock({ out }: { out: KeywordTrendOutput }) {
  const [group, setGroup] = useState<string | null>(null);
  if (out.season.length === 0) return <p className="text-sm text-ink-700">{out.season_reason ?? "시즌 키워드가 없어요."}</p>;
  const list = group === null ? out.season : out.season.filter((s) => s.group === group);
  const tab = (key: string | null, label: string) => (
    <button key={key ?? "__all"} type="button" aria-pressed={group === key} onClick={() => setGroup(key)}
      className={`rounded-md px-2.5 py-1 text-xs ring-1 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${group === key ? "bg-brand-50 font-bold text-brand-700 ring-brand-500" : "text-ink-500 ring-ink-100 hover:bg-ink-50"}`}>
      {label}
    </button>
  );
  return (
    <div className="space-y-2">
      <div role="group" aria-label="시즌 키워드 분류" className="flex flex-wrap gap-1">
        {tab(null, `전체 ${out.season.length}`)}
        {out.season_groups.map((g) => tab(g.name, `${g.name} ${g.count}`))}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-ink-100 text-left text-xs text-ink-500">
              <th className="whitespace-nowrap px-1 py-1.5 font-medium">키워드</th>
              <th className="whitespace-nowrap px-1 py-1.5 text-right font-medium">월 평균</th>
              <th className="whitespace-nowrap px-1 py-1.5 text-right font-medium">배수</th>
              <th className="whitespace-nowrap px-1 py-1.5 font-medium">몰리는 시기</th>
              <th className="whitespace-nowrap px-1 py-1.5 font-medium">상품</th>
            </tr>
          </thead>
          <tbody>
            {list.map((s) => (
              <tr key={s.keyword} className="border-b border-ink-100 align-top">
                <td className="min-w-[8rem] px-1 py-2 font-semibold text-ink-900">{s.keyword}</td>
                <td className="whitespace-nowrap px-1 py-2 text-right tabular-nums">
                  {s.month_volume !== null ? compactKo(s.month_volume) : <small className="text-[11px] text-ink-500">{s.volume_reason ?? KEY_LATER}</small>}
                </td>
                <td className="whitespace-nowrap px-1 py-2 text-right tabular-nums">{timesKo(s.ratio)}</td>
                <td className="whitespace-nowrap px-1 py-2 tabular-nums" title={s.peak_reason ?? undefined}>
                  {s.peak_from && s.peak_to ? `${s.peak_from} ~ ${s.peak_to}` : "-"}
                </td>
                <td className="min-w-[7rem] px-1 py-2"><SeasonProducts s={s} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {list.length === 0 && <p className="text-sm text-ink-500">이 분류의 키워드가 없어요.</p>}
    </div>
  );
}

// ------------------------------------------------------------------- 본문

/** state: 부모가 받은 keyword_trend 응답, win·onWindow: 일간/주간 (부모가 다시 받는다) */
export function KeywordTrend({ state, win, onWindow, onRetry }: {
  state: SkillState<KeywordTrendOutput>; win: KeywordWindow; onWindow: (w: KeywordWindow) => void; onRetry?: () => void;
}) {
  const tools = (
    <>
      <span className="text-xs text-ink-500">검색광고 · 데이터랩</span>
      <div role="group" aria-label="검색 트렌드 기간" className="flex gap-1">
        {WINDOWS.map((w) => (
          <button key={w.key} type="button" aria-pressed={win === w.key} onClick={() => w.key !== win && onWindow(w.key)}
            className={`rounded-md px-2.5 py-1 text-xs ring-1 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${win === w.key ? "bg-brand-50 font-bold text-brand-700 ring-brand-500" : "text-ink-500 ring-ink-100 hover:bg-ink-50"}`}>
            {w.label}
          </button>
        ))}
      </div>
    </>
  );
  const out = state.kind === "ok" ? state.out : null;
  const hint = out ? [
    out.pool_day ? `키워드 풀 ${out.pool_day}` : null,
    out.pool_size ? `${out.pool_size.toLocaleString("ko-KR")}개` : null,
    out.generated_at ? `계산 ${when(out.generated_at)}` : null,
  ].filter(Boolean).join(" · ") || undefined : undefined;
  return (
    <div className="space-y-4">
      <Panel id="data-trend-keyword-rising" title="급상승 검색어" hint={hint} tools={tools}>
        {state.kind === "loading" ? <Waiting what="검색 트렌드를 받는 중…" />
          : state.kind === "failed" ? <ErrorBox message={state.reason} onRetry={onRetry} />
          : <RisingBlock out={state.out} />}
      </Panel>
      {out && (
        <>
          <Panel id="data-trend-keyword-summary" title="AI 요약" tools={<span className="text-xs text-ink-500">AI · 숫자 검증</span>}>
            {out.summary ? (
              <div className="space-y-1">
                <p className="whitespace-pre-line text-sm leading-relaxed text-ink-700">{out.summary}</p>
                {out.summary_fallback && <p className="text-xs text-ink-500">AI 문장을 쓰지 못해 위 숫자로만 정리했어요.</p>}
              </div>
            ) : <p className="text-sm text-ink-500">{out.summary_reason ?? "요약이 없어요."}</p>}
          </Panel>
          <Panel id="data-trend-keyword-new" title="새로 나타난 키워드" hint="지난 30일에 없던 것">
            {out.new_keywords.length === 0 ? <p className="text-sm text-ink-700">{out.reason ?? "새로 나타난 키워드가 없어요."}</p> : (
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {out.new_keywords.map((d) => <NewDay key={d.day} d={d} />)}
              </div>
            )}
          </Panel>
          <Panel id="data-trend-keyword-season" title={out.season_month ? `${out.season_month}월 시즌 키워드` : "시즌 키워드"}>
            <SeasonBlock out={out} />
          </Panel>
          {out.limits.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-500">
              {out.limits.map((l) => <li key={l}>{l}</li>)}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
