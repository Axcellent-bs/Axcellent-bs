import { useEffect, useState } from "react";
import { Chip, Empty, Panel, TONE, Waiting } from "@/components/finance/bits";
import type { LoopProbe } from "@/lib/contentLoop";
import { DATA_SERVER, dataAi, kpiLive, type PromoConcept, type PromoConceptsOutput, type PromoTrendKind } from "@/lib/data";

// 트렌드 2단계 맨 위 "광고·홍보 영상 기획안": 데이터 AI(8106) promo_concepts 출력을 그대로 보여 준다 (계약 PromoConceptsOutput, #415).
// - 기획안 카드: 제목·컨셉·형식 칩·콘티 3컷·등장 상품·빌려 온 트렌드 칩·주의사항·코드 경고. 숫자는 서버 값만
// - 재료 요약(트렌드·상품 수·출처 상태와 이유)과 검증에서 버린 기획안(제목·이유)
// - "다시 만들기"는 계약의 regenerate 옵션. 서버는 하루 캐시라 처음에는 오늘 만든 기획안을 그대로 준다
// - 👍/👎는 피드백을 저장할 곳(안전 관문 등록)이 아직 없어 꺼 둔다
// - 응답 결과(확인 중·정상·실패)는 콘텐츠 루프 2단계 칩으로 올린다(onProbe)

const TIME = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const when = (iso: string | null) => (iso ? TIME.format(new Date(iso)) : null);
const won = (v: number | null) => (typeof v === "number" ? `${v.toLocaleString("ko-KR")}원` : null);
const NO_REASON = "서버가 이유를 주지 않았어요";
const FEEDBACK_BLOCKED = "피드백 저장은 안전 관문 등록 후 열림";

const KIND_LABEL: Record<PromoTrendKind, string> = { popular: "지금 인기", season: "시즌", event: "회사 이벤트" };

function ConceptCard({ c }: { c: PromoConcept }) {
  return (
    <li className="card space-y-3 p-4">
      <div className="space-y-1">
        <p className="flex flex-wrap items-center gap-2 font-semibold text-ink-900">
          {c.title}
          {c.format && <Chip label={c.format} tone={TONE.info} />}
        </p>
        {c.concept && <p className="text-sm text-ink-700">{c.concept}</p>}
        <div className="flex flex-wrap gap-1.5">
          <Chip label={`빌려 온 트렌드 · ${c.borrowed_trend}`} tone={TONE.muted} />
          {c.trend_kind && <Chip label={KIND_LABEL[c.trend_kind]} tone={TONE.muted} />}
        </div>
      </div>
      {c.storyboard.length > 0 && (
        <ol className="grid gap-2 sm:grid-cols-3" aria-label="콘티">
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
    out.season_status === "known" ? { text: "카테고리 시즌 읽음", warn: false }
      : { text: `시즌: ${out.season_reason ?? NO_REASON}`, warn: true },
  ];
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
    </div>
  );
}

type State = { kind: "loading" } | { kind: "ok"; out: PromoConceptsOutput } | { kind: "failed"; reason: string };

export function PromoConcepts({ onProbe }: { onProbe: (p: LoopProbe) => void }) {
  // regenerate: 0 = 처음(하루 캐시), 1 이상 = "다시 만들기"를 누른 횟수
  const [regen, setRegen] = useState(0);
  const [state, setState] = useState<State>({ kind: "loading" });
  useEffect(() => {
    if (!kpiLive) return;
    let live = true;
    setState({ kind: "loading" });
    dataAi.promoConcepts(regen > 0)
      .then((r) => live && setState({ kind: "ok", out: r.output }))
      .catch((e: Error) => live && setState({ kind: "failed", reason: e.message }));
    return () => { live = false; };
  }, [regen]);
  useEffect(() => {
    onProbe(!kpiLive ? "failed" : state.kind === "ok" ? "ok" : state.kind === "failed" ? "failed" : "checking");
  }, [state.kind, onProbe]);
  if (!kpiLive) return null;

  const out = state.kind === "ok" ? state.out : null;
  const meta = out ? [
    out.generated_at ? `만든 시각 ${when(out.generated_at)}` : null,
    out.cached ? "오늘 만든 기획안" : null,
    `네이버 호출 ${out.naver_calls}번`,
  ].filter(Boolean).join(" · ") : "회사 상품과 지금 인기·시즌 트렌드로 만든 홍보 영상 기획안이에요.";

  return (
    <Panel id="data-trend-promo" title="광고·홍보 영상 기획안" hint={meta}
      tools={<button type="button" className="btn-ghost" disabled={state.kind === "loading"} onClick={() => setRegen((n) => n + 1)}>다시 만들기</button>}>
      {state.kind === "loading" ? <Waiting what="기획안을 받는 중…" />
        : state.kind === "failed" ? <Empty>기획안을 받지 못했어요. {state.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>
        : out && (
          <div className="space-y-4">
            {out.summary && <p className="text-sm text-ink-700">{out.summary}</p>}
            {out.concepts.length === 0
              ? <Empty>{out.empty_reason ?? "기획안이 없어요."}</Empty>
              : <ul className="space-y-3">{out.concepts.map((c) => <ConceptCard key={c.concept_id} c={c} />)}</ul>}
            <Ingredients out={out} />
            {out.dropped.length > 0 && (
              <details className="text-xs text-ink-700">
                <summary className="cursor-pointer font-semibold text-ink-900">버린 기획안 {out.dropped.length}개</summary>
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
