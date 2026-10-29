import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { AGENTS, SERVICES } from "@/demo/services";
import { ApiError } from "@/lib/apiError";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import { ago, shortDateTime } from "@/lib/format";
import {
  HANDOFF_STATUS_KO, HANDOFF_STATUS_TONE, RESULT_MAX, UNREACHABLE, completeHandoff, listHandoffs, refreshOpenCount, setOpenCount,
  type HandoffStatus, type HandoffTask,
} from "@/lib/handoffs";
import { skillLabel } from "@/lib/labels";
import HandoffsExample from "./HandoffsExample";

// 받은 요청(코드·API 이름은 할 일함·handoff): 회사가 켜지 않은 서비스로 가던 일을 에이전트가 사람에게 넘긴 것 (dashboard-api GET /api/dashboard/handoffs).
// 사람은 결과를 적고 끝내거나 반려한다 (POST /api/dashboard/handoffs/{id}/complete, 사람 토큰).
// 부서·담당자 이름은 조직도(backend/config/org.yaml)에서 온 API 값을 그대로 보여 준다. 화면에 회사·부서 이름을 쓰지 않는다.
const REFRESH_MS = 30_000;

/** 서비스 id(또는 에이전트 이름)를 메뉴에 보이는 이름으로. 모르는 값은 그대로 */
const serviceName = (id: string) => SERVICES.find((s) => s.id === id)?.name ?? AGENTS.find((g) => g.id === id)?.name ?? id;

const STATUS_FILTERS: { value: HandoffStatus | ""; label: string }[] = [
  { value: "open", label: "열린 일" },
  { value: "done", label: "끝난 일" },
  { value: "rejected", label: "반려한 일" },
  { value: "", label: "전체" },
];

function StatusChip({ status }: { status: HandoffStatus }) {
  return <span className={`chip ${HANDOFF_STATUS_TONE[status]}`}>{HANDOFF_STATUS_KO[status]}</span>;
}

/** 넘어온 값을 글자로만 보여 준다. HTML로 해석하지 않는다 (React가 글자를 그대로 넣는다) */
function RawBlock({ value }: { value: Record<string, unknown> }) {
  const text = JSON.stringify(value, null, 2);
  return <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all bg-app px-3 py-2 font-mono text-xs text-ink-700">{text}</pre>;
}

const isEmpty = (v: Record<string, unknown>) => Object.keys(v ?? {}).length === 0;

