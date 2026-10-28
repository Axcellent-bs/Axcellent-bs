import { useCallback, useEffect, useState } from "react";
import { Loading } from "@/components/admin/bits";
import {
  INCIDENT_STATUS, SHARED_KO, STEP_STATUS, adminRelay, clock, deptKo, relayEnabled, relayError,
  type Incident,
} from "@/lib/adminRelay";
import { ago } from "@/lib/format";

// 총괄 화면 (재무 AI 화면과 같은 짜임): 머리말 → 요약 카드 → 사건 목록과 상세.
// 캣북스에서 문제가 터지면 총괄이 사건 하나로 묶고 부서에 나눠 맡긴다. 이 화면은 읽기 전용이다.
const POLL_MS = 10_000;

export default function OrchestratorPage() {
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = useCallback(async () => {
    if (!relayEnabled) return;
    try {
      const { data } = await adminRelay.incidents();
      setIncidents(data); setError(null); setUpdatedAt(new Date());
      setSelected((cur) => cur ?? data.at(-1)?.context_id ?? null);
    } catch (e) { setError(relayError(e as { status?: number; message: string })); }
  }, []);
  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  if (!relayEnabled) return <Loading error="총괄 읽기 연결이 꺼져 있습니다. 개발 서버에서는 자동으로 켜지고, 그 밖에서는 VITE_SHOP_ADMIN_TOKEN과 VITE_SHOP_ADMIN_API_URL이 필요합니다." />;
  if (!incidents) return <Loading error={error} what="총괄 사건을 불러오는 중…" />;

  const list = [...incidents].reverse();
  const current = list.find((i) => i.context_id === selected) ?? null;
  const open = incidents.filter((i) => i.status === "open").length;
  const holds = incidents.reduce((n, i) => n + i.pending_holds.length, 0);
  const tone = holds ? "text-amber-800" : "text-emerald-800";

  return (
    <div className="max-w-[1320px] space-y-8 px-8 pb-12 pt-2">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">총괄·사건</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-500">캣북스에서 문제가 터지면 총괄 에이전트가 사건 하나로 묶고, 7개 부서에 일을 나눠 맡깁니다. 사람 승인이 필요한 일만 사람에게 올립니다. 이 화면은 읽기 전용입니다.</p>
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs">
          <button className="btn-ghost" onClick={load}>새로고침{updatedAt ? ` · ${clock(updatedAt.toISOString())}` : ""}</button>
        </div>
      </header>

      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">{error}</p>}

      {/* 요약: 화면에서 가장 중요한 곳 */}
      <section aria-labelledby="orch-summary" className={`rounded-[28px] p-6 ring-1 ${holds ? "bg-amber-50 ring-amber-100" : "bg-emerald-50 ring-emerald-100"}`}>
        <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
          <div>
            <h2 id="orch-summary" className={`text-sm font-semibold ${tone}`}>사람 승인을 기다리는 일</h2>
            <p className={`mt-1 text-4xl font-bold tabular-nums ${tone}`}>{holds ? `${holds}건` : "없음"}</p>
          </div>
          <dl className={`flex gap-8 pb-1 text-sm ${tone}`}>
            <div><dt className="text-xs opacity-80">전체 사건</dt><dd className="text-lg font-semibold tabular-nums">{incidents.length}건</dd></div>
            <div><dt className="text-xs opacity-80">진행 중</dt><dd className="text-lg font-semibold tabular-nums">{open}건</dd></div>
          </dl>
          <p className={`ml-auto max-w-sm pb-1 text-xs ${tone}`}>승인·거절은 총괄이 하지 않고 사람이 안전 관문 보류 큐에서 합니다. 대부분의 단계는 총괄이 규칙대로 부서에 맡겨 끝냅니다.</p>
        </div>
      </section>

      {list.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-ink-300 px-5 py-8 text-center text-sm text-ink-500">
          아직 사건이 없습니다. 총괄 시연 화면(/demo)에서 장애를 주입하면 여기에 나타납니다.
        </p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
          <ul className="space-y-2" aria-label="사건 목록">
            {list.map((i) => (
              <li key={i.context_id}>
                <button onClick={() => setSelected(i.context_id)}
                  className={`w-full rounded-2xl border p-3 text-left text-sm transition ${i.context_id === selected ? "border-brand-500 bg-brand-50" : "border-ink-100 bg-surface hover:border-brand-500"}`}>
                  <span className="flex items-center justify-between gap-2">
                    <span className={`chip ${INCIDENT_STATUS[i.status].tone}`}>{INCIDENT_STATUS[i.status].label}</span>
                    <span className="text-xs text-ink-500">{ago(i.opened_at)}</span>
                  </span>
                  <span className="mt-1.5 block truncate font-semibold">{i.title}</span>
                  <span className="block truncate text-xs text-ink-500">{i.context_id}</span>
                </button>
              </li>
            ))}
          </ul>
          {current && <IncidentDetail incident={current} />}
        </div>
      )}
    </div>
  );
}

