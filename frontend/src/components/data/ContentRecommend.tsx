import { useEffect, useState } from "react";
import { Chip, Empty, ErrorBox, Panel, TONE, Waiting } from "@/components/finance/bits";
import { SEND_TO_MARKETING_BLOCKED, type LoopProbe } from "@/lib/contentLoop";
import { DATA_SERVER, UNKNOWN, dataAi, kpiLive, type ContentRecommendOutput, type ContentRecommendation } from "@/lib/data";
import { strengthLabel } from "@/lib/trendWording";

// 트렌드 탭 2단계 "영상 소재 추천" (#trend/recommend): 데이터 AI(8106) content_recommend 출력을 그대로 보여 준다.
// - 순위·배수·두 소스·회사 관련성·아이디어 문장·형식은 모두 서버 값이다. 화면은 자릿수와 쉬운 말만 정한다
//   ("평소보다 N배 많이 검색돼요" + 변화 정도는 1단계 카드와 같은 lib/trendWording.ts)
// - 아이디어 문장이 서버 기본 문장(fallback)이면 "기본 문장" 칩을 단다
// - 추천이 없으면 서버 이유(empty_reason)를 그대로 보여 준다. 화면이 채우지 않는다
// - "마케팅에 소재로 보내기"는 아직 못 해서 누를 수 없고, 이유는 콘텐츠 루프와 같은 문장이다
// - 응답 결과(확인 중·정상·실패)는 콘텐츠 루프 2단계 칩으로 올린다(onProbe). 칩을 위해 따로 부르지 않는다
// - 소재마다 LLM 문장을 쓰므로 30초 새로고침을 따르지 않는다. 처음 한 번 + "다시 추천"을 누를 때만 부른다

const fixed = (v: number | null | undefined, digits = 2) =>
  typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("ko-KR", { maximumFractionDigits: digits }) : null;

const SHORT_TIME = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const when = (iso: string | null) => (iso ? SHORT_TIME.format(new Date(iso)) : null);

/** 아이디어 문장을 기본 문장으로 바꾼 이유 (서버 fallback_reason) → 쉬운 말 */
const FALLBACK_KO: Record<string, string> = {
  llm_unavailable: "AI 문장 설정이 없어 기본 문장을 썼어요",
  llm_error: "AI 문장을 받지 못해 기본 문장을 썼어요",
  format_error: "AI 문장 형식이 맞지 않아 기본 문장을 썼어요",
  number_invalid: "AI 문장에 계산에 없는 숫자가 있어 기본 문장을 썼어요",
};

function RecommendCard({ item }: { item: ContentRecommendation }) {
  const s = item.score;
  const strength = strengthLabel(s.z);
  const fallbackNote = item.fallback ? FALLBACK_KO[item.fallback_reason ?? ""] ?? "기본 문장을 썼어요" : null;
  return (
    <article className="card flex flex-col gap-3 p-4 sm:p-5" aria-label={`추천 ${item.rank}위 ${item.keyword}`}>
      <div className="min-w-0">
        <p className="text-[11px] font-semibold text-ink-500">#{item.rank}</p>
        <h3 className="truncate text-lg font-bold text-ink-900">{item.keyword}</h3>
        {item.aliases.length > 0 && <p className="text-[11px] text-ink-500">함께 본 이름: {item.aliases.join(", ")}</p>}
      </div>
      <div>
        <p className="text-xl font-bold leading-snug text-red-600">평소보다 {fixed(s.ratio) ?? "-"}배 많이 검색돼요</p>
        {strength && <p className="mt-1 text-sm font-semibold text-ink-700">변화 정도: {strength}</p>}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {s.two_sources ? (
          <Chip label={`✓ 두 소스 (검색 + YouTube 인기 영상${s.youtube_videos != null ? ` ${s.youtube_videos}개` : ""})`} tone={TONE.info} />
        ) : (
          <Chip label="검색 급상승만" tone={TONE.muted} />
        )}
        {item.company_relevance === "related" ? (
          <Chip label={`회사 관련 있음${item.company_match.length ? ` (${item.company_match.join(", ")})` : ""}`} tone={TONE.ok} />
        ) : (
          <Chip label="회사 관련 낮음" tone={TONE.warn} />
        )}
        <Chip label={item.format} tone={TONE.info} />
        {item.fallback && <Chip label="기본 문장" tone={TONE.muted} />}
      </div>
      <div>
        <p className="text-xs font-semibold text-ink-500">영상 아이디어</p>
        <p className="mt-1 border-l-2 border-brand-500 bg-ink-50 px-3 py-2 text-sm leading-relaxed text-ink-700">{item.idea}</p>
        {fallbackNote && <p className="mt-1 text-[11px] text-ink-500">{fallbackNote}</p>}
      </div>
      <div className="mt-auto flex flex-wrap items-center gap-2">
        <button type="button" className="btn-primary disabled:cursor-not-allowed disabled:opacity-50" disabled title={SEND_TO_MARKETING_BLOCKED}>
          마케팅에 소재로 보내기
        </button>
      </div>
    </article>
  );
}

