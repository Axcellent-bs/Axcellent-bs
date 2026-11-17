import { useCallback, useEffect, useState } from "react";
import {
  STATUS_LABEL, canRevoke, deliveryOf, isQuiet,
  type AgentDiagnosisRow,
} from "@/lib/agentDiagnosis";
import { agentName } from "@/lib/agentEval";
import { SAFETY_HUMAN_TOKEN } from "@/lib/env";
import { shortDateTime } from "@/lib/format";
import { Link } from "@/lib/nav";
import { orchestrator } from "@/lib/orchestrator";

// 총괄 화면의 "에이전트 평가 진단": 인사 AI가 에이전트 평가에서 같은 단계의 확인 필요가 반복된 것을 찾아 보내면,
// 총괄 AI가 근거를 모아 진단하고 해당 에이전트에 개선 노트로 전달한다. 사람이 누를 버튼은 철회 하나뿐이다.
// 이 진단은 평가가 아니라 원인 설명이고 틀릴 수 있다. 화면은 총괄이 저장한 값을 그대로 보여 준다.

const SHOWN = 10;
const errText = (e: unknown) => (e instanceof Error ? e.message : "불러오지 못했습니다");

function Row({ row, onChanged }: { row: AgentDiagnosisRow; onChanged: () => void }) {
  const status = STATUS_LABEL[row.status];
  const delivery = deliveryOf(row);
  const diagnosis = row.diagnosis;
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const revoke = async () => {
    setBusy(true);
    setFailed(null);
    try {
      await orchestrator.revokeDiagnosis(row.request_id);
      setAsking(false);
      onChanged();
    } catch (e) {
      setFailed(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="space-y-2 px-4 py-3 text-sm">
      <p className="flex flex-wrap items-center gap-2">
        <b className="font-medium text-ink-900">{agentName(row.agent)} · {row.step_label}</b>
        <span className={`chip ${status.tone}`}>{status.label}</span>
        {delivery && row.delivery && <span className={`chip ${delivery.tone}`} title={row.delivery.reason ?? undefined}>{delivery.label}</span>}
        <span className="ml-auto tabular-nums text-ink-500">{shortDateTime(row.updated_at)}</span>
      </p>
      <p className="text-ink-700">
        {diagnosis
          ? diagnosis.summary // 진단 문장이 "최근 N건 중 M건…"으로 시작하니 같은 말을 앞에 다시 쓰지 않는다
          : `최근 ${row.window}건 중 ${row.flagged}건에서 확인이 필요했어요. ${row.status === "diagnosing" ? "근거를 모아 진단하는 중이에요." : "진단을 기다리고 있어요."}`}
      </p>
      {row.delivery?.reason && row.delivery.status !== "accepted" && (
        <p className="text-xs text-ink-500">{row.delivery.reason}</p>
      )}
      {diagnosis && diagnosis.notes.length > 0 && <p className="text-xs text-amber-800">{diagnosis.notes.join(" · ")}</p>}
      {diagnosis && (
        <details className="text-xs text-ink-600">
          <summary className="cursor-pointer select-none font-medium text-ink-700">근거 {diagnosis.items.length}개 보기</summary>
          <ul className="mt-2 space-y-1">
            {diagnosis.items.map((it) => (
              <li key={it.id} className="flex gap-2">
                <span className="w-7 shrink-0 font-mono text-ink-500">{it.id}</span>
                <span className="min-w-0 break-words">
                  <Link href={`/archive/incidents?ctx=${encodeURIComponent(it.context_id)}`} className="underline underline-offset-2">{it.text}</Link>
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-ink-500">방식: {diagnosis.method === "rules" ? "규칙으로 겹치는 점을 찾음" : "AI 요약"}. 틀릴 수 있어서 근거를 같이 보여 줘요.</p>
        </details>
      )}
      {canRevoke(row) && (
        <div className="flex flex-wrap items-center gap-2">
          {asking ? (
            <>
              <span className="text-xs text-ink-600">에이전트가 이 노트를 쓰지 않게 거둘까요?</span>
              <button type="button" className="btn-ghost text-xs" disabled={busy} onClick={() => void revoke()}>{busy ? "철회하는 중…" : "철회하기"}</button>
              <button type="button" className="btn-ghost text-xs" disabled={busy} onClick={() => setAsking(false)}>취소</button>
            </>
          ) : (
            <button type="button" className="btn-ghost text-xs" disabled={!SAFETY_HUMAN_TOKEN}
              title={SAFETY_HUMAN_TOKEN ? undefined : "사람 권한 토큰이 없어 철회할 수 없어요"} onClick={() => setAsking(true)}>전달 철회</button>
          )}
          {failed && <span role="alert" className="text-xs text-red-700">{failed}</span>}
        </div>
      )}
    </li>
  );
}

export function AgentDiagnosisCard({ tick = 0 }: { tick?: number }) {
  const [rows, setRows] = useState<AgentDiagnosisRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showQuiet, setShowQuiet] = useState(false);
  const load = useCallback(async () => {
    try {
      setRows(await orchestrator.agentDiagnoses());
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, []);
  useEffect(() => { void load(); }, [load, tick]);

  const quiet = rows?.filter(isQuiet).length ?? 0;
  const visible = rows?.filter((r) => showQuiet || !isQuiet(r)) ?? [];

  return (
    <section className="card space-y-3 p-6" aria-labelledby="orch-eval">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="orch-eval" className="text-lg font-semibold">에이전트 평가 진단</h2>
        {rows && <span className="text-sm tabular-nums text-ink-500">받은 진단 {rows.length}건</span>}
        <Link href="/agent-eval" className="ml-auto text-sm font-medium text-brand-700 underline underline-offset-2">에이전트 평가 보기 →</Link>
      </div>
      <p className="text-sm text-ink-500">
        인사 AI가 같은 단계의 확인 필요가 반복되는 에이전트를 찾으면 자동으로 이곳에 올라와요. 총괄 AI가 근거를 모아 진단하고, 해당 에이전트에 개선 노트로 바로 전달합니다.
        원인을 가릴 근거가 없으면 "원인 미확정"으로 사실만 전달해요. 노트는 알림이라 에이전트의 동작을 바꾸지 않고, 진단은 평가가 아니라 원인 설명이라 틀릴 수 있어요.
      </p>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {rows === null && !error ? <p className="text-sm text-ink-500">불러오는 중…</p> : rows && rows.length === 0 ? (
        <p className="text-sm text-ink-500">아직 받은 진단이 없어요. 같은 단계가 최근 3건 중 2건 이상 확인 필요가 되면 올라옵니다.</p>
      ) : rows && (
        <>
          {visible.length === 0 && <p className="text-sm text-ink-500">보여 줄 진단이 없어요. 철회했거나 쉬는 진단만 있어요.</p>}
          {visible.length > 0 && <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
            {visible.slice(0, SHOWN).map((r) => <Row key={r.request_id} row={r} onChanged={() => void load()} />)}
          </ul>}
          {visible.length > SHOWN && <p className="text-sm text-ink-500">최근 {SHOWN}건만 보입니다.</p>}
          {quiet > 0 && (
            <button type="button" className="btn-ghost text-xs" onClick={() => setShowQuiet((v) => !v)}>
              {showQuiet ? "철회·쉬는 진단 숨기기" : `철회했거나 쉬는 진단 ${quiet}건 보기`}
            </button>
          )}
        </>
      )}
    </section>
  );
}
