import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router";
import { Chip, Empty, ErrorBox, Panel, TONE, Waiting, useResource } from "@/components/finance/bits";
import { PreparingChip } from "@/components/Preparing";
import { CampaignEffect } from "@/components/data/CampaignEffect";
import { ContentRecommend } from "@/components/data/ContentRecommend";
import { TrendPopular } from "@/components/data/TrendPopular";
import { YoutubeVelocity } from "@/components/data/YoutubeVelocity";
import { ContentLoop, type LoopTrendState } from "@/components/data/ContentLoop";
import { LOOP_STAGES, SEND_TO_MARKETING_BLOCKED, stageFromHash, trendHash, type LoopProbe, type LoopStage } from "@/lib/contentLoop";
import {
  DATA_SERVER, TREND_MAX_GROUPS, dataAi, evidenceLines, kpiLive,
  type TrendCandidatesOutput, type TrendScanOutput, type TrendVerdict, type TrendingSearchEvidence, type YouTubeTagEvidence,
} from "@/lib/data";

// 트렌드 탭 (#trend): 콘텐츠 루프 5단계와 같은 이름·순서의 하위 탭 5개 (단계 정의는 lib/contentLoop.ts 한 곳).
// - 주소에 하위 탭을 남긴다: #trend/detect · #trend/recommend · #trend/video · #trend/campaign · #trend/feedback (없으면 1단계)
// - 루프 띠의 단계를 누르면 그 하위 탭으로 간다. 루프 띠는 모든 하위 탭 위에 있다
// - 1 트렌드·밈 감지: 맨 위 지금 인기(trend_popular), 그 아래 급상승 한 줄 목록(trend_scan·trend_candidates), YouTube 지금 속도.
//   교차 출처 목록(평소 수준·판정 불가)·판정 방법 안내·월별 흐름·계절 패턴은 화면을 줄이려고 뺐다
// - 2 영상 소재 추천: content_recommend 출력(components/data/ContentRecommend.tsx). 하위 탭을 오가도 남게 숨기기만 한다
// - 3·5: 준비 중. 단계 정의의 할 일·필요한 것만 보여 준다(숫자·예시 없음)
// - 4 게시·성과 측정: 캠페인 효과(campaign_effect). 하위 탭을 오가도 측정 결과가 남게 화면에서 내리지 않고 숨긴다
//
// 1단계 급상승 한 줄
// - 주제 · "평소보다 N배 많이 검색돼요"(서버 배수) · 판정에 쓴 마지막 날. 순서는 배수 큰 순. 그래프는 없다
// - 기본 판정(입력 비움) = 회사 기본 키워드(trend_scan) + YouTube 인기 태그 후보(trend_candidates, 후보마다 네이버 판정).
//   같은 이름이면 YouTube 근거가 있는 후보 쪽 판정을 쓴다. 직접 입력이면 trend_scan만 부른다
// - 판정마다 서버가 LLM으로 문장을 쓰므로 30초 새로고침을 따르지 않는다. 처음 한 번 + "판정"·"다시 판정"을 누를 때만 부른다
// "마케팅에 소재로 보내기"를 아직 못 하는 이유는 콘텐츠 루프 2단계와 같은 문장이다 (lib/contentLoop.ts)

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

