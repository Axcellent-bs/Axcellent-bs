// 영상 비용·정책 설정 호출(dashboard-api). 오류는 상태 코드만 담아 던진다.
import { statusOnlyError } from "./apiError";
import type { CostLimitsAnswer } from "./costLimits";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";

const BASE = "/api/dashboard/marketing/cost-limits";

async function parse(res: Response): Promise<CostLimitsAnswer> {
  const json = await res.json().catch(() => null);
  if (!res.ok || !json?.data) throw statusOnlyError(json, res.status);
  return json.data as CostLimitsAnswer;
}

async function post(path: string, body: unknown): Promise<CostLimitsAnswer> {
  return parse(
    await fetch(`${DASHBOARD_API_URL}${BASE}${path}`, {
      method: "POST", headers: { "Content-Type": "application/json", ...dashboardAuth() }, body: JSON.stringify(body), cache: "no-store",
    }),
  );
}

export async function getCostLimits(): Promise<CostLimitsAnswer> {
  return parse(await fetch(`${DASHBOARD_API_URL}${BASE}`, { cache: "no-store", headers: dashboardAuth() }));
}

export interface LimitValues { video_per_item_krw?: number; monthly_budget_krw?: number }
export const lowerCostLimits = (v: LimitValues, reason: string, confirmed: boolean) => post("/lower", { ...v, reason, confirmed });
export const raiseCostLimits = (v: LimitValues, reason: string) => post("/raise", { ...v, reason });
export const setLowerSelfApprove = (lower_self_approve: boolean, reason: string) => post("/option", { lower_self_approve, reason });
