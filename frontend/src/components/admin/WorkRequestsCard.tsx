import { useCallback, useEffect, useState } from "react";
import { deptKo } from "@/lib/adminRelay";
import { SAFETY_HUMAN_TOKEN } from "@/lib/env";
import { ago, shortDateTime } from "@/lib/format";
import { orchestrator } from "@/lib/orchestrator";
import { requestActionError } from "@/lib/requestCards";
import {
  REQUEST_STATUS, STEP_STATUS, canDecide, canRerun, confidenceText,
  type WorkRequestRow, type WorkRequestTask,
} from "@/lib/workRequests";

// 총괄 화면의 "받은 의뢰": 고객사 담당자가 총괄 대화방에서 한 의뢰를 총괄이 어떤 일로 골라 어느 부서에 어떤 순서로 시켰는지.
// 사람이 할 일은 둘이다: 확인·승인을 기다리는 단계를 승인·거절하는 것, 잘못 골랐으면 일을 바꿔 다시 돌리는 것.
// 부서가 돌려준 문장은 의뢰 기록에 없다. 화면에는 상태·번호·정해진 문구만 나온다. 고객 글은 글자 그대로만 보여 준다.

const SHOWN = 10;
const errText = (e: unknown) => (e instanceof Error ? e.message : "처리하지 못했습니다");
const excerpt = (text: string, n = 90) => (text.length > n ? `${text.slice(0, n)}…` : text);

