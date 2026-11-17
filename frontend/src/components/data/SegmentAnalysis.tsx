import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Empty, Waiting } from "@/components/finance/bits";
import { PreparingChip } from "@/components/Preparing";
import { dataAi, kpiLive, type A2AResult, type BasketLevel, type SalesBasis, type SegmentPeriod } from "@/lib/data";
import { calcOfResult } from "@/lib/salesView";
import { useStaleReload } from "./staleReload";
import {
  NOT_YET, SUPPRESSED, basketNoun, basketView, dataNote, coverageView, favoritesView, filterLabel, nextMonth, optionsView,
  parseTarget, profileView, proposalsView, questionView, retentionView, sectionState, sendResultText,
  type BasketView, type CoverageRowView, type CoverageView, type FavoriteList, type IndexTone, type FavoritesView, type GateView, type OptionsView, type ProfileView,
  type ProposalView, type ProposalsView, type QuestionView, type RetentionView, type SectionState,
} from "@/lib/segmentView";

// 고객·시장 분석: 물어보기(글로 묻기 segment_question → segment_favorites) · ○○은 누가 사나(segment_profile)
// · 함께 사는 장르·품목(basket_pairs) · 시장 커버(market_coverage)
// · 다음 달 이벤트 제안(event_proposal, 기획에 넘기기 event_proposal_send).
// 선택지(속성·값·장르·분류·자주 묻는 질문·기간)는 segment_options 응답에서만 읽는다. 보여 줄 글자는 lib/segmentView.ts가 만든다.
// skill이 아직 없거나 실패하면 탭 전체가 아니라 그 칸만 "준비 중"·다시 시도로 둔다.

type Load<T> = { data: T | null; state: SectionState | null; loading: boolean; reload: () => void };