/** 급상승 한 줄: 주제 · 쉬운 말 근거 하나(서버 배수) · 시각(판정에 쓴 마지막 날) */
function RisingRow({ v, rank }: { v: TrendVerdict; rank: number }) {
  const day = v.evidence.latest_period;
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2.5">
      <span className="w-6 text-sm font-semibold text-ink-500">{rank}</span>
      <span className="font-semibold text-ink-900">{v.group_name}</span>
      <span className="text-sm text-red-700">평소보다 {fixed(v.ratio) ?? "-"}배 많이 검색돼요</span>
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

/** 시각 → 이 컴퓨터 시간 */
const when = (iso: string | null | undefined) => {
  const t = iso ? Date.parse(iso) : Number.NaN;
  return Number.isNaN(t) ? null : new Date(t).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
};

type CandState =
  | { kind: "off" }
  | { kind: "loading" }
  | { kind: "ok"; out: TrendCandidatesOutput }
  | { kind: "failed"; reason: string };

/** 스냅샷 줄: 시각·영상 수, 또는 서버 이유 그대로 */
function snapshotLine(c: CandState): { text: string; warn: boolean } | null {
  if (c.kind === "off") return null;
  if (c.kind === "loading") return { text: "YouTube 후보를 판정하는 중…", warn: false };
  if (c.kind === "failed") return { text: `YouTube 후보: ${c.reason}`, warn: true };
  if (c.out.status === "unknown") return { text: `YouTube 후보: ${c.out.unknown_reason ?? "서버가 이유를 주지 않았어요"}`, warn: true };
  return {
    text: `YouTube 스냅샷 ${when(c.out.snapshot_at) ?? "-"} · 인기 영상 ${c.out.snapshot_videos}개 · 후보 ${c.out.candidates.length}개 · 네이버 호출 ${c.out.naver_calls}번`,
    warn: false,
  };
}

function Result({ entries, headline }: { entries: Entry[]; headline: string | null }) {
  const rising = entries.filter((e) => e.v.status === "rising").sort((a, b) => (b.v.ratio ?? 0) - (a.v.ratio ?? 0));
  return (
    <section aria-labelledby="trend-rising-title" className="card space-y-2 p-4 sm:p-5">
      <div>
        <p className="panel-title">지금 뜨는 소재</p>
        <h2 id="trend-rising-title" className="mt-1 text-lg font-bold text-ink-900">{headline ?? `급상승 ${rising.length}개`}</h2>
      </div>
      {rising.length === 0 ? (
        <Empty>이번 판정 구간에 급상승한 키워드 그룹이 없어요.</Empty>
      ) : (
        <ol className="divide-y divide-ink-100">
          {rising.map((e, i) => <RisingRow key={`${e.youtube ? "yt" : e.trending ? "tr" : "kw"}-${e.v.group_name}`} v={e.v} rank={i + 1} />)}
        </ol>
      )}
      {rising.length > 0 && <p className="text-xs text-ink-500">마케팅에 소재로 보내기: {SEND_TO_MARKETING_BLOCKED}</p>}
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
  const stage = stageFromHash(location.hash);
  const openStage = useCallback((s: LoopStage) => {
    navigate({ search: location.search, hash: trendHash(s) }, { replace: true });
  }, [navigate, location.search]);

  const [text, setText] = useState("");
  const [groups, setGroups] = useState<string[]>([]);
  const [inputError, setInputError] = useState<string | null>(null);
  const [run, setRun] = useState(0);
  const [campaignProbe, setCampaignProbe] = useState<LoopProbe>("idle");
  const [recommendProbe, setRecommendProbe] = useState<LoopProbe>("checking");
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
  const snapshot = snapshotLine(cands);
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
    <Panel id="data-trend" title="검색어 급상승">
      <Empty>데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 트렌드를 받지 않아요. backend/의 scripts/run_web.sh로 화면을 켜면 토큰이 들어갑니다.</Empty>
    </Panel>
  ) : (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-ink-500">
            {out ? `판정 ${windowFrom ?? out.start_date} ~ ${out.end_date} · 기준선 직전 56일 · ` : ""}
            {groups.length ? `직접 고른 그룹 ${groups.length}개` : "기본 키워드 (회사 설정 trends.toml) + YouTube 인기 태그 후보"}
          </p>
          {snapshot && <p className={`mt-0.5 text-[11px] ${snapshot.warn ? "text-amber-700" : "text-ink-500"}`}>{snapshot.text}</p>}
          <div className="mt-1 flex flex-wrap gap-1.5">
            <Chip label="네이버 검색" tone={TONE.info} />
            {useCandidates && <Chip label="YouTube 인기 태그" tone={TONE.info} />}
          </div>
        </div>
        <form onSubmit={submit} className="flex w-full flex-wrap items-start gap-2 sm:w-auto" aria-label="키워드 직접 판정">
          <div className="min-w-0 flex-1 sm:w-80">
            <label htmlFor="trend-groups" className="sr-only">키워드 직접 판정</label>
            <input id="trend-groups" className="field" value={text} onChange={(e) => setText(e.target.value)}
              placeholder={`키워드 직접 판정 (쉼표로 최대 ${TREND_MAX_GROUPS}개, 비우면 기본 키워드 + YouTube 후보)`} aria-describedby="trend-groups-help" />
            <p id="trend-groups-help" className={`mt-1 text-[11px] ${inputError ? "text-red-700" : "text-ink-500"}`}>
              {inputError ?? "그룹마다 네이버를 한 번씩 부릅니다."}
            </p>
          </div>
          <button type="submit" className="btn-primary" disabled={res.loading}>판정</button>
          <button type="button" className="btn-ghost" disabled={res.loading} onClick={() => setRun((n) => n + 1)}>다시 판정</button>
        </form>
      </div>

      {res.loading && <p className="text-xs text-ink-500" aria-live="polite">판정하는 중… (그룹마다 네이버를 부르고 급상승마다 설명을 씁니다)</p>}
      {!res.env ? (
        res.loading ? <Waiting /> : (
          <ErrorBox message={`${res.error ?? "판정 결과를 받지 못했습니다"}. ${DATA_SERVER}가 켜져 있는지, 네이버 키(NAVER_HUB_CLIENT_ID)가 있는지 확인해 주세요.`} onRetry={res.reload} />
        )
      ) : (
        <>
          {res.error && <ErrorBox message={`새로 판정하지 못해 이전 결과를 보여 줍니다. ${res.error}`} onRetry={res.reload} />}
          {out && <Result entries={entries} headline={headline} />}
        </>
      )}
    </div>
  );

  return (
    <div className="space-y-5">
      <ContentLoop trend={loopTrend} probes={{ trend_scan: trendProbe, content_recommend: recommendProbe, campaign_effect: loopCampaignProbe }} active={stage.no} onSelect={openStage} />

      {/* 하위 탭: 루프 5단계와 같은 이름·순서 */}
      <div role="tablist" aria-label="콘텐츠 루프 단계" className="flex w-full gap-1 overflow-x-auto rounded-2xl bg-surface p-1 ring-1 ring-ink-100">
        {LOOP_STAGES.map((s) => (
          <button key={s.tab} type="button" role="tab" id={`trend-sub-${s.tab}`} aria-selected={stage.no === s.no} aria-controls={`trend-sub-panel-${s.tab}`}
            onClick={() => openStage(s)}
            className={`flex-1 whitespace-nowrap rounded-xl px-3 py-2 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${stage.no === s.no ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>
            {s.no} {s.title}
          </button>
        ))}
      </div>

      {LOOP_STAGES.map((s) => (
        <div key={s.tab} role="tabpanel" id={`trend-sub-panel-${s.tab}`} aria-labelledby={`trend-sub-${s.tab}`} hidden={stage.no !== s.no}>
          {s.tab === "detect" ? (
            <div className="space-y-5">
              {/* 지금 인기 (trend_popular): 급상승 판정과 별개. 1단계 맨 위, 급상승 화면은 아래 */}
              <TrendPopular />
              {detect}
              {/* YouTube 지금 속도: 판정 없이 보여 주기 (youtube_velocity). 보기와 상관없이 1단계 아래에 */}
              <YoutubeVelocity />
            </div>
          ) : s.tab === "recommend" ? (
            // 추천 결과가 하위 탭을 오가도 남도록 늘 그려 두고 숨기기만 한다
            <ContentRecommend onProbe={setRecommendProbe} dataUp={dataUp} />
          ) : s.tab === "campaign" ? (
            // 측정 결과가 하위 탭을 오가도 남도록 늘 그려 두고 숨기기만 한다
            <CampaignEffect onProbe={setCampaignProbe} />
          ) : (
            <PreparingStage stage={s} />
          )}
        </div>
      ))}
    </div>
  );
}
