import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router";
import { Chip, ErrorBox, Panel, TONE, Waiting, useResource } from "@/components/finance/bits";
import { PreparingChip } from "@/components/Preparing";
import { CampaignEffect } from "@/components/data/CampaignEffect";
import { PromoConcepts } from "@/components/data/PromoConcepts";
import { TrendPopular, TrendSummary, materialOf } from "@/components/data/TrendPopular";
import { okOut, useSkill } from "@/components/data/useSkill";
import { ContentLoop, type LoopTrendState } from "@/components/data/ContentLoop";
import { LOOP_STAGES, TREND_TABS, trendTabFromHash, trendTabHash, trendTabOfStage, type LoopProbe, type LoopStage, type TrendTab } from "@/lib/contentLoop";
import {
  DATA_SERVER, TREND_MAX_GROUPS, dataAi, evidenceLines, kpiLive,
  type PromoConceptsOutput, type TrendCandidatesOutput, type TrendPopularOutput, type TrendScanOutput, type TrendVerdict,
  type TrendingSearchEvidence, type YouTubeTagEvidence,
} from "@/lib/data";

// 트렌드&콘텐츠 화면 (/data/trend): 콘텐츠 루프 4단계와 같은 하위 탭 4개 (단계 정의는 lib/contentLoop.ts 한 곳).
// - 주소에 하위 탭을 남긴다: /data/trend#detect · #recommend · #handoff · #result (없으면 감지). 옛 #trend/<단계> 주소는 Data.tsx가 새 주소로 옮긴다
// - 루프 띠의 단계를 누르면 그 단계가 속한 하위 탭으로 간다. 루프 띠는 모든 하위 탭 위에 있다
// - 1 감지: 요약 카드 3개(오늘 가장 큰 화제·기획안에 쓸 수 있는 트렌드·다가오는 시즌) → 카테고리 버튼(지금 인기, 고른 칸만 그림)
//   → 맨 아래 급상승 한 줄 목록(trend_scan·trend_candidates). 그래프 없음. YouTube 속도 목록은 화면을 줄이려고 뺐다
// - trend_popular·promo_concepts는 이 화면이 한 번 받아 1단계 요약·칩 색과 2단계 기획안이 같이 쓴다 (useSkill)
// - 2 추천: 숏츠 광고 기획안 초안(promo_concepts, 왼쪽 형식 막대 + shorts_stats 표본 한계). 처음 열 때 그리고, 그 뒤에는 하위 탭을 오가도 남게 숨기기만 한다
// - 3 넘김: "기획팀이 초안을 확정하고 마케팅이 제작·게시하는 단계 (준비 중)". 루프 띠와 같은 단계 설명 문장만 보여 준다(숫자·예시 없음)
// - 4 성과(게시·성과 측정, 다음 추천 반영은 준비 중): 캠페인 효과(campaign_effect). 측정 결과가 남게 숨기기만 한다
//
// 1단계 급상승 한 줄
// - 주제 · "평소의 N배"(서버 배수) · 판정에 쓴 마지막 날. 순서는 배수 큰 순. 그래프는 없다
// - "광고 재료 아님" 칩: 기획안 재료에서 뺀 주제(promo_concepts skipped_trends, 서버 이유)이거나
//   회사 "추천 안 함" 분류(trend_popular brand_safe = false)일 때만
// - 기본 판정(입력 비움) = 회사 기본 키워드(trend_scan) + YouTube 인기 태그 후보(trend_candidates, 후보마다 네이버 판정).
//   같은 이름이면 YouTube 근거가 있는 후보 쪽 판정을 쓴다. 직접 입력(접힘)이면 trend_scan만 부른다
// - 판정마다 서버가 LLM으로 문장을 쓰므로 30초 새로고침을 따르지 않는다. 처음 한 번 + "판정"·"다시 판정"을 누를 때만 부른다

const fixed = (v: number | null | undefined, digits = 2) =>
  typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("ko-KR", { maximumFractionDigits: digits }) : null;

/** "2026-10-26" → "10-26" */
const shortDay = (day: string | null | undefined) => (day ? day.slice(5) : "");

/** 입력 "고양이 밈, 챌린지" → 그룹 이름 목록 (쉼표로 나눔, 빈 칸·중복 제거) */
function parseGroups(text: string): string[] {
  return [...new Set(text.split(/[,，]/).map((t) => t.trim()).filter(Boolean))];
}

