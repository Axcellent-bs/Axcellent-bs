import { failureError } from "./apiError";
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
  /** shop-api 규격(`shop_api`) 또는 다른 형식 API를 사이트 변환기로 잇기(`adapter`). 옛 파일은 없을 수 있다 */
  kind?: "shop_api" | "adapter";
  adapter?: { base_url: string } | null;
  /** 회사 설정 폴더 id (config/tenants/<id>). 연결할 때 고른 회사 */
  tenant?: string | null;
}

/** 고를 수 있는 회사 (서버 config/tenants/<id>/tenant.toml) */
export interface TenantOption {
  id: string;
  name: string;
}

/** 바꾼 기록 한 줄 (새것부터) */
export interface ConnectionHistory {
  at: string;
  by: string;
  action: "save" | "mode" | "recheck" | "clear";
  from: string | null;
  from_enabled: boolean | null;
  to: string | null;
  to_enabled: boolean | null;
}

export interface ConnectionState {
  shop_api_url: string;
  source: ConnectionSource;
  mode: ConnectionMode;
  /** 내부 데이터 모드에서 읽는 주소 */
  internal_url: string;
  connection: StoredConnection | null;
  editable: boolean;
  history: ConnectionHistory[];
  /** 회사 사이트에 이어져 있는지. false면 "연결 안 됨"(캣북스 기본값 없음) */
  connected?: boolean;
  /** 지금 회사. 연결 전이면 null */
  tenant?: TenantOption | null;
  tenants?: TenantOption[];
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

export async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${DASHBOARD_API_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...writeAuth(method, path) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  // 앞단 nginx가 막은 403·405(HTML)는 "서버 허용 경로 추가" 안내로, 앱 거절은 서버 문장 그대로
  if (!res.ok) throw failureError(parsed, res.status);
  return (parsed as { data: T }).data;
}

export const companyConnection = {
  get: () => call<ConnectionState>("GET", PATH),
  test: (url: string) => call<ConnectionTest>("POST", `${PATH}/test`, { url }),
  save: (url: string, tenant?: string) =>
    call<ConnectionState & { checks: ConnectionCheck[] }>("POST", `${PATH}/save`, { url, tenant: tenant || undefined }),
  mode: (mode: ConnectionMode) => call<ConnectionState>("POST", `${PATH}/mode`, { mode }),
  // 저장·해제는 POST로 부른다(운영 서버 nginx 게이트가 POST만 열 수 있다. 서버는 PUT·DELETE도 그대로 받는다)
  clear: () => call<ConnectionState>("POST", `${PATH}/clear`),
};
