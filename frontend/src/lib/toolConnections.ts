import { ApiError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";

// 시스템 관리 > 도구 연결: GitHub 앱·배포 도구(GitHub Actions·Jenkins)·DejaFix 연결. dashboard-api `tool_connections.py`.
// 연결은 공용 보관함 한 곳에 두고 인프라 AI·개발 AI·DejaFix 프록시가 재시작 없이 읽는다. 읽기·쓰기 모두 사람 토큰이 필요하다(dashboardAuth).
// 비밀 값은 보내기만 한다. 응답에는 가린 값(`secrets_set`: •••• · 개인 키는 설정됨)만 온다.
// 화면 없는 순수 함수(폼 상태·목록 모양)는 toolForms.ts (node --test로 시험).

export type ToolKind = "github_app" | "github_actions" | "jenkins" | "dejafix";
export const TOOL_KINDS: readonly ToolKind[] = ["github_app", "github_actions", "jenkins", "dejafix"];
export const DEPLOY_KINDS: readonly ToolKind[] = ["github_actions", "jenkins"];

/** 종류별 입력 칸 정의 (서버 `FIELDS`). 화면은 이대로 칸을 그린다 */
export interface ToolField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
}

/** 저장된 연결 (`public()`, 비밀 값 없음) */
export interface ToolConnection {
  id: string;
  kind: ToolKind;
  name: string;
  config: Record<string, string>;
  /** 저장된 비밀 칸 이름 → 가린 값 */
  secrets_set: Record<string, string>;
  checked_at: string | null;
  check_ok: boolean | null;
  check_detail: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

export interface ToolList {
  connections: ToolConnection[];
  fields: Partial<Record<ToolKind, ToolField[]>>;
}

/** 연결 확인 항목 하나 */
export interface ToolCheck {
  key: string;
  label: string;
  ok: boolean;
  detail: string;
}

export interface ToolTestResult {
  ok: boolean;
  checks: ToolCheck[];
}

export interface ToolBody {
  kind: ToolKind;
  name: string;
  config: Record<string, string>;
  /** 비운 칸은 보내지 않는다 (저장된 값을 그대로 둔다) */
  secrets: Record<string, string>;
  id?: string;
}

/** 저장이 연결 확인에서 막힘(422 CHECK_FAILED). 항목별 결과를 같이 담는다 */
export class ToolCheckError extends ApiError {
  checks: ToolCheck[];
  constructor(message: string, status: number, checks: ToolCheck[]) {
    super(message, status, undefined, "CHECK_FAILED");
    this.checks = checks;
  }
}

const PATH = "/api/dashboard/tool-connections";

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${DASHBOARD_API_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) {
    const error = (parsed as { error?: { code?: string; message?: string; checks?: ToolCheck[] } } | undefined)?.error;
    const message = error?.message ?? `요청 실패 (${res.status})`;
    if (error?.code === "CHECK_FAILED" && Array.isArray(error.checks)) throw new ToolCheckError(message, res.status, error.checks);
    throw new ApiError(message, res.status, undefined, error?.code ?? null);
  }
  return (parsed as { data: T }).data;
}

/** 서비스별 지금 돌고 있는 배포 (인프라가 배포 도구에서 받아 둔 성공 배포만, 시연 이력은 오지 않는다. dashboard-api `/deployments/latest`) */
export interface LatestDeploy {
  service: string;
  version: string;
  deployed_at: string;
  /** 배포 도구 이름 (`github_actions`·`jenkins`) */
  source: string;
  /** 인프라가 롤백으로 다시 배포한 실행 */
  is_rollback: boolean;
  /** 버전이 아니라 커밋 앞자리 (GitHub 실행 제목에 버전이 없음) */
  version_from_sha: boolean;
}

const one = (id: string) => `${PATH}/${encodeURIComponent(id)}`;

export const listTools = () => call<ToolList>("GET", PATH);
/** 저장하지 않고 연결 확인. `id`가 있으면 비운 비밀 칸은 저장된 값으로 확인한다 */
export const testTool = (body: ToolBody) => call<ToolTestResult>("POST", `${PATH}/test`, body);
/** 다시 확인하고 모두 통과하면 저장. `id`가 있으면 그 연결을 바꾼다 */
export const saveTool = (body: ToolBody, id?: string) =>
  id
    ? call<{ connection: ToolConnection; checks: ToolCheck[] }>("PUT", one(id), body)
    : call<{ connection: ToolConnection; checks: ToolCheck[] }>("POST", PATH, body);
export const checkTool = (id: string) =>
  call<{ connection: ToolConnection; ok: boolean; checks: ToolCheck[] }>("POST", `${one(id)}/check`);
/** 서비스별 최근 배포. 기록이 없는 서비스는 목록에 없다 */
export const listLatestDeploys = () => call<LatestDeploy[]>("GET", "/api/dashboard/deployments/latest");
export const deleteTool = (id: string) => call<{ deleted: string }>("DELETE", one(id));
