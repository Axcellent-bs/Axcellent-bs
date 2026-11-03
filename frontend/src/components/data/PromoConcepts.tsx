import { Chip, Empty, Waiting } from "@/components/finance/bits";
import { useSkill, type SkillState } from "@/components/data/useSkill";
import { DATA_SERVER, dataAi, kpiLive, type PromoConcept, type PromoConceptsOutput, type PromoTrendKind } from "@/lib/data";

// 트렌드 2단계(추천): 캣북스 숏츠 광고 기획안 초안. promo_concepts(계약 PromoConceptsOutput) 응답 그대로 보여 준다.
// - 맨 위: 제목 · 다시 만들기(계약 regenerate, 서버는 하루 캐시) · 재료 한 줄(상품·트렌드·시즌·형식 개수, 못 읽은 출처는 서버 이유)
//   · "뺀 재료 N개 보기"(접힘: 뺀 지금 인기 주제·시즌 확인 키워드·버린 초안과 서버 이유)
// - 왼쪽: 잘 되는 숏츠 형식 막대. 값은 기획안 재료 shorts_formats(서버 대표값)라 기획안 칩의 평균 조회수와 같다.
//   막대 길이는 그중 가장 큰 값에 대한 비율(그리기용). 아래 표본 한계는 shorts_stats의 서버 문장 그대로
// - 오른쪽 카드: 칩(길이·형식·트렌드 출처) → 제목 → "왜 맞나" → 콘티(넓은 화면 가로, 좁은 화면 세로) → 등장 상품·가격 → 주의(노랑)
//   → [기획팀에 보내기] 꺼 둠(승인 절차 등록 전)
// - 트렌드 출처 색: 지금 인기 = 파랑, 시즌 = 보라, 회사 이벤트 = 초록, 트렌드 없음 = 회색
// - 콘티 문장 앞의 "1컷:" 같은 번호는 떼고 화면이 컷 번호를 한 번만 붙인다
// - 응답은 트렌드 화면(TrendScan)이 한 번 받아 1단계 요약 카드와 같이 쓴다. 응답 결과는 루프 2단계 칩에도 쓴다

const TIME = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const when = (iso: string | null) => (iso ? TIME.format(new Date(iso)) : null);
const won = (v: number | null) => (typeof v === "number" ? `${v.toLocaleString("ko-KR")}원` : null);
const count = (v: number) => Math.round(v).toLocaleString("ko-KR");
const NO_REASON = "서버가 이유를 주지 않았어요";
const SEND_BLOCKED = "보내기는 승인 절차 등록 후 열려요";

const KIND: Record<PromoTrendKind, { label: string; tone: string }> = {
  popular: { label: "지금 인기", tone: "bg-brand-50 text-brand-700" },
  season: { label: "시즌", tone: "bg-violet-50 text-violet-700" },
  event: { label: "회사 이벤트", tone: "bg-emerald-50 text-emerald-700" },
};
const NO_TREND = "bg-ink-100 text-ink-500";

/** "1컷: 문장", "1컷 | 문장", "1. 문장" → "문장" (번호는 화면이 붙인다) */
const cutText = (s: string) => s.replace(/^\s*(\d+\s*컷|컷\s*\d+|\d+)\s*[:|.)\-–·]?\s*/, "");

function TrendChip({ c }: { c: PromoConcept }) {
  if (!c.borrowed_trend) return <Chip label="트렌드 없음" tone={NO_TREND} />;
  const k = c.trend_kind ? KIND[c.trend_kind] : null;
  return <Chip label={k ? `${k.label} · ${c.borrowed_trend}` : c.borrowed_trend} tone={k?.tone ?? NO_TREND} />;
}

