import { Fragment, useCallback, useEffect, useMemo, useState, type ReactNode, type SyntheticEvent } from "react";
import { useNavigate } from "react-router";
import { Link } from "@/lib/nav";
import { ago, shortDateTime } from "@/lib/format";
import { ANALYSIS, SEVERITY, currentIncidents, readableTitle, dashboardSummary, recentIncidents, type DashboardSummary, type Incident, type IncidentMeta, type Measured } from "@/lib/dejafix";
import { DEJAFIX_LIVE } from "@/lib/env";

// 장애 대응 탭 = 지금 진행 중인 장애만. DejaFix API(GET /api/incidents?status=problem)에서 받아 AXcellent가 그린다.
// 장애를 누르면 AXcellent 장애 상세(/infra/:id, IncidentDetail.tsx)로 간다.
// DejaFix 현황 화면의 가장 급한 장애·요약·언제 · 어디서도 같은 API 데이터로 다시 그린다. DejaFix 화면을 이 안에 띄우지 않는다.
const REFRESH_MS = 30_000;

type State = { data: Incident[]; meta: IncidentMeta } | null;

/** 값 하나. 모르면 0·빈칸 대신 '확인할 수 없음' (DejaFix SAFE-10과 같은 규칙) */
function Num({ m }: { m: Measured | undefined }) {
  if (!m || m.unknown || m.value === null) return <span className="text-sm font-normal text-ink-500">확인할 수 없음</span>;
  return <>{m.value.toLocaleString("ko-KR")}</>;
}

const unitOf = (m: Measured | undefined) => (m && !m.unknown && m.value !== null ? m.unit : "");

/** 심각도별 카드 왼쪽 띠 색 */
const BAND: Record<number, string> = { 5: "border-l-red-600", 4: "border-l-red-500", 3: "border-l-amber-500", 2: "border-l-amber-400" };

/** 가장 급한 장애 1건 = 심각도 높은 순, 같으면 최근 시작 순 */
function UrgentCard({ incident, loading }: { incident: Incident | undefined; loading: boolean }) {
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold text-ink-700">가장 급한 장애</h2>
      {loading ? (
        <p className="card px-5 py-6 text-sm text-ink-500">불러오는 중…</p>
      ) : !incident ? (
        <p className="card border-l-4 border-l-emerald-500 px-5 py-6 text-sm text-ink-700">해결되지 않은 장애가 없습니다.</p>
      ) : (
        <Link href={`/infra/${incident.id}`} className={`card block space-y-3 border-l-4 px-5 py-4 transition hover:shadow-md ${BAND[incident.severity.level] ?? "border-l-ink-300"}`}>
          <Chip map={SEVERITY} k={incident.severity.level} />
          <p className="font-semibold text-ink-900" title={incident.title}>{readableTitle(incident.title)}</p>
          <dl className="grid grid-cols-3 gap-2 text-xs">
            <div><dt className="text-ink-500">호스트</dt><dd className="mt-0.5 truncate font-mono font-semibold" title={incident.host.name ?? undefined}>{incident.host.name ?? "확인 중"}</dd></div>
            <div><dt className="text-ink-500">시작</dt><dd className="mt-0.5 font-semibold">{shortDateTime(incident.started_at)}</dd></div>
            <div><dt className="text-ink-500">경과</dt><dd className="mt-0.5 font-semibold">{ago(incident.started_at)}</dd></div>
          </dl>
        </Link>
      )}
    </section>
  );
}

/** 요약 4칸 = DejaFix 현황 KPI (GET /api/dashboard/summary) */
function SummaryTiles({ s, error }: { s: DashboardSummary | null; error: string | null }) {
  const tile = (label: string, dot: string, value: ReactNode, sub: string) => (
    <div className="card px-4 py-3">
      <p className="flex items-center gap-1.5 text-xs text-ink-500"><i className={`h-2 w-2 rounded-full ${dot}`} />{label}</p>
      <p className="mt-1 text-xl font-semibold">{value}</p>
      <p className="text-xs text-ink-500">{sub}</p>
    </div>
  );
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold text-ink-700">요약</h2>
      {error && !s && <p className="card px-4 py-3 text-xs text-red-700">{error}</p>}
      <div className="grid grid-cols-2 gap-2">
        {tile("진행 중", "bg-amber-400", s ? <Num m={s.active_incidents} /> : "…", unitOf(s?.active_incidents))}
        {tile("높음 이상", "bg-red-500", s ? <Num m={s.high_incidents} /> : "…", unitOf(s?.high_incidents))}
        {tile("정상 호스트", "bg-sky-500", s ? <><Num m={s.hosts.ok} /> / <Num m={s.hosts.total} /></> : "…",
          s && !s.hosts.unknown.unknown && s.hosts.unknown.value ? `확인 불가 ${s.hosts.unknown.value}대` : "대")}
        {tile("오늘 리포트", "bg-orange-500", s ? <Num m={s.reports_today} /> : "…",
          s && !s.avg_analysis_sec.unknown && s.avg_analysis_sec.value !== null ? `평균 분석 ${s.avg_analysis_sec.value}초` : unitOf(s?.reports_today))}
      </div>
    </section>
  );
}

