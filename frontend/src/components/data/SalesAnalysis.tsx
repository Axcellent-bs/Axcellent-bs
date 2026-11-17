import { useState } from "react";
import { Empty, ErrorBox, Panel, Waiting, useResource } from "@/components/finance/bits";
import { DATA_SERVER, dataAi, kpiLive, type SalesPeriod } from "@/lib/data";
import { PreparingChip } from "@/components/Preparing";
import {
  SALES_PERIODS, calcOfResult, channelValueView, compareLabel, salesView,
  type ChannelValueView, type ChannelView, type ProductView, type SalesGateView, type SalesOkView, type Tone,
} from "@/lib/salesView";
import { sectionState } from "@/lib/segmentView";
import { useStaleReload } from "./staleReload";

// 매출 분석: 무엇이 팔리나 · 누가 사나 · 유입 경로별 매출 · 유입 경로별 고객 가치(channel_value).
// 데이터 AI의 sales_analysis·channel_value 결과를 그대로 보여 준다.
// 보여 줄 글자는 lib/salesView.ts가 만든다(숫자는 서버 값만, 여기서는 그리기만).

const TONE_CLASS: Record<Tone, string> = { up: "text-emerald-700", down: "text-red-700", flat: "text-ink-500" };

function Bar({ pct }: { pct: number }) {
  return (
    <div className="h-2 w-full min-w-[4rem] rounded-full bg-ink-100" aria-hidden>
      <div className="h-2 rounded-full bg-brand-500" style={{ width: `${pct}%` }} />
    </div>
  );
}

function Gate({ view }: { view: SalesGateView }) {
  return (
    <section className="card space-y-3 p-4 sm:p-5">
      <h2 className="text-base font-semibold text-ink-900">지금은 매출을 계산하지 않았어요</h2>
      <ul className="space-y-1 text-sm text-ink-900">
        {view.reasons.map((r, i) => <li key={i}>{r}</li>)}
      </ul>
      <ul className="flex flex-wrap gap-2">
        {view.facts.map((f) => <li key={f} className="chip bg-ink-100 text-ink-700">{f}</li>)}
      </ul>
    </section>
  );
}

