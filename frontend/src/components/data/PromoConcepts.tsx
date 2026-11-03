import { Chip, Empty, Panel, TONE, Waiting } from "@/components/finance/bits";
import type { SkillState } from "@/components/data/useSkill";
import { DATA_SERVER, kpiLive, type PromoConcept, type PromoConceptsOutput, type PromoTrendKind } from "@/lib/data";

// 트렌드 2단계 맨 위 "광고·홍보 숏츠 기획안 초안": 데이터 AI(8106) promo_concepts 출력을 그대로 보여 준다 (계약 PromoConceptsOutput).
// - 초안 카드: 제목·컨셉·길이·콘티·빌려 온 숏츠 형식(평균 조회수)·트렌드(없으면 "트렌드 없음")·맞는 이유·등장 상품·주의사항·코드 경고. 숫자는 서버 값만
// - 흐름: 데이터(감지·분석·초안) → 기획(확정) → 마케팅(제작·게시) → 데이터(성과 측정·다음 초안에 반영)
// - 재료 요약(트렌드·상품 수·출처 상태와 이유)과 검증에서 버린 기획안(제목·이유)
// - "다시 만들기"는 계약의 regenerate 옵션. 서버는 하루 캐시라 처음에는 오늘 만든 기획안을 그대로 준다
// - 👍/👎는 피드백을 저장할 곳(안전 관문 등록)이 아직 없어 꺼 둔다
// - 응답 결과(확인 중·정상·실패)는 트렌드 화면이 콘텐츠 루프 2단계 칩으로 쓴다

const TIME = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const when = (iso: string | null) => (iso ? TIME.format(new Date(iso)) : null);
const won = (v: number | null) => (typeof v === "number" ? `${v.toLocaleString("ko-KR")}원` : null);
const views = (v: number | null | undefined) => (typeof v === "number" ? `평균 조회수 ${Math.round(v).toLocaleString("ko-KR")}회` : null);
const NO_REASON = "서버가 이유를 주지 않았어요";
const FEEDBACK_BLOCKED = "피드백 저장은 안전 관문 등록 후 열림";

const KIND_LABEL: Record<PromoTrendKind, string> = { popular: "지금 인기", season: "시즌", event: "회사 이벤트" };