// ------------------------------------------------------------------- 급상승 한 줄

/** 화면에 그릴 판정 하나. youtube가 있으면 YouTube 후보에서 온 것 */
/** risingNames: 이름마다 따로 판정해 급상승인 이름 (별칭이 있는 YouTube 후보만, 서버 값) */
/** trending: 인기 검색어 근거 (서버 값). youtube 없이 trending만 있으면 인기 검색어에서만 나온 후보 */
type Entry = { v: TrendVerdict; youtube?: YouTubeTagEvidence; trending?: TrendingSearchEvidence | null; aliases?: string[]; autoGrouped?: string[]; risingNames?: string[] };

/** 급상승 한 줄: 주제 · 평소의 N배(서버 배수) · 광고 재료 아님(서버 이유) · 판정에 쓴 마지막 날 */
function RisingRow({ v, rank, notAd }: { v: TrendVerdict; rank: number; notAd: string | null }) {
  const day = v.evidence.latest_period;
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5">
      <span className="w-6 text-sm font-semibold text-ink-500">{rank}</span>
      <span className="font-semibold text-ink-900">{v.group_name}</span>
      <span className="text-sm font-semibold text-red-700">평소의 {fixed(v.ratio) ?? "-"}배</span>
      {notAd != null && (
        <span className={`chip max-w-full text-xs ${TONE.muted}`} title={notAd || undefined}>
          광고 재료 아님{notAd ? <span className="ml-1 truncate">· {notAd}</span> : null}
        </span>
      )}
      {day && <span className="ml-auto text-xs text-ink-500">{shortDay(day)} 기준</span>}
    </li>
  );
}

// ------------------------------------------------------------------- 1단계 본문

/** 기본 판정이면 trend_scan 결과와 YouTube 후보를 합친다. 같은 이름(대소문자 무시)은 후보 쪽을 쓴다 */
function mergeEntries(out: TrendScanOutput, cands: TrendCandidatesOutput | null): Entry[] {
  // 이름 하나가 올라 급상승이 된 후보(naver_from = name)는 판정의 group_name이 그 이름이라, 카드 제목은 후보 이름으로 둔다
  const fromYoutube: Entry[] = (cands?.candidates ?? []).map((c) => ({
    v: { ...c.naver, group_name: c.keyword }, youtube: c.youtube, trending: c.trending, aliases: c.aliases, autoGrouped: c.auto_grouped, risingNames: c.rising_names,
  }));
  // 인기 검색어에서만 나온 후보 (YouTube 후보와 겹치는 것은 서버가 이미 그 후보에 붙였다)
  const fromTrending: Entry[] = (cands?.trending_candidates ?? []).map((c) => ({
    v: { ...c.naver, group_name: c.keyword }, trending: c.trending, aliases: c.aliases, risingNames: c.rising_names,
  }));
  const cand = [...fromYoutube, ...fromTrending];
  const names = new Set(cand.map((e) => e.v.group_name.toLowerCase()));
  return [...out.results.filter((r) => !names.has(r.group_name.toLowerCase())).map((v) => ({ v })), ...cand];
}

type CandState =
  | { kind: "off" }
  | { kind: "loading" }
  | { kind: "ok"; out: TrendCandidatesOutput }
  | { kind: "failed"; reason: string };

/** 광고 재료가 아니면 서버 이유(없으면 빈 문자열), 재료이거나 모르면 null */
function notAdOf(e: Entry, popular: TrendPopularOutput | null, promo: PromoConceptsOutput | null): string | null {
  const names = [e.v.group_name, ...(e.aliases ?? [])];
  const keys = names.map((n) => n.trim().toLowerCase());
  const topic = popular?.items.find((t) => [t.keyword, ...t.aliases].some((n) => keys.includes(n.trim().toLowerCase()))) ?? null;
  const m = materialOf(names, promo, topic);
  return m.kind === "excluded" ? (m.reason ?? "") : null;
}

