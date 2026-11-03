import { useEffect, useState } from "react";
import { Chip, Empty, ErrorBox, Panel, TONE, Waiting } from "@/components/finance/bits";
import { SEND_TO_MARKETING_BLOCKED, type LoopProbe } from "@/lib/contentLoop";
import { CANDIDATE_ORIGIN_LABEL, DATA_SERVER, UNKNOWN, dataAi, kpiLive, type ContentRecommendOutput, type ContentRecommendation, type ReferenceTopic } from "@/lib/data";
import { strengthLabel } from "@/lib/trendWording";

// 트렌드 탭 2단계 "영상 소재 추천" (#trend/recommend): 데이터 AI(8106) content_recommend 출력을 그대로 보여 준다.
// - 순위·배수·두 소스·회사 관련성·아이디어 문장·형식은 모두 서버 값이다. 화면은 자릿수와 쉬운 말만 정한다
//   ("평소보다 N배 많이 검색돼요" + 변화 정도는 1단계 카드와 같은 lib/trendWording.ts)
// - 아이디어 문장이 서버 기본 문장(fallback)이면 "기본 문장" 칩을 단다
// - 추천이 없으면 서버 이유(empty_reason)를 그대로 보여 준다. 화면이 채우지 않는다
// - "마케팅에 소재로 보내기"는 아직 못 해서 누를 수 없고, 이유는 콘텐츠 루프와 같은 문장이다
// - 응답 결과(확인 중·정상·실패)는 콘텐츠 루프 2단계 칩으로 올린다(onProbe). 칩을 위해 따로 부르지 않는다
// - 묶음은 서버 값(group) 그대로: "회사 상품과 연결된 소재"와 "일반 화제". 순위는 서버 순위 그대로
// - 회사 설정의 "추천 안 함" 분류라 뺀 소재(reference_items)는 접어서 분류·이유와 함께 보여 준다
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
  product_invalid: "AI 문장에 연결되지 않은 상품이 있어 기본 문장을 썼어요",
};

/** 유사도를 재지 못한 까닭 (서버 similarity_failure) → 쉬운 말 */
const SIM_FAILURE_KO: Record<string, string> = {
  not_configured: "임베딩 설정이 없음",
  config_error: "임베딩 설정 오류",
  request_failed: "임베딩 요청 실패",
};