function ConceptCard({ c }: { c: PromoConcept }) {
  const cautions = [...c.warnings, ...c.cautions];
  const cuts = c.storyboard.map(cutText).filter(Boolean);
  return (
    <li className="card space-y-3 p-4">
      <div className="flex flex-wrap gap-1.5">
        {typeof c.duration_seconds === "number" ? <Chip label={`${c.duration_seconds}초`} tone="bg-ink-100 text-ink-700" />
          : c.format && <Chip label={c.format} tone="bg-ink-100 text-ink-700" />}
        {c.shorts_format && (
          <Chip label={[`형식 · ${c.shorts_format}`, typeof c.shorts_format_avg_views === "number" ? `평균 조회수 ${count(c.shorts_format_avg_views)}회` : null].filter(Boolean).join(" · ")}
            tone="bg-ink-100 text-ink-700" />
        )}
        <TrendChip c={c} />
      </div>
      <div>
        <h3 className="text-lg font-bold text-ink-900">{c.title}</h3>
        {c.concept && <p className="mt-0.5 text-sm text-ink-700">{c.concept}</p>}
      </div>
      {c.fit_reason && (
        <div className="rounded-xl bg-brand-50 px-3 py-2 text-sm text-ink-900">
          <p className="text-xs font-semibold text-brand-700">왜 맞나</p>
          <p className="mt-0.5">{c.fit_reason}</p>
        </div>
      )}
      {cuts.length > 0 && (
        <ol aria-label="콘티" className="flex flex-col gap-2 sm:flex-row">
          {cuts.map((cut, i) => (
            <li key={i} className="min-w-0 flex-1 rounded-xl bg-ink-50 px-3 py-2 text-sm leading-relaxed text-ink-700">
              <span className="mb-1 block text-xs font-semibold text-ink-500">{i + 1}컷</span>
              {cut}
            </li>
          ))}
        </ol>
      )}
      {c.products.length > 0 && (
        <div className="text-sm">
          <p className="mb-1 text-xs font-semibold text-ink-500">등장 상품</p>
          <ul className="flex flex-wrap gap-1.5">
            {c.products.map((p) => (
              <li key={p.product_id} className="chip bg-surface text-sm text-ink-900 ring-1 ring-ink-100">
                {p.name}{won(p.price) && <b className="ml-1.5">{won(p.price)}</b>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {cautions.length > 0 && (
        <div className="rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <p className="text-xs font-semibold text-amber-700">주의</p>
          <ul className="mt-0.5 list-disc space-y-0.5 pl-4">{cautions.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 border-t border-ink-100 pt-3">
        <button type="button" className="btn-primary" disabled aria-describedby={`send-${c.concept_id}`}>기획팀에 보내기</button>
        <span id={`send-${c.concept_id}`} className="text-xs text-ink-500">{SEND_BLOCKED}</span>
      </div>
    </li>
  );
}

/** 재료 한 줄: 개수, 못 읽은 출처는 서버 이유 */
function Ingredients({ out }: { out: PromoConceptsOutput }) {
  const trends = out.trends.filter((t) => t.kind === "popular").length;
  const seasons = out.trends.filter((t) => t.kind === "season").length;
  const parts = [
    out.catalog_status === "known" ? `상품 ${out.products.toLocaleString("ko-KR")}개` : null,
    `트렌드 ${trends}개`,
    `시즌 ${seasons}개`,
    out.shorts_status === "known" ? `형식 ${out.shorts_formats?.length ?? 0}개` : null,
  ].filter(Boolean);
  const warns = [
    out.catalog_status === "unknown" ? `상품: ${out.catalog_reason ?? NO_REASON}` : null,
    out.popular_status === "unknown" ? `지금 인기: ${out.popular_reason ?? NO_REASON}` : null,
    out.season_status === "unknown" ? `시즌: ${out.season_reason ?? NO_REASON}` : null,
    out.shorts_status !== "known" ? `숏츠 형식: ${out.shorts_reason ?? NO_REASON}` : null,
    out.popular_trend_note ? `지금 인기 트렌드를 쓴 초안 없음: ${out.popular_trend_note}` : null,
  ].filter((x): x is string => Boolean(x));
  const skipped = out.skipped_trends ?? [];
  const checks = out.season_checks ?? [];
  const ratio = (v: number | null) => (typeof v === "number" ? `${v.toLocaleString("ko-KR")}배` : "–");
  return (
    <div className="space-y-1 text-sm">
      <p className="text-ink-700"><span className="font-semibold text-ink-900">재료</span> · {parts.join(" · ")}</p>
      {warns.map((w) => <p key={w} className="text-xs text-amber-700">{w}</p>)}
      {(skipped.length > 0 || checks.length > 0 || out.dropped.length > 0) && (
        <details className="text-xs text-ink-700">
          <summary className="cursor-pointer font-semibold text-ink-900">
            {skipped.length > 0 ? `뺀 재료 ${skipped.length}개 보기` : "뺀 재료 없음 · 시즌 확인·버린 초안 보기"}
          </summary>
          <div className="mt-2 space-y-2 rounded-xl bg-ink-50 px-3 py-2">
            {skipped.length > 0 && (
              <ul className="space-y-0.5">{skipped.map((t) => <li key={t.name}><b>{t.name}</b> · {t.reason}</li>)}</ul>
            )}
            {checks.length > 0 && (
              <div>
                <p className="font-semibold text-ink-900">시즌 확인한 키워드 {checks.length}개 (작년 같은 달·다음 달 검색 ÷ 기간 평균)</p>
                <ul className="mt-0.5 space-y-0.5">
                  {checks.map((s) => (
                    <li key={s.keyword}>
                      <b>{s.keyword}</b> · {s.status === "known" ? `같은 달 ${ratio(s.same_month_ratio)} · 다음 달 ${ratio(s.next_month_ratio)}` : (s.reason ?? NO_REASON)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {out.dropped.length > 0 && (
              <div>
                <p className="font-semibold text-ink-900">검사에서 버린 초안 {out.dropped.length}개</p>
                <ul className="mt-0.5 space-y-0.5">{out.dropped.map((d, i) => <li key={`${d.title}-${i}`}><b>{d.title}</b> · {d.reason}</li>)}</ul>
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

/** 왼쪽: 잘 되는 숏츠 형식 막대(기획안 재료 값) + 표본 한계(shorts_stats 서버 문장) */
function FormatBars({ out }: { out: PromoConceptsOutput }) {
  const shorts = useSkill(() => dataAi.shortsStats());
  const formats = out.shorts_formats ?? [];
  const max = Math.max(0, ...formats.map((f) => f.avg_views));
  const s = shorts.state;
  return (
    <section aria-labelledby="promo-formats-title" className="card space-y-3 p-4">
      <h3 id="promo-formats-title" className="text-base font-semibold text-ink-900">잘 되는 숏츠 형식</h3>
      {formats.length === 0 ? (
        <p className="text-sm text-ink-500">{out.shorts_reason ?? "기획안에 쓴 숏츠 형식이 없어요."}</p>
      ) : (
        <ul className="space-y-3">
          {formats.map((f) => (
            <li key={f.name}>
              <p className="flex items-baseline justify-between gap-2 text-sm">
                <span className="font-semibold text-ink-900">{f.name}</span>
                <span className="tabular-nums text-ink-700">평균 조회수 {count(f.avg_views)}회</span>
              </p>
              <div className="mt-1 h-2.5 rounded-full bg-ink-100" aria-hidden>
                <div className="h-2.5 rounded-full bg-brand-500" style={{ width: `${max > 0 ? Math.max(2, (f.avg_views / max) * 100) : 0}%` }} />
              </div>
              <p className="mt-0.5 text-xs text-ink-500">숏츠 {f.count.toLocaleString("ko-KR")}개</p>
            </li>
          ))}
        </ul>
      )}
      <div className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">
        <p className="font-semibold">표본 한계</p>
        {s.kind === "loading" ? <p>받는 중…</p>
          : s.kind === "failed" ? <p>받지 못했어요. {s.reason}</p>
          : <p>{s.out.status === "unknown" ? (s.out.reason ?? NO_REASON) : (s.out.limitation || NO_REASON)}</p>}
      </div>
    </section>
  );
}

/** 응답은 트렌드 화면(TrendScan)이 한 번 받는다. onRegenerate = "다시 만들기" */
export function PromoConcepts({ state, onRegenerate }: { state: SkillState<PromoConceptsOutput>; onRegenerate: () => void }) {
  if (!kpiLive) return null;
  const out = state.kind === "ok" ? state.out : null;
  const meta = out ? [out.generated_at ? `만든 시각 ${when(out.generated_at)}` : null, out.cached ? "오늘 만든 초안" : null].filter(Boolean).join(" · ") : null;
  return (
    <section id="data-trend-promo" aria-labelledby="data-trend-promo-title" className="space-y-4">
      <div className="card space-y-2 p-4 sm:p-5">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <h2 id="data-trend-promo-title" className="text-lg font-bold text-ink-900">캣북스 숏츠 광고 기획안 초안</h2>
            <p className="text-xs text-ink-500">{meta || "확정은 기획팀, 제작·게시는 마케팅이 해요."}</p>
          </div>
          <button type="button" className="btn-ghost" disabled={state.kind === "loading"} onClick={onRegenerate}>다시 만들기</button>
        </div>
        {out && <Ingredients out={out} />}
      </div>
      {state.kind === "loading" ? <Waiting what="기획안 초안을 받는 중…" />
        : state.kind === "failed" ? <Empty>기획안 초안을 받지 못했어요. {state.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
        : out && (
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2.4fr)]">
            <FormatBars out={out} />
            {out.concepts.length === 0
              ? <Empty>{out.empty_reason ?? "기획안 초안이 없어요."}</Empty>
              : <ul className="space-y-4">{out.concepts.map((c) => <ConceptCard key={c.concept_id} c={c} />)}</ul>}
          </div>
        )}
    </section>
  );
}
