import { useEffect, useState } from "react";
import { clockKo, incidentTitle, type OrchIncident } from "@/lib/agentChat";
import { hr, type AuditEntry } from "@/lib/hr";

// 고른 이슈에서 아카이브 AI가 한 일(dashboard-api /hr-activity)을 시간순으로 보여 준다.
// 화면에는 사람이 읽는 말만 쓰고, 코드는 마우스를 올리면(title) 보인다.
const str = (v: unknown) => (typeof v === "string" ? v : null);
const CONF_KO: Record<string, string> = { low: "낮음", mid: "보통", high: "높음" };
// 아카이브 AI 감사 기록의 종류 (domains/hr audit.record)
const AUDIT_KIND_KO: Record<string, string> = {
  "record_case.card_created": "결정 카드를 기록으로 저장",
  "record_case.candidate": "확정 전 기록으로 저장 (사람 확인 대기)",
  "record_case.duplicate": "같은 기록 요청이 다시 와서 건너뜀",
  record_only: "결정이 없어 기록만 남김",
  card_write_blocked: "안전 확인 단계가 카드 저장을 막음",
  "card_decision.duplicate": "같은 승인·폐기 요청이 다시 와서 건너뜀",
  check_plan: "새 계획을 예전 결정과 견줘 봄",
  start_handover: "인수인계 시작",
  "start_handover.duplicate": "같은 인수인계 시작 요청을 건너뜀",
  answer_handover_question: "인수인계 질문에 답 기록",
  "answer.duplicate": "같은 답을 다시 받아 건너뜀",
  confirm_handover: "인수인계 확정",
  "confirm_handover.held": "인수인계 확정이 사람 승인 대기로 감",
  "confirm_handover.rejected": "인수인계 확정이 거절됨",
  "confirm_handover.gate_unavailable": "안전 확인 단계에 연결하지 못해 확정하지 못함",
  "handover.question_reasked": "비어 있는 항목을 다시 물음",
  "timer.shift_handover": "교대 시간이 되어 인수인계를 시작",
  "timer.upstream_error": "교대 인수인계 중 다른 서비스 연결 문제",
};

/** 감사 기록 한 줄의 내용을 사람이 읽는 말로 (값은 기록된 그대로, 코드는 빼고) */
function auditDetail(e: AuditEntry): string {
  const p = e.payload;
  const parts: string[] = [];
  const conf = str(p.confidence);
  if (conf) parts.push(`확신 ${CONF_KO[conf] ?? conf}`);
  if (Array.isArray(p.superseded)) parts.push(p.superseded.length ? `예전 기록 ${p.superseded.length}건을 새 기록으로 교체` : "교체한 예전 기록 없음");
  if (typeof p.pct === "number") parts.push(`채워진 정도 ${p.pct}%`);
  return parts.join(" · ");
}

export function AiActivity({ inc }: { inc: OrchIncident }) {
  const [rows, setRows] = useState<AuditEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setRows(null); setErr(null);
    hr.activity(inc.context_id)
      .then((r) => { if (alive) setRows(r); })
      .catch((e) => { if (alive) setErr(e instanceof Error ? e.message : "처리 내역을 받지 못했습니다"); });
    return () => { alive = false; };
  }, [inc.context_id]);

  const step = inc.steps.find((s) => s.skill === "record_case");
  if (err) return <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{err}</p>;
  if (!rows) return <p className="text-xs text-ink-500">불러오는 중…</p>;
  return (
    <ol className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
      {/* 총괄 AI가 아카이브 AI에게 기록을 맡긴 시각 (총괄 이슈 기록) */}
      {step?.started_at && (
        <li className="flex flex-wrap items-baseline gap-x-3 px-4 py-2 text-xs" title={`${step.skill} · ${inc.context_id}`}>
          <span className="tabular-nums text-ink-500">{clockKo(step.started_at)}</span>
          <span className="font-medium">총괄 AI가 아카이브 AI에게 이슈 기록을 맡김</span>
          <span className="text-ink-500">{incidentTitle(inc)}</span>
        </li>
      )}
      {rows.map((r) => (
        <li key={r.seq} className="flex flex-wrap items-baseline gap-x-3 px-4 py-2 text-xs" title={`${r.kind} · ${JSON.stringify(r.payload)}`}>
          <span className="tabular-nums text-ink-500">{clockKo(r.at)}</span>
          <span className="font-medium">{AUDIT_KIND_KO[r.kind] ?? "아카이브 AI 처리 기록"}</span>
          <span className="text-ink-500">{auditDetail(r)}</span>
        </li>
      ))}
      {rows.length === 0 && <li className="px-4 py-2 text-xs text-ink-500">아카이브 AI 쪽에는 이 이슈의 처리 내역이 아직 없습니다.</li>}
    </ol>
  );
}
