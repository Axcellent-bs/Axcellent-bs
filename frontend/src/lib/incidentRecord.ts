import { STEP_STATUS, type Incident, type Step } from "./adminRelay";

// 총괄 사건에서 인사 AI가 사건을 기록하는 단계(`record_case`)의 상태. 총괄이 준 값을 그대로 옮기고 새로 계산하지 않는다.
// 총괄 화면(사건 상세)과 인사 화면(결정 기록)이 같은 문구와 색을 쓰도록 한곳에 둔다.

export const RECORD_SKILL = "record_case";

export interface RecordState {
  /** 사건에서 인사가 맡은 단계. 총괄 계획에 없으면 null */
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

/** 인사 감사 기록의 종류 → 한글. 모르는 종류는 원래 이름을 그대로 보여 준다 */
const AUDIT_KIND_KO: Record<string, string> = {
  "record_case.card_created": "결정 카드 만듦",
  "record_case.duplicate": "같은 기록 요청이라 한 번만 처리",
  record_only: "결정 없이 기록만 남김",
  card_write_blocked: "카드 저장이 막힘",
  "card.approve": "후보 카드 확정",
};
export const auditKindKo = (kind: string) => AUDIT_KIND_KO[kind] ?? kind;