function IncidentDetail({ incident }: { incident: Incident }) {
  const shared = Object.entries(incident.shared ?? {});
  const done = incident.steps.filter((s) => s.status === "completed").length;
  return (
    <div className="min-w-0 space-y-5">
      <section className="card space-y-3 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold">{incident.title}</h2>
          <span className={`chip ${INCIDENT_STATUS[incident.status].tone}`}>{INCIDENT_STATUS[incident.status].label}</span>
          <span className="ml-auto text-xs tabular-nums text-ink-500">단계 {done}/{incident.steps.length} 완료</span>
        </div>
        <p className="text-xs text-ink-500">{incident.context_id} · 시작 {clock(incident.opened_at)} · 종료 {clock(incident.closed_at)}</p>
        {!!shared.length && (
          <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
            {shared.map(([key, value]) => (
              <div key={key} className="flex gap-2"><dt className="text-ink-500">{SHARED_KO[key] ?? key}</dt><dd className="font-medium tabular-nums">{String(value)}</dd></div>
            ))}
          </dl>
        )}
        {!!incident.pending_holds.length && (
          <div className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800">
            <p className="font-semibold">사람 승인 대기 {incident.pending_holds.length}건</p>
            <ul className="mt-1 list-inside list-disc">{incident.pending_holds.map((h) => <li key={h.step_id}>{h.label}</li>)}</ul>
          </div>
        )}
      </section>

      <section className="card overflow-x-auto p-5">
        <h3 className="mb-3 text-[15px] font-semibold">단계</h3>
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-ink-500">
            <tr><th className="py-1 pr-3 font-medium">단계</th><th className="pr-3 font-medium">부서</th><th className="pr-3 font-medium">상태</th><th className="pr-3 font-medium">시도</th><th className="font-medium">메모</th></tr>
          </thead>
          <tbody>
            {incident.steps.map((s) => (
              <tr key={s.step_id} className="border-t border-ink-100 align-top">
                <td className="py-2 pr-3"><span className="font-medium">{s.label}</span><span className="block text-xs text-ink-500">{s.skill}</span></td>
                <td className="whitespace-nowrap py-2 pr-3">
                  {deptKo(s.dept)}
                  {s.transferred_to && <span className="block text-xs text-ink-500">→ {deptKo(s.transferred_to)}로 이관</span>}
                </td>
                <td className="whitespace-nowrap py-2 pr-3">
                  <span className={`chip ${(STEP_STATUS[s.status] ?? STEP_STATUS.pending).tone}`}>{(STEP_STATUS[s.status] ?? { label: s.status }).label}</span>
                  {s.grade && <span className="ml-1 text-[11px] text-ink-500">{s.grade}</span>}
                </td>
                <td className="py-2 pr-3 tabular-nums">{s.attempts || "–"}</td>
                <td className="py-2 text-xs text-ink-500">{s.note || "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {!!incident.summary.length && (
        <section className="card p-5">
          <h3 className="mb-2 text-[15px] font-semibold">요약</h3>
          <ul className="list-inside list-disc space-y-0.5 text-sm">{incident.summary.map((line) => <li key={line}>{line}</li>)}</ul>
        </section>
      )}

      <section className="card p-5">
        <h3 className="mb-2 text-[15px] font-semibold">진행 기록</h3>
        <ol className="space-y-1 text-sm">
          {incident.events.map((e, idx) => (
            <li key={`${e.at}-${idx}`} className="flex gap-3"><span className="w-16 shrink-0 text-xs tabular-nums text-ink-500">{clock(e.at)}</span><span>{e.message}</span></li>
          ))}
        </ol>
      </section>
    </div>
  );
}