function ProductList({ title, rows }: { title: string; rows: ProductView[] }) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-ink-900">{title}</h3>
      {rows.length === 0 ? <p className="text-xs text-ink-500">해당하는 상품이 없어요</p> : (
        <ul className="space-y-2">
          {rows.map((p, i) => (
            <li key={i} className="flex items-center gap-2 rounded-xl bg-surface-2 px-3 py-2 text-sm">
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium text-ink-900">{p.label}</span>
                <span className="block text-xs text-ink-500">{p.category ? `${p.category} · ` : ""}{p.revenue} · 주문 {p.orders}</span>
              </span>
              <span className={`shrink-0 text-sm font-semibold tabular-nums ${TONE_CLASS[p.tone]}`}>{p.change}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Channels({ rows }: { rows: ChannelView[] }) {
  if (rows.length === 0) return <Empty>유입 경로별 매출이 아직 없어요</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-ink-500">
            <th className="py-2 pr-3 font-medium">유입 경로</th><th className="py-2 pr-3 text-right font-medium">매출</th>
            <th className="py-2 pr-3 text-right font-medium">주문</th><th className="py-2 font-medium">비중</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c, i) => (
            <tr key={i} className="border-t border-ink-100">
              <td className="py-2 pr-3 font-medium text-ink-900">{c.label}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{c.revenue}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{c.orders}</td>
              <td className="py-2"><div className="flex items-center gap-2"><Bar pct={c.sharePct} /><span className="w-12 shrink-0 text-right text-xs tabular-nums text-ink-700">{c.share}</span></div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 유입 경로별 고객 가치 (channel_value). 위 기간 버튼과 상관없이 최근 1년 첫 구매 고객의 N일 가치 */
function ChannelValue({ tick }: { tick: number }) {
  const res = useResource("channel-value", () => dataAi.channelValue("365d").then((r) => ({ data: r, meta: { mock: false } })), tick);
  const view: ChannelValueView | null = res.env ? channelValueView(res.env.data.output) : null;
  const c = calcOfResult(res.env?.data);
  useStaleReload(c.stale, c.stamp, res.reload);
  const calc = c.line;
  const missing = res.error != null && sectionState(res.error).kind === "missing";

  return (
    <Panel id="sales-channel-value" title="유입 경로별 고객 가치">
      {missing ? (
        <p role="status" className="flex flex-wrap items-center gap-2 rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-800">
          <PreparingChip /> 유입 경로별 고객 가치를 데이터 AI에 붙이는 중이에요. 준비되면 여기에 나옵니다.
        </p>
      ) : res.error ? (
        <ErrorBox message={`유입 경로별 고객 가치를 받지 못했어요. ${res.error}`} onRetry={res.reload} />
      ) : !view ? <Waiting /> : view.kind === "gate" ? (
        <div className="space-y-1 rounded-xl bg-surface-2 px-4 py-3 text-sm">
          <p className="font-medium text-ink-900">지금은 계산하지 않았어요</p>
          <ul className="space-y-1 text-ink-700">{view.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-ink-500">{view.hint}</p>
          {view.empty ? <Empty>{view.empty}</Empty> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-ink-500">
                    {view.columns.map((c, i) => <th key={c} className={`whitespace-nowrap py-2 pr-3 font-medium ${i > 0 ? "text-right" : ""}`}>{c}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {view.rows.map((r) => (
                    <tr key={r.label} className="border-t border-ink-100">
                      <td className="py-2 pr-3 font-medium text-ink-900">{r.label}</td>
                      <td className={`py-2 pr-3 text-right tabular-nums ${r.suppressed ? "text-xs text-ink-400" : ""}`}>{r.customers}</td>
                      <td className={`py-2 pr-3 text-right tabular-nums ${r.best ? "font-semibold text-emerald-700" : ""}`}>{r.revenue}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{r.repeat}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{r.orders}</td>
                      <td className="py-2 pr-3 text-right text-xs text-ink-500">{r.cost}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {view.summary && <p className="rounded-xl bg-surface-2 px-3 py-2 text-sm text-ink-700"><b>요약</b> {view.summary}</p>}
          {calc && <p className="text-xs text-ink-500">{calc}</p>}
          <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-900">{view.note}</p>
        </div>
      )}
    </Panel>
  );
}

function Body({ view, tick, calc }: { view: SalesOkView; tick: number; calc: string | null }) {
  return (
    <div className="space-y-4">
      <ul className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {view.kpis.map((k) => (
          <li key={k.label} className="card p-4">
            <p className="text-xs font-medium text-ink-500">{k.label}</p>
            <p className="mt-1 text-xl font-bold tabular-nums text-ink-900 sm:text-2xl">{k.value}</p>
            <p className={`mt-1 text-xs font-medium tabular-nums ${TONE_CLASS[k.tone]}`}>{k.change === "비교 없음" ? k.change : `${k.change} 직전 대비`}</p>
          </li>
        ))}
      </ul>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel id="sales-what" title="무엇이 팔리나">
          {view.categories.length === 0 ? <Empty>분류별 매출이 아직 없어요</Empty> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-ink-500">
                    <th className="py-2 pr-3 font-medium">카테고리</th><th className="py-2 pr-3 font-medium">매출 비중</th>
                    <th className="py-2 pr-3 text-right font-medium">매출</th><th className="py-2 text-right font-medium">변화</th>
                  </tr>
                </thead>
                <tbody>
                  {view.categories.map((c, i) => (
                    <tr key={i} className="border-t border-ink-100">
                      <td className="py-2 pr-3 font-medium text-ink-900">{c.label}</td>
                      <td className="py-2 pr-3"><div className="flex items-center gap-2"><Bar pct={c.sharePct} /><span className="w-12 shrink-0 text-right text-xs tabular-nums text-ink-700">{c.share}</span></div></td>
                      <td className="py-2 pr-3 text-right tabular-nums">{c.revenue}</td>
                      <td className={`py-2 text-right tabular-nums ${TONE_CLASS[c.tone]}`}>{c.change}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <ProductList title="많이 늘어난 상품" rows={view.productsUp} />
            <ProductList title="많이 줄어든 상품" rows={view.productsDown} />
          </div>
          {view.lowSample && <p className="mt-3 text-xs text-ink-500">{view.lowSample}</p>}
        </Panel>

        <Panel id="sales-who" title="누가 사나">
          {view.customers == null ? <Empty>고객 구분 자료가 아직 없어요</Empty> : (
            <ul className="grid gap-3 sm:grid-cols-3">
              {view.customers.map((s) => (
                <li key={s.label} className="rounded-2xl bg-surface-2 px-4 py-3">
                  <p className="text-xs font-medium text-ink-700">{s.label}</p>
                  <p className="mt-1 text-xl font-bold tabular-nums text-ink-900">{s.value}</p>
                </li>
              ))}
            </ul>
          )}
          <h3 className="mb-2 mt-6 text-sm font-semibold text-ink-900">유입 경로별 매출</h3>
          <Channels rows={view.channels} />
        </Panel>
      </div>

      <ChannelValue tick={tick} />

      <p className="text-xs text-ink-500">품질 · {view.quality.join(" · ")}{calc ? ` · ${calc}` : ""}</p>
    </div>
  );
}

function Live({ tick }: { tick: number }) {
  const [period, setPeriod] = useState<SalesPeriod>("7d");
  const res = useResource(`sales-${period}`, () => dataAi.salesAnalysis(period).then((r) => ({ data: r, meta: { mock: false } })), tick);
  const c = calcOfResult(res.env?.data);
  useStaleReload(c.stale, c.stamp, res.reload);
  const view = res.env ? salesView(res.env.data.output) : null;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">매출 분석</h1>
        <p className="mt-1 text-sm text-ink-500">{view?.kind === "ok" && view.summary ? view.summary : "무엇이 팔리고 누가 사는지 직전 기간과 견줘 봅니다."}</p>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="기간" className="inline-flex rounded-xl bg-ink-100 p-1">
          {SALES_PERIODS.map((p) => (
            <button key={p.id} type="button" aria-pressed={period === p.id} onClick={() => setPeriod(p.id)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium ${period === p.id ? "bg-white text-ink-900 shadow-sm" : "text-ink-500"}`}>
              {p.label}
            </button>
          ))}
        </div>
        <span className="chip bg-ink-100 text-ink-700">{compareLabel(period)}</span>
        {view?.kind === "ok" && <span className="chip bg-ink-100 text-ink-700">{view.asOf}</span>}
        <button className="btn-ghost ml-auto" onClick={res.reload}>새로고침</button>
      </div>

      {view?.kind === "ok" && (
        <p className="text-xs leading-relaxed text-ink-500">{view.compare}. {view.definition}</p>
      )}

      {!res.env && !res.error && <Waiting />}
      {res.error && (
        <ErrorBox message={res.env ? `새로 받지 못해 이전 값을 보여 줍니다. ${res.error}` : `${res.error}. ${DATA_SERVER}가 켜져 있는지 확인해 주세요.`} onRetry={res.reload} />
      )}
      {view?.kind === "gate" && <Gate view={view} />}
      {view?.kind === "ok" && (
        <Body view={view} tick={tick}
          calc={c.line} />
      )}
    </div>
  );
}

export function SalesAnalysis({ tick }: { tick: number }) {
  if (!kpiLive) {
    return (
      <Panel id="data-sales" title="매출 분석">
        <Empty>데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 매출 분석을 받지 않아요. backend/의 scripts/run_web.sh로 화면을 켜면 토큰이 들어갑니다.</Empty>
      </Panel>
    );
  }
  return <Live tick={tick} />;
}