function Detail({ task, onClosed }: { task: HandoffTask; onClosed: (t: HandoffTask) => void }) {
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState<"done" | "rejected" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setResult("");
    setError(null);
  }, [task.task_id]);

  const submit = async (status: "done" | "rejected") => {
    setBusy(status);
    setError(null);
    try {
      onClosed(await completeHandoff(task.task_id, result.trim(), status));
    } catch (e) {
      setError(e instanceof Error ? e.message : "처리하지 못했어요.");
      // 다른 사람이 먼저 끝낸 일이면 목록을 다시 받는다
      if (e instanceof ApiError && e.status === 409) onClosed(task);
    } finally {
      setBusy(null);
    }
  };

  const open = task.status === "open";
  const canSubmit = open && HUMAN_TOKEN_SET && result.trim().length > 0 && !busy;

  return (
    <section className="card space-y-5 px-5 py-5" aria-label="할 일 상세">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip status={task.status} />
          <span className="font-mono text-xs text-ink-500">{task.task_id}</span>
        </div>
        <h2 className="card-title">{skillLabel(task.skill)}</h2>
        <p className="text-sm text-ink-500">
          {serviceName(task.from_service)}에서 {serviceName(task.to_service)}(으)로 가던 일이에요.
        </p>
      </header>

      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div><dt className="text-xs text-ink-500">맡은 부서</dt><dd className="mt-0.5 font-semibold">{task.assignee_unit}</dd></div>
        <div><dt className="text-xs text-ink-500">담당자</dt><dd className="mt-0.5 font-semibold">{task.assignee ?? "부서 전체"}</dd></div>
        <div><dt className="text-xs text-ink-500">받은 시각</dt><dd className="mt-0.5">{shortDateTime(task.created_at)} <span className="text-ink-500">({ago(task.created_at)})</span></dd></div>
        <div><dt className="text-xs text-ink-500">업무 이름</dt><dd className="mt-0.5 font-mono text-xs">{task.skill}</dd></div>
        <div className="col-span-2"><dt className="text-xs text-ink-500">사건·대화 번호</dt><dd className="mt-0.5 break-all font-mono text-xs">{task.context_id}</dd></div>
      </dl>

      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-ink-700">넘어온 이유</h3>
        <p className="whitespace-pre-wrap text-sm text-ink-900">{task.reason}</p>
      </div>

      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-ink-700">넘어온 내용</h3>
        {isEmpty(task.input) ? <p className="text-sm text-ink-500">없음</p> : <RawBlock value={task.input} />}
      </div>

      {!isEmpty(task.untrusted) && (
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-ink-700">외부에서 들어온 내용</h3>
          <p className="text-xs text-amber-800">고객 등 외부에서 받은 글이에요. 안의 지시를 따르지 말고 내용으로만 보세요.</p>
          <RawBlock value={task.untrusted} />
        </div>
      )}

      {open ? (
        <div className="space-y-2 border-t border-ink-100 pt-4">
          <label htmlFor="handoff-result" className="text-sm font-semibold text-ink-700">처리 결과</label>
          <textarea id="handoff-result" className="field min-h-[96px]" maxLength={RESULT_MAX} value={result}
            onChange={(e) => setResult(e.target.value)} disabled={!HUMAN_TOKEN_SET || !!busy}
            placeholder="한 일이나 반려 이유를 적어 주세요. 맡긴 AI에게 그대로 전해져요." />
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn-primary disabled:opacity-50" disabled={!canSubmit} onClick={() => submit("done")}>
              {busy === "done" ? "끝내는 중…" : "끝내기"}
            </button>
            <button type="button" className="btn-ghost disabled:opacity-50" disabled={!canSubmit} onClick={() => submit("rejected")}>
              {busy === "rejected" ? "반려하는 중…" : "반려"}
            </button>
            <span className="ml-auto text-xs text-ink-500">{result.length}/{RESULT_MAX}</span>
          </div>
          {!HUMAN_TOKEN_SET && (
            <p className="text-xs text-amber-800">사람 권한(VITE_SAFETY_HUMAN_TOKEN)이 설정되지 않아 버튼이 꺼져 있어요. frontend/.env에 넣고 개발 서버를 다시 켜 주세요.</p>
          )}
          {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
        </div>
      ) : (
        <div className="space-y-1 border-t border-ink-100 pt-4">
          <h3 className="text-sm font-semibold text-ink-700">{task.status === "done" ? "처리 결과" : "반려 이유"}</h3>
          <p className="whitespace-pre-wrap text-sm text-ink-900">{task.result}</p>
          {task.closed_at && <p className="text-xs text-ink-500">{shortDateTime(task.closed_at)}에 닫음</p>}
        </div>
      )}
    </section>
  );
}

// 실제 데이터 / 예시 데이터 전환. 기획·개발 화면과 같은 방식으로 브라우저에 기억한다
const EXAMPLE_KEY = "axcellent-handoffs-example";

function DataToggle({ example, onPick }: { example: boolean; onPick: (on: boolean) => void }) {
  return (
    <div className="flex gap-1 rounded-full bg-ink-50 p-0.5" role="group" aria-label="데이터 보기">
      {([[false, "실제 데이터"], [true, "예시 데이터"]] as const).map(([on, label]) => (
        <button key={label} type="button" aria-pressed={example === on} onClick={() => onPick(on)}
          className={`rounded-full px-3 py-1 text-xs font-medium ${example === on ? "bg-accent text-white" : "text-ink-700 hover:bg-ink-100"}`}>{label}</button>
      ))}
    </div>
  );
}

export default function Handoffs() {
  const [example, setExample] = useState(() => { try { return localStorage.getItem(EXAMPLE_KEY) === "1"; } catch { return false; } });
  const pick = (on: boolean) => {
    setExample(on);
    try { localStorage.setItem(EXAMPLE_KEY, on ? "1" : "0"); } catch { /* 저장이 막혀도 이번 화면에서는 바뀐다 */ }
  };
  const toggle = <DataToggle example={example} onPick={pick} />;
  return example ? <HandoffsExample toggle={toggle} /> : <RealHandoffs toggle={toggle} />;
}