/** 상품 연결 근거 (서버 match) → 쉬운 말 */
const MATCH_KO: Record<string, string> = { name: "이름이 겹침", category: "카테고리가 겹침", embedding: "뜻이 가까움" };

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
          <Chip label={item.relevance_from === "products" ? "회사 상품과 연결됨"
            : `회사 관련 있음${item.company_match.length ? ` (${item.company_match.join(", ")})` : ""}`} tone={TONE.ok} />
        ) : (
          <Chip label="회사 관련 낮음" tone={TONE.warn} />
        )}
        {(item.origins ?? []).map((o) => <Chip key={o} label={`출처: ${CANDIDATE_ORIGIN_LABEL[o]}`} tone={TONE.muted} />)}
        {/* 밈 형식: 서버 값 그대로. 분류하지 못했으면 이유를 마우스를 올리면 보여 준다 */}
        {item.meme_format && (item.meme_format === "unknown" ? (
          <span title={item.meme_reason ?? undefined}><Chip label="밈 형식: 확인할 수 없음" tone={TONE.muted} /></span>
        ) : (
          <span title={`대표 영상 제목 ${item.meme_titles ?? 0}개로 분류${item.meme_reason ? ` · ${item.meme_reason}` : ""}`}><Chip label={`밈 형식: ${item.meme_format}`} tone={TONE.info} /></span>
        ))}
        {/* 주제 분류: 서버 값 그대로. 분류하지 못했거나 목록 밖 답이었으면 이유를 마우스를 올리면 보여 준다 */}
        {item.topic_category && (
          <span title={item.topic_category_reason ?? undefined}>
            <Chip label={`주제: ${item.topic_category === "unknown" ? "확인할 수 없음" : item.topic_category}`} tone={TONE.muted} />
          </span>
        )}
        <Chip label={item.format} tone={TONE.info} />
        {item.fallback && <Chip label="기본 문장" tone={TONE.muted} />}
      </div>
      {/* 연결할 실제 상품: 서버가 회사 상품 목록에서 고른 값 그대로 (없으면 표시 안 함) */}
      {item.products && item.products.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-ink-500">연결할 상품</p>
          <ul className="mt-1 space-y-0.5 text-sm text-ink-800">
            {item.products.map((p) => (
              <li key={p.product_id}>
                <b>{p.name}</b> <span className="text-[11px] text-ink-500">{p.product_id}{p.category_name ? ` · ${p.category_name}` : ""} · {MATCH_KO[p.match] ?? p.match}{p.similarity != null ? ` (유사도 ${fixed(p.similarity)}${p.margin != null ? `, 2등과 차이 ${fixed(p.margin)}` : ""})` : ""}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
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

/** 참고용(추천 안 함) 소재 한 줄: 서버 분류·이유 그대로 */
function ReferenceRow({ item }: { item: ReferenceTopic }) {
  return (
    <li className="flex flex-wrap items-center gap-1.5 py-1.5">
      <b className="text-sm text-ink-900">{item.keyword}</b>
      <Chip label={`주제: ${item.topic_category}`} tone={TONE.warn} />
      <span className="text-xs text-ink-600">평소보다 {fixed(item.ratio) ?? "-"}배 · {item.reason}</span>
      {item.topic_category_reason && <span className="text-[11px] text-ink-500">({item.topic_category_reason})</span>}
    </li>
  );
}

/** 묶음 하나 (제목 + 카드 목록). 비었으면 서버 값이 없다는 한 줄 */
function RecommendGroup({ title, hint, items, empty }: { title: string; hint: string; items: ContentRecommendation[]; empty: string }) {
  return (
    <section aria-label={title} className="space-y-2">
      <h3 className="text-sm font-bold text-ink-900">{title} <span className="font-normal text-ink-500">{items.length}개</span></h3>
      <p className="text-[11px] text-ink-500">{hint}</p>
      {items.length === 0 ? (
        <p className="text-xs text-ink-500">{empty}</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => <RecommendCard key={`${item.rank}-${item.keyword}`} item={item} />)}
        </div>
      )}
    </section>
  );
}

/** 묶음: 서버 group 값. 예전 응답(group 없음)은 연결 상품 유무로 본다 */
const groupOf = (i: ContentRecommendation) => i.group ?? ((i.products?.length ?? 0) > 0 ? "product_linked" : "general");

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
  const references = out?.reference_items ?? [];
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
          회사 상품과 연결된 소재와 일반 화제로 나눠 보여 주고, 회사가 추천하지 않기로 한 주제는 참고용으로 접어 둬요.
        </p>
        {meta && <p className="text-[11px] text-ink-500">{meta}</p>}
        {out?.youtube_status === "unknown" && out.youtube_reason && (
          <p className="text-xs text-amber-700">YouTube 후보: {UNKNOWN} · {out.youtube_reason}</p>
        )}
        {out?.catalog_status === "unknown" && out.catalog_reason && out.items.length > 0 && (
          <p className="text-xs text-amber-700">상품 연결: {UNKNOWN} · {out.catalog_reason}</p>
        )}
        {out?.catalog_status === "known" && out.similarity_status === "unknown" && out.similarity_reason && (
          <p className="text-[11px] text-ink-500">
            상품 {out.catalog_products ?? 0}개와 이름·카테고리 글자만 맞춰 봤어요 (뜻 유사도:{" "}
            {out.similarity_failure ? `${SIM_FAILURE_KO[out.similarity_failure] ?? out.similarity_failure} · ` : ""}{out.similarity_reason})
          </p>
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
          <div className="space-y-6">
            <RecommendGroup
              title="회사 상품과 연결된 소재"
              hint="회사 상품 이름·카테고리가 겹치거나 뜻이 가장 가까운 상품이 기준 이상인 소재예요."
              items={out.items.filter((i) => groupOf(i) === "product_linked")}
              empty="이번에는 회사 상품과 연결된 소재가 없어요."
            />
            <RecommendGroup
              title="일반 화제"
              hint="급상승했지만 회사 상품과는 연결되지 않은 소재예요."
              items={out.items.filter((i) => groupOf(i) === "general")}
              empty="일반 화제가 없어요."
            />
          </div>
        )}
        {references.length > 0 && (
          <details className="rounded-lg border border-ink-200 px-3 py-2">
            <summary className="cursor-pointer text-sm font-semibold text-ink-700">
              참고용 (추천 안 함) {references.length}개
            </summary>
            <p className="mt-1 text-[11px] text-ink-500">
              급상승했지만 회사 설정에서 추천하지 않기로 한 주제예요{out?.excluded_categories?.length ? ` (${out.excluded_categories.join(", ")})` : ""}.
            </p>
            <ul className="divide-y divide-ink-100">
              {references.map((r) => <ReferenceRow key={r.keyword} item={r} />)}
            </ul>
          </details>
        )}
        <p className="text-[11px] text-ink-500">마케팅에 소재로 보내기: {SEND_TO_MARKETING_BLOCKED}</p>
      </div>
    </Panel>
  );
}
