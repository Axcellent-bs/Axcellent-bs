import { Suspense, lazy, useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { ErrorBox, Panel, TONE, Waiting } from "@/components/finance/bits";
import { PlanningHandoff, proposalsOf, usePlanningProposals } from "@/components/data/PlanningHandoff";
import { PlanningProposals, ReportInsights, useInsightReports } from "@/components/data/ReportInsights";
import { FilterButton, TrendHeadline, materialOf } from "@/components/data/TrendPopular";
import { DETECT_VIEWS, detectViewFromHash, hashWithView, type DetectView } from "@/lib/detectView";
import { VideoExplorer } from "@/components/data/VideoExplorer";
import { TrendMemes } from "@/components/data/TrendMemes";
import { KeywordSummaryCards, KeywordTrend } from "@/components/data/KeywordTrend";
import { IgAudioSummaryCard, InstagramAudio, KEY_LATER, igKeyMissing } from "@/components/data/InstagramAudio";
import YoutubeMemeKeywordsSetup from "@/components/data/YoutubeMemeKeywordsSetup";
import { okOut, useSkill, type SkillState } from "@/components/data/useSkill";
import { Basis, Sources, when } from "@/components/data/Sources";
import { PROBE_STATUS, TREND_TABS, trendTabFromHash, trendTabHash, type LoopProbe, type TrendTab } from "@/lib/contentLoop";
import { activeCount, dataProposals, insightFilterFromHash, insightFilterHash, type InsightFilter } from "@/lib/handoffView";
import {
  DATA_SERVER, TREND_MAX_GROUPS, dataAi, evidenceLines, kpiLive,
  type A2AResult, type CampaignEffectOutput, type KeywordWindow, type PromoConceptsOutput, type TrendInsightBriefOutput, type TrendPopularOutput, type TrendScanOutput,
  type TrendVerdict, type VideoPerformanceVideo,
} from "@/lib/data";
import { campaignRows, resultTabTag } from "@/lib/resultCampaigns";
import { fadedChip } from "@/lib/keywordTrendView";

// 감지 탭이 아닌 하위 탭·인스타 칩 칸은 처음 열 때 받는다. 첫 화면(감지)은 이 코드 없이 먼저 그린다 (11/25 Lighthouse)
const CampaignEffect = lazy(() => import("@/components/data/CampaignEffect").then((m) => ({ default: m.CampaignEffect })));
const VideoPerformance = lazy(() => import("@/components/data/VideoPerformance").then((m) => ({ default: m.VideoPerformance })));
const PromoConcepts = lazy(() => import("@/components/data/PromoConcepts").then((m) => ({ default: m.PromoConcepts })));
const InsightBrief = lazy(() => import("@/components/data/InsightBrief").then((m) => ({ default: m.InsightBrief })));
const DataSources = lazy(() => import("@/components/data/DataSources").then((m) => ({ default: m.DataSources })));
const TrendRequest = lazy(() => import("@/components/data/TrendRequest").then((m) => ({ default: m.TrendRequest })));
const InstagramRadar = lazy(() => import("@/components/data/InstagramRadar").then((m) => ({ default: m.InstagramRadar })));
const InstagramRadarSetup = lazy(() => import("@/components/data/InstagramRadarSetup"));
const InstagramMemo = lazy(() => import("@/components/data/InstagramMemo").then((m) => ({ default: m.InstagramMemo })));

// 트렌드&콘텐츠 화면 (/data/trend, 11/20 v3): 하위 탭 감지 → 분석 의뢰 → 인사이트 → 기획 전달 → 성과 + 탭 줄 오른쪽 끝 "⚙ 데이터 출처" (정의는 lib/contentLoop.ts 한 곳).
// - 주소에 하위 탭을 남긴다: /data/trend#detect · #request(보고서는 #request?id=<report_id>) · #recommend(필터 ?from=report|brief) · #handoff · #result · #sources.
//   없으면 감지. 옛 #trend/<단계> 주소는 Data.tsx가 새 주소로 옮긴다
// - 머리: 제목 · 설명 접힘 · YouTube 인기 기준 시각. 예전 단계 줄(① 감지 → … ④ 성과)은 탭과 같은 내용이라 뺐다
// - 탭 옆: 기획 전달 = 진행 중 제안 수(0이면 숨김), 성과 = 지금 단계 칩 문구(측정 대기 등)
// - 1 감지: 맨 위 한 줄(오늘 가장 큰 화제, trend_popular) → 플랫폼 칩(#detect?view=all|youtube|instagram|search, lib/detectView.ts)
//   인스타 = 인스타 레이더 → 인기 오디오 → 인스타 트렌드 메모. 키가 없으면 레이더·오디오 대신 한 줄
//   종합 = 요약 줄(1위 유행·검색 급상승 1위·인스타 오디오) + 요즘 유행·밈 / 유튜브 = 유행 표(쇼츠 칸 중심) + 영상 탐색 /
//   검색 = 검색 트렌드(keyword_trend: 급상승 검색어 동그란 구름·카드, KeywordTrend) + 맨 아래 "키워드 직접 판정" 입력.
//   예전 "검색 급상승 N개" 블록과 기본 trend_scan·trend_candidates 호출은 뺐다(급상승 판정은 keyword_trend 하나). 직접 입력했을 때만 trend_scan(groups)
// - 2 분석 의뢰: TrendRequest (의뢰 넣기 · 지난 의뢰 · 보고서)
// - 3 인사이트: 필터(전체 · 분석 의뢰 보고서 N · 매일 브리프 N) → 보고서 인사이트 카드(ReportInsights) → 인사이트 브리프(trend_insight_brief, 카드마다 전달 상태 칩)
//   + 맨 아래 참고용 예시(promo_concepts 기획안 카드, 접힘)
// - 4 기획 전달: 기획 AI 기록(proposals from data)의 사슬 (PlanningHandoff). 같은 기록을 인사이트 전달 칩·탭 배지가 쓴다 (30초마다 한 번 받는다)
// - 5 성과: 게시 영상 성과 + 캠페인 효과(campaign_effect). 측정 결과가 남게 숨기기만 한다
// - 2번째 이후 하위 탭은 처음 열 때 그리고, 그 뒤에는 숨기기만 한다

const fixed = (v: number | null | undefined, digits = 2) =>
  typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("ko-KR", { maximumFractionDigits: digits }) : null;

/** "2026-10-26" → "10-26" */
const shortDay = (day: string | null | undefined) => (day ? day.slice(5) : "");

/** 입력 "고양이 밈, 챌린지" → 그룹 이름 목록 (쉼표로 나눔, 빈 칸·중복 제거) */
function parseGroups(text: string): string[] {
  return [...new Set(text.split(/[,，]/).map((t) => t.trim()).filter(Boolean))];
}

// ------------------------------------------------------------------- 키워드 직접 판정 결과 (trend_scan(groups))

/** 판정 근거 줄 (서버 값이 있는 것만) */
function risingBasis(v: TrendVerdict) {
  const ev = v.evidence;
  const rows: { source: string; body: ReactNode }[] = [];
  if (ev.latest_value != null && ev.baseline_value != null) {
    rows.push({
      source: "네이버 데이터랩 검색어 트렌드",
      body: `${shortDay(ev.latest_period)} 값 ${fixed(ev.latest_value)} ÷ 평소 ${fixed(ev.baseline_value)} (전 ${ev.baseline_points}일 중앙값) · 값은 검색 건수가 아닌 상대값`,
    });
  }
  return rows;
}

/** 광고 재료가 아니면 서버 이유(없으면 빈 문자열), 재료이거나 모르면 null */
function notAdOf(v: TrendVerdict, popular: TrendPopularOutput | null, promo: PromoConceptsOutput | null): string | null {
  const names = [v.group_name];
  const keys = names.map((n) => n.trim().toLowerCase());
  const topic = popular?.items.find((t) => [t.keyword, ...t.aliases].some((n) => keys.includes(n.trim().toLowerCase()))) ?? null;
  const m = materialOf(names, promo, topic);
  return m.kind === "excluded" ? (m.reason ?? "") : null;
}

/** 직접 판정 한 줄: 주제 · 평소의 N배(서버 배수) · 판정(급상승·평범·판정 못 함) · 광고 재료 아님 · 판정에 쓴 마지막 날 + 근거 */
function JudgedRow({ v, notAd }: { v: TrendVerdict; notAd: string | null }) {
  const day = v.evidence.latest_period;
  const faded = v.status === "rising" ? fadedChip(v.evidence) : null;
  const label = faded ? "지난 급상승" : v.status === "rising" ? "급상승" : v.status === "normal" ? "평범" : "판정 못 함";
  return (
    <li className="space-y-1.5 py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-semibold text-ink-900">{v.group_name}</span>
        <span className={`text-sm font-semibold ${v.status === "rising" && !faded ? "text-red-700" : "text-ink-700"}`}>
          {v.ratio != null ? `평소의 ${fixed(v.ratio)}배` : "배수 모름"} · {label}
        </span>
        {faded && <span className={`chip text-xs ${TONE.warn}`} title="정점이 지나고 지금 값은 평소 범위 안이에요">{faded}</span>}
        {notAd != null && (
          <span className={`chip max-w-full text-xs ${TONE.muted}`} title={notAd || undefined}>
            광고 재료 아님{notAd ? <span className="ml-1 truncate">· {notAd}</span> : null}
          </span>
        )}
        {day && <span className="ml-auto text-xs text-ink-500">{shortDay(day)} 기준</span>}
      </div>
      <Basis rows={risingBasis(v)} />
    </li>
  );
}

type ScanLoad = { kind: "idle" } | { kind: "loading" } | { kind: "ok"; res: A2AResult<TrendScanOutput> } | { kind: "failed"; reason: string };

/** "키워드 직접 판정": 입력했을 때만 trend_scan(groups)을 부른다 (기본 판정은 위 급상승 검색어 패널이 한다) */
function DirectJudge({ popular, promo }: { popular: TrendPopularOutput | null; promo: PromoConceptsOutput | null }) {
  const [text, setText] = useState("");
  const [groups, setGroups] = useState<string[]>([]);
  const [inputError, setInputError] = useState<string | null>(null);
  const [run, setRun] = useState(0);
  const [scan, setScan] = useState<ScanLoad>({ kind: "idle" });
  useEffect(() => {
    if (!kpiLive || groups.length === 0) return;
    let live = true;
    setScan({ kind: "loading" });
    dataAi.trendScan(groups)
      .then((r) => { if (live) setScan({ kind: "ok", res: r }); })
      .catch((e: Error) => { if (live) setScan({ kind: "failed", reason: e.message }); });
    return () => { live = false; };
  }, [groups, run]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const next = parseGroups(text);
    if (next.length === 0) {
      setInputError("판정할 키워드를 쉼표로 적어 주세요.");
      return;
    }
    if (next.length > TREND_MAX_GROUPS) {
      setInputError(`한 번에 ${TREND_MAX_GROUPS}개 그룹까지 판정해요 (지금 ${next.length}개).`);
      return;
    }
    setInputError(null);
    setGroups(next);
    setRun((n) => n + 1);
  };
  const out = scan.kind === "ok" ? scan.res.output : null;
  const headline = scan.kind === "ok" ? evidenceLines(scan.res.evidence?.data_summary).headline : null;
  const windowFrom = out?.results.find((r) => r.window_from)?.window_from ?? null;
  // 끝난 급상승(faded)은 뒤로, 그 안에서 배수 큰 순
  const results = out ? [...out.results].sort((a, b) => Number(!!a.evidence.faded) - Number(!!b.evidence.faded) || (b.ratio ?? -1) - (a.ratio ?? -1)) : [];
  return (
    <details className="card p-4 text-sm sm:p-5">
      <summary className="cursor-pointer font-semibold text-ink-700">키워드 직접 판정</summary>
      <form onSubmit={submit} className="mt-2 flex flex-wrap items-start gap-2" aria-label="키워드 직접 판정">
        <div className="min-w-0 flex-1 sm:max-w-md">
          <label htmlFor="trend-groups" className="sr-only">키워드 직접 판정</label>
          <input id="trend-groups" className="field" value={text} onChange={(e) => setText(e.target.value)}
            placeholder={`쉼표로 최대 ${TREND_MAX_GROUPS}개`} aria-describedby="trend-groups-help" />
          <p id="trend-groups-help" className={`mt-1 text-[11px] ${inputError ? "text-red-700" : "text-ink-500"}`}>
            {inputError ?? "키워드마다 네이버 검색량을 한 번씩 봐요."}
          </p>
        </div>
        <button type="submit" className="btn-primary" disabled={scan.kind === "loading"}>판정</button>
        {groups.length > 0 && <button type="button" className="btn-ghost" disabled={scan.kind === "loading"} onClick={() => setRun((n) => n + 1)}>다시 판정</button>}
      </form>
      {scan.kind === "loading" && <Waiting what="판정하는 중…" />}
      {scan.kind === "failed" && (
        <ErrorBox message={`${scan.reason}. ${DATA_SERVER}가 켜져 있는지, 네이버 키(NAVER_HUB_CLIENT_ID)가 있는지 확인해 주세요.`} onRetry={() => setRun((n) => n + 1)} />
      )}
      {out && (
        <div className="mt-2 space-y-2">
          {headline && <p className="text-sm text-ink-700">{headline}</p>}
          <p className="text-xs text-ink-500">네이버 검색 {windowFrom ?? out.start_date} ~ {out.end_date} · 평소 = 그 전 56일 · 직접 고른 {groups.length}개</p>
          <ol className="divide-y divide-ink-100">
            {results.map((v) => <JudgedRow key={v.group_name} v={v} notAd={notAdOf(v, popular, promo)} />)}
          </ol>
          <Sources evidence={scan.kind === "ok" ? scan.res.evidence : null} label="근거 · 키워드 판정이 불러온 곳" />
        </div>
      )}
    </details>
  );
}

// ------------------------------------------------------------------- 1 감지 플랫폼 칩

/** 종합(기본) = 요약 줄(1위 유행·검색 급상승 1위·이달 시즌 1위·인스타 인기 오디오 1위) + 요즘 유행·밈 표 / 유튜브 = 같은 유행 표(쇼츠 칸 중심) + 영상 탐색 그대로 /
 *  인스타 = 갑자기 뜬 게시물 그리드·레이더 묶음(instagram_radar) → 인기 오디오(instagram_audio) → 인스타 트렌드 메모(수동 입력). 유행 표는 숨김 /
 *  검색 = 검색 트렌드(keyword_trend, KeywordTrend) + 맨 아래 키워드 직접 판정(searchExtra).
 *  칩은 주소 #detect?view=… (lib/detectView.ts). 유행 표는 칩을 바꿔도 같은 자리(검색 칩에서는 숨김)라 다시 받지 않는다.
 *  keyword_trend는 여기서 한 번 받아 검색 칩과 종합 요약 줄이 같이 쓴다 (일간/주간을 바꾸면 다시 받는다) */
function PlatformViews({ searchExtra }: { searchExtra?: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const view = detectViewFromHash(location.hash);
  const pick = (next: DetectView) => {
    if (next === view) return;
    navigate({ pathname: location.pathname, search: location.search, hash: hashWithView(location.hash, next) }, { replace: true });
  };
  const [kwWindow, setKwWindow] = useState<KeywordWindow>("day");
  const kw = useSkill(() => dataAi.keywordTrend(kwWindow));
  const pickWindow = (next: KeywordWindow) => {
    if (next === kwWindow) return;
    setKwWindow(next);
    kw.reload();
  };
  // 인스타 인기 오디오(음악)는 여기서 한 번 받아 인스타 칩 카드와 종합 요약 칸이 같이 쓴다
  const igMusic = useSkill(() => dataAi.instagramAudio("music"));
  // 토큰 자체가 없을 때만(오디오·Business Discovery 둘 다 key_missing) 오디오·레이더 대신 한 줄. 권한 부족은 카드마다 자기 이유
  const igKeyLine = igKeyMissing(igMusic.state);
  return (
    <div className="space-y-4">
      <div role="group" aria-label="플랫폼" className="flex flex-wrap gap-2">
        {DETECT_VIEWS.map((v) => <FilterButton key={v.key} on={view === v.key} onClick={() => pick(v.key)}>{v.label}</FilterButton>)}
      </div>
      <div hidden={view === "search" || view === "instagram"}>
        <TrendMemes focus={view} summaryCards={<><KeywordSummaryCards state={kw.state} /><IgAudioSummaryCard state={igMusic.state} /></>} />
      </div>
      {view === "youtube" && (
        <>
          <VideoExplorer />
          {/* 요즘 유행·밈의 쇼츠 검색이 찾을 키워드 */}
          <YoutubeMemeKeywordsSetup />
        </>
      )}
      {view === "instagram" && (
        <ErrorBoundary title="인스타그램 칸을 그리지 못했습니다">
        <Suspense fallback={<Waiting />}>
        <section aria-label="인스타그램" className="space-y-4">
          {igKeyLine !== null ? (
            <p className="card p-4 text-sm text-ink-700">
              <b className="text-ink-900">{KEY_LATER}</b>
              {igKeyLine && <span className="block text-xs text-ink-500">{igKeyLine}</span>}
            </p>
          ) : (
            <>
              <InstagramRadar />
              <InstagramAudio music={igMusic.state} onRetryMusic={igMusic.reload} />
            </>
          )}
          {/* 레이더가 지켜볼 계정: 키가 없어도 미리 정해 둘 수 있다 */}
          <InstagramRadarSetup />
          <InstagramMemo />
        </section>
        </Suspense>
        </ErrorBoundary>
      )}
      {view === "search" && (
        <>
          <KeywordTrend state={kw.state} win={kwWindow} onWindow={pickWindow} onRetry={kw.reload} />
          {searchExtra}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- 3 인사이트 (필터 · 보고서 인사이트 · 매일 브리프)

function InsightsTab({ brief, examples, examplesCount }: { brief: SkillState<TrendInsightBriefOutput>; examples: ReactNode; examplesCount: number | null }) {
  const location = useLocation();
  const navigate = useNavigate();
  const filter = insightFilterFromHash(location.hash);
  const reports = useInsightReports();
  const pick = (f: InsightFilter) => {
    if (f !== filter) navigate({ pathname: location.pathname, search: location.search, hash: insightFilterHash(f) }, { replace: true });
  };
  const reportN = reports.kind === "ok" ? reports.items.length : null;
  const briefN = brief.kind === "ok" ? brief.out.insights.length : null;
  const n = (v: number | null) => (v === null ? "" : ` ${v}`);
  return (
    <div className="space-y-4">
      <div role="group" aria-label="인사이트 출처" className="flex flex-wrap gap-2">
        <FilterButton on={filter === "all"} onClick={() => pick("all")}>전체{reportN !== null && briefN !== null ? ` ${reportN + briefN}` : ""}</FilterButton>
        <FilterButton on={filter === "report"} onClick={() => pick("report")}>분석 의뢰 보고서{n(reportN)}</FilterButton>
        <FilterButton on={filter === "brief"} onClick={() => pick("brief")}>매일 브리프{n(briefN)}</FilterButton>
      </div>
      {filter !== "brief" && (
        <section aria-label="분석 의뢰 보고서 인사이트" className="space-y-2">
          <h2 className="text-base font-bold text-ink-900">분석 의뢰 보고서</h2>
          <ReportInsights load={reports} />
        </section>
      )}
      {filter !== "report" && (
        <ErrorBoundary title="매일 브리프를 그리지 못했습니다">
          <Suspense fallback={<Waiting />}><InsightBrief state={brief} examplesCount={examplesCount} examples={examples} /></Suspense>
        </ErrorBoundary>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- 트렌드 탭

/** dataUp: 화면(Data.tsx)이 이미 받은 데이터 AI 상태 확인(dataAi.health) 결과. null이면 아직 모름 */
export function TrendScan({ dataUp = null, company = null }: { dataUp?: boolean | null; company?: string | null }) {
  const location = useLocation();
  const navigate = useNavigate();
  const tab = trendTabFromHash(location.hash);
  const openTab = useCallback((t: TrendTab) => {
    navigate({ pathname: location.pathname, search: location.search, hash: trendTabHash(t) }, { replace: true });
  }, [navigate, location.pathname, location.search]);

  const [campaignProbe, setCampaignProbe] = useState<LoopProbe>("idle");
  // 지금 인기·기획안은 한 번 받아 1단계 요약·칩 색과 인사이트 탭이 같이 쓴다
  const popularRes = useSkill(() => dataAi.trendPopular());
  const promoRes = useSkill((n) => dataAi.promoConcepts(n > 0));
  const popularOut = okOut(popularRes.state);
  const promoOut = okOut(promoRes.state);
  const briefRes = useSkill(() => dataAi.trendInsightBrief());
  // 기획 AI 기록: 기획 전달 탭 · 인사이트 전달 칩 · 탭 배지가 같이 쓴다 (30초마다)
  const planning = usePlanningProposals();
  const planningAll = proposalsOf(planning.load);
  const handoffBadge = activeCount(dataProposals(planningAll));
  // 성과 탭 옆 칩: 측정 전(측정 대기)이라도 데이터 AI 상태 확인이 실패했으면 "연결 확인 필요"
  const resultProbe: LoopProbe = !kpiLive ? "failed" : campaignProbe === "idle" && dataUp === false ? "failed" : campaignProbe;
  // 성과 탭 (11/26): 마케팅에 넘어간 캠페인(기획 기록 handoff) · 영상 성과가 받은 영상 · 이 화면에서 잰 캠페인 효과
  const campaigns = campaignRows(dataProposals(planningAll));
  const [videos, setVideos] = useState<VideoPerformanceVideo[] | null>(null);
  const [effects, setEffects] = useState<Readonly<Record<string, CampaignEffectOutput>>>({});
  const onEffect = useCallback((id: string, out: CampaignEffectOutput) => setEffects((all) => ({ ...all, [id]: out })), []);
  const resultTag = resultTabTag(PROBE_STATUS[resultProbe].label, resultProbe, campaigns, new Set(Object.keys(effects)));
  // 2번째 이후 하위 탭은 처음 열 때 그린다(감지를 빨리 열려고). 그 뒤에는 숨기기만 한다
  const [seen, setSeen] = useState<ReadonlySet<string>>(() => new Set([tab.key]));
  useEffect(() => {
    setSeen((s) => (s.has(tab.key) ? s : new Set([...s, tab.key])));
  }, [tab.key]);

  const detect = !kpiLive ? (
    <Panel id="data-trend" title="트렌드 감지">
      <p className="text-sm text-ink-500">데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 트렌드를 받지 않아요. backend/의 scripts/run_web.sh로 화면을 켜면 토큰이 들어갑니다.</p>
    </Panel>
  ) : <DirectJudge popular={popularOut} promo={promoOut} />;

  const snapshot = popularOut?.youtube_status === "known" ? popularOut.snapshot_at : null;
  const tabButton = (t: TrendTab) => {
    const on = tab.key === t.key;
    return (
      <button key={t.key} type="button" role="tab" id={`trend-sub-${t.key}`} aria-selected={on} aria-controls={`trend-sub-panel-${t.key}`}
        onClick={() => openTab(t)}
        className={`-mb-px flex items-center gap-1.5 whitespace-nowrap border-b-2 px-4 py-2.5 text-[15px] transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${t.side ? "ml-auto" : ""} ${on ? "border-brand-600 font-bold text-brand-700" : `border-transparent ${t.side ? "text-ink-400" : "text-ink-500"} hover:text-ink-900`}`}>
        {t.side ? `⚙ ${t.title}` : t.title}
        {t.key === "handoff" && handoffBadge > 0 && (
          <span className="rounded-full bg-brand-50 px-1.5 text-[11px] font-bold text-brand-700" aria-label={`진행 중 제안 ${handoffBadge}개`}>{handoffBadge}</span>
        )}
        {t.key === "result" && <span className="text-[12.5px] font-normal text-ink-500">{resultTag}</span>}
      </button>
    );
  };
  return (
    <PlanningProposals.Provider value={planningAll}>
      <div className="space-y-4">
        {/* 제목 한 줄: 트렌드&콘텐츠 · 설명 접힘 · 오른쪽 YouTube 인기 기준 시각 (서버 snapshot_at) */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <h1 className="text-2xl font-bold tracking-tight text-ink-900">트렌드&amp;콘텐츠</h1>
          <details className="text-xs text-ink-500">
            <summary className="cursor-pointer select-none">설명</summary>
            <p className="mt-1 max-w-3xl leading-relaxed">
              {company ? `${company} ` : ""}데이터분석팀이 감지 → 분석 의뢰 → 인사이트 → 기획 전달 → 성과를 봅니다. 숫자는 서버가 계산한 값만 보여 주고, 없으면 빈 상태를 보여 줍니다.
            </p>
          </details>
          {snapshot && <span className="ml-auto text-xs text-ink-500">YouTube 인기 {when(snapshot)} 기준</span>}
        </div>

        {/* 하위 탭: 감지 → 분석 의뢰 → 인사이트 → 기획 전달 → 성과 + 오른쪽 끝 ⚙ 데이터 출처 (lib/contentLoop.ts TREND_TABS) */}
        <div role="tablist" aria-label="트렌드&콘텐츠 단계" className="flex w-full gap-2 overflow-x-auto border-b border-ink-100">
          {TREND_TABS.map(tabButton)}
        </div>

        {TREND_TABS.map((t) => (
          <div key={t.key} role="tabpanel" id={`trend-sub-panel-${t.key}`} aria-labelledby={`trend-sub-${t.key}`} hidden={tab.key !== t.key}>
            {t.key === "detect" ? (
              // 순서: 오늘 가장 큰 화제 한 줄 → 플랫폼 칩(종합 / 유튜브 / 인스타 / 검색: 급상승 검색어 + 맨 아래 키워드 직접 판정)
              // 토큰이 없으면 칩 없이 안내(detect)만
              <div className="space-y-4">
                {kpiLive && <TrendHeadline popular={popularRes.state} />}
                {kpiLive ? <PlatformViews searchExtra={detect} /> : detect}
              </div>
            ) : !seen.has(t.key) ? null : <ErrorBoundary title="이 탭을 그리지 못했습니다"><Suspense fallback={<Waiting />}>{t.key === "recommend" ? (
              // 처음 열 때 그리고, 그 뒤에는 하위 탭을 오가도 남도록 숨기기만 한다
              <InsightsTab brief={briefRes.state} examplesCount={promoOut ? promoOut.concepts.length : null}
                examples={<PromoConcepts state={promoRes.state} popular={popularOut} onRegenerate={promoRes.reload} />} />
            ) : t.key === "request" ? (
              // 분석 의뢰 (루프와 별개): 의뢰 넣기 · 지난 의뢰 · 보고서(#request?id=<report_id>). 처음 열 때 그린다
              <TrendRequest />
            ) : t.key === "sources" ? (
              // 데이터 출처 (루프와 별개, 탭 줄 오른쪽 끝): 회사 이해 · 수집 계획 · 출처 현황. 처음 열 때 받는다
              <DataSources />
            ) : t.key === "handoff" ? (
              // 기획 전달: 기획 AI가 받은 데이터 인사이트와 진행 상태
              <PlanningHandoff load={planning.load} onRetry={planning.reload} />
            ) : (
              // 성과 (11/26): 마케팅에 넘어간 캠페인 목록 + [측정](위) → 게시 영상 성과(아래). 측정 결과가 하위 탭을 오가도 남게 숨기기만 한다
              <div className="flex flex-col gap-4">
                <CampaignEffect onProbe={setCampaignProbe} campaigns={campaigns} videos={videos} effects={effects} onEffect={onEffect} />
                <VideoPerformance onVideos={setVideos} />
              </div>
            )}</Suspense></ErrorBoundary>}
          </div>
        ))}
      </div>
    </PlanningProposals.Provider>
  );
}
