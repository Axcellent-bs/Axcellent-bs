// 총괄이 접수한 고객 의뢰 (GET /api/requests). 형식은 orchestrator/src/ax_orchestrator/work_requests.py 기준.
// 고객 글이 들어 있어 사람 토큰으로만 읽는다. 부서가 돌려준 문장은 의뢰 기록에 없고, 상태·번호·코드만 있다.

export type RequestStatus = "received" | "classified" | "running" | "waiting" | "done" | "failed" | "rejected" | "needs_human";

/** 열린 의뢰 (아직 부서가 일하거나 사람을 기다림). 끝난 의뢰는 done·failed·rejected·needs_human.
 *  AI 대화방 조회 줄 거르기(lib/commFilter.ts requestClosedTimes)와 AI 오피스 자리 불(lib/officeRequests.ts)이 같이 쓴다 */
export const OPEN_REQUEST_STATES: ReadonlySet<RequestStatus> = new Set(["received", "classified", "running", "waiting"]);
export const isOpenRequest = (r: { status: RequestStatus }) => OPEN_REQUEST_STATES.has(r.status);
/** 의뢰의 공통 로그 context (서버가 따로 주지 않으면 "ctx_" + request_id = ctx_wr_…) */
export const requestContext = (r: { request_id: string; context_id?: string | null }) => r.context_id || `ctx_${r.request_id}`;

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
  /** 공통 로그 context (서버 기록에 있을 때만. 없으면 ctx_<request_id>) */
  context_id?: string | null;
  handoff_task_id?: string | null;
  corrections?: { at: string; by: string | null; from_task: string | null; to_task: string }[];
  /** 11/23 사람이 삭제(멈추고 숨김)한 때·사람. 기본 목록에는 오지 않는다 (?hidden=1로만) */
  hidden_at?: string | null;
  hidden_by?: string | null;
  /** 11/23 삭제로 멈춘 때. 되돌려 숨김을 풀어도 남고, 이 의뢰는 다시 돌릴 수 없다 (의뢰문 수정으로 새로 접수) */
  stopped_at?: string | null;
  /** 의뢰문을 고쳐 다시 접수한 두 의뢰의 연결 */
  replaces?: string | null;
  replaced_by?: string | null;
}

// ---- 의뢰 삭제·의뢰문 수정 (11/23, 화면 시안 문구 그대로)

export const EDIT_NOTE = "고친 글로 새 의뢰를 접수하고, 지금 의뢰는 멈추고 목록에서 숨겨요. 이미 만든 보고서·기획서는 새 의뢰가 다시 써요(같은 글이 아니면 새로 만듦).";
export const EDIT_MAX = 2000;
const DONE_STEP: ReadonlySet<StepStatus> = new Set(["completed", "failed", "skipped", "rejected_by_human"]);

/** 삭제하면 멈추는 남은 단계 이름 (계획 순서). 끝난 의뢰면 빈 목록 */
export function remainingSteps(r: Pick<WorkRequestRow, "plan" | "steps" | "status">): string[] {
  if (!isOpenRequest(r) && r.status !== "needs_human") return [];
  return r.plan.filter((p) => !DONE_STEP.has(r.steps[p.step_id]?.status ?? "pending")).map((p) => p.label);
}

/** 삭제 확인 상자: 제목 · 본문 (남은 단계 이름을 넣는다) */
export function deleteConfirm(title: string, r: Pick<WorkRequestRow, "plan" | "steps" | "status">): { head: string; body: string } {
  const left = remainingSteps(r);
  const stop = left.length ? `남은 단계(${left.join("·")})를 멈추고 목록에서 숨겨요.` : "목록에서 숨겨요.";
  return { head: `"${title}" 의뢰를 삭제할까요?`, body: `${stop} 이미 만든 보고서·기획서·영상은 각 부서에 남아요.` };
}

/** 고친 글을 보낼 수 있나: 비었거나 길거나 원문과 같으면 이유 (서버도 같은 규칙: 422 "바뀐 내용이 없어요") */
export function editProblem(text: string, original: string): string | null {
  const norm = (s: string) => s.split(/\s+/).filter(Boolean).join(" ");
  if (!norm(text)) return "의뢰문을 적어 주세요";
  if (text.length > EDIT_MAX) return `의뢰문은 ${EDIT_MAX.toLocaleString("ko-KR")}자까지예요`;
  if (norm(text) === norm(original)) return "바뀐 내용이 없어요";
  return null;
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

/** 다시 돌릴 수 있나: 부서가 일하는 중이거나 기다리는 단계가 있으면 안 된다.
 *  삭제로 멈춘 의뢰(stopped_at, 숨긴 목록에서 되돌린 의뢰 포함)도 안 된다 (서버 409, 의뢰문 수정으로 새로 접수) */
export const canRerun = (r: Pick<WorkRequestRow, "steps" | "stopped_at">) =>
  !r.stopped_at && !["running", "held", "waiting_async"].some((s) => Object.values(r.steps).some((x) => x.status === s));

/** 확신도를 "85%"로. 사람이 골랐거나 값이 없으면 null */
export const confidenceText = (c: number | null | undefined) => (typeof c === "number" ? `${Math.round(c * 100)}%` : null);