function Result({ entries, headline, popular, promo, range }: {
  entries: Entry[]; headline: string | null; popular: TrendPopularOutput | null; promo: PromoConceptsOutput | null; range: string;
}) {
  const rising = entries.filter((e) => e.v.status === "rising").sort((a, b) => (b.v.ratio ?? 0) - (a.v.ratio ?? 0));
  return (
    <section aria-labelledby="trend-rising-title" className="card space-y-2 p-4 sm:p-5">
      <div>
        <p className="panel-title">검색 급상승</p>
        <h2 id="trend-rising-title" className="mt-1 text-lg font-bold text-ink-900">{headline ?? `급상승 ${rising.length}개`}</h2>
        <p className="text-xs text-ink-500">{range}</p>
      </div>
      {rising.length === 0 ? (
        <p className="text-sm text-ink-500">이번 판정 구간에 급상승한 주제가 없어요.</p>
      ) : (
        <ol className="divide-y divide-ink-100">
          {rising.map((e, i) => (
            <RisingRow key={`${e.youtube ? "yt" : e.trending ? "tr" : "kw"}-${e.v.group_name}`} v={e.v} rank={i + 1} notAd={notAdOf(e, popular, promo)} />
          ))}
        </ol>
      )}
    </section>
  );
}

// ------------------------------------------------------------------- 준비 중 하위 탭 (2·3·5)

