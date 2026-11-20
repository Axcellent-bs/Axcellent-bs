// 키 관리 호출(dashboard-api). 값은 요청 본문으로만 나가고 어디에도 남기지 않는다. 오류는 상태·오류 코드만 담는다.
import { ApiError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";

const BASE = "/api/dashboard/secrets";

async function parse(res: Response): Promise<unknown> {
  const json = await res.json().catch(() => null);
  if (!res.ok || !json?.data) {
    const code = (json as { error?: { code?: unknown } } | null)?.error?.code;
    throw new ApiError(`요청 실패 (${res.status})`, res.status, undefined, typeof code === "string" ? code : null);
  }
  return json.data;
}

const post = async (path: string, body?: unknown): Promise<unknown> =>
  parse(await fetch(`${DASHBOARD_API_URL}${BASE}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json", ...dashboardAuth() }, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store",
  }));

const key = (tenant: string, name: string) => `/${encodeURIComponent(tenant)}/${encodeURIComponent(name)}`;

export async function getSecrets(): Promise<unknown> {
  return parse(await fetch(`${DASHBOARD_API_URL}${BASE}`, { cache: "no-store", headers: dashboardAuth() }));
}
export const addSecret = (tenant: string, name: string, value: string) => post(`${key(tenant, name)}/add`, { value });
export const replaceSecret = (tenant: string, name: string, value: string, reason: string) => post(`${key(tenant, name)}/replace`, { value, reason });
export const deleteSecret = (tenant: string, name: string, reason: string) => post(`${key(tenant, name)}/delete`, { reason });
export const applySecretRequest = (id: string) => post(`/requests/${encodeURIComponent(id)}/apply`);
