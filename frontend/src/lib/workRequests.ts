// 총괄이 접수한 고객 의뢰 (GET /api/requests). 형식은 orchestrator/src/ax_orchestrator/work_requests.py 기준.
// 고객 글이 들어 있어 사람 토큰으로만 읽는다. 부서가 돌려준 문장은 의뢰 기록에 없고, 상태·번호·코드만 있다.

export type RequestStatus = "received" | "classified" | "running" | "waiting" | "done" | "failed" | "rejected" | "needs_human";

export type StepStatus =
  | "pending" | "running" | "completed" | "failed" | "skipped" | "rejected_by_human"
  | "held" | "waiting_human" | "waiting_async";

export interface WorkRequestPlanStep {
  step_id: string;
  dept: string | null;
  skill: string;
  label: string;
  after: string[];
  kind?: string;
}

export interface WorkRequestStep {
  status: StepStatus;
  attempts: number;
  grade: string | null;
  note: string;
  reason_code: string | null;
  result_ref: string | null;
  review_id: string | null;
  handoff_task_id: string | null;
  async_task_id: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface WorkRequestRow {
  request_id: string;
  account: string;
  text: string;
  created_at: string;
  updated_at: string;
  status: RequestStatus;
  task: string | null;
  confidence: number | null;
  reason: string | null;
  alternatives: string[];
  classification: { prompt_version?: string; kind?: string; outcome?: string; confidence?: number | null } | null;
  plan: WorkRequestPlanStep[];
  steps: Record<string, WorkRequestStep>;
  log: { at: string; message: string }[];
  thread_id?: string | null;
  handoff_task_id?: string | null;
  corrections?: { at: string; by: string | null; from_task: string | null; to_task: string }[];
}

export interface WorkRequestTask { name: string; label: string }

type Tone = { label: string; tone: string };

export const REQUEST_STATUS: Record<RequestStatus, Tone> = {
  received: { label: "되묻는 중", tone: "bg-ink-100 text-ink-700" },
  classified: { label: "접수", tone: "bg-sky-50 text-sky-800" },
  running: { label: "진행 중", tone: "bg-sky-50 text-sky-800" },
  waiting: { label: "확인 대기", tone: "bg-amber-50 text-amber-800" },
  done: { label: "끝남", tone: "bg-emerald-50 text-emerald-800" },
  failed: { label: "진행 안 함", tone: "bg-ink-100 text-ink-700" },
  rejected: { label: "거절", tone: "bg-red-50 text-red-800" },
  needs_human: { label: "사람이 확인", tone: "bg-amber-50 text-amber-800" },
};

export const STEP_STATUS: Record<StepStatus, Tone> = {
  pending: { label: "대기", tone: "bg-ink-100 text-ink-700" },
  running: { label: "진행 중", tone: "bg-sky-50 text-sky-800" },
  completed: { label: "완료", tone: "bg-emerald-50 text-emerald-800" },
  failed: { label: "실패", tone: "bg-red-50 text-red-800" },
  skipped: { label: "건너뜀", tone: "bg-ink-100 text-ink-700" },
  rejected_by_human: { label: "사람이 거절", tone: "bg-red-50 text-red-800" },
  held: { label: "승인 대기", tone: "bg-amber-50 text-amber-800" },
  waiting_human: { label: "사람 확인 대기", tone: "bg-amber-50 text-amber-800" },
  waiting_async: { label: "긴 작업 중", tone: "bg-sky-50 text-sky-800" },
};

/** 사람이 승인·거절할 수 있는 단계 (사람 확인 단계, 상태를 바꾸는 단계의 승인 대기). 부서 대기·재시작 확인은 아니다 */
export const canDecide = (s: WorkRequestStep) =>
  s.status === "waiting_human" && (s.reason_code === "human_check" || s.reason_code === "needs_approval");

/** 다시 돌릴 수 있나: 부서가 일하는 중이거나 기다리는 단계가 있으면 안 된다 */
export const canRerun = (r: WorkRequestRow) =>
  !["running", "held", "waiting_async"].some((s) => Object.values(r.steps).some((x) => x.status === s));

/** 확신도를 "85%"로. 사람이 골랐거나 값이 없으면 null */
export const confidenceText = (c: number | null | undefined) => (typeof c === "number" ? `${Math.round(c * 100)}%` : null);