/** 단계 정의(lib/contentLoop.ts)의 담당·설명·할 일·필요한 것만 보여 준다. 숫자·예시는 없다 */
function PreparingStage({ stage }: { stage: LoopStage }) {
  return (
    <Panel id={`data-trend-${stage.tab}`} title={`${stage.no}. ${stage.title}`} hint={`담당: ${stage.owner}`}
      tools={<>
        <PreparingChip />
        {stage.status.label !== "준비 중" && <Chip label={stage.status.label} tone={TONE[stage.status.tone]} />}
      </>}>
      <div className="space-y-3 text-sm leading-relaxed text-ink-700">
        <p>{stage.description}</p>
        {stage.todo && <p><b className="text-ink-900">할 일</b> · {stage.todo}</p>}
        {stage.needs && stage.needs.length > 0 && (
          <div>
            <p className="font-semibold text-ink-900">필요한 것</p>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              {stage.needs.map((n) => <li key={n}>{n}</li>)}
            </ul>
          </div>
        )}
        <p className="text-xs text-ink-500">아직 이 단계의 데이터가 없어서 숫자를 보여 주지 않아요.</p>
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------------- 트렌드 탭

/** dataUp: 화면(Data.tsx)이 이미 받은 데이터 AI 상태 확인(dataAi.health) 결과. null이면 아직 모름 */
export function TrendScan({ dataUp = null }: { dataUp?: boolean | null }) {
  const location = useLocation();
  const navigate = useNavigate();
  const tab = trendTabFromHash(location.hash);
  const openTab = useCallback((t: TrendTab) => {
    navigate({ pathname: location.pathname, search: location.search, hash: trendTabHash(t) }, { replace: true });
  }, [navigate, location.pathname, location.search]);
  const openStage = useCallback((s: LoopStage) => openTab(trendTabOfStage(s)), [openTab]);
  const stageOf = (key: string) => LOOP_STAGES.find((s) => s.tab === key)!;

  const [text, setText] = useState("");
  const [groups, setGroups] = useState<string[]>([]);
  const [inputError, setInputError] = useState<string | null>(null);
  const [run, setRun] = useState(0);
  const [campaignProbe, setCampaignProbe] = useState<LoopProbe>("idle");
  // 지금 인기·기획안은 한 번 받아 1단계 요약·칩 색과 2단계가 같이 쓴다
  const popularRes = useSkill(() => dataAi.trendPopular());
  const promoRes = useSkill((n) => dataAi.promoConcepts(n > 0));
  const popularOut = okOut(popularRes.state);
  const promoOut = okOut(promoRes.state);
  const promoProbe: LoopProbe = !kpiLive ? "failed"
    : promoRes.state.kind === "ok" ? "ok" : promoRes.state.kind === "failed" ? "failed" : "checking";
  // 2단계 이후 하위 탭은 처음 열 때 그린다(1단계를 빨리 열려고). 그 뒤에는 숨기기만 한다
  const [seen, setSeen] = useState<ReadonlySet<string>>(() => new Set([tab.key]));
  useEffect(() => {
    setSeen((s) => (s.has(tab.key) ? s : new Set([...s, tab.key])));
  }, [tab.key]);

  const res = useResource(`trend-${groups.join("|")}`, () => dataAi.trendScan(groups).then((r) => ({ data: r, meta: { mock: false } })), run);
  // YouTube 후보는 기본 판정(입력 비움)에서만 부른다. 직접 입력이면 부르지 않는다
  const useCandidates = groups.length === 0;
  const [cands, setCands] = useState<CandState>({ kind: "off" });
  useEffect(() => {
    if (!kpiLive || !useCandidates) {
      setCands({ kind: "off" });
      return;
    }
    let live = true;
    setCands({ kind: "loading" });
    dataAi.trendCandidates()
      .then((r) => live && setCands({ kind: "ok", out: r.output }))
      .catch((e: Error) => live && setCands({ kind: "failed", reason: e.message }));
    return () => { live = false; };
  }, [useCandidates, run]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const next = parseGroups(text);
    if (next.length > TREND_MAX_GROUPS) {
      setInputError(`한 번에 ${TREND_MAX_GROUPS}개 그룹까지 판정해요 (지금 ${next.length}개).`);
      return;
    }
    setInputError(null);
    setGroups(next);
    setRun((n) => n + 1);
  };

  const out = kpiLive ? res.env?.data.output ?? null : null;
  const candOut = cands.kind === "ok" ? cands.out : null;
  const entries = out ? mergeEntries(out, candOut) : [];
  // 서버 headline은 trend_scan 결과만 센 문장이라, YouTube 후보를 합친 기본 판정에서는 쓰지 않는다
  const headline = candOut?.candidates.length ? null : evidenceLines(res.env?.data.evidence?.data_summary).headline;
  const windowFrom = out?.results.find((r) => r.window_from)?.window_from ?? null;
  const youtubeNote = cands.kind === "failed" ? cands.reason
    : candOut?.status === "unknown" ? (candOut.unknown_reason ?? "서버가 이유를 주지 않았어요") : undefined;
  // 콘텐츠 루프 1단계: 이 탭이 이미 받은 응답의 판정 개수 (다시 부르지 않는다)
  const loopTrend: LoopTrendState = !kpiLive
    ? { kind: "unknown", reason: "데이터 AI 토큰이 없어 트렌드를 받지 않아요" }
    : out
      ? {
          kind: "counts",
          counts: {
            rising: entries.filter((e) => e.v.status === "rising").length,
            normal: entries.filter((e) => e.v.status === "normal").length,
            unknown: entries.filter((e) => e.v.status === "unknown").length,
            youtube: candOut?.status === "known" ? candOut.candidates.length : undefined,
            confirmed: entries.filter((e) => e.v.status === "rising" && e.youtube).length,
            youtubeNote,
          },
        }
      : res.loading
        ? { kind: "loading" }
        : { kind: "unknown", reason: res.error ?? "판정 결과를 받지 못했어요" };
  // 콘텐츠 루프 1단계 칩: 마지막 trend_scan 응답이 정상인지 (다시 받는 중이면 직전 결과를 유지)
  const trendProbe: LoopProbe = !kpiLive ? "failed"
    : res.loading ? (out && !res.error ? "ok" : "checking")
    : (out && !res.error ? "ok" : "failed");
  // 콘텐츠 루프 4단계 칩: 측정 전(측정 대기)이라도 데이터 AI 상태 확인이 실패했으면 "연결 확인 필요"
  const loopCampaignProbe: LoopProbe = !kpiLive ? "failed"
    : campaignProbe === "idle" && dataUp === false ? "failed" : campaignProbe;

  const detect = !kpiLive ? (
    <Panel id="data-trend" title="트렌드 감지">
      <p className="text-sm text-ink-500">데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 트렌드를 받지 않아요. backend/의 scripts/run_web.sh로 화면을 켜면 토큰이 들어갑니다.</p>
    </Panel>
  ) : (
    <div className="space-y-2">
      {!res.env ? (
        res.loading ? <Waiting what="급상승을 판정하는 중…" /> : (
          <ErrorBox message={`${res.error ?? "판정 결과를 받지 못했습니다"}. ${DATA_SERVER}가 켜져 있는지, 네이버 키(NAVER_HUB_CLIENT_ID)가 있는지 확인해 주세요.`} onRetry={res.reload} />
        )
      ) : (
        <>
          {res.error && <ErrorBox message={`새로 판정하지 못해 이전 결과를 보여 줍니다. ${res.error}`} onRetry={res.reload} />}
          {out && (
            <Result entries={entries} headline={headline} popular={popularOut} promo={promoOut}
              range={`네이버 검색 ${windowFrom ?? out.start_date} ~ ${out.end_date} · 평소 = 그 전 56일${groups.length ? ` · 직접 고른 ${groups.length}개` : ""}`} />
          )}
        </>
      )}
      {youtubeNote && <p className="text-xs text-amber-700">YouTube 후보를 받지 못했어요. {youtubeNote}</p>}
      <details className="text-sm">
        <summary className="cursor-pointer text-ink-700">키워드 직접 판정 · 다시 판정</summary>
        <form onSubmit={submit} className="mt-2 flex flex-wrap items-start gap-2" aria-label="키워드 직접 판정">
          <div className="min-w-0 flex-1 sm:max-w-md">
            <label htmlFor="trend-groups" className="sr-only">키워드 직접 판정</label>
            <input id="trend-groups" className="field" value={text} onChange={(e) => setText(e.target.value)}
              placeholder={`쉼표로 최대 ${TREND_MAX_GROUPS}개 (비우면 기본 키워드 + YouTube 후보)`} aria-describedby="trend-groups-help" />
            <p id="trend-groups-help" className={`mt-1 text-[11px] ${inputError ? "text-red-700" : "text-ink-500"}`}>
              {inputError ?? "키워드마다 네이버 검색량을 한 번씩 봐요."}
            </p>
          </div>
          <button type="submit" className="btn-primary" disabled={res.loading}>판정</button>
          <button type="button" className="btn-ghost" disabled={res.loading} onClick={() => setRun((n) => n + 1)}>다시 판정</button>
        </form>
      </details>
    </div>
  );

  return (
    <div className="space-y-5">
      <ContentLoop trend={loopTrend} probes={{ trend_scan: trendProbe, promo_concepts: promoProbe, campaign_effect: loopCampaignProbe }} active={stageOf(tab.stages[0]).no} onSelect={openStage} />

      {/* 하위 탭 4개: 감지 → 추천 → 넘김 → 성과 (루프 4단계와 같다, lib/contentLoop.ts TREND_TABS) */}
      <div role="tablist" aria-label="트렌드&콘텐츠 단계" className="flex w-full gap-1 overflow-x-auto rounded-2xl bg-surface p-1 ring-1 ring-ink-100">
        {TREND_TABS.map((t, i) => (
          <button key={t.key} type="button" role="tab" id={`trend-sub-${t.key}`} aria-selected={tab.key === t.key} aria-controls={`trend-sub-panel-${t.key}`}
            onClick={() => openTab(t)}
            className={`flex-1 whitespace-nowrap rounded-xl px-3 py-2 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${tab.key === t.key ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>
            {i + 1} {t.title}
          </button>
        ))}
      </div>

      {TREND_TABS.map((t) => (
        <div key={t.key} role="tabpanel" id={`trend-sub-panel-${t.key}`} aria-labelledby={`trend-sub-${t.key}`} hidden={tab.key !== t.key}>
          {t.key === "detect" ? (
            <div className="space-y-5">
              {kpiLive && <TrendSummary popular={popularRes.state} promo={promoRes.state} />}
              {kpiLive && <TrendPopular popular={popularRes.state} promo={promoOut} onReload={popularRes.reload} />}
              {detect}
            </div>
          ) : !seen.has(t.key) ? null : t.key === "recommend" ? (
            // 처음 열 때 그리고, 그 뒤에는 하위 탭을 오가도 남도록 숨기기만 한다
            <div className="space-y-5">
              {/* 숏츠 광고 기획안 초안 (promo_concepts, 왼쪽 숏츠 형식 막대 + 표본 한계 shorts_stats) */}
              <PromoConcepts state={promoRes.state} onRegenerate={promoRes.reload} />
            </div>
          ) : t.key === "handoff" ? (
            // 마케팅·영상 제작으로 넘기기: 아직 준비 중
            <PreparingStage stage={stageOf("handoff")} />
          ) : (
            // 성과: 캠페인 효과. 측정 결과가 하위 탭을 오가도 남게 숨기기만 한다
            <CampaignEffect onProbe={setCampaignProbe} />
          )}
        </div>
      ))}
    </div>
  );
}