/** key가 null이면 부르지 않는다. 실패는 문자열 대신 칸 상태(준비 중·실패)로 */
function useLoad<T>(key: string | null, load: () => Promise<T>): Load<T> {
  const [data, setData] = useState<T | null>(null);
  const [state, setState] = useState<SectionState | null>(null);
  const [loading, setLoading] = useState(false);
  const [n, setN] = useState(0);
  const ref = useRef(load);
  useEffect(() => { ref.current = load; });
  useEffect(() => {
    if (key == null) return;
    let live = true;
    setLoading(true);
    ref.current()
      .then((d) => { if (live) { setData(d); setState(null); } })
      .catch((e: Error) => { if (live) setState(sectionState(e.message)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [key, n]);
  const reload = useCallback(() => setN((x) => x + 1), []);
  return { data, state, loading, reload };
}

function Badge({ text }: { text: string | null }) {
  return text ? <span className="chip bg-amber-50 text-amber-800">{text}</span> : null;
}

/** 칸 아래 출처 줄 "계산 HH:MM (판매 기록 HH:MM까지)" (응답 근거의 계산 시각 + basis 마지막 수집) */
/** 예전 결과를 먼저 받았으면(basis.stale) "갱신 중"을 붙이고 30초 뒤 한 번 다시 부른다 */
function useCalc(res: Load<A2AResult<{ basis?: SalesBasis | null }>>): string | null {
  const c = calcOfResult(res.data);
  useStaleReload(c.stale, c.stamp, res.reload);
  return c.line;
}

function Section({ id, title, hint, badge, calc, children }: { id: string; title: string; hint?: string; badge?: string | null; calc?: string | null; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="card min-w-0 space-y-3 p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={`${id}-title`} className="text-base font-semibold text-ink-900">{title}</h2>
        <Badge text={badge ?? null} />
      </div>
      {hint && <p className="text-xs text-ink-500">{hint}</p>}
      {children}
      {calc && <p className="text-xs text-ink-400">{calc}</p>}
    </section>
  );
}

/** 칸 상태: 준비 중(skill 없음) · 실패(다시 시도) · 불러오는 중 */
function StateBox({ load, what }: { load: { state: SectionState | null; loading: boolean; reload: () => void; data: unknown }; what: string }) {
  if (load.state?.kind === "missing") {
    return (
      <p role="status" className="flex flex-wrap items-center gap-2 rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-800">
        <PreparingChip /> {what} 기능을 데이터 AI에 붙이는 중이에요. 준비되면 여기에 나옵니다.
      </p>
    );
  }
  if (load.state?.kind === "failed") {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">
        <span className="min-w-0 flex-1">{what}: {load.state.message}</span>
        <button type="button" className="btn-ghost" onClick={load.reload}>다시 시도</button>
      </div>
    );
  }
  if (load.loading && load.data == null) return <Waiting />;
  return null;
}

function Gate({ view }: { view: GateView }) {
  return (
    <div className="space-y-2 rounded-xl bg-surface-2 px-4 py-3 text-sm">
      <p className="font-medium text-ink-900">지금은 계산하지 않았어요</p>
      <ul className="space-y-1 text-ink-700">{view.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
      {view.facts.length > 0 && <ul className="flex flex-wrap gap-2">{view.facts.map((f) => <li key={f} className="chip bg-ink-100 text-ink-700">{f}</li>)}</ul>}
    </div>
  );
}

function Select({ label, value, onChange, children }: { label: string; value: string; onChange: (v: string) => void; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs text-ink-500 sm:w-auto">
      {label}
      <select className="field w-full sm:w-auto" value={value} onChange={(e) => onChange(e.target.value)}>{children}</select>
    </label>
  );
}

function Bar({ pct, muted }: { pct: number; muted?: boolean }) {
  return (
    <div className="h-2 w-full min-w-[4rem] rounded-full bg-ink-100" aria-hidden>
      <div className={`h-2 rounded-full ${muted ? "bg-ink-300" : "bg-brand-500"}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

// ---- 1) 물어보기

function FavoriteBlock({ list }: { list: FavoriteList }) {
  return (
    <div className="min-w-0 space-y-1">
      <p className="text-xs font-semibold text-brand-700">{list.title}</p>
      {list.rows.length === 0 ? <p className="text-sm text-ink-500">보여 줄 순위가 없어요</p> : (
        <ol className="list-decimal space-y-1 pl-5 text-sm">
          {list.rows.map((r, i) => (
            <li key={i}>
              <span className={`font-medium ${r.high ? "text-emerald-700" : "text-ink-900"}`}>{r.label}</span>{" "}
              <span className={`text-xs ${r.suppressed ? "text-ink-400" : "text-ink-500"}`}>— {r.detail}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Ask({ options }: { options: OptionsView }) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [period, setPeriod] = useState<SegmentPeriod>((options.periods.find((p) => p.value === "90d") ?? options.periods[0])?.value as SegmentPeriod ?? "90d");
  const [ask, setAsk] = useState<{ filters: Record<string, string>; period: SegmentPeriod } | null>(null);
  const [text, setText] = useState("");
  const [reading, setReading] = useState(false);
  const [question, setQuestion] = useState<QuestionView | null>(null);
  const [questionNote, setQuestionNote] = useState<string | null>(null);
  const key = ask ? JSON.stringify(ask) : null;
  const res = useLoad(key, () => dataAi.segmentFavorites(ask!.filters, ask!.period));
  const calc = useCalc(res);
  const view: FavoritesView | null = res.data ? favoritesView(res.data.output, options.itemNoun) : null;
  const picked = Object.fromEntries(Object.entries(draft).filter(([, v]) => v));
  const chosen = filterLabel(picked, options.attributes);

  /** 글 질문 → 고르는 칸에 채우고 바로 분석. 못 읽으면 고르는 칸을 쓰게 안내 */
  const readQuestion = async () => {
    const q = text.trim();
    if (!q) return;
    setReading(true);
    setQuestionNote(null);
    try {
      const v = questionView((await dataAi.segmentQuestion(q)).output, options);
      setQuestion(v);
      if (v.kind === "ok") {
        const p = v.period ?? period;
        setDraft(v.filters);
        setPeriod(p);
        setAsk({ filters: v.filters, period: p });
      }
    } catch (e) {
      const s = sectionState((e as Error).message);
      setQuestion(null);
      setQuestionNote(s.kind === "missing" ? "글로 묻기는 준비 중이에요. 아래에서 골라 주세요." : s.message);
    } finally {
      setReading(false);
    }
  };

  return (
    <Section id="seg-ask" title="물어보기" hint="집단을 고르면 우리 쇼핑몰에서 많이 산 것과 바깥 시장 인기를 나란히 봅니다." badge={view?.kind === "ok" ? view.badge : null} calc={calc}>
      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); void readQuestion(); }}>
        <label className="sr-only" htmlFor="seg-question">질문</label>
        <input id="seg-question" className="field min-w-0 flex-1" maxLength={200} value={text} onChange={(e) => setText(e.target.value)}
          placeholder={`예: ${options.chips[0]?.label ?? "궁금한"} 고객이 좋아하는 ${options.itemNoun}은?`} />
        <button type="submit" className="btn-primary" disabled={reading || !text.trim()}>{reading ? "읽는 중…" : "분석"}</button>
      </form>
      {question?.kind === "ok" && <p role="status" className="text-sm font-medium text-brand-700">{question.text}</p>}
      {question?.kind === "unparsed" && (
        <p role="status" className="text-sm text-amber-800">{question.text}{question.reason ? ` (${question.reason})` : ""}</p>
      )}
      {questionNote && <p role="status" className="text-xs text-ink-500">{questionNote}</p>}
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-end">
        {options.attributes.map((a) => (
          <Select key={a.key} label={a.label} value={draft[a.key] ?? ""} onChange={(v) => setDraft((d) => ({ ...d, [a.key]: v }))}>
            <option value="">전체</option>
            {a.values.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
          </Select>
        ))}
        {options.periods.length > 0 && (
          <Select label="기간" value={period} onChange={(v) => setPeriod(v as SegmentPeriod)}>
            {options.periods.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </Select>
        )}
        <button type="button" className="btn-primary" disabled={!chosen} onClick={() => setAsk({ filters: picked, period })}>
          {chosen ? `${chosen} 분석` : "집단을 골라 주세요"}
        </button>
      </div>
      {options.chips.length > 0 && (
        <ul className="flex flex-wrap gap-2" aria-label="자주 묻는 질문">
          {options.chips.map((c) => (
            <li key={c.label}>
              <button type="button" className="chip bg-ink-100 text-ink-700 hover:bg-brand-50 hover:text-brand-700" onClick={() => { setDraft(c.filters); setAsk({ filters: c.filters, period }); }}>{c.label}</button>
            </li>
          ))}
        </ul>
      )}
      <StateBox load={res} what="물어보기" />
      {ask == null && !res.state && <p className="text-xs text-ink-500">질문을 쓰거나, 속성을 고르거나, 자주 묻는 질문을 누르면 분석합니다.</p>}
      {view?.kind === "gate" && <Gate view={view} />}
      {view?.kind === "ok" && (
        <div className="space-y-3">
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="min-w-0 space-y-3 rounded-xl bg-brand-50 px-4 py-3">
              <p className="text-sm font-semibold text-ink-900">{view.header}</p>
              <FavoriteBlock list={view.genres} />
              <FavoriteBlock list={view.products} />
            </div>
            <div className="min-w-0 space-y-3 rounded-xl bg-sky-50 px-4 py-3">
              <p className="text-xs font-semibold text-sky-800">바깥 시장</p>
              {view.externalNote && <p className="text-xs text-ink-500">{view.externalNote}</p>}
              {view.external.length === 0 && !view.externalNote ? <p className="text-sm text-ink-500">이 집단에 맞춰 볼 바깥 출처가 없어요</p> : view.external.map((e, i) => (
                <div key={i} className="min-w-0 space-y-1">
                  <p className="text-sm font-medium text-ink-900">{e.title}{e.meta && <span className="text-xs font-normal text-ink-500"> · {e.meta}</span>}</p>
                  {e.note ? <p className="text-xs text-ink-500">{e.note}</p> : (
                    <ul className="space-y-0.5 text-sm">{e.rows.map((r, k) => <li key={k} className="truncate">{r.label} {r.detail && <span className="text-xs text-ink-500">— {r.detail}</span>}</li>)}</ul>
                  )}
                </div>
              ))}
            </div>
          </div>
          {view.summary && <p className="text-xs text-ink-500">요약: {view.summary}</p>}
        </div>
      )}
    </Section>
  );
}

// ---- 2) ○○은 누가 사나

function Who({ options }: { options: OptionsView }) {
  const [target, setTarget] = useState(options.targets[0]?.value ?? "");
  const [period, setPeriod] = useState<SegmentPeriod>((options.periods.find((p) => p.value === "90d") ?? options.periods[0])?.value as SegmentPeriod ?? "90d");
  const t = parseTarget(target);
  const res = useLoad(t ? `${target}|${period}` : null, () => dataAi.segmentProfile(t!.kind, t!.code, period));
  const calc = useCalc(res);
  const view: ProfileView | null = res.data ? profileView(res.data.output) : null;
  const groups = useMemo(() => [...new Set(options.targets.map((x) => x.group))], [options.targets]);
  const name = options.targets.find((x) => x.value === target)?.label;

  return (
    <Section id="seg-who" title={name ? `${name}은(는) 누가 사나` : "누가 사나"} calc={calc}
      hint="이 분야 구매자의 구성을 전체 고객 구성과 견줍니다. 배수가 1.2배 이상이면 그 집단이 특히 많이 사는 것으로 보고 초록으로 표시합니다." badge={view?.kind === "ok" ? view.badge : null}>
      {options.targets.length === 0 ? <Empty>고를 분류·장르가 아직 없어요</Empty> : (
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-end">
          <Select label="분류·장르" value={target} onChange={setTarget}>
            {groups.map((g) => (
              <optgroup key={g} label={g}>
                {options.targets.filter((x) => x.group === g).map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
              </optgroup>
            ))}
          </Select>
          {options.periods.length > 0 && (
            <Select label="기간" value={period} onChange={(v) => setPeriod(v as SegmentPeriod)}>
              {options.periods.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </Select>
          )}
        </div>
      )}
      <StateBox load={res} what="누가 사나" />
      {view?.kind === "gate" && <Gate view={view} />}
      {view?.kind === "ok" && (
        <div className="space-y-4">
          <p className="text-sm font-medium text-ink-900">{view.header}</p>
          {view.tables.length === 0 && <p className="text-sm text-ink-500">표본 부족이라 구성을 보여 주지 않아요</p>}
          {view.tables.map((tb) => (
            <div key={tb.label} className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-ink-500">
                    <th className="py-2 pr-3 font-medium">{tb.label}</th>
                    <th className="w-2/5 py-2 pr-3 font-medium">이 분야 구매자</th>
                    <th className="py-2 pr-3 text-right font-medium">전체 고객</th>
                    <th className="py-2 text-right font-medium">배수</th>
                  </tr>
                </thead>
                <tbody>
                  {tb.rows.map((r, i) => (
                    <tr key={i} className="border-t border-ink-100">
                      <td className="py-2 pr-3 font-medium text-ink-900">{r.label}</td>
                      <td className="py-2 pr-3">
                        {r.suppressed ? <span className="text-xs text-ink-400">{r.target}</span> : (
                          <div className="flex items-center gap-2"><Bar pct={r.targetPct} /><span className="w-12 shrink-0 text-right text-xs tabular-nums text-ink-700">{r.target}</span></div>
                        )}
                      </td>
                      <td className="py-2 pr-3 text-right tabular-nums text-ink-700">{r.overall}</td>
                      <td className={`py-2 text-right tabular-nums ${r.high ? "font-semibold text-emerald-700" : "text-ink-700"}`}>{r.lift}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {tb.unknown && <p className="mt-1 text-xs text-ink-500">{tb.unknown}</p>}
            </div>
          ))}
          {view.summary && <p className="text-xs text-ink-500">요약: {view.summary}</p>}
        </div>
      )}
    </Section>
  );
}

// ---- 2-1) 함께 사는 장르·품목

function Together({ options }: { options: OptionsView }) {
  const [level, setLevel] = useState<BasketLevel>("genre");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [period, setPeriod] = useState<SegmentPeriod>((options.periods.find((p) => p.value === "90d") ?? options.periods[0])?.value as SegmentPeriod ?? "90d");
  const filters = useMemo(() => Object.fromEntries(Object.entries(draft).filter(([, v]) => v)), [draft]);
  const key = JSON.stringify([level, period, filters]);
  const res = useLoad(key, () => dataAi.basketPairs(level, period, filters));
  const calc = useCalc(res);
  const view: BasketView | null = res.data ? basketView(res.data.output, options.attributes, options.itemNoun) : null;

  return (
    <Section id="seg-together" title={`함께 사는 장르·${options.itemNoun}`} calc={calc}
      hint="같은 사람이 기간 안에 둘 다 산 것을 셉니다(같은 주문이 아니어도 됩니다). 배수는 따로 살 때 기대보다 몇 배 자주 같이 사는지이고, 1.2배 이상이면 초록으로 표시합니다." badge={view?.kind === "ok" ? view.badge : null}>
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-end">
        <Select label="단위" value={level} onChange={(v) => setLevel(v as BasketLevel)}>
          <option value="genre">{basketNoun("genre")}</option>
          <option value="product">{basketNoun("product", options.itemNoun)}</option>
        </Select>
        {options.attributes.map((a) => (
          <Select key={a.key} label={a.label} value={draft[a.key] ?? ""} onChange={(v) => setDraft((d) => ({ ...d, [a.key]: v }))}>
            <option value="">전체</option>
            {a.values.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
          </Select>
        ))}
        {options.periods.length > 0 && (
          <Select label="기간" value={period} onChange={(v) => setPeriod(v as SegmentPeriod)}>
            {options.periods.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </Select>
        )}
      </div>
      <StateBox load={res} what="함께 사는 것" />
      {view?.kind === "gate" && <Gate view={view} />}
      {view?.kind === "ok" && (
        <div className="space-y-3">
          <p className="text-sm font-medium text-ink-900">{view.header}</p>
          {view.empty ? <p className="text-sm text-ink-500">{view.empty}</p> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-ink-500">
                    <th className="py-2 pr-3 font-medium">함께 사는 것</th>
                    <th className="py-2 pr-3 text-right font-medium">둘 다 산 사람</th>
                    <th className="py-2 pr-3 font-medium">앞의 것을 산 사람 중</th>
                    <th className="py-2 text-right font-medium">배수</th>
                  </tr>
                </thead>
                <tbody>
                  {view.rows.map((r, i) => (
                    <tr key={i} className="border-t border-ink-100">
                      <td className="py-2 pr-3 font-medium text-ink-900">{r.pair}</td>
                      <td className={`py-2 pr-3 text-right tabular-nums ${r.suppressed ? "text-xs text-ink-400" : "text-ink-700"}`}>{r.together}</td>
                      <td className="py-2 pr-3 text-xs text-ink-700">{r.confidence}</td>
                      <td className={`py-2 text-right tabular-nums ${r.high ? "font-semibold text-emerald-700" : "text-ink-700"}`}>{r.lift}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {view.summary && <p className="text-xs text-ink-500">요약: {view.summary}</p>}
        </div>
      )}
    </Section>
  );
}

// ---- 3) 우리 고객은 어느 층에 몰려 있나 (고객 구성 지수)

const INDEX_TONE: Record<IndexTone, string> = {
  high: "font-semibold text-emerald-700",
  low: "font-semibold text-red-700",
  normal: "text-ink-700",
};

/** 우리 비중(브랜드색) · 인구 비중(회색) 두 막대 */
function TwoBars({ r }: { r: CoverageRowView }) {
  return (
    <div className="min-w-[6rem] space-y-1" aria-hidden>
      <Bar pct={r.buyerPct} />
      <Bar pct={r.populationPct} muted />
    </div>
  );
}

function Coverage({ options }: { options: OptionsView }) {
  const [combo, setCombo] = useState(options.coverage[0]?.value ?? "");
  const res = useLoad(combo ? combo : null, () => dataAi.marketCoverage(combo.split(",")));
  const calc = useCalc(res);
  const view: CoverageView | null = res.data ? coverageView(res.data.output, options.attributes) : null;

  return (
    <Section id="seg-coverage" title="우리 고객은 어느 층에 몰려 있나"
      hint="우리 구매자(최근 1년) 구성을 공공 인구 구성과 견줍니다. 지수 100이면 인구와 같은 비율이고, 120 이상은 초록(많이 몰림), 80 미만은 빨강(적음)입니다. 지수 높은 묶음이 위에 옵니다."
      badge={view?.kind === "ok" ? view.badge : null} calc={calc}>
      {options.coverage.length === 0 ? <Empty>인구 통계와 맞춰 볼 속성 조합이 설정에 없어요</Empty> : options.coverage.length > 1 && (
        <Select label="묶음" value={combo} onChange={setCombo}>
          {options.coverage.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </Select>
      )}
      <StateBox load={res} what="고객 구성 지수" />
      {view?.kind === "gate" && <Gate view={view} />}
      {view?.kind === "buyers" && (
        <div className="space-y-2">
          <p className="rounded-xl bg-surface-2 px-3 py-2 text-xs text-ink-700">{view.note}</p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-ink-500">
                  <th className="py-2 pr-3 font-medium">{view.corner}</th>
                  {view.columns.map((c) => <th key={c} className="py-2 pr-3 text-right font-medium">{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {view.rows.map((r) => (
                  <tr key={r.label} className="border-t border-ink-100">
                    <td className="py-2 pr-3 font-medium text-ink-900">{r.label}</td>
                    {r.cells.map((c, i) => <td key={i} className={`py-2 pr-3 text-right tabular-nums ${c === SUPPRESSED ? "text-xs text-ink-400" : "text-ink-700"}`}>{c}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {view?.kind === "ok" && (view.rows.length === 0 ? <Empty>보여 줄 묶음이 없어요</Empty> : (
        <div className="space-y-2">
          <ul className="space-y-2 sm:hidden">
            {view.rows.map((r, i) => (
              <li key={i} className="space-y-1 rounded-xl bg-surface-2 px-3 py-2 text-sm">
                <div className="flex items-baseline justify-between gap-2">
                  <p className="min-w-0 font-medium text-ink-900">{r.label}</p>
                  <span className={`shrink-0 tabular-nums ${INDEX_TONE[r.tone]}`}>지수 {r.index}</span>
                </div>
                {r.bands && <p className="text-xs text-ink-500">{r.bands}</p>}
                <p className="text-xs text-ink-700">우리 구매자 {r.buyers} · 우리 비중 {r.buyerShare} · 인구 비중 {r.populationShare}</p>
                {r.note ? <p className="text-xs text-ink-500">{r.note}</p> : <TwoBars r={r} />}
              </li>
            ))}
          </ul>
          <div className="hidden overflow-x-auto sm:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-ink-500">
                  <th className="py-2 pr-3 font-medium">묶음</th>
                  <th className="py-2 pr-3 text-right font-medium">우리 구매자</th>
                  <th className="py-2 pr-3 text-right font-medium">우리 비중</th>
                  <th className="py-2 pr-3 text-right font-medium">인구 비중</th>
                  <th className="w-1/4 py-2 pr-3 font-medium"><span className="sr-only">우리 비중과 인구 비중 막대</span></th>
                  <th className="py-2 text-right font-medium">지수</th>
                </tr>
              </thead>
              <tbody>
                {view.rows.map((r, i) => (
                  <tr key={i} className="border-t border-ink-100 align-top">
                    <td className="py-2 pr-3">
                      <span className="font-medium text-ink-900">{r.label}</span>
                      {r.bands && <span className="block text-xs text-ink-500">{r.bands}</span>}
                    </td>
                    <td className={`py-2 pr-3 text-right tabular-nums ${r.buyers === SUPPRESSED ? "text-xs text-ink-400" : ""}`}>{r.buyers}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{r.buyerShare}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{r.populationShare}</td>
                    <td className="py-2 pr-3">{r.note ? <span className="text-xs text-ink-500">{r.note}</span> : <TwoBars r={r} />}</td>
                    <td className={`py-2 text-right tabular-nums ${INDEX_TONE[r.tone]}`}>{r.index}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {view.summary && <p className="text-xs text-ink-500">요약: {view.summary}</p>}
          {view.source && <p className="text-xs text-ink-400">{view.source}</p>}
        </div>
      ))}
    </Section>
  );
}

// ---- 4) 다음 달 이벤트 제안

function ProposalCard({ p }: { p: ProposalView }) {
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<string | null>(p.sent);
  const [failed, setFailed] = useState<string | null>(null);
  const done = result != null && !result.startsWith("기획이 받지 않음");

  const send = async () => {
    setSending(true);
    setFailed(null);
    try {
      const r = await dataAi.eventProposalSend(p.id);
      setResult(sendResultText(r.output));
    } catch (e) {
      const s = sectionState((e as Error).message);
      setFailed(s.kind === "missing" ? "기획에 넘기는 기능을 붙이는 중이에요 (준비 중)" : s.message);
    } finally {
      setSending(false);
    }
  };

  return (
    <article className="min-w-0 space-y-3 rounded-2xl border-2 border-brand-200 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold text-ink-900">{p.title}</h3>
        {p.assumption && <span className="chip bg-amber-50 text-amber-800">가정</span>}
      </div>
      {p.summary && <p className="text-sm text-ink-700">{p.summary}</p>}
      <ul className="grid gap-2 lg:grid-cols-3">
        {p.reasons.map((r, i) => (
          <li key={i} className="rounded-xl bg-surface-2 px-3 py-2 text-xs text-ink-700">
            <b className="block text-sm text-ink-900">{r.title}</b>{r.text}
            <span className="mt-1 block text-ink-500">출처: {r.source}</span>
          </li>
        ))}
      </ul>
      <div className="rounded-xl bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
        <p>예상 효과 <b className="text-base">{p.effect}</b></p>
        <p className="text-xs">{p.basis}{p.assumption ? " (가정)" : ""}{p.cost ? ` · ${p.cost}` : ""}</p>
        {p.formula && <p className="text-xs">계산: {p.formula}</p>}
      </div>
      {p.limits.length > 0 && <p className="text-xs text-ink-500">한계: {p.limits.join(" · ")}</p>}
      {p.past.length > 0 && (
        <div className="text-xs text-ink-700">
          <p className="font-medium text-ink-900">지난 제안 예상 vs 실제</p>
          <ul className="mt-1 space-y-0.5">{p.past.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-primary" disabled={sending || done} onClick={send}>
          {sending ? "넘기는 중…" : done ? "넘김" : "기획에 넘기기"}
        </button>
        {result && <span className="chip bg-ink-100 text-ink-700" role="status">{result}</span>}
        {failed && <span className="text-xs text-red-700" role="alert">{failed}</span>}
      </div>
    </article>
  );
}

function Proposals() {
  const month = useMemo(() => nextMonth(new Date()), []);
  const res = useLoad(month, () => dataAi.eventProposal(month));
  const calc = useCalc(res);
  const view: ProposalsView | null = res.data ? proposalsView(res.data.output) : null;

  return (
    <Section id="seg-event" title={view?.kind === "ok" ? `다음 달(${view.month}) 이벤트 제안` : "다음 달 이벤트 제안"} calc={calc}
      hint="누구에게·언제·무엇을 할지 근거 3개와 예상 효과 범위를 함께 봅니다. 숫자는 코드가 계산하고 AI는 제목·요약만 씁니다." badge={view?.kind === "ok" ? view.badge : null}>
      <StateBox load={res} what="이벤트 제안" />
      {view?.kind === "gate" && <Gate view={view} />}
      {view?.kind === "ok" && (
        <div className="space-y-3">
          {view.empty && <Empty>{view.empty}</Empty>}
          {view.items.map((p) => <ProposalCard key={p.id} p={p} />)}
          {view.excluded && (
            <details className="rounded-xl bg-surface-2 px-4 py-2 text-sm text-ink-700">
              <summary className="cursor-pointer font-medium text-ink-900">{view.excluded.title}</summary>
              <ul className="mt-2 space-y-1 text-xs">{view.excluded.rows.map((x, i) => <li key={i}>{x}</li>)}</ul>
            </details>
          )}
          {view.summary && <p className="text-xs text-ink-500">요약: {view.summary}</p>}
        </div>
      )}
    </Section>
  );
}

// ---- 5) 다시 사나 · 떠나나 (retention)

function Retention() {
  const res = useLoad("retention", () => dataAi.retention(3));
  const calc = useCalc(res);
  const view: RetentionView | null = res.data ? retentionView(res.data.output, new Date()) : null;

  return (
    <Section id="seg-retention" title="다시 사나 · 떠나나" badge={view?.kind === "ok" ? view.badge : null} calc={calc}
      hint="첫 구매 달별로 30·60·90일 안에 다시 산 비율과, 단골 중 한동안 안 산 사람이 많은 묶음을 봅니다.">
      <StateBox load={res} what="재구매·이탈" />
      {view?.kind === "gate" && <Gate view={view} />}
      {view?.kind === "ok" && (
        <div className="space-y-3">
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="min-w-0 space-y-2 rounded-xl bg-surface-2 px-4 py-3">
              <p className="text-xs font-semibold text-ink-500">{view.cohortTitle}</p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-ink-500">
                      <th className="py-2 pr-3 font-medium">첫 구매</th>
                      <th className="py-2 pr-3 text-right font-medium">고객</th>
                      {view.columns.map((c) => <th key={c} className="py-2 pr-3 text-right font-medium">{c}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {view.cohorts.map((c) => (
                      <tr key={c.month} className="border-t border-ink-100">
                        <td className="py-2 pr-3 font-medium text-ink-900">{c.month}</td>
                        <td className={`py-2 pr-3 text-right tabular-nums ${c.suppressed ? "text-xs text-ink-400" : "text-ink-700"}`}>{c.customers}</td>
                        {c.cells.map((x, i) => (
                          <td key={i} className={`py-2 pr-3 text-right tabular-nums ${x === NOT_YET || x === SUPPRESSED || x === "-" ? "text-xs text-ink-400" : "text-ink-700"}`}>{x}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-ink-500">{view.cohortHint}</p>
            </div>
            <div className="min-w-0 space-y-2 rounded-xl bg-surface-2 px-4 py-3">
              <p className="text-xs font-semibold text-ink-500">{view.riskTitle}</p>
              {view.riskEmpty ? <p className="text-sm text-ink-500">{view.riskEmpty}</p> : (
                <ul className="space-y-2">
                  {view.atRisk.map((r) => (
                    <li key={r.label} className="border-b border-ink-100 pb-2 text-sm last:border-0">
                      <div className="flex items-baseline justify-between gap-2">
                        <b className="min-w-0 text-ink-900">{r.label}</b>
                        <span className="shrink-0 font-semibold tabular-nums text-red-700">{r.count}</span>
                      </div>
                      {r.detail && <p className="text-xs text-ink-500">{r.detail}</p>}
                    </li>
                  ))}
                </ul>
              )}
              {/* 보내기는 다음 단계(safety 등록 필요). 지금은 화면에만 두고 누를 수 없다 */}
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" className="btn-ghost" disabled title="준비 중">마케팅에 재방문 대상 고객 수 보내기</button>
                <PreparingChip />
              </div>
            </div>
          </div>
          <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-900">{view.note}</p>
          {view.summary && <p className="text-xs text-ink-500">요약: {view.summary}</p>}
        </div>
      )}
    </Section>
  );
}

// ---- 탭

function OptionsMissing({ load }: { load: Load<unknown> }) {
  return (
    <>
      {["물어보기", "누가 사나", "함께 사는 것", "우리 고객은 어느 층에 몰려 있나"].map((t, i) => (
        <Section key={t} id={`seg-wait-${i}`} title={t}>
          <StateBox load={load} what="분석 선택지" />
        </Section>
      ))}
    </>
  );
}

export function SegmentAnalysis() {
  const opts = useLoad(kpiLive ? "options" : null, () => dataAi.segmentOptions().then((r) => r.output));
  const options = opts.data ? optionsView(opts.data) : null;

  return (
    <div className="space-y-4">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold tracking-tight">고객·시장 분석</h1>
        {dataNote(options) && (
          <p className="flex flex-wrap items-center gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-900">
            {dataNote(options)}
            <Badge text={options?.badge ?? null} />
          </p>
        )}
      </header>

      {!kpiLive ? (
        <Empty>데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 고객·시장 분석을 받지 않아요. backend/의 scripts/run_web.sh로 화면을 켜면 토큰이 들어갑니다.</Empty>
      ) : (
        <>
          {options ? (
            <>
              <Ask options={options} />
              <Who options={options} />
              <Together options={options} />
              <Coverage options={options} />
            </>
          ) : <OptionsMissing load={opts} />}
          <Proposals />
          <Retention />
        </>
      )}
    </div>
  );
}