function Row({ row, tasks, onChanged }: { row: WorkRequestRow; tasks: WorkRequestTask[]; onChanged: () => void }) {
  const status = REQUEST_STATUS[row.status];
  const label = tasks.find((t) => t.name === row.task)?.label ?? row.task;
  const conf = confidenceText(row.confidence);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [pick, setPick] = useState("");

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setFailed(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      // 결정·다시 돌리기 실패 문구는 대화방 의뢰 카드와 같은 함수 (403 서버 차단·409 이미 처리·404·상태 코드)
      const err = requestActionError(e);
      setFailed(err.text);
      if (err.reload) onChanged();
    } finally {
      setBusy(false);
    }
  };

  const alternatives = row.alternatives.map((a) => tasks.find((t) => t.name === a)?.label ?? a);

  return (
    <li className="space-y-2 px-4 py-3 text-sm">
      <p className="flex flex-wrap items-center gap-2">
        <b className="font-medium text-ink-900">{label ?? "일 미정"}</b>
        <span className={`chip ${status.tone}`}>{status.label}</span>
        <span className="font-mono text-xs text-ink-500">{row.request_id}</span>
        <span className="ml-auto tabular-nums text-ink-500" title={shortDateTime(row.created_at)}>{ago(row.created_at)}</span>
      </p>
      <p className="break-words text-ink-700" title={row.text}>“{excerpt(row.text)}”</p>

      <details className="text-xs text-ink-700">
        <summary className="cursor-pointer select-none font-medium">진행과 고른 근거 보기</summary>
        <div className="mt-3 space-y-3">
          <section aria-label="고른 근거" className="space-y-1">
            <h3 className="font-semibold text-ink-900">고른 근거</h3>
            <p>
              {row.reason ?? "이유 없음"}
              {conf && <span className="ml-2 text-ink-500">확신도 {conf}</span>}
              {row.classification?.prompt_version && <span className="ml-2 text-ink-500">지시문 {row.classification.prompt_version}</span>}
            </p>
            {alternatives.length > 0 && <p className="text-ink-500">차선 후보: {alternatives.join(", ")}</p>}
            <p className="text-ink-500">의뢰자 {row.account}</p>
          </section>

          <section aria-label="진행" className="space-y-1">
            <h3 className="font-semibold text-ink-900">진행</h3>
            {row.plan.length === 0 ? <p className="text-ink-500">아직 시킬 일이 정해지지 않았어요.</p> : (
              <ol className="space-y-2">
                {row.plan.map((p, i) => {
                  const s = row.steps[p.step_id];
                  const tone = s ? STEP_STATUS[s.status] : null;
                  return (
                    <li key={p.step_id} className="rounded-xl border border-ink-100 px-3 py-2">
                      <p className="flex flex-wrap items-center gap-2">
                        <span className="tabular-nums text-ink-500">{i + 1}.</span>
                        <b className="font-medium text-ink-900">{p.label}</b>
                        <span className="text-ink-500">{p.dept ? deptKo(p.dept) : "사람 확인"}</span>
                        {tone && <span className={`chip ${tone.tone}`}>{tone.label}</span>}
                        {s && s.attempts > 1 && <span className="text-ink-500">{s.attempts}번 시도</span>}
                        {s?.result_ref && <span className="font-mono text-ink-500">결과 번호 {s.result_ref}</span>}
                      </p>
                      {s?.note && <p className="mt-1 text-ink-500">{s.note}</p>}
                      {s && canDecide(s) && (
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <button type="button" className="btn-primary text-xs" disabled={busy || !SAFETY_HUMAN_TOKEN}
                            title={SAFETY_HUMAN_TOKEN ? undefined : "로그인이 필요해요"}
                            onClick={() => void run(() => orchestrator.decideRequestStep(row.request_id, p.step_id, true))}>승인</button>
                          <button type="button" className="btn-ghost text-xs" disabled={busy || !SAFETY_HUMAN_TOKEN}
                            onClick={() => void run(() => orchestrator.decideRequestStep(row.request_id, p.step_id, false))}>거절</button>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </section>

          {canRerun(row) && (
            <section aria-label="다시 돌리기" className="space-y-1">
              <h3 className="font-semibold text-ink-900">잘못 골랐나요?</h3>
              <div className="flex flex-wrap items-center gap-2">
                <select className="field text-xs" value={pick} onChange={(e) => setPick(e.target.value)} aria-label="바꿀 일">
                  <option value="">바꿀 일 고르기</option>
                  {tasks.map((t) => <option key={t.name} value={t.name}>{t.name === row.task ? `${t.label} (지금 일, 처음부터 다시)` : t.label}</option>)}
                </select>
                <button type="button" className="btn-ghost text-xs" disabled={busy || !pick || !SAFETY_HUMAN_TOKEN}
                  title={SAFETY_HUMAN_TOKEN ? undefined : "로그인이 필요해요"}
                  onClick={() => void run(() => orchestrator.rerunRequest(row.request_id, pick))}>이 일로 다시 돌리기</button>
              </div>
              <p className="text-ink-500">처음 고른 일은 기록에 남아 분류 정확도를 잴 때 쓰여요. 상태를 바꾸는 일은 이번에도 승인을 거쳐요.</p>
            </section>
          )}
          {row.corrections && row.corrections.length > 0 && (
            <p className="text-ink-500">바꾼 기록: {row.corrections.map((c) => `${c.by ?? "사람"}이 ${shortDateTime(c.at)}에 바꿈`).join(" · ")}</p>
          )}
          {failed && <p role="alert" className="text-red-700">{failed}</p>}

          <details>
            <summary className="cursor-pointer select-none text-ink-500">기록 {row.log.length}줄</summary>
            <ul className="mt-1 space-y-0.5 text-ink-500">
              {row.log.slice(-8).map((l, i) => <li key={`${l.at}-${i}`}><span className="tabular-nums">{shortDateTime(l.at)}</span> {l.message}</li>)}
            </ul>
          </details>
        </div>
      </details>
    </li>
  );
}

export function WorkRequestsCard({ tick = 0 }: { tick?: number }) {
  const [rows, setRows] = useState<WorkRequestRow[] | null>(null);
  const [tasks, setTasks] = useState<WorkRequestTask[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const { data, meta } = await orchestrator.workRequests();
      setRows(data);
      setTasks(meta.tasks ?? []);
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, []);
  useEffect(() => { void load(); }, [load, tick]);

  const waiting = rows?.filter((r) => r.status === "waiting" || r.status === "needs_human").length ?? 0;

  return (
    <section className="card space-y-3 p-6" aria-labelledby="orch-requests">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="orch-requests" className="text-lg font-semibold">받은 의뢰</h2>
        {rows && <span className="text-sm tabular-nums text-ink-500">{rows.length}건{waiting ? ` · 사람 확인 ${waiting}건` : ""}</span>}
      </div>
      <p className="text-sm text-ink-500">
        고객사 담당자가 총괄 대화방에서 한 의뢰를 총괄 AI가 어떤 일로 분류해 어느 부서에 어떤 순서로 시켰는지 보여 줘요.
        확인이 필요한 단계는 여기서 승인·거절하고, 잘못 분류했으면 일을 바꿔 다시 돌릴 수 있어요. 고객 글은 데이터로만 다뤄요.
      </p>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {rows === null && !error ? <p className="text-sm text-ink-500">불러오는 중…</p> : rows && rows.length === 0 ? (
        <p className="text-sm text-ink-500">아직 받은 의뢰가 없어요. 고객이 대화방에서 “~ 만들어 주세요”처럼 일을 맡기면 올라옵니다.</p>
      ) : rows && (
        <>
          <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
            {rows.slice(0, SHOWN).map((r) => <Row key={r.request_id} row={r} tasks={tasks} onChanged={() => void load()} />)}
          </ul>
          {rows.length > SHOWN && <p className="text-sm text-ink-500">최근 {SHOWN}건만 보입니다.</p>}
        </>
      )}
    </section>
  );
}