type State =
  | { kind: "loading"; previous: ContentRecommendOutput | null }
  | { kind: "ok"; out: ContentRecommendOutput }
  | { kind: "failed"; reason: string; previous: ContentRecommendOutput | null };

/** dataUp: 화면이 이미 받은 데이터 AI 상태 확인 결과 (false면 부르지 않고 연결 확인 필요) */
export function ContentRecommend({ onProbe, dataUp = null }: { onProbe: (p: LoopProbe) => void; dataUp?: boolean | null }) {
  const [run, setRun] = useState(0);
  const [state, setState] = useState<State>({ kind: "loading", previous: null });

  useEffect(() => {
    if (!kpiLive) return;
    let live = true;
    setState((s) => ({ kind: "loading", previous: s.kind === "ok" ? s.out : s.previous }));
    dataAi.contentRecommend()
      .then((r) => live && setState({ kind: "ok", out: r.output }))
      .catch((e: Error) => live && setState((s) => ({ kind: "failed", reason: e.message, previous: s.kind === "loading" ? s.previous : null })));
    return () => { live = false; };
  }, [run]);

  useEffect(() => {
    if (!kpiLive || dataUp === false) {
      onProbe("failed");
      return;
    }
    onProbe(state.kind === "ok" ? "ok" : state.kind === "failed" ? "failed" : "checking");
  }, [state.kind, dataUp, onProbe]);

  if (!kpiLive) {
    return (
      <Panel id="data-trend-recommend" title="2. 영상 소재 추천">
        <Empty>데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 추천을 받지 않아요. backend/의 scripts/run_web.sh로 화면을 켜면 토큰이 들어갑니다.</Empty>
      </Panel>
    );
  }

  const out = state.kind === "ok" ? state.out : state.previous;
  const meta = out ? [
    out.end_date ? `판정 ${out.end_date}까지` : null,
    `네이버 호출 ${out.naver_calls}번`,
    out.youtube_status === "known" && out.snapshot_at ? `YouTube 스냅샷 ${when(out.snapshot_at)}` : null,
  ].filter(Boolean).join(" · ") : null;

  return (
    <Panel id="data-trend-recommend" title="2. 영상 소재 추천" hint="담당: 데이터분석 AI · content_recommend → 마케팅"
      tools={<button type="button" className="btn-ghost" disabled={state.kind === "loading"} onClick={() => setRun((n) => n + 1)}>다시 추천</button>}>
      <div className="space-y-4">
        <p className="text-sm leading-relaxed text-ink-700">
          급상승한 소재만 순위대로 보여 줘요. 검색 급상승과 YouTube 인기 영상에 둘 다 나온 소재가 앞에 와요.
          회사 관련성은 표시만 하고 순위에서 빼지 않아요.
        </p>
        {meta && <p className="text-[11px] text-ink-500">{meta}</p>}
        {out?.youtube_status === "unknown" && out.youtube_reason && (
          <p className="text-xs text-amber-700">YouTube 후보: {UNKNOWN} · {out.youtube_reason}</p>
        )}
        {out && out.naver_failed.length > 0 && (
          <p className="text-xs text-amber-700">네이버를 읽지 못한 것: {out.naver_failed.join(", ")}</p>
        )}
        {state.kind === "loading" && <p className="text-xs text-ink-500" aria-live="polite">추천하는 중… (급상승 소재를 고르고 소재마다 아이디어 문장을 씁니다)</p>}
        {state.kind === "failed" && (
          <ErrorBox message={`${out ? "새로 추천하지 못해 이전 결과를 보여 줍니다. " : ""}${state.reason}. ${DATA_SERVER}가 켜져 있는지 확인해 주세요.`} onRetry={() => setRun((n) => n + 1)} />
        )}
        {!out ? (
          state.kind === "loading" ? <Waiting /> : null
        ) : out.items.length === 0 ? (
          <Empty>{out.empty_reason ?? "추천할 소재가 없어요"}</Empty>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {out.items.map((item) => <RecommendCard key={`${item.rank}-${item.keyword}`} item={item} />)}
          </div>
        )}
        <p className="text-[11px] text-ink-500">마케팅에 소재로 보내기: {SEND_TO_MARKETING_BLOCKED}</p>
      </div>
    </Panel>
  );
}