const HOURS = 24;
const MAX_ROWS = 8;

/** 언제 · 어디서 = 최근 24시간, 호스트별 · 시간별로 장애가 열린 횟수. 최근 장애 목록(해결 포함)에서 센다 */
function Heatmap({ incidents, end }: { incidents: Incident[]; end: number }) {
  const navigate = useNavigate();
  // 호버한 칸: DejaFix 차트처럼 호스트 · 시간 · 건수를 말풍선으로 띄운다
  const [tip, setTip] = useState<{ x: number; y: number; host: string; hour: number; items: Incident[] } | null>(null);
  const { rows, max, startHour } = useMemo(() => {
    const hourMs = 3_600_000;
    const last = Math.floor(end / hourMs) * hourMs; // 기준 시각이 속한 시간 칸의 시작
    const first = last - (HOURS - 1) * hourMs;
    const byHost = new Map<string, Incident[][]>();
    for (const i of incidents) {
      const t = Date.parse(i.started_at);
      if (t < first || t >= last + hourMs) continue;
      const name = i.host.name ?? "(호스트 확인 중)";
      const cells = byHost.get(name) ?? Array.from({ length: HOURS }, () => [] as Incident[]);
      cells[Math.floor((t - first) / hourMs)].push(i);
      byHost.set(name, cells);
    }
    const sorted = [...byHost.entries()]
      .map(([name, cells]) => ({ name, cells, total: cells.reduce((a, c) => a + c.length, 0) }))
      .sort((a, b) => b.total - a.total);
    const top = sorted.slice(0, MAX_ROWS);
    const others = sorted.slice(MAX_ROWS);
    if (others.length) top.push({ name: `그 밖 ${others.length}대`, cells: Array.from({ length: HOURS }, (_, h) => others.flatMap((o) => o.cells[h])), total: 0 });
    return { rows: top, max: Math.max(1, ...top.flatMap((r) => r.cells.map((c) => c.length))), startHour: new Date(first).getHours() };
  }, [incidents, end]);

  const show = (e: SyntheticEvent<HTMLElement>, host: string, h: number, items: Incident[]) => {
    // 카드가 넘치는 부분을 자르므로 말풍선은 화면 기준(fixed)으로 띄운다
    const cell = e.currentTarget.getBoundingClientRect();
    setTip({ x: cell.left + cell.width / 2, y: cell.top, host, hour: (startHour + h) % 24, items });
  };

  return (
    <section className="card px-6 py-5">
      <div className="mb-4 flex flex-wrap items-baseline gap-x-3">
        <h2 className="font-semibold">언제 · 어디서 장애가 났나</h2>
        <span className="text-xs text-ink-500">최근 24시간 · 호스트별 장애가 열린 횟수 · 칸을 누르면 그 장애로</span>
      </div>
      {rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-ink-500">최근 24시간 동안 열린 장애가 없습니다.</p>
      ) : (
        <div onMouseLeave={() => setTip(null)}>
          <div className="overflow-x-auto" onScroll={() => setTip(null)}>
          <div className="grid min-w-[640px] gap-[3px] text-[11px]" style={{ gridTemplateColumns: `minmax(96px,160px) repeat(${HOURS}, minmax(0,1fr))` }}>
            {rows.map((r) => (
              <Fragment key={r.name}>
                <span className="truncate pr-2 font-mono leading-6 text-ink-700" title={r.name}>{r.name}</span>
                {r.cells.map((items, h) => {
                  const n = items.length;
                  return (
                    <span key={h} role={n ? "button" : undefined} tabIndex={n ? 0 : undefined}
                      aria-label={`${r.name} ${(startHour + h) % 24}시 ${n}건`}
                      className={`h-6 ${n ? "cursor-pointer hover:outline hover:outline-2 hover:outline-ink-900" : "bg-ink-100"}`}
                      style={n ? { background: `rgba(220, 38, 38, ${0.15 + 0.85 * (n / max)})` } : undefined}
                      onMouseEnter={(e) => show(e, r.name, h, items)}
                      onFocus={(e) => show(e, r.name, h, items)}
                      onBlur={() => setTip(null)}
                      onClick={() => n && navigate(`/infra/${items[items.length - 1].id}`)}
                      onKeyDown={(e) => n && e.key === "Enter" && navigate(`/infra/${items[items.length - 1].id}`)} />
                  );
                })}
              </Fragment>
            ))}
            <span />
            {Array.from({ length: HOURS }, (_, h) => (
              <span key={h} className="text-center text-ink-500">{h % 3 === 0 ? `${(startHour + h) % 24}시` : ""}</span>
            ))}
          </div>
          </div>
          {tip && (
            <div role="tooltip" className="pointer-events-none fixed z-50 w-64 -translate-x-1/2 -translate-y-full border border-ink-100 bg-surface px-3 py-2 text-xs shadow-lg"
              style={{ left: Math.min(Math.max(tip.x, 136), window.innerWidth - 136), top: tip.y - 6 }}>
              <p className="font-semibold"><span className="font-mono">{tip.host}</span> · {tip.hour}시 · {tip.items.length}건</p>
              {tip.items.slice(-3).reverse().map((i) => (
                <p key={i.id} className="mt-1 truncate text-ink-700">#{i.id} {readableTitle(i.title)}</p>
              ))}
              {tip.items.length > 3 && <p className="mt-1 text-ink-500">외 {tip.items.length - 3}건</p>}
            </div>
          )}
          <p className="mt-3 flex items-center justify-end gap-2 text-xs text-ink-500">
            적음 <span className="h-3 w-24" style={{ background: "linear-gradient(to right, rgba(220,38,38,.15), rgba(220,38,38,1))" }} /> 많음
          </p>
        </div>
      )}
    </section>
  );
}