function ConceptCard({ c }: { c: PromoConcept }) {
  return (
    <li className="card space-y-3 p-4">
      <div className="space-y-1">
        <p className="flex flex-wrap items-center gap-2 font-semibold text-ink-900">
          {c.title}
          {typeof c.duration_seconds === "number" ? <Chip label={`숏츠 ${c.duration_seconds}초`} tone={TONE.info} />
            : c.format && <Chip label={c.format} tone={TONE.info} />}
        </p>
        {c.concept && <p className="text-sm text-ink-700">{c.concept}</p>}
        <div className="flex flex-wrap gap-1.5">
          {c.shorts_format && <Chip label={[`빌려 온 숏츠 형식 · ${c.shorts_format}`, views(c.shorts_format_avg_views)].filter(Boolean).join(" · ")} tone={TONE.info} />}
          <Chip label={c.borrowed_trend ? `빌려 온 트렌드 · ${c.borrowed_trend}` : "트렌드 없음 (상품 + 숏츠 형식)"} tone={TONE.muted} />
          {c.trend_kind && <Chip label={KIND_LABEL[c.trend_kind]} tone={TONE.muted} />}
        </div>
        {c.fit_reason && <p className="text-xs text-ink-600">맞는 이유: {c.fit_reason}</p>}
      </div>
      {c.storyboard.length > 0 && (
        <ol className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6" aria-label="콘티">
          {c.storyboard.map((cut, i) => (
            <li key={i} className="rounded-xl bg-ink-50 px-3 py-2 text-xs leading-relaxed text-ink-700">
              <span className="mb-1 block text-[11px] font-semibold text-ink-500">{i + 1}컷</span>
              {cut}
            </li>
          ))}
        </ol>
      )}
      {c.products.length > 0 && (
        <div className="text-xs text-ink-700">
          <p className="mb-1 font-semibold text-ink-900">등장 상품</p>
          <ul className="flex flex-wrap gap-1.5">
            {c.products.map((p) => (
              <li key={p.product_id} className="chip bg-ink-100 text-ink-700">
                {[p.name, p.category_name, won(p.price)].filter(Boolean).join(" · ")}
              </li>
            ))}
          </ul>
        </div>
      )}
      {c.warnings.length > 0 && (
        <ul className="space-y-0.5 text-xs text-red-700">{c.warnings.map((w) => <li key={w}>⚠ {w}</li>)}</ul>
      )}
      {c.cautions.length > 0 && (
        <div className="text-xs text-ink-700">
          <p className="mb-1 font-semibold text-ink-900">주의사항</p>
          <ul className="list-disc space-y-0.5 pl-4">{c.cautions.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-ghost" disabled aria-label="좋아요" title={FEEDBACK_BLOCKED}>👍</button>
        <button type="button" className="btn-ghost" disabled aria-label="별로예요" title={FEEDBACK_BLOCKED}>👎</button>
        <span className="text-[11px] text-ink-500">{FEEDBACK_BLOCKED}</span>
      </div>
    </li>
  );
}

/** 재료 요약: 출처마다 읽었는지, 못 읽었으면 서버 이유 그대로 */
function Ingredients({ out }: { out: PromoConceptsOutput }) {
  const lines = [
    out.catalog_status === "known" ? { text: `회사 상품 ${out.products.toLocaleString("ko-KR")}개`, warn: false }
      : { text: `회사 상품: ${out.catalog_reason ?? NO_REASON}`, warn: true },
    out.popular_status === "known" ? { text: "지금 인기 트렌드 읽음", warn: false }
      : { text: `지금 인기: ${out.popular_reason ?? NO_REASON}`, warn: true },
    out.shorts_status === "known" ? { text: `인기 숏츠 형식 ${out.shorts_formats?.length ?? 0}개`, warn: false }
      : { text: `숏츠 형식: ${out.shorts_reason ?? NO_REASON}`, warn: true },
    out.season_status === "known" ? { text: out.season_reason ?? "시즌 키워드 읽음", warn: Boolean(out.season_reason) }
      : { text: `시즌: ${out.season_reason ?? NO_REASON}`, warn: true },
  ];
  // 시즌 재료(kind = season)가 비면 서버가 확인한 키워드·배율 요약을 펼쳐 둔다
  const noSeason = !out.trends.some((t) => t.kind === "season");
  const ratio = (v: number | null) => (typeof v === "number" ? `${v.toLocaleString("ko-KR")}배` : "–");
  return (
    <div className="rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-700">
      <p className="panel-title">재료</p>
      <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
        {lines.map((l) => <li key={l.text} className={l.warn ? "text-amber-700" : undefined}>{l.text}</li>)}
      </ul>
      {out.trends.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {out.trends.map((t) => (
            <li key={`${t.kind}-${t.name}`} className="flex flex-wrap items-center gap-1.5">
              <Chip label={KIND_LABEL[t.kind]} tone={TONE.muted} />
              <span className="font-semibold text-ink-900">{t.name}</span>
              {t.detail && <span className="text-ink-500">{t.detail}</span>}
              {t.ip_risk && <Chip label={t.entity_names.length ? `실존 이름 · ${t.entity_names.join(", ")}` : "실존 이름 포함"} tone={TONE.warn} />}
            </li>
          ))}
        </ul>
      )}
      {out.popular_trend_note && <p className="mt-2 text-amber-700">지금 인기 트렌드를 쓴 초안 없음: {out.popular_trend_note}</p>}
      {(out.skipped_trends?.length ?? 0) > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer font-semibold text-ink-900">재료에서 뺀 지금 인기 {out.skipped_trends?.length}개</summary>
          <ul className="mt-1 space-y-0.5 pl-4">
            {out.skipped_trends?.map((t) => <li key={t.name}><b>{t.name}</b> · {t.reason}</li>)}
          </ul>
        </details>
      )}
      {(out.season_checks?.length ?? 0) > 0 && (
        <details className="mt-2" open={noSeason}>
          <summary className="cursor-pointer font-semibold text-ink-900">{noSeason ? "시즌 재료 없음 · " : ""}시즌 확인한 키워드 {out.season_checks?.length}개 (작년 같은 달·다음 달 ÷ 평균)</summary>
          <ul className="mt-1 space-y-0.5 pl-4">
            {out.season_checks?.map((s) => (
              <li key={s.keyword}>
                <b>{s.keyword}</b> {s.source === "category" ? "(카테고리)" : "(상품)"} ·{" "}
                {s.status === "known" ? `같은 달 ${ratio(s.same_month_ratio)} · 다음 달 ${ratio(s.next_month_ratio)}` : (s.reason ?? NO_REASON)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/** 응답은 트렌드 화면(TrendScan)이 한 번 받아 1단계 요약 카드와 같이 쓴다. onRegenerate = "다시 만들기" */
export function PromoConcepts({ state, onRegenerate }: { state: SkillState<PromoConceptsOutput>; onRegenerate: () => void }) {
  if (!kpiLive) return null;

  const out = state.kind === "ok" ? state.out : null;
  const meta = out ? [
    out.generated_at ? `만든 시각 ${when(out.generated_at)}` : null,
    out.cached ? "오늘 만든 초안" : null,
    `네이버 호출 ${out.naver_calls}번`,
  ].filter(Boolean).join(" · ") : "회사 상품과 인기 숏츠 형식·지금 인기·시즌으로 만든 숏츠 광고 기획안 초안이에요. 확정은 기획, 제작·게시는 마케팅이 해요.";

  return (
    <Panel id="data-trend-promo" title="광고·홍보 숏츠 기획안 초안" hint={meta}
      tools={<button type="button" className="btn-ghost" disabled={state.kind === "loading"} onClick={onRegenerate}>다시 만들기</button>}>
      {state.kind === "loading" ? <Waiting what="기획안 초안을 받는 중…" />
        : state.kind === "failed" ? <Empty>기획안 초안을 받지 못했어요. {state.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
        : out && (
          <div className="space-y-4">
            {out.summary && <p className="text-sm text-ink-700">{out.summary}</p>}
            {out.concepts.length === 0
              ? <Empty>{out.empty_reason ?? "기획안 초안이 없어요."}</Empty>
              : <ul className="space-y-3">{out.concepts.map((c) => <ConceptCard key={c.concept_id} c={c} />)}</ul>}
            <Ingredients out={out} />
            {out.dropped.length > 0 && (
              <details className="text-xs text-ink-700">
                <summary className="cursor-pointer font-semibold text-ink-900">버린 초안 {out.dropped.length}개</summary>
                <ul className="mt-1 space-y-0.5 pl-4">
                  {out.dropped.map((d, i) => <li key={`${d.title}-${i}`}><b>{d.title}</b> · {d.reason}</li>)}
                </ul>
              </details>
            )}
          </div>
        )}
    </Panel>
  );
}
