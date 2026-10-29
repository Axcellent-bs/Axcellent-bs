import { useCallback, useEffect, useState } from "react";
import { clockKo, incidentTitle, INCIDENT_STATUS_KO, type OrchIncident, type OrchStep } from "@/lib/agentChat";
import { dateStr } from "@/lib/format";
import { hr, type AuditEntry } from "@/lib/hr";
import { Link } from "@/lib/nav";
import RepeatCompare from "@/components/hr/RepeatCompare";
import ResolutionForm from "@/components/hr/ResolutionForm";

// 결정 기록 화면(실제 모드): 캣북스 사건이 나면 총괄 AI가 인사 AI에게 사건 기록(record_case)을 맡긴다.
// 그 단계를 총괄 사건 목록(dashboard-api /incidents)에서 꺼내 "어느 사건을, 언제, 무엇으로, 어떤 상태로" 기록했는지 보여 주고,
// 고른 사건에서 인사 AI가 한 일(dashboard-api /hr-activity)을 시간순으로 보여 준다.
// 화면에는 사람이 읽는 말만 쓰고, 사건 번호·카드 번호·기록 종류 코드는 마우스를 올리면(title) 보인다.

const RECORD_SKILL = "record_case";

const STEP_KO: Record<string, { label: string; tone: string }> = {
  completed: { label: "기록됨", tone: "bg-emerald-50 text-emerald-700" },
  running: { label: "기록 중", tone: "bg-brand-50 text-brand-700" },
  pending: { label: "기록 전", tone: "bg-ink-100 text-ink-500" },
  held: { label: "사람 확인 대기", tone: "bg-amber-50 text-amber-700" },
  waiting_human: { label: "담당 부서 처리 대기", tone: "bg-violet-50 text-violet-700" },
  failed: { label: "기록 실패", tone: "bg-red-50 text-red-700" },
  skipped: { label: "건너뜀", tone: "bg-ink-100 text-ink-500" },
  rejected_by_human: { label: "사람이 거절", tone: "bg-red-50 text-red-700" },
};
// 인사 AI가 돌려준 기록 결과
const OUTCOME_KO: Record<string, string> = {
  card_created: "결정 카드를 회사 기억에 저장했어요",
  candidate: "확신이 낮아 후보 기록으로 남겼어요 (사람 확인 필요)",
  record_only: "결정이 없는 사건이라 기록만 남겼어요",
  duplicate: "이미 기록된 사건이라 다시 저장하지 않았어요",
};
const CONF_KO: Record<string, string> = { low: "낮음", mid: "보통", high: "높음" };
// 인사 AI 감사 기록의 종류 (domains/hr audit.record)
const AUDIT_KIND_KO: Record<string, string> = {
  "record_case.card_created": "결정 카드를 회사 기억에 저장",
  "record_case.candidate": "후보 기록으로 저장 (사람 확인 대기)",
  "record_case.duplicate": "같은 기록 요청이 다시 와서 건너뜀",
  record_only: "결정이 없어 기록만 남김",
  card_write_blocked: "안전 관문이 카드 저장을 막음",
  "card_decision.duplicate": "같은 승인·폐기 요청이 다시 와서 건너뜀",
  check_plan: "새 계획을 예전 결정과 비교",
  start_handover: "인수인계 시작",
  "start_handover.duplicate": "같은 인수인계 시작 요청을 건너뜀",
  answer_handover_question: "인수인계 질문에 답 기록",
  "answer.duplicate": "같은 답을 다시 받아 건너뜀",
  confirm_handover: "인수인계 확정",
  "confirm_handover.held": "인수인계 확정이 사람 확인 대기로 감",
  "confirm_handover.rejected": "인수인계 확정이 거절됨",
  "confirm_handover.gate_unavailable": "안전 관문 연결 문제로 확정하지 못함",
  "handover.question_reasked": "비어 있는 항목을 다시 물음",
  "timer.shift_handover": "교대 시간이 되어 인수인계를 시작",
  "timer.upstream_error": "교대 인수인계 중 다른 서비스 연결 문제",
};

