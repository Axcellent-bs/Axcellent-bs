// 사람 할 일함: 켜지 않은 서비스로 가던 일을 에이전트가 dashboard-api(8020)에 넣고, 사람이 여기서 끝낸다.
// 계약은 backend/shared/contracts/src/ax_contracts/schemas/handoff.py (HandoffTask, HandoffCompleteBody).
// 녹화본이 없다. 데모 모드에서도 이 주소는 가로채지 않고 실제 dashboard-api를 부른다 (src/mock/install.ts).
import { useEffect, useSyncExternalStore } from "react";
import { ApiError } from "./apiError";
import { DASHBOARD_API_URL, SAFETY_HUMAN_TOKEN } from "./env";

export type HandoffStatus = "open" | "done" | "rejected";

export type HandoffTask = {
  task_id: string;
  tenant_id: string;
  context_id: string;
  from_service: string;
  to_service: string;
  skill: string;
  input: Record<string, unknown>;
  /** 외부에서 들어온 내용. 화면은 글자 그대로만 보여 준다 */
  untrusted: Record<string, unknown>;
  reason: string;
  /** 맡은 부서. 조직도(backend/config/org.yaml) 값 그대로다 */
  assignee_unit: string;
  assignee: string | null;
  status: HandoffStatus;
  result: string | null;
  created_at: string;
  closed_at: string | null;
};

export const HANDOFF_STATUS_KO: Record<HandoffStatus, string> = { open: "열림", done: "끝남", rejected: "반려" };
export const HANDOFF_STATUS_TONE: Record<HandoffStatus, string> = {
  open: "bg-amber-50 text-amber-700",
  done: "bg-emerald-50 text-emerald-700",
  rejected: "bg-ink-100 text-ink-500",
};

/** 결과 입력 최대 길이 (HandoffCompleteBody.result max_length) */
export const RESULT_MAX = 2000;

export const UNREACHABLE = "dashboard-api(8020)에 연결할 수 없어요";
const BASE = `${DASHBOARD_API_URL}/api/dashboard/handoffs`;

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(UNREACHABLE, 0);
  }
  const body = (await res.json().catch(() => undefined)) as { data?: T; error?: { code?: string; message?: string } } | undefined;
  if (!res.ok) {
    const msg =
      res.status === 401 || res.status === 403
        ? "사람 권한이 없어 처리하지 못했어요. VITE_SAFETY_HUMAN_TOKEN을 확인하세요."
        : body?.error?.message ?? `요청 실패 (${res.status})`;
    throw new ApiError(msg, res.status);
  }
  return body?.data as T;
}

/** 할 일 목록. 최근 일이 먼저다. unit은 맡은 부서, status는 open·done·rejected */
export function listHandoffs(q: { unit?: string; status?: HandoffStatus } = {}) {
  const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => !!v) as [string, string][]).toString();
  return call<HandoffTask[]>(`${BASE}${qs ? `?${qs}` : ""}`);
}

/** 결과를 적고 끝내거나 반려한다. 사람 토큰이 필요하다 */
export function completeHandoff(taskId: string, result: string, status: "done" | "rejected") {
  return call<HandoffTask>(`${BASE}/${encodeURIComponent(taskId)}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${SAFETY_HUMAN_TOKEN}` },
    body: JSON.stringify({ result, status }),
  });
}

// ---------------------------------------------------------------- 메뉴에 띄우는 열린 일 개수
// 메뉴(AdminShell)와 할 일함 화면이 같은 값을 본다. 서버에 연결하지 못하면 null(개수를 띄우지 않음).
const POLL_MS = 30_000;
let openCount: number | null = null;
const listeners = new Set<() => void>();

export function setOpenCount(n: number | null) {
  if (n === openCount) return;
  openCount = n;
  listeners.forEach((l) => l());
}

export async function refreshOpenCount() {
  try {
    setOpenCount((await listHandoffs({ status: "open" })).length);
  } catch {
    setOpenCount(null);
  }
}

/** 열린 일 개수. poll이 true면 30초마다 다시 센다 */
export function useOpenHandoffCount(poll: boolean) {
  useEffect(() => {
    if (!poll) return;
    refreshOpenCount();
    const t = setInterval(refreshOpenCount, POLL_MS);
    return () => clearInterval(t);
  }, [poll]);
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => openCount,
  );
}
