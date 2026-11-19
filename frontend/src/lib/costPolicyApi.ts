// 비용·안전 정책 창 호출(dashboard-api). 오류는 상태 코드와 오류 코드만 담아 던진다.
import { statusOnlyError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";

const BASE = "/api/dashboard/cost-policy";

async function parse(res: Response): Promise<unknown> {
  const json = await res.json().catch(() => null);
  if (!res.ok || !json?.data) throw statusOnlyError(json, res.status);
  return json.data;
}

export async function getCostPolicy(): Promise<unknown> {
  return parse(await fetch(`${DASHBOARD_API_URL}${BASE}`, { cache: "no-store", headers: dashboardAuth() }));
}

export async function requestPolicyValue(rule_key: string, requested_value: number, reason: string): Promise<unknown> {
  return parse(await fetch(`${DASHBOARD_API_URL}${BASE}/requests`, {
    method: "POST", headers: { "Content-Type": "application/json", ...dashboardAuth() }, body: JSON.stringify({ rule_key, requested_value, reason }), cache: "no-store",
  }));
}

export async function applyPolicyRequest(reviewId: string): Promise<unknown> {
  return parse(await fetch(`${DASHBOARD_API_URL}${BASE}/requests/${encodeURIComponent(reviewId)}/apply`, {
    method: "POST", headers: { ...dashboardAuth() }, cache: "no-store",
  }));
}
