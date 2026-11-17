// 총괄 AI의 에이전트 평가 진단 (GET /api/agent-diagnoses): 인사 AI가 같은 단계의 확인 필요가 반복된 것을 찾아 보내면
// 총괄 AI가 근거를 모아 진단하고 해당 에이전트에 개선 노트로 전달한다. 형식은 총괄 서버(agent_diagnoses.py) 기준이다.
// 이 진단은 평가가 아니라 원인 설명이고 틀릴 수 있다. 화면은 값을 다시 계산하지 않고 보여 주기만 한다.

import type { EvaluatedEvent } from "./agentEval";

export type DiagnosisStatus = "received" | "diagnosing" | "diagnosed" | "insufficient";
export type DeliveryStatus = "accepted" | "duplicate" | "rejected" | "failed" | "revoked" | "skipped";

export interface DiagnosisEvidence {
  context_id: string;
  title: string;
  verdict: string;
  work_seconds: number | null;
  attempts: number;
  limit_seconds: number;
  reason: string | null;
  at: string | null;
}
export interface DiagnosisItem { id: string; kind: "evaluation" | "orchestrator" | "card"; context_id: string; text: string }
export interface DiagnosisFinding { kind: string; text: string; causal: boolean; evidence_ids: string[]; contexts: string[] }
export interface DiagnosisResult {
  status: "diagnosed" | "insufficient";
  method: "rules" | "llm";
  summary: string;
  findings: DiagnosisFinding[];
  items: DiagnosisItem[];
  notes: string[];
}
export interface DiagnosisDelivery {
  note_id: string;
  status: DeliveryStatus;
  reason: string | null;
  attempts: number;
  delivered_at: string | null;
  revoked_at?: string;
}
export interface AgentDiagnosisRow {
  request_id: string;
  agent: string;
  step_id: string;
  step_label: string;
  window: number;
  flagged: number;
  criteria_version: number;
  evidence: DiagnosisEvidence[];
  status: DiagnosisStatus;
  received_at: string;
  updated_at: string;
  diagnosed_at?: string;
  diagnosis?: DiagnosisResult;
  delivery?: DiagnosisDelivery;
}

export const STATUS_LABEL: Record<DiagnosisStatus, { label: string; tone: string }> = {
  received: { label: "접수", tone: "bg-ink-100 text-ink-700" },
  diagnosing: { label: "진단 중", tone: "bg-sky-50 text-sky-800" },
  diagnosed: { label: "원인 확정", tone: "bg-emerald-50 text-emerald-800" },
  insufficient: { label: "원인 미확정", tone: "bg-amber-50 text-amber-800" },
};

export const DELIVERY_LABEL: Record<DeliveryStatus, { label: string; tone: string }> = {
  accepted: { label: "에이전트가 받았어요", tone: "bg-emerald-50 text-emerald-800" },
  duplicate: { label: "이미 받은 노트예요", tone: "bg-emerald-50 text-emerald-800" },
  rejected: { label: "에이전트가 거절했어요", tone: "bg-red-50 text-red-800" },
  failed: { label: "아직 못 보냈어요", tone: "bg-red-50 text-red-800" },
  revoked: { label: "철회했어요", tone: "bg-ink-100 text-ink-700" },
  skipped: { label: "일주일에 한 번만 보내서 쉬어요", tone: "bg-ink-100 text-ink-700" },
};

/** 진단 한 줄의 전달 상태 문구. 아직 전달하지 않았으면(접수·진단 중이거나 전달 장치가 꺼짐) null */
export const deliveryOf = (row: AgentDiagnosisRow) => (row.delivery ? DELIVERY_LABEL[row.delivery.status] : null);

/** 사람이 철회할 수 있는 건: 에이전트가 받은 노트(받았거나 이미 받은 것)만 */
export const canRevoke = (row: AgentDiagnosisRow) =>
  row.delivery?.status === "accepted" || row.delivery?.status === "duplicate";

/** 기본으로는 숨기는 건: 철회했거나 쉬느라 보내지 않은 것 */
export const isQuiet = (row: AgentDiagnosisRow) => row.delivery?.status === "revoked" || row.delivery?.status === "skipped";

export interface BeforeAfter {
  /** 전달 시각. 이 시각 전/후의 같은 단계 판정을 견준다 */
  at: string;
  before: { events: number; late: number };
  after: { events: number; late: number };
}

const LATE = new Set(["check", "silent"]);

/**
 * 진단을 에이전트에 전달한 시각 전후로, 같은 에이전트·같은 단계의 판정을 센다.
 * 판정은 저장된 값을 그대로 쓰고 다시 계산하지 않는다. 기준값 버전이 다른 이슈는 견주지 않는다(섞이면 안 된다).
 * 전달하지 않았거나 시각이 없으면 null. `late`는 확인 필요·응답 끊김.
 */
export function beforeAfter(row: AgentDiagnosisRow, events: EvaluatedEvent[]): BeforeAfter | null {
  const at = row.delivery?.delivered_at;
  if (!at || (row.delivery?.status !== "accepted" && row.delivery?.status !== "duplicate")) return null;
  const cut = Date.parse(at);
  if (Number.isNaN(cut)) return null;
  const out: BeforeAfter = { at, before: { events: 0, late: 0 }, after: { events: 0, late: 0 } };
  for (const e of events) {
    if (!e.at || e.criteria_version !== row.criteria_version) continue;
    const step = e.steps.find((s) => s.dept === row.agent && s.step_id === row.step_id);
    // 판정을 할 수 있었던 기록만 센다(사람에게 넘김·기록 없음·진행 중은 제외)
    if (!step || !["normal", "check", "silent", "blocked", "approval_expired"].includes(step.verdict)) continue;
    const side = Date.parse(e.at) >= cut ? out.after : out.before;
    side.events += 1;
    if (LATE.has(step.verdict)) side.late += 1;
  }
  return out;
}

/** 에이전트별 진단 건수(철회·쉬는 것 제외 안 함: 진단 자체의 건수) */
export function countByAgent(rows: AgentDiagnosisRow[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) out[r.agent] = (out[r.agent] ?? 0) + 1;
  return out;
}
