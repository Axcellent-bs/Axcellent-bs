import { STEP_STATUS, type Incident, type Step } from "./adminRelay";

// 총괄 이벤트에서 인사 AI가 이벤트를 기록하는 단계(`record_case`)의 상태. 총괄이 준 값을 그대로 옮기고 새로 계산하지 않는다.
// 총괄 화면(이벤트 상세)과 인사 화면(결정 기록)이 같은 문구와 색을 쓰도록 한곳에 둔다.

export const RECORD_SKILL = "record_case";

export interface RecordState {
  /** 이벤트에서 인사가 맡은 단계. 총괄 계획에 없으면 null */
  step: Step | null;
  label: string;
  tone: string;
  /** 인사가 기록을 마쳤다 (총괄 단계가 완료) */
  done: boolean;
}

export function recordState(incident: Incident): RecordState {
  const step = incident.steps.find((s) => s.skill === RECORD_SKILL) ?? null;
  if (!step) return { step: null, label: "인사 기록 단계 없음", tone: "bg-ink-100 text-ink-500", done: false };
  const st = STEP_STATUS[step.status] ?? { label: step.status, tone: "bg-ink-100 text-ink-700" };
  return { step, label: `인사 기록 ${st.label}`, tone: st.tone, done: step.status === "completed" };
}