const recordStep = (inc: OrchIncident): OrchStep | undefined => inc.steps.find((s) => s.skill === RECORD_SKILL);
const str = (v: unknown) => (typeof v === "string" ? v : null);
/** "결제가 서버 오류로 자꾸 실패해요 · 오후 4:38" */
const incidentName = (inc: OrchIncident) => `${incidentTitle(inc)}${inc.opened_at ? ` · ${clockKo(inc.opened_at)}` : ""}`;
const Chip = ({ label, tone, title }: { label: string; tone: string; title?: string }) => <span className={`chip ${tone}`} title={title}>{label}</span>;

/** 감사 기록 한 줄의 내용을 사람이 읽는 말로 (값은 기록된 그대로, 코드는 빼고) */
function auditDetail(e: AuditEntry): string {
  const p = e.payload;
  const parts: string[] = [];
  const conf = str(p.confidence);
  if (conf) parts.push(`확신 ${CONF_KO[conf] ?? conf}`);
  if (Array.isArray(p.superseded)) parts.push(p.superseded.length ? `예전 기록 ${p.superseded.length}건을 대체` : "대체한 예전 기록 없음");
  if (typeof p.pct === "number") parts.push(`완성도 ${p.pct}%`);
  return parts.join(" · ");
}

// ---------------------------------------------------------------- 사건 기록 목록 + 고른 사건의 타임라인
export default function IncidentRecords({ initialCtx = null }: { initialCtx?: string | null }) {
  const [incidents, setIncidents] = useState<OrchIncident[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(initialCtx); // 총괄 사건 화면에서 넘어오면 그 사건을 먼저 보여 준다

  const load = useCallback(async () => {
    try {
      const all = await hr.incidents();
      // 인사 AI에게 사건 기록을 맡긴 사건만, 최근 사건이 위로
      const mine = all.filter((i) => recordStep(i)).sort((a, b) => (b.opened_at ?? "").localeCompare(a.opened_at ?? ""));
      setIncidents(mine); setErr(null);
    } catch (e) { setErr(e instanceof Error ? e.message : "사건 목록을 받지 못했습니다"); }
  }, []);
  useEffect(() => {
    load();
    const poll = setInterval(load, 15000); // 15초마다 새로 받는다
    return () => clearInterval(poll);
  }, [load]);

  // 고른 사건이 없으면 가장 최근 사건
  const current = incidents?.find((i) => i.context_id === picked) ?? incidents?.[0] ?? null;

  return (
    <div className="space-y-6">
      <section className="card space-y-4 p-6">
        <div>
          <h2 className="text-lg font-semibold">AI가 남긴 사건 기록</h2>
          <p className="mt-1 text-xs text-ink-500">캣북스에 사건이 나면 총괄 AI가 인사 AI에게 기록을 맡깁니다. 줄을 누르면 아래에서 인사 AI가 한 일을 봅니다.</p>
        </div>
        {err && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{err}</p>}
        {!incidents ? (!err && <p className="py-6 text-center text-sm text-ink-500">불러오는 중…</p>)
          : incidents.length === 0 ? <p className="py-6 text-center text-sm text-ink-500">아직 인사 AI가 기록한 사건이 없습니다.</p>
          : (
            <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
              {incidents.map((inc) => <RecordRow key={inc.context_id} inc={inc} active={inc.context_id === current?.context_id} onPick={() => setPicked(inc.context_id)} />)}
            </ul>
          )}
      </section>

      <section className="card space-y-4 p-6">
        <div>
          <h2 className="text-lg font-semibold">AI가 한 일</h2>
          <p className="mt-1 text-xs text-ink-500">고른 사건에서 인사 AI가 한 일을 시간순으로 봅니다. 기록된 그대로이고 새로 계산하지 않습니다.</p>
        </div>
        {incidents && incidents.length > 0 && current && (
          <select className="field" value={current.context_id} onChange={(e) => setPicked(e.target.value)} aria-label="사건 고르기">
            {incidents.map((inc) => <option key={inc.context_id} value={inc.context_id}>{incidentName(inc)}{inc.opened_at ? ` (${dateStr(inc.opened_at)})` : ""}</option>)}
          </select>
        )}
        {current && <Link href={`/orchestrator?ctx=${encodeURIComponent(current.context_id)}`} className="inline-block text-xs font-semibold text-brand-600 underline underline-offset-2">총괄 사건에서 보기 →</Link>}
        {current ? <Timeline inc={current} /> : <p className="text-xs text-ink-500">볼 사건이 없습니다.</p>}
      </section>

      {current && <ResolutionForm inc={current} />}

      <RepeatCompare />
    </div>
  );
}

function RecordRow({ inc, active, onPick }: { inc: OrchIncident; active: boolean; onPick: () => void }) {
  const step = recordStep(inc)!;
  const st = STEP_KO[step.status] ?? { label: "상태 확인 필요", tone: "bg-ink-100 text-ink-500" };
  const outcome = str(step.output.outcome) ?? (step.status === "completed" ? step.note : null);
  const conf = str(step.output.confidence);
  const cardId = str(step.output.card_id);
  const when = step.finished_at ?? step.started_at;
  const code = [inc.context_id, outcome, cardId].filter(Boolean).join(" · ");
  return (
    <li>
      <button type="button" onClick={onPick} aria-pressed={active}
        className={`grid w-full gap-1 px-4 py-3 text-left text-sm sm:grid-cols-[1fr_auto] sm:items-center ${active ? "bg-brand-50" : "hover:bg-ink-50"}`}>
        <span className="min-w-0">
          <span className="block font-medium" title={`${inc.title} · ${inc.context_id}`}>{incidentName(inc)}</span>
          <span className="mt-0.5 block text-xs text-ink-500" title={code}>
            {outcome ? OUTCOME_KO[outcome] ?? "기록 결과를 받았어요" : step.status === "completed" ? "기록을 마쳤어요" : "아직 기록 결과가 없어요"}
            {conf && ` · 확신 ${CONF_KO[conf] ?? conf}`}
            {when && ` · ${dateStr(when)} ${clockKo(when)} 기록`}
          </span>
        </span>
        <span className="flex flex-wrap gap-1.5">
          <Chip label={st.label} tone={st.tone} title={step.status} />
          <Chip label={`사건 ${INCIDENT_STATUS_KO[inc.status] ?? "상태 확인 필요"}`} tone="bg-ink-100 text-ink-500" title={inc.status} />
        </span>
      </button>
    </li>
  );
}

function Timeline({ inc }: { inc: OrchIncident }) {
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

  const step = recordStep(inc);
  if (err) return <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{err}</p>;
  if (!rows) return <p className="text-xs text-ink-500">불러오는 중…</p>;
  return (
    <ol className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
      {/* 총괄 AI가 인사 AI에게 기록을 맡긴 시각 (총괄 사건 기록) */}
      {step?.started_at && (
        <li className="flex flex-wrap items-baseline gap-x-3 px-4 py-2 text-xs" title={`${step.skill} · ${inc.context_id}`}>
          <span className="tabular-nums text-ink-500">{clockKo(step.started_at)}</span>
          <span className="font-medium">총괄 AI가 사건 기록을 맡김</span>
          <span className="text-ink-500">{incidentTitle(inc)}</span>
        </li>
      )}
      {rows.map((r) => (
        <li key={r.seq} className="flex flex-wrap items-baseline gap-x-3 px-4 py-2 text-xs" title={`${r.kind} · ${JSON.stringify(r.payload)}`}>
          <span className="tabular-nums text-ink-500">{clockKo(r.at)}</span>
          <span className="font-medium">{AUDIT_KIND_KO[r.kind] ?? "인사 AI 처리 기록"}</span>
          <span className="text-ink-500">{auditDetail(r)}</span>
        </li>
      ))}
      {rows.length === 0 && <li className="px-4 py-2 text-xs text-ink-500">인사 AI 쪽에는 이 사건의 처리 내역이 아직 없습니다.</li>}
    </ol>
  );
}
