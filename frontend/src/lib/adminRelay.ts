import { ApiError } from "./apiError";
import { RELAY_API_URL, RELAY_TOKEN } from "./env";

// 총괄 사건·부서 사용량 읽기. 화면은 총괄을 직접 부르지 않고 shop-api 관리 읽기 중계(GET만)를 부른다.
// 응답 모양은 ax-backend `orchestrator/src/ax_orchestrator/models.py`(`Incident.view()`)와
// `main.py`(`departments()`) 기준이고, 중계 응답은 OpenAPI에 모양이 없어 손으로 적는다.
// 토큰은 환경변수(개발용)에서만 오고 브라우저 저장소에는 두지 않는다. 진짜 로그인은 사람 인증 뒤에 붙는다.

export type StepStatus = "pending" | "running" | "completed" | "failed" | "held" | "skipped" | "rejected_by_human" | "transfer_queued";

export interface Step {
  step_id: string;
  skill: string;
  label: string;
  dept: string | null;
  status: StepStatus;
  attempts: number;
  note: string;
  grade: string | null;
  started_at: string | null;
  finished_at: string | null;
  transferred_to: string | null;
  reason_code: string | null;
}
export interface PendingHold { step_id: string; skill: string; label: string }
export interface Incident {
  context_id: string;
  kind: string;
  title: string;
  component: string;
  status: "open" | "resolved" | "degraded";
  opened_at: string;
  closed_at: string | null;
  steps: Step[];
  events: { at: string; message: string }[];
  summary: string[];
  metrics: { mttr_seconds?: number; steps_total?: number; steps_completed?: number; held?: number; transfers?: number; retries?: number };
  /** 총괄이 보관한 값(예: 점검 공지 번호). 부서가 주지 않으면 없다 */
  shared?: Record<string, unknown>;
  pending_holds: PendingHold[];
}

// 부서 카드·사용량. 사용량은 총괄이 부른 호출을 센 실측이고, 값이 없으면 null(화면은 "확인할 수 없음")
export interface UsageSummary { calls: number; errors: number; waiting: number; success_rate: number | null; avg_ms: number | null; p95_ms: number | null; retries: number; last_called_at: string | null }
export interface UsageBucket { t: string; calls: number; errors: number; avg_ms: number | null }
export interface DeptSkill { id: string; grade: string; priority: string; orchestrator: boolean; calls: number; errors: number; success_rate: number | null; avg_ms: number | null; retries: number; series: UsageBucket[] }
export interface RecentCall { at: string; skill: string; status: string; outcome: string | null; duration_ms: number; context_id: string }
export interface Department {
  name: string;
  owner: string | null;
  mode: "real" | "mock";
  available: boolean;
  unavailable_reason: string | null;
  skills: DeptSkill[];
  usage: UsageSummary;
  series: UsageBucket[];
  recent: RecentCall[];
}
export interface DepartmentsMeta { mock?: boolean; window_seconds?: number; bucket_seconds?: number }

/** 토큰이 없으면(운영 빌드 기본) 총괄 화면과 부서 사용량이 꺼진다 */
export const relayEnabled = Boolean(RELAY_TOKEN && RELAY_API_URL);

async function get<T, M = Record<string, unknown>>(path: string): Promise<{ data: T; meta: M }> {
  if (!relayEnabled) throw new ApiError("총괄 읽기 연결이 꺼져 있습니다", 403);
  const res = await fetch(`${RELAY_API_URL}${path}`, { headers: { Authorization: `Bearer ${RELAY_TOKEN}` }, cache: "no-store" });
  let body: { data?: T; meta?: M; error?: { message?: string } } = {};
  try { body = await res.json(); } catch { /* 본문이 JSON이 아님 */ }
  if (!res.ok || body.data === undefined) throw new ApiError(body.error?.message ?? `요청 실패 (${res.status})`, res.status);
  return { data: body.data, meta: (body.meta ?? {}) as M };
}

export const adminRelay = {
  incidents: () => get<Incident[]>("/api/shop/admin/orchestrator/incidents"),
  departments: () => get<Department[], DepartmentsMeta>("/api/shop/admin/orchestrator/departments"),
};

/** 연결 오류를 화면 문구로 */
export function relayError(error: { status?: number; message: string }): string {
  if (error.status === 502) return "총괄에 연결할 수 없습니다. 총괄 서버가 켜져 있는지 확인해 주세요.";
  if (error.status === 504) return "총괄이 제때 응답하지 않았습니다.";
  if (error.status === 404) return "부서 사용량 경로가 서버에 없습니다. shop-api를 최신으로 올려 주세요.";
  if (error.status === 401 || error.status === 403) return "총괄 읽기 권한이 없습니다.";
  return error.message;
}

// 부서 이름(영문 id) → 한글. 부서 id는 총괄 Agent Card 레지스트리의 이름이다.
const DEPT_KO: Record<string, string> = { infra: "인프라", data: "데이터분석", planning: "기획개발", marketing: "마케팅", support: "고객지원", finance: "재무·정산", hr: "인사운영" };
export const deptKo = (name: string | null) => (name ? DEPT_KO[name] ?? name : "미정");

export const STEP_STATUS: Record<StepStatus, { label: string; tone: string }> = {
  pending: { label: "대기", tone: "bg-ink-100 text-ink-700" },
  running: { label: "진행 중", tone: "bg-brand-50 text-brand-700" },
  completed: { label: "완료", tone: "bg-emerald-50 text-emerald-700" },
  failed: { label: "실패", tone: "bg-red-50 text-red-700" },
  held: { label: "사람 승인 대기", tone: "bg-amber-50 text-amber-700" },
  skipped: { label: "건너뜀", tone: "bg-ink-100 text-ink-500" },
  rejected_by_human: { label: "보류 후 거절", tone: "bg-red-50 text-red-700" },
  transfer_queued: { label: "이관 큐로 넘김", tone: "bg-brand-50 text-brand-700" },
};
export const INCIDENT_STATUS: Record<Incident["status"], { label: string; tone: string }> = {
  open: { label: "진행 중", tone: "bg-brand-50 text-brand-700" },
  resolved: { label: "해결", tone: "bg-emerald-50 text-emerald-700" },
  degraded: { label: "일부 미완료", tone: "bg-amber-50 text-amber-700" },
};
export const SHARED_KO: Record<string, string> = { notice_id: "점검 공지 번호", recovered_notice_id: "정상화 공지 번호" };
export const GRADE: Record<string, { label: string; tone: string }> = {
  read: { label: "조회", tone: "bg-ink-100 text-ink-700" },
  A: { label: "A 바로", tone: "bg-emerald-50 text-emerald-700" },
  B: { label: "B 확인 후", tone: "bg-brand-50 text-brand-700" },
  C: { label: "C 사람 승인", tone: "bg-amber-50 text-amber-700" },
};

export const UNKNOWN = "확인할 수 없음";
export const rate = (v: number | null) => (v == null ? UNKNOWN : `${Math.round(v * 100)}%`);
export const millis = (v: number | null) => (v == null ? UNKNOWN : `${v.toLocaleString("ko-KR")}ms`);
export const clock = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour12: false }) : UNKNOWN);
