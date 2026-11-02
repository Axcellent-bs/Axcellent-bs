import { useEffect, useState } from "react";
import { Chip, Empty, ErrorBox, Panel, TONE, Waiting } from "@/components/finance/bits";
import { DATA_SERVER, UNKNOWN, dataAi, kpiLive, type TrendHistoryGroup, type TrendHistoryOutput } from "@/lib/data";

// 트렌드 1단계의 "월별 흐름" 보기: 데이터 AI(8106) trend_history 출력을 그대로 그린다.
// - 판정하지 않는다(급상승·계절 판정은 평가가 없어 서버도 하지 않는다). 값과 "작년 같은 달 대비" 배수만 보여 준다
// - 그룹마다 따로 요청한 상대값(그 그룹 안 최댓값 = 100)이라 그룹끼리 높이는 비교하지 않는다
// - 선 그래프는 새 라이브러리 없이 SVG. 네이버가 주지 않은 달은 선을 끊는다(0으로 잇지 않음)
// - 이 보기를 처음 열 때 한 번 + "다시 보기"를 누를 때만 부른다(그룹마다 네이버 1번)

const W = 300;
const H = 90;
const PAD = 6;

const fixed = (v: number | null | undefined, digits = 2) =>
  typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("ko-KR", { maximumFractionDigits: digits }) : null;

/** "2026-10-01" → "2026년 10월" */
const monthKo = (m: string | null | undefined) => (m ? `${Number(m.slice(0, 4))}년 ${Number(m.slice(5, 7))}월` : "");
/** "2026-10-01" → "26.10" */
const monthShort = (m: string) => `${m.slice(2, 4)}.${m.slice(5, 7)}`;
/** 달 번호 (빠진 달을 찾으려고) */
const monthIndex = (m: string) => Number(m.slice(0, 4)) * 12 + Number(m.slice(5, 7)) - 1;

function MonthChart({ g }: { g: TrendHistoryGroup }) {
  const pts = g.points;
  if (pts.length < 2) return <p className="py-6 text-center text-xs text-ink-500">그래프로 그릴 달별 값이 없어요.</p>;
  const first = monthIndex(pts[0].month);
  const span = Math.max(1, monthIndex(pts[pts.length - 1].month) - first);
  const top = Math.max(...pts.map((p) => p.value)) * 1.08 || 1;
  const x = (m: string) => ((monthIndex(m) - first) / span) * W;
  const y = (v: number) => H - PAD - (v / top) * (H - 2 * PAD);
  const d = pts
    .map((p, i) => `${i === 0 || monthIndex(p.month) - monthIndex(pts[i - 1].month) > 1 ? "M" : "L"}${x(p.month).toFixed(1)},${y(p.value).toFixed(1)}`)
    .join(" ");
  const last = pts[pts.length - 1];
  const lastYear = pts.find((p) => monthIndex(p.month) === monthIndex(last.month) - 12);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-24 w-full" role="img"
        aria-label={`${g.group_name} 달별 값 ${monthKo(pts[0].month)}부터 ${monthKo(last.month)}까지`}>
        {lastYear && (
          <line x1={x(lastYear.month)} x2={x(lastYear.month)} y1={0} y2={H} className="stroke-ink-300" strokeWidth={1} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
        )}
        <path d={d} fill="none" className="stroke-brand-600" strokeWidth={1.6} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        {lastYear && <circle cx={x(lastYear.month)} cy={y(lastYear.value)} r={2.5} className="fill-ink-500" />}
        <circle cx={x(last.month)} cy={y(last.value)} r={3} className="fill-brand-600" />
      </svg>
      <div className="mt-1 flex justify-between text-[10px] text-ink-500" aria-hidden>
        <span>{monthShort(pts[0].month)}</span>
        {lastYear && <span>점선 = 작년 같은 달</span>}
        <span>{monthShort(last.month)}</span>
      </div>
    </div>
  );
}

