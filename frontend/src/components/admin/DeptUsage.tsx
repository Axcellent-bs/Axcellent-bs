import { useCallback, useEffect, useState } from "react";
import {
  GRADE, UNKNOWN, adminRelay, clock, deptKo, millis, rate, relayEnabled, relayError,
  type Department, type DepartmentsMeta, type UsageBucket,
} from "@/lib/adminRelay";

// 대시보드의 「부서별 호출 현황」. 총괄이 부서를 몇 번, 얼마나 잘 불렀는지 총괄이 센 실측으로 보여 준다.
// 값이 없으면 0이나 빈칸이 아니라 "확인할 수 없음"으로 적는다. 읽기 전용이다.
const POLL_MS = 10_000;

function Bars({ series }: { series: UsageBucket[] }) {
  const max = Math.max(1, ...series.map((b) => b.calls));
  return (
    <div className="flex h-9 items-end gap-px" role="img" aria-label="최근 호출 수 추이">
      {series.map((b) => (
        <span key={b.t} title={`${clock(b.t)} · 호출 ${b.calls}${b.errors ? ` · 오류 ${b.errors}` : ""}`}
          className={`w-full rounded-sm ${b.errors ? "bg-red-400" : b.calls ? "bg-brand-400" : "bg-ink-100"}`}
          style={{ height: `${Math.max(8, (b.calls / max) * 100)}%` }} />
      ))}
    </div>
  );
}

function Cell({ label, value, tone = "" }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="rounded-xl bg-ink-50 px-3 py-2">
      <p className="text-[11px] text-ink-500">{label}</p>
      <p className={`mt-0.5 text-sm font-semibold tabular-nums ${tone}`}>{value}</p>
    </div>
  );
}

function DeptCard({ d, open, onToggle }: { d: Department; open: boolean; onToggle: () => void }) {
  return (
    <article className="card space-y-3 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-[15px] font-semibold">{deptKo(d.name)}</h3>
        <span className={`chip ${d.mode === "real" ? "bg-emerald-50 text-emerald-700" : "bg-brand-50 text-brand-700"}`}>{d.mode === "real" ? "실제 연결" : "가상 부서"}</span>
        {!d.available && <span className="chip bg-red-50 text-red-700">카드 없음</span>}
        <span className="ml-auto text-[11px] text-ink-500">담당 {d.owner ?? UNKNOWN}</span>
      </header>
      {!d.available ? (
        <p className="rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700">{d.unavailable_reason ?? "부서 카드를 읽지 못했습니다."}</p>
      ) : (
        <>
          <div className="grid grid-cols-4 gap-2">
            <Cell label="호출" value={d.usage.calls} />
            <Cell label="오류" value={d.usage.errors} tone={d.usage.errors ? "text-red-700" : ""} />
            <Cell label="성공률" value={rate(d.usage.success_rate)} />
            <Cell label="평균 응답" value={millis(d.usage.avg_ms)} />
          </div>
          <Bars series={d.series} />
          <p className="text-[11px] text-ink-500">
            {d.usage.calls === 0 ? "아직 총괄이 부른 기록이 없습니다." : `마지막 호출 ${clock(d.usage.last_called_at)} · 재시도 ${d.usage.retries}건`}
            {d.usage.waiting ? ` · 사람 입력 대기 ${d.usage.waiting}건` : ""}
          </p>
          <button className="btn-ghost text-xs" onClick={onToggle} aria-expanded={open}>{open ? "접기" : `skill ${d.skills.length}개와 최근 호출`}</button>
          {open && <Detail d={d} />}
        </>
      )}
    </article>
  );
}

function Detail({ d }: { d: Department }) {
  return (
    <div className="space-y-4 border-t border-ink-100 pt-3">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-ink-500">
            <tr>
              <th className="py-1 pr-3 font-medium">skill</th><th className="pr-3 font-medium">등급</th>
              <th className="pr-3 text-right font-medium">호출</th><th className="pr-3 text-right font-medium">오류</th>
              <th className="pr-3 text-right font-medium">성공률</th><th className="text-right font-medium">평균</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {d.skills.map((s) => (
              <tr key={s.id}>
                <td className="py-1.5 pr-3 font-mono">{s.id}{!s.orchestrator && <span className="ml-1 text-ink-500">(총괄은 안 부름)</span>}</td>
                <td className="pr-3"><span className={`chip ${(GRADE[s.grade] ?? GRADE.read).tone}`}>{(GRADE[s.grade] ?? { label: s.grade }).label}</span></td>
                <td className="pr-3 text-right tabular-nums">{s.calls}</td>
                <td className="pr-3 text-right tabular-nums">{s.errors}</td>
                <td className="pr-3 text-right tabular-nums">{rate(s.success_rate)}</td>
                <td className="text-right tabular-nums">{millis(s.avg_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <p className="mb-1 text-xs font-medium">최근 호출</p>
        {d.recent.length === 0 ? <p className="text-xs text-ink-500">기록이 없습니다.</p> : (
          <ul className="divide-y divide-ink-100 text-xs">
            {d.recent.slice(0, 6).map((r, i) => (
              <li key={`${r.at}${i}`} className="flex flex-wrap items-baseline gap-x-3 py-1">
                <span className="tabular-nums text-ink-500">{clock(r.at)}</span>
                <span className="font-mono">{r.skill}</span>
                <span className={r.status === "completed" ? "text-emerald-700" : "text-amber-700"}>{r.status}</span>
                {r.outcome && <span className="text-ink-500">{r.outcome}</span>}
                <span className="ml-auto tabular-nums text-ink-500">{millis(r.duration_ms)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export function DeptUsage() {
  const [depts, setDepts] = useState<Department[] | null>(null);
  const [meta, setMeta] = useState<DepartmentsMeta>({});
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!relayEnabled) return;
    try {
      const { data, meta: m } = await adminRelay.departments();
      setDepts(data); setMeta(m); setError(null);
    } catch (e) { setError(relayError(e as { status?: number; message: string })); }
  }, []);
  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  if (!relayEnabled) return null;
  const windowMin = meta.window_seconds ? Math.round(meta.window_seconds / 60) : null;
  return (
    <section aria-labelledby="dept-usage">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3">
        <h2 id="dept-usage" className="text-base font-semibold">부서별 호출 현황</h2>
        <p className="text-xs text-ink-500">총괄이 부서를 부른 호출을 센 실측입니다{windowMin ? ` (최근 ${windowMin}분)` : ""}. 읽기 전용입니다.</p>
        {meta.mock && <span className="chip bg-brand-50 text-brand-700">가상 데이터</span>}
      </div>
      {error && <p role="alert" className="mb-3 rounded-xl bg-amber-50 px-4 py-2 text-xs text-amber-800">{error}</p>}
      {!depts && !error && <p className="text-xs text-ink-500">불러오는 중…</p>}
      {depts && (
        <div className="grid gap-4 xl:grid-cols-2">
          {depts.map((d) => <DeptCard key={d.name} d={d} open={open === d.name} onToggle={() => setOpen(open === d.name ? null : d.name)} />)}
        </div>
      )}
    </section>
  );
}
