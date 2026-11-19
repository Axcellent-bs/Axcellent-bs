import { useState, type ReactNode } from "react";
import { ErrorBox, Panel, Waiting } from "@/components/finance/bits";
import { when } from "@/components/data/Sources";
import type { SkillState } from "@/components/data/useSkill";
import { RisingKeywords } from "@/components/data/RisingKeywords";
import { judgedLine, topRising } from "@/lib/keywordTrendView";
import type { KeywordTrendOutput, KeywordWindow, NewKeyword, NewKeywordDay, SeasonKeyword } from "@/lib/data";

// 트렌드 1 감지 하위 탭 "검색" 칩의 검색 트렌드 · 도서 분야 (keyword_trend). 서버가 미리 계산한 결과를 읽기만 한다.
// - 블록 4개: 급상승 검색어(RisingKeywords: 동그란 단어 구름 · 급상승 카드 · 오르는 중 · 나머지 표) · AI 요약 · 새로 나타난 키워드(최근 4일) · 이달 시즌 키워드(분류 탭)
// - 숫자·판정은 서버 값 그대로. 검색광고·확산 키가 없으면 급상승 패널 위에 한 줄로만 알린다(줄마다 반복하지 않음)
// - 받기(useSkill)는 부모(PlatformViews)가 한다. 종합 칩 요약 칸(KeywordSummaryCards)도 같은 응답·같은 판정(level rising 첫 줄)을 쓴다
// - 단어 구름 자리는 lib/wordOrb.ts 순수 함수. 라이브러리 없음

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
  // 급상승 패널과 같은 판정: level rising 첫 줄 (직전 판정 재사용이면 "(MM-DD 판정)")
  const topHit = topRising(out);
  const top = topHit?.row ?? null;
  const season = out.season[0] ?? null;
  return (
    <>
      <SummaryCard label="검색 급상승 1위">
        {top ? (
          <SummaryBody title={top.keyword} lines={[
            top.ratio !== null ? `평소 ${timesKo(top.ratio)}` : null,
            top.products !== null ? (top.products > 0 ? `상품 ${top.products}종` : "상품 없음") : null,
            topHit?.stale,
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

// 본문은 RisingKeywords.tsx (동그란 단어 구름 · 급상승 카드 · 오르는 중 · 나머지 표, 11/20 v3)

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
  // 머리 줄: "MM-DD HH:mm 판정 · 데이터랩 N번" (예전 응답도 계산 시각으로 같은 모양)
  const hint = out ? (judgedLine(out) ?? (out.generated_at ? `계산 ${when(out.generated_at)}` : undefined)) : undefined;
  return (
    <div className="space-y-4">
      <Panel id="data-trend-keyword-rising" title="급상승 검색어" hint={hint} tools={tools}>
        {state.kind === "loading" ? <Waiting what="검색 트렌드를 받는 중…" />
          : state.kind === "failed" ? <ErrorBox message={state.reason} onRetry={onRetry} />
          : <RisingKeywords out={state.out} win={win} />}
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
