import { useState, type ReactNode } from "react";
import { ErrorBox, Panel, TONE, Waiting } from "@/components/finance/bits";
import { useSkill, type SkillState } from "@/components/data/useSkill";
import { SummaryBody, SummaryCard } from "@/components/data/KeywordTrend";
import { dataAi, type MemeWindow, type TrendMeme, type TrendMemesOutput } from "@/lib/data";
import type { DetectView } from "@/lib/detectView";

// 트렌드 1 감지 하위 탭의 "요즘 유행·밈" (trend_memes, 오늘 가장 큰 화제 한 줄 바로 아래 플랫폼 칩 안). 영상 하나가 아니라 여러 채널에서 같이 늘어난 해시태그·구절 묶음.
// - 숫자·순위·판정은 서버 값 그대로. 설명 한 줄만 서버 LLM(숫자 없음)
// - 이번 주(7일 vs 직전 7일) / 오늘(어제 vs 지난 4주 같은 요일) 토글. 서버가 미리 계산한 결과를 읽는다
// - 대표 영상은 링크만 (썸네일을 걸지 않는다)

const WINDOWS: { key: MemeWindow; label: string; before: string; span: string }[] = [
  { key: "week", label: "이번 주", before: "지난주", span: "이번 주" },
  { key: "day", label: "오늘", before: "지난 4주 같은 요일", span: "어제" },
];

const AD: Record<TrendMeme["ad_material"], { label: string; tone: string }> = {
  ok: { label: "가능", tone: TONE.ok },
  caution: { label: "주의", tone: TONE.warn },
  excluded: { label: "뺌", tone: TONE.bad },
  unknown: { label: "분류 대기", tone: TONE.muted },
};

const times = (n: number) => `${n.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}배`;

function Ratio({ m }: { m: TrendMeme }) {
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
      {m.top_videos.map((v) => (
        <li key={v.video_id} className="text-xs">
          {v.video_url ? <a href={v.video_url} target="_blank" rel="noreferrer" className="text-brand-700 underline-offset-2 hover:underline">{v.title || v.video_id}</a> : (v.title || v.video_id)}
          <small className="block text-[11px] text-ink-500">{[v.channel_title, v.views !== null ? `조회 ${v.views.toLocaleString("ko-KR")}` : null].filter(Boolean).join(" · ")}</small>
        </li>
      ))}
    </ul>
  );
}

function criteriaLine(out: TrendMemesOutput, span: string, before: string) {
  const c = out.criteria;
  if (!c) return null;
  return `기준: ${span} ${c.min_channels}개 이상 채널 · 영상 ${c.min_videos}개 이상 · ${before}의 ${c.min_ratio}배 이상(${before}에 없었으면 새로 등장). 인물 이름 자체·뉴스·사건은 유행에서 뺍니다(기존 분류기 그대로).`;
}

/** 종합 칩 맨 위 요약 줄의 첫 칸: 1위 유행 하나 (서버 순위 그대로). 받는 중·실패·없음이면 짧은 문장 */
function TopMeme({ s, label, before }: { s: SkillState<TrendMemesOutput>; label: string; before: string }) {
  const m = s.kind === "ok" ? s.out.items[0] ?? null : null;
  const ratio = !m ? null : m.ratio_status === "known" && m.ratio !== null ? `${before} 대비 ${times(m.ratio)}` : m.ratio_status === "new" ? "새로 등장" : null;
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
function Table({ out, before, youtube }: { out: TrendMemesOutput; before: string; youtube: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-ink-100 text-left text-xs text-ink-500">
            <th className="whitespace-nowrap px-1 py-1.5 font-medium">유행</th>
            <th className="whitespace-nowrap px-1 py-1.5 text-right font-medium">퍼진 정도</th>
            <th className="whitespace-nowrap px-1 py-1.5 text-right font-medium">{before} 대비</th>
            {!youtube && <th className="whitespace-nowrap px-1 py-1.5 text-right font-medium">네이버 검색</th>}
            {!youtube && <th className="whitespace-nowrap px-1 py-1.5 font-medium">광고 재료</th>}
            <th className="whitespace-nowrap px-1 py-1.5 font-medium">{youtube ? "대표 쇼츠" : "대표 영상"}</th>
          </tr>
        </thead>
        <tbody>
          {out.items.map((m) => (
            <tr key={m.name} className="border-b border-ink-100 align-top">
              <td className="min-w-[12rem] px-1 py-2">
                <b className="text-ink-900">{m.name}</b>
                <small className="block text-xs text-ink-500">{m.description ?? m.description_reason ?? ""}</small>
              </td>
              <td className="whitespace-nowrap px-1 py-2 text-right tabular-nums">채널 {m.channels}곳<small className="block text-[11px] text-ink-500">영상 {m.videos}개</small></td>
              <td className="whitespace-nowrap px-1 py-2 text-right tabular-nums"><Ratio m={m} /></td>
              {!youtube && <td className="whitespace-nowrap px-1 py-2 text-right tabular-nums"><Search m={m} /></td>}
              {!youtube && <td className="px-1 py-2"><Material m={m} /></td>}
              <td className="px-1 py-2"><Videos m={m} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** focus: 감지 플랫폼 칩 (all = 종합: 요약 줄 + 전체 칸, youtube = 쇼츠 칸 중심). 기획 넘기기 버튼·인스타·검색 플랫폼 칸은 그리지 않는다.
 *  summaryCards: 종합 요약 줄에서 1위 유행 칸 뒤에 붙일 칸 (검색 급상승 1위·이달 시즌 1위, 부모가 받은 keyword_trend) */
export function TrendMemes({ focus = "all", summaryCards }: { focus?: DetectView; summaryCards?: ReactNode }) {
  const youtube = focus === "youtube";
  const [win, setWin] = useState<MemeWindow>("week");
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
        <div className="mb-3 grid grid-cols-2 gap-2 lg:grid-cols-3">
          <TopMeme s={s} label={meta.label} before={meta.before} />
          {summaryCards}
        </div>
      )}
      {s.kind === "loading" ? <Waiting what="유행을 받는 중…" />
        : s.kind === "failed" ? <ErrorBox message={s.reason} onRetry={res.reload} />
        : (
          <div className="space-y-2">
            {s.out.items.length ? <Table out={s.out} before={meta.before} youtube={youtube} /> : (
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
          </div>
        )}
    </Panel>
  );
}
