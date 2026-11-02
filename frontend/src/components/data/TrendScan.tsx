import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router";
import { Chip, Empty, ErrorBox, Panel, TONE, Waiting, useResource } from "@/components/finance/bits";
import { PreparingChip } from "@/components/Preparing";
import { CampaignEffect } from "@/components/data/CampaignEffect";
import { ContentLoop, type LoopTrendState } from "@/components/data/ContentLoop";
import { LOOP_STAGES, SEND_TO_MARKETING_BLOCKED, stageFromHash, trendHash, type LoopProbe, type LoopStage } from "@/lib/contentLoop";
import {
  DATA_SERVER, TREND_MAX_GROUPS, dataAi, evidenceLines, kpiLive,
  type TrendCandidatesOutput, type TrendScanOutput, type TrendVerdict, type YouTubeTagEvidence,
} from "@/lib/data";
import { UNKNOWN_TREND_TEXT, ratioNote, strengthLabel } from "@/lib/trendWording";

// 트렌드 탭 (#trend): 콘텐츠 루프 5단계와 같은 이름·순서의 하위 탭 5개 (단계 정의는 lib/contentLoop.ts 한 곳).
// - 주소에 하위 탭을 남긴다: #trend/detect · #trend/recommend · #trend/video · #trend/campaign · #trend/feedback (없으면 1단계)
// - 루프 띠의 단계를 누르면 그 하위 탭으로 간다. 루프 띠는 모든 하위 탭 위에 있다
// - 1 트렌드·밈 감지: 데이터 AI(8106) trend_scan·trend_candidates 출력을 그대로. 보기 전환 "지금 급상승 / 월별 흐름 / 계절 패턴"
//   (월별·계절은 서버 기능이 없어 준비 중)
// - 2·3·5: 준비 중. 단계 정의의 할 일·필요한 것만 보여 준다(숫자·예시 없음)
// - 4 게시·성과 측정: 캠페인 효과(campaign_effect). 하위 탭을 오가도 측정 결과가 남게 화면에서 내리지 않고 숨긴다
//
// 1단계 카드 문구
// - 판정·배수·z·평소 범위·문장은 모두 서버 값이다. 화면은 순서(배수 큰 순)·자릿수·쉬운 말만 정한다
// - 큰 글씨는 "평소보다 N배 많이 검색돼요" + "변화 정도"(z 구간을 말로, lib/trendWording.ts). z 숫자는 "근거 보기" 안에만
// - 값은 검색 건수가 아니라 "그 요청 안 최댓값 = 100"인 상대값이다. 그룹마다 따로 요청해서 그룹끼리 높이는 비교하지 않는다
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

// ------------------------------------------------------------------- 평소 범위 그래프 (SVG)

const W = 320;
const H = 110;
const PAD_TOP = 16;
const PAD_BOTTOM = 6;