/** 목록 아래 줄 태그: 제목에 이미 나온 서비스 이름·ID는 빼고 짧은 분류만 남긴다(전체는 title 속성) */
function shortTags(i: Incident): string[] {
  const title = i.title.toLowerCase();
  // 'windows'·'os' 같은 짧은 분류는 제목에 있어도 남긴다
  return [...new Set(i.tags.map((t) => t.value))].filter((v) => v.length <= 12 || !title.includes(v.toLowerCase()));
}

function Chip({ map, k }: { map: Record<string | number, { label: string; tone: string }>; k: string | number }) {
  const v = map[k] ?? { label: String(k), tone: "bg-ink-100 text-ink-700" };
  return <span className={`chip whitespace-nowrap ${v.tone}`}>{v.label}</span>;
}

export default function Incidents() {
  const navigate = useNavigate();
  const [state, setState] = useState<State>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<string | null>(null);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [recent, setRecent] = useState<Incident[] | null>(null);

  const load = useCallback(async () => {
    // 셋은 따로 받는다. 하나가 실패해도 나머지는 그린다
    const [cur, sum, rec] = await Promise.allSettled([currentIncidents(), dashboardSummary(), recentIncidents()]);
    const msg = (r: PromiseRejectedResult, fallback: string) => (r.reason instanceof Error ? r.reason.message : fallback);
    if (cur.status === "fulfilled") {
      setState(cur.value);
      setError(null);
      setLoadedAt(new Date().toISOString());
    } else setError(msg(cur, "장애 목록을 불러오지 못했습니다."));
    if (sum.status === "fulfilled") {
      setSummary(sum.value);
      setSummaryError(null);
    } else setSummaryError(msg(sum, "요약을 불러오지 못했습니다."));
    if (rec.status === "fulfilled") setRecent(rec.value.data);
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const list = state?.data ?? [];
  const high = list.filter((i) => i.severity.level >= 4).length;
  const asOf = state?.meta.data_as_of ?? loadedAt;
  const urgent = useMemo(
    () => [...list].sort((a, b) => b.severity.level - a.severity.level || Date.parse(b.started_at) - Date.parse(a.started_at))[0],
    [list],
  );
  // 시간 분포의 기준 시각: 실시간이면 받은 시각, 녹화본이면 녹화된 마지막 장애 시각 (녹화본은 지난 날짜라서)
  const heatEnd = useMemo(
    () => (DEJAFIX_LIVE || !recent?.length ? Date.parse(loadedAt ?? "") || Date.now() : Math.max(...recent.map((i) => Date.parse(i.started_at)))),
    [recent, loadedAt],
  );

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-8 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="flex items-center gap-2 text-sm text-ink-500">
            DejaFix 데이터
            {/* 녹화본인지 실제 서버인지 화면에 밝힌다 */}
            {DEJAFIX_LIVE
              ? <span className="chip bg-emerald-50 text-emerald-700">실시간</span>
              : <span className="chip bg-amber-50 text-amber-700" title="VITE_DEJAFIX_LIVE=on 이면 실제 DejaFix를 부릅니다">데모 녹화본</span>}
          </p>
          <h1 className="text-2xl font-semibold">현재 장애</h1>
          <p className="mt-1 text-sm text-ink-500">지금 해결되지 않은 장애만 보여 줍니다.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className={`chip ${list.length ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-700"}`}>진행 중 {list.length}건</span>
          {high > 0 && <span className="chip bg-red-600 text-white">높음 이상 {high}건</span>}
          {asOf && <span className="text-ink-500">{ago(asOf)} 갱신</span>}
          <button type="button" className="btn-ghost" onClick={load}>새로고침</button>
        </div>
      </header>

      {state?.meta.stale && (
        <p role="status" className="card border-l-4 border-l-amber-500 px-5 py-3 text-sm text-amber-800">
          DejaFix의 장애 수집이 멈춰 있습니다. 아래 목록은 마지막으로 수집된 내용입니다.
        </p>
      )}
      {error && (
        <p role="alert" className="card border-l-4 border-l-red-500 px-5 py-3 text-sm text-red-700">
          {error} {state && "아래는 마지막으로 받은 목록입니다."}
        </p>
      )}

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0 space-y-6">
      {!state && !error && <p className="card px-6 py-10 text-center text-sm text-ink-500">장애 목록을 불러오는 중…</p>}

      {state && list.length === 0 && (
        <p className="card px-6 py-10 text-center text-sm text-ink-700">지금 진행 중인 장애가 없습니다.</p>
      )}

      {recent && <Heatmap incidents={recent} end={heatEnd} />}

      {list.length > 0 && (
        <section className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-app text-left text-xs text-ink-500">
              <tr>
                <th className="whitespace-nowrap px-6 py-3 font-medium">심각도</th>
                <th className="px-3 py-3 font-medium">장애</th>
                <th className="px-3 py-3 font-medium">호스트</th>
                <th className="px-3 py-3 font-medium">시작</th>
                <th className="whitespace-nowrap px-6 py-3 font-medium">AI 분석</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {list.map((i) => {
                const tags = shortTags(i);
                return (
                <tr key={i.id} className="cursor-pointer align-top hover:bg-app" onClick={() => navigate(`/infra/${i.id}`)}>
                  <td className="px-6 py-3"><Chip map={SEVERITY} k={i.severity.level} /></td>
                  <td className="px-3 py-3">
                    <Link href={`/infra/${i.id}`} className="font-medium text-ink-900 hover:underline" title={i.title} onClick={(e) => e.stopPropagation()}>{readableTitle(i.title)}</Link>
                    <p className="mt-0.5 text-xs text-ink-500" title={i.tags.map((t) => t.value).join(", ")}>#{i.id}{tags.length > 0 && ` · ${tags.join(", ")}`}</p>
                  </td>
                  <td className="px-3 py-3">
                    {i.host.name ? <span className="whitespace-nowrap font-mono text-xs">{i.host.name}</span> : <span className="text-xs text-ink-500" title="DejaFix가 아직 호스트 정보를 채우지 않았습니다">확인 중</span>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-xs text-ink-700" title={shortDateTime(i.started_at)}>{ago(i.started_at)}</td>
                  <td className="px-6 py-3"><Chip map={ANALYSIS} k={i.analysis.status} /></td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      </div>

      {/* 오른쪽 레일: DejaFix 현황 화면과 같은 자리 */}
      <aside className="space-y-6 xl:sticky xl:top-0" aria-label="요약">
        <UrgentCard incident={urgent} loading={!state && !error} />
        <SummaryTiles s={summary} error={summaryError} />
      </aside>
      </div>
    </div>
  );
}
