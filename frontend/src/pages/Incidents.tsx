import { useCallback, useEffect, useState } from "react";
import { ago, shortDateTime } from "@/lib/format";
import { ANALYSIS, SEVERITY, currentIncidents, incidentUrl, type Incident, type IncidentMeta } from "@/lib/dejafix";
import { DEJAFIX_LIVE } from "@/lib/env";

// 장애 대응 탭 = 지금 진행 중인 장애만. DejaFix API(GET /api/incidents?status=problem)에서 받아 AXcellent가 그린다.
// 상세(리포트·근거·변경 계획)는 DejaFix 화면으로 보낸다. DejaFix 화면을 이 안에 띄우지 않는다.
const REFRESH_MS = 30_000;

type State = { data: Incident[]; meta: IncidentMeta } | null;

function Chip({ map, k }: { map: Record<string | number, { label: string; tone: string }>; k: string | number }) {
  const v = map[k] ?? { label: String(k), tone: "bg-ink-100 text-ink-700" };
  return <span className={`chip ${v.tone}`}>{v.label}</span>;
}

export default function Incidents() {
  const [state, setState] = useState<State>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setState(await currentIncidents());
      setError(null);
      setLoadedAt(new Date().toISOString());
    } catch (e) {
      setError(e instanceof Error ? e.message : "장애 목록을 불러오지 못했습니다.");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const list = state?.data ?? [];
  const high = list.filter((i) => i.severity.level >= 4).length;
  const asOf = state?.meta.data_as_of ?? loadedAt;

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-8 py-8">
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
          <p className="mt-1 text-sm text-ink-500">지금 해결되지 않은 장애만 보여 줍니다. 원인 분석과 변경 계획은 DejaFix에서 봅니다.</p>
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

      {!state && !error && <p className="card px-6 py-10 text-center text-sm text-ink-500">장애 목록을 불러오는 중…</p>}

      {state && list.length === 0 && (
        <p className="card px-6 py-10 text-center text-sm text-ink-700">지금 진행 중인 장애가 없습니다.</p>
      )}

      {list.length > 0 && (
        <section className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-app text-left text-xs text-ink-500">
              <tr>
                <th className="px-6 py-3 font-medium">심각도</th>
                <th className="px-3 py-3 font-medium">장애</th>
                <th className="px-3 py-3 font-medium">호스트</th>
                <th className="px-3 py-3 font-medium">시작</th>
                <th className="px-3 py-3 font-medium">AI 분석</th>
                <th className="px-6 py-3 font-medium"><span className="sr-only">상세</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {list.map((i) => (
                <tr key={i.id} className="align-top">
                  <td className="px-6 py-3"><Chip map={SEVERITY} k={i.severity.level} /></td>
                  <td className="px-3 py-3">
                    <p className="font-medium text-ink-900">{i.title}</p>
                    <p className="mt-0.5 text-xs text-ink-500">#{i.id}{i.tags.length > 0 && ` · ${i.tags.map((t) => t.value).join(", ")}`}</p>
                  </td>
                  <td className="px-3 py-3">
                    {i.host.name ? <span className="font-mono text-xs">{i.host.name}</span> : <span className="text-xs text-ink-500" title="DejaFix가 아직 호스트 정보를 채우지 않았습니다">확인 중</span>}
                  </td>
                  <td className="px-3 py-3 text-xs text-ink-700" title={shortDateTime(i.started_at)}>{ago(i.started_at)}</td>
                  <td className="px-3 py-3"><Chip map={ANALYSIS} k={i.analysis.status} /></td>
                  <td className="px-6 py-3 text-right">
                    <a href={incidentUrl(i.id)} target="_blank" rel="noreferrer" className="btn-quiet whitespace-nowrap">DejaFix에서 보기 ↗</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