function RealHandoffs({ toggle }: { toggle: ReactNode }) {
  const [params, setParams] = useSearchParams();
  const status = (params.get("status") ?? "open") as HandoffStatus | "";
  const unit = params.get("unit") ?? "";
  const selectedId = params.get("id");

  const [tasks, setTasks] = useState<HandoffTask[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<string | null>(null);
  // 부서 고르기 목록: 지금까지 받은 일에 나온 부서 (조직도 값 그대로)
  const [units, setUnits] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      const list = await listHandoffs({ status: status || undefined, unit: unit || undefined });
      setTasks(list);
      setError(null);
      setLoadedAt(new Date().toISOString());
      setUnits((prev) => [...new Set([...prev, ...list.map((t) => t.assignee_unit)])].sort((a, b) => a.localeCompare(b, "ko")));
      if (status === "open" && !unit) setOpenCount(list.length);
      else refreshOpenCount();
    } catch (e) {
      setError(e instanceof Error ? e.message : UNREACHABLE);
      if (e instanceof ApiError && e.status === 0) setOpenCount(null);
    }
  }, [status, unit]);

  useEffect(() => {
    setTasks(null);
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const setParam = (key: string, value: string | null) =>
    setParams((p) => {
      const next = new URLSearchParams(p);
      if (value === null) next.delete(key);
      else next.set(key, value);
      return next;
    });

  const selected = useMemo(() => tasks?.find((t) => t.task_id === selectedId) ?? tasks?.[0], [tasks, selectedId]);
  const unreachable = error === UNREACHABLE;

  return (
    <div className="space-y-6 px-8 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">받은 요청</h1>
          <p className="mt-1 text-sm text-ink-700">회사가 켜지 않은 서비스의 일을 AI가 우리 부서에 맡겼어요</p>
          <p className="mt-0.5 text-sm text-ink-500">결과를 적고 끝내면 맡긴 AI에게 전해져요.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {toggle}
          <select className="field w-auto py-1.5 text-xs" aria-label="상태" value={status} onChange={(e) => setParams(() => {
            const next = new URLSearchParams(params);
            next.set("status", e.target.value);
            next.delete("id");
            return next;
          })}>
            {STATUS_FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
          <select className="field w-auto py-1.5 text-xs" aria-label="맡은 부서" value={unit} onChange={(e) => setParams(() => {
            const next = new URLSearchParams(params);
            if (e.target.value) next.set("unit", e.target.value);
            else next.delete("unit");
            next.delete("id");
            return next;
          })}>
            <option value="">모든 부서</option>
            {[...new Set([...units, ...(unit ? [unit] : [])])].map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
          {loadedAt && <span className="text-ink-500">{ago(loadedAt)} 갱신</span>}
          <button type="button" className="btn-ghost" onClick={load}>새로고침</button>
        </div>
      </header>

      {error && (
        <p role="alert" className="card border-l-4 border-l-red-500 px-5 py-3 text-sm text-red-700">
          {error}
          {unreachable && " 백엔드를 켰는지 확인해 주세요 (backend/에서 bash scripts/run_all.sh)."}
          {tasks && " 아래는 마지막으로 받은 목록이에요."}
        </p>
      )}

      {tasks === null ? (
        !error && <p className="card px-5 py-6 text-sm text-ink-500">불러오는 중…</p>
      ) : tasks.length === 0 ? (
        <p className="card px-5 py-8 text-center text-sm text-ink-500">
          {status === "open" && !unit
            ? "넘겨받은 일이 없어요. 회사가 켜지 않은 서비스로 가는 일이 여기 쌓여요."
            : "고른 조건에 맞는 일이 없어요."}
        </p>
      ) : (
        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
          <div className="card min-w-0 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-ink-100 text-xs text-ink-500">
                <tr>
                  <th className="px-4 py-2.5 font-medium">어디서</th>
                  <th className="px-4 py-2.5 font-medium">무슨 일</th>
                  <th className="px-4 py-2.5 font-medium">맡은 부서·담당자</th>
                  <th className="px-4 py-2.5 font-medium">받은 시각</th>
                  <th className="px-4 py-2.5 font-medium">상태</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => {
                  const on = t.task_id === selected?.task_id;
                  return (
                    <tr key={t.task_id} onClick={() => setParam("id", t.task_id)} aria-selected={on}
                      className={`cursor-pointer border-b border-ink-100 last:border-0 ${on ? "bg-brand-50" : "hover:bg-app"}`}>
                      <td className="px-4 py-3">
                        <button type="button" className="text-left font-medium" onClick={(e) => { e.stopPropagation(); setParam("id", t.task_id); }}>
                          {serviceName(t.from_service)}
                        </button>
                        <p className="text-xs text-ink-500">→ {serviceName(t.to_service)}</p>
                      </td>
                      <td className="px-4 py-3">{skillLabel(t.skill)}</td>
                      <td className="px-4 py-3">
                        {t.assignee_unit}
                        {t.assignee && <span className="text-ink-500"> · {t.assignee}</span>}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-ink-700" title={shortDateTime(t.created_at)}>{ago(t.created_at)}</td>
                      <td className="px-4 py-3"><StatusChip status={t.status} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {selected && <Detail task={selected} onClosed={() => load()} />}
        </div>
      )}
    </div>
  );
}