function GroupCard({ g }: { g: TrendHistoryGroup }) {
  if (g.status === "unknown") {
    return (
      <article className="card flex flex-col gap-2 p-4" aria-label={`${g.group_name} 월별 흐름`}>
        <h3 className="text-base font-bold text-ink-900">{g.group_name}</h3>
        <p className="text-sm text-amber-700">{UNKNOWN} · {g.unknown_reason}</p>
      </article>
    );
  }
  const ratio = fixed(g.latest_vs_last_year);
  return (
    <article className="card flex flex-col gap-2 p-4" aria-label={`${g.group_name} 월별 흐름`}>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h3 className="text-base font-bold text-ink-900">{g.group_name}</h3>
        {g.keywords.length > 1 && <span className="text-[11px] text-ink-500">({g.keywords.join(", ")})</span>}
      </div>
      <p className="text-sm text-ink-700">
        {monthKo(g.latest_month)}:{" "}
        {ratio != null
          ? <b className="text-ink-900">작년 이맘때보다 {ratio}배</b>
          : <span className="text-ink-500">작년 같은 달 값이 없어 비교할 수 없어요</span>}
      </p>
      <MonthChart g={g} />
    </article>
  );
}

type State =
  | { kind: "loading" }
  | { kind: "ok"; out: TrendHistoryOutput }
  | { kind: "failed"; reason: string };

export function TrendHistory() {
  const [run, setRun] = useState(0);
  const [state, setState] = useState<State>({ kind: "loading" });
  useEffect(() => {
    if (!kpiLive) return;
    let live = true;
    setState({ kind: "loading" });
    dataAi.trendHistory()
      .then((r) => live && setState({ kind: "ok", out: r.output }))
      .catch((e: Error) => live && setState({ kind: "failed", reason: e.message }));
    return () => { live = false; };
  }, [run]);

  if (!kpiLive) {
    return (
      <Panel id="data-trend-monthly" title="월별 흐름">
        <Empty>데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 월별 흐름을 받지 않아요.</Empty>
      </Panel>
    );
  }
  const out = state.kind === "ok" ? state.out : null;
  return (
    <Panel id="data-trend-monthly" title="월별 흐름"
      hint={out ? `${monthKo(out.start_month)} ~ ${monthKo(out.end_month)} · ${out.months}개월 · 네이버 호출 ${out.naver_calls}번` : undefined}
      tools={<>
        <Chip label="판정 없음" tone={TONE.muted} />
        <button type="button" className="btn-ghost" disabled={state.kind === "loading"} onClick={() => setRun((n) => n + 1)}>다시 보기</button>
      </>}>
      <div className="space-y-4">
        <p className="text-sm leading-relaxed text-ink-700">
          회사 기본 키워드의 달별 검색 흐름이에요. 급상승이나 계절 반복은 판정하지 않고, 마지막 달을 작년 같은 달과만 비교해요.
          값은 키워드마다 따로 받은 상대값(그 키워드에서 가장 많은 달 = 100)이라 키워드끼리 높이는 비교하지 않아요.
        </p>
        {state.kind === "loading" && <Waiting what="달별 값을 받는 중… (키워드마다 네이버를 한 번씩 불러요)" />}
        {state.kind === "failed" && (
          <ErrorBox message={`${state.reason}. ${DATA_SERVER}가 켜져 있는지, 네이버 키가 있는지 확인해 주세요.`} onRetry={() => setRun((n) => n + 1)} />
        )}
        {out && (out.groups.length === 0 ? <Empty>볼 키워드 그룹이 없어요.</Empty> : (
          <div className="grid gap-4 md:grid-cols-2">
            {out.groups.map((g) => <GroupCard key={g.group_name} g={g} />)}
          </div>
        ))}
        {out?.summary && <p className="text-xs text-ink-500">{out.summary}</p>}
      </div>
    </Panel>
  );
}
