import { ApiError } from "./apiError";
import { writeAuth } from "./api";
import { DASHBOARD_API_URL } from "./env";

// 시스템 관리 > 회사 연결: 고객사 판매 서버(shop-api 규격) 주소. dashboard-api `company_connection.py`.
// 저장하면 모든 부서 AI가 재시작 없이 이 주소를 읽는다. 모드 전환으로 내부 데이터와 오갈 수 있다(주소는 남김). 쓰기는 사람 토큰이 필요하다(writeAuth).

export type ConnectionSource = "connection" | "env" | "default";
/** 내부 데이터(원래 판매 서버) ↔ 실제 연결(저장한 고객사 주소) */
export type ConnectionMode = "internal" | "connected";

export interface StoredConnection {
  shop_api_url: string;
  /** false면 주소는 남겨 두고 쓰지 않는다 (내부 데이터 모드) */
  enabled: boolean;
  verified_at: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

export interface ConnectionState {
  shop_api_url: string;
  source: ConnectionSource;
  mode: ConnectionMode;
  /** 내부 데이터 모드에서 읽는 주소 */
  internal_url: string;
  connection: StoredConnection | null;
  editable: boolean;
}

export interface ConnectionCheck {
  key: "health" | "products" | "users";
  label: string;
  ok: boolean;
  detail: string;
}

export interface ConnectionTest {
  url: string;
  ok: boolean;
  checks: ConnectionCheck[];
  checked_at: string;
}

const PATH = "/api/dashboard/company-connection";

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${DASHBOARD_API_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...writeAuth(method, path) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) {
    const message = (parsed as { error?: { message?: string } } | undefined)?.error?.message;
    throw new ApiError(message ?? `요청 실패 (${res.status})`, res.status);
  }
  return (parsed as { data: T }).data;
}

export const companyConnection = {
  get: () => call<ConnectionState>("GET", PATH),
  test: (url: string) => call<ConnectionTest>("POST", `${PATH}/test`, { url }),
  save: (url: string) => call<ConnectionState & { checks: ConnectionCheck[] }>("PUT", PATH, { url }),
  mode: (mode: ConnectionMode) => call<ConnectionState>("POST", `${PATH}/mode`, { mode }),
  clear: () => call<ConnectionState>("DELETE", PATH),
};