function TrendChart({ v }: { v: TrendVerdict }) {
  const points = v.series ?? [];
  const e = v.evidence;
  if (points.length < 2) return <p className="py-6 text-center text-xs text-ink-500">그래프로 그릴 날짜별 값이 없어요.</p>;
  const t0 = Date.parse(points[0].day);
  const span = Math.max(1, (Date.parse(points[points.length - 1].day) - t0) / 86_400_000);
  const dayOf = (day: string) => (Date.parse(day) - t0) / 86_400_000;
  const top = Math.max(...points.map((p) => p.value), e.band_high ?? 0, e.baseline_value ?? 0) * 1.08 || 1;
  const x = (day: string) => (dayOf(day) / span) * W;
  const y = (value: number) => H - PAD_BOTTOM - (value / top) * (H - PAD_TOP - PAD_BOTTOM);

  // 빠진 날(하루 넘게 비는 곳)은 선을 끊는다. 빈 날을 0으로 잇지 않는다
  const path = (list: typeof points) =>
    list.map((p, i) => `${i > 0 && dayOf(p.day) - dayOf(list[i - 1].day) > 1 ? "M" : i === 0 ? "M" : "L"}${x(p.day).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const windowFrom = v.window_from ?? null;
  const windowX = windowFrom ? x(windowFrom) : null;
  const firstWindow = windowFrom ? points.findIndex((p) => p.day >= windowFrom) : -1;
  const windowPart = firstWindow >= 0 ? points.slice(Math.max(0, firstWindow - 1)) : [];
  const baseEnd = windowX ?? W;
  const hasBand = e.band_low != null && e.band_high != null;

  // 그림은 카드 폭에 맞춰 늘린다(preserveAspectRatio="none"). 글자는 늘어나지 않게 SVG 밖 HTML로 올린다
  return (
    <div className="relative">
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-28 w-full" role="img"
      aria-label={`${v.group_name} 날짜별 값. 평소 범위 ${fixed(e.band_low) ?? "-"}~${fixed(e.band_high) ?? "-"}, 판정 구간 ${shortDay(windowFrom)}부터`}>
      {windowX != null && <rect x={windowX} y={0} width={W - windowX} height={H} className="fill-red-50" />}
      {hasBand && (
        <rect x={0} y={y(e.band_high as number)} width={baseEnd} height={Math.max(1, y(e.band_low as number) - y(e.band_high as number))}
          className="fill-brand-100" opacity={0.7} />
      )}
      {e.baseline_value != null && (
        <line x1={0} x2={baseEnd} y1={y(e.baseline_value)} y2={y(e.baseline_value)} className="stroke-brand-500" strokeWidth={1} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
      )}
      <path d={path(points)} fill="none" className="stroke-ink-700" strokeWidth={1.4} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      {windowPart.length > 1 && <path d={path(windowPart)} fill="none" className="stroke-red-600" strokeWidth={2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
    </svg>
    {hasBand && (
      <span aria-hidden className="pointer-events-none absolute left-1 text-[10px] text-brand-700"
        style={{ top: `calc(${(Math.max(10, y(e.band_high as number) - 4) / H) * 100}% - 10px)` }}>평소 범위</span>
    )}
    {windowX != null && (
      <span aria-hidden className="pointer-events-none absolute top-0 text-[10px] text-red-600"
        style={{ right: `calc(${100 - (windowX / W) * 100}% + 4px)` }}>판정 구간 →</span>
    )}
    </div>
  );
}

// ------------------------------------------------------------------- 급상승 카드

/** 화면에 그릴 판정 하나. youtube가 있으면 YouTube 후보에서 온 것 */
type Entry = { v: TrendVerdict; youtube?: YouTubeTagEvidence; aliases?: string[] };

/** 평범·판정 불가 한 줄 설명: 판정 불가는 "자료가 부족해 판단할 수 없어요 · 서버 이유" */
function restLine(v: TrendVerdict): string {
  if (v.status === "unknown") return `${UNKNOWN_TREND_TEXT}${v.unknown_reason ? ` · ${v.unknown_reason}` : ""}`;
  const note = ratioNote(v.status, v.ratio);
  return `평소 수준${note ? ` · ${note}` : ""}`;
}

function RisingCard({ v, youtube, aliases, rank }: { v: TrendVerdict; youtube?: YouTubeTagEvidence; aliases?: string[]; rank: number }) {
  const [open, setOpen] = useState(false);
  const e = v.evidence;
  const strength = strengthLabel(e.score);
  const note = ratioNote(v.status, v.ratio);
  return (
    <article className="card flex flex-col gap-3 p-4 sm:p-5" aria-label={`급상승 ${rank}위 ${v.group_name}`}>
      <div className="min-w-0">
        <p className="text-[11px] font-semibold text-ink-500">#{rank}</p>
        <h3 className="truncate text-lg font-bold text-ink-900">{v.group_name}</h3>
        {/* 서버가 묶은 별칭 (#339). 네이버 판정에 함께 넣은 이름이다. 없으면 표시하지 않는다 */}
        {aliases && aliases.length > 0 && <p className="text-[11px] text-ink-500">함께 본 이름: {aliases.join(", ")}</p>}
      </div>
      <div>
        <p className="text-2xl font-bold leading-snug text-red-600">평소보다 {fixed(v.ratio) ?? "-"}배 많이 검색돼요</p>
        {strength && <p className="mt-1 text-sm font-semibold text-ink-700">변화 정도: {strength}</p>}
        {note && <p className="mt-1 text-xs text-ink-500">{note}</p>}
      </div>
      <TrendChart v={v} />
      <div className="flex flex-wrap gap-1.5">
        <Chip label="급상승" tone={TONE.bad} />
        <Chip label="✓ 네이버 검색" tone={TONE.info} />
        {youtube && <Chip label={`✓ YouTube 인기 태그 (영상 ${youtube.video_count}개)`} tone={TONE.info} />}
      </div>
      {v.summary && <p className="border-l-2 border-brand-500 bg-ink-50 px-3 py-2 text-sm leading-relaxed text-ink-700">{v.summary}</p>}
      {open && (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 bg-ink-50 px-3 py-2 text-xs text-ink-700">
          <dt className="text-ink-500">변화 정도 (z)</dt><dd>{fixed(e.score) ?? "-"} (평소 흔들림 대비)</dd>
          <dt className="text-ink-500">기준선(중앙값)</dt><dd>{fixed(e.baseline_value, 4) ?? "-"}</dd>
          <dt className="text-ink-500">평소 범위</dt><dd>{fixed(e.band_low, 4) ?? "-"} ~ {fixed(e.band_high, 4) ?? "-"}</dd>
          <dt className="text-ink-500">기준선에 쓴 날</dt><dd>{e.baseline_points}일</dd>
          <dt className="text-ink-500">최근값</dt><dd>{fixed(e.latest_value, 4) ?? "-"} ({shortDay(e.latest_period)})</dd>
          <dt className="text-ink-500">흔들림(MAD)</dt><dd>{fixed(e.spread, 4) ?? "-"}</dd>
          <dt className="text-ink-500">판정 구간</dt><dd>{shortDay(v.window_from)}부터</dd>
        </dl>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-2">
        <button type="button" className="btn-primary disabled:cursor-not-allowed disabled:opacity-50" disabled title={SEND_TO_MARKETING_BLOCKED}>
          마케팅에 소재로 보내기
        </button>
        <button type="button" className="btn-ghost" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? "근거 접기" : "근거 보기"}
        </button>
        <PreparingChip />
      </div>
    </article>
  );
}

// ------------------------------------------------------------------- 1단계 본문

/** 기본 판정이면 trend_scan 결과와 YouTube 후보를 합친다. 같은 이름(대소문자 무시)은 후보 쪽을 쓴다 */
function mergeEntries(out: TrendScanOutput, cands: TrendCandidatesOutput | null): Entry[] {
  const fromYoutube: Entry[] = (cands?.candidates ?? []).map((c) => ({ v: c.naver, youtube: c.youtube, aliases: c.aliases }));
  const names = new Set(fromYoutube.map((e) => e.v.group_name.toLowerCase()));
  return [...out.results.filter((r) => !names.has(r.group_name.toLowerCase())).map((v) => ({ v })), ...fromYoutube];
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

/** YouTube에서 뜨지만 검색은 평소 수준 (교차 확인에서 걸러진 것) + 검색 판정 불가 후보 */
function YoutubeOnly({ cands }: { cands: CandState }) {
  if (cands.kind === "off") return null;
  const line = snapshotLine(cands);
  const list = cands.kind === "ok" ? cands.out.candidates.filter((c) => c.naver.status !== "rising") : [];
  let body;
  if (cands.kind === "loading") body = <Waiting what="YouTube 후보를 판정하는 중…" />;
  else if (cands.kind === "failed") body = <Empty>YouTube 후보를 받지 못했어요. {cands.reason}</Empty>;
  else if (cands.out.status === "unknown") body = <Empty>{cands.out.unknown_reason}</Empty>;
  else if (list.length === 0) body = <Empty>YouTube 후보가 모두 검색에서도 급상승이에요.</Empty>;
  else {
    body = (
      <>
        <ul className="divide-y divide-ink-100">
          {list.map((c) => (
            <li key={c.keyword} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-ink-900">{c.keyword}</p>
                <p className="text-xs text-ink-500">YouTube 인기 영상 {c.youtube.video_count}개 · 검색은 {restLine(c.naver)}</p>
              </div>
              {c.naver.status === "normal" && <span className="text-sm text-ink-500">평소 대비 {fixed(c.naver.ratio) ?? "-"}배</span>}
              <Chip label={c.naver.status === "unknown" ? "판정 불가" : "평범"} tone={c.naver.status === "unknown" ? TONE.warn : TONE.ok} />
            </li>
          ))}
        </ul>
        {cands.out.summary && <p className="mt-2 text-xs text-ink-500">{cands.out.summary}</p>}
      </>
    );
  }
  return (
    <Panel id="data-trend-youtube" title="YouTube에서 뜨지만 검색은 평소 수준"
      hint={line && <span className={line.warn ? "text-amber-700" : undefined}>{line.text}</span>}>
      {body}
    </Panel>
  );
}

function Result({ entries, headline, cands }: { entries: Entry[]; headline: string | null; cands: CandState }) {
  const rising = entries.filter((e) => e.v.status === "rising").sort((a, b) => (b.v.ratio ?? 0) - (a.v.ratio ?? 0));
  // 아래 "평소 수준 · 판정 불가"는 기본 키워드·직접 입력 그룹만. YouTube 후보는 YoutubeOnly에서 따로
  const rest = entries.filter((e) => e.v.status !== "rising" && !e.youtube).map((e) => e.v);
  return (
    <div className="space-y-5">
      <section aria-labelledby="trend-rising-title" className="space-y-3">
        <div>
          <p className="panel-title">지금 뜨는 소재</p>
          <h2 id="trend-rising-title" className="mt-1 text-lg font-bold text-ink-900">{headline ?? `급상승 ${rising.length}개`}</h2>
          <p className="mt-1 text-xs text-ink-500">급상승은 늘어난 배수만이 아니라, 그 키워드가 평소에 얼마나 오르내리는지와 비교해서 정해요.</p>
        </div>
        {rising.length === 0 ? (
          <Empty>이번 판정 구간에 급상승한 키워드 그룹이 없어요.</Empty>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {rising.map((e, i) => <RisingCard key={`${e.youtube ? "yt" : "kw"}-${e.v.group_name}`} v={e.v} youtube={e.youtube} aliases={e.aliases} rank={i + 1} />)}
          </div>
        )}
        {rising.length > 0 && <p className="text-xs text-ink-500">마케팅에 소재로 보내기: {SEND_TO_MARKETING_BLOCKED}</p>}
      </section>

      <YoutubeOnly cands={cands} />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Panel id="data-trend-rest" title="평소 수준 · 판정 불가">
            {rest.length === 0 ? <Empty>평소 수준이거나 판정 불가인 그룹이 없어요.</Empty> : (
              <ul className="divide-y divide-ink-100">
                {rest.map((v) => (
                  <li key={v.group_name} className="flex flex-wrap items-center gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold text-ink-900">{v.group_name}</p>
                      <p className="text-xs text-ink-500">{restLine(v)}</p>
                    </div>
                    {v.status === "normal" && <span className="text-sm text-ink-500">평소 대비 {fixed(v.ratio) ?? "-"}배</span>}
                    <Chip label={v.status === "unknown" ? "판정 불가" : "평범"} tone={v.status === "unknown" ? TONE.warn : TONE.ok} />
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
        <Panel id="data-trend-method" title="판정 방법">
          <div className="space-y-2 text-sm leading-relaxed text-ink-700">
            <p>네이버 검색량을 <b>직전 56일의 평소 범위</b>와 비교합니다. 마지막 7일 안에서 이어진 날들이 평소 흔들림보다 크게 벗어나면 급상승입니다.</p>
            <p>그래서 평소에 아주 일정한 키워드는 작은 배수도 급상승이 되고, 원래 크게 오르내리는 키워드는 큰 배수도 평소 수준일 수 있어요.</p>
            <p>값은 검색 건수가 아니라 그룹마다 따로 받은 상대값(그 요청의 최댓값 = 100)이라, 같은 그룹 안에서만 비교합니다. 계절 반복은 구분하지 않습니다.</p>
            <p>자료가 없거나 기준선에 값이 있는 날이 모자라면 평범이 아니라 <b>판정 불가</b>와 이유를 보여 줍니다.</p>
            <p className="text-xs text-ink-500">계산은 서버 코드가 하고, 문장만 AI가 씁니다. 기준값과 평가 결과: backend/domains/data/README.md "trend_scan".</p>
          </div>
        </Panel>
      </div>
    </div>
  );
}

/** 1단계 보기 전환. 월별·계절은 서버 기능이 없어 준비 중 */
type DetectView = "now" | "monthly" | "seasonal";
const DETECT_VIEWS: { id: DetectView; label: string; todo?: string }[] = [
  { id: "now", label: "지금 급상승" },
  { id: "monthly", label: "월별 흐름", todo: "키워드별 검색량을 달마다 이어서 보여 주기 (데이터 AI에 월 단위로 보는 기능이 아직 없어요)" },
  { id: "seasonal", label: "계절 패턴", todo: "해마다 같은 때 오르는 키워드를 구분하기 (지금 급상승 판정은 계절 반복을 구분하지 않아요)" },
];

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

  const [view, setView] = useState<DetectView>("now");
  const [text, setText] = useState("");
  const [groups, setGroups] = useState<string[]>([]);
  const [inputError, setInputError] = useState<string | null>(null);
  const [run, setRun] = useState(0);
  const [campaignProbe, setCampaignProbe] = useState<LoopProbe>("idle");
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
          {out && <Result entries={entries} headline={headline} cands={cands} />}
        </>
      )}
    </div>
  );
  const detectView = DETECT_VIEWS.find((d) => d.id === view) ?? DETECT_VIEWS[0];

  return (
    <div className="space-y-5">
      <ContentLoop trend={loopTrend} probes={{ trend_scan: trendProbe, campaign_effect: loopCampaignProbe }} active={stage.no} onSelect={openStage} />

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
              <div role="group" aria-label="보기 전환" className="flex flex-wrap gap-1.5">
                {DETECT_VIEWS.map((d) => (
                  <button key={d.id} type="button" aria-pressed={view === d.id} onClick={() => setView(d.id)}
                    className={view === d.id ? "btn-primary" : "btn-ghost"}>
                    {d.label}
                  </button>
                ))}
              </div>
              {view === "now" ? detect : (
                <Panel id={`data-trend-${detectView.id}`} title={detectView.label} tools={<PreparingChip />}>
                  <div className="space-y-2 text-sm leading-relaxed text-ink-700">
                    <p><b className="text-ink-900">할 일</b> · {detectView.todo}</p>
                    <p className="text-xs text-ink-500">서버 기능이 아직 없어서 숫자를 보여 주지 않아요.</p>
                  </div>
                </Panel>
              )}
            </div>
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
