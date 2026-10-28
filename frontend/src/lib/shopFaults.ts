import { RELAY_API_URL, RELAY_TOKEN } from "./env";

// 고객사 쇼핑몰(캣북스, shop-api)의 결제 장애 주입·해제 (SHOP-06). 실제 모드 AI 대화방이 쓴다.
// 장애를 넣으면 쇼핑몰의 실제 결제가 실패하고, 쇼핑몰 감시가 그걸 재서 `shop.degraded`를 내면 총괄이 사건을 연다.
// 주입은 관리자(admin) 토큰만 된다. 실제 모드 토큰은 backend/scripts/run_web.sh 가 VITE_SHOP_ADMIN_TOKEN으로 넘긴다.

export type FaultKind = "failure" | "latency";
export interface FaultState { active: boolean; delay_ms: number; failing: boolean; degraded: boolean }

async function call(method: "GET" | "POST" | "DELETE", body?: unknown): Promise<FaultState> {
  const res = await fetch(`${RELAY_API_URL}/api/shop/admin/faults`, {
    method,
    headers: { Authorization: `Bearer ${RELAY_TOKEN}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const json = (await res.json().catch(() => ({}))) as { data?: FaultState; error?: { message?: string } };
  if (!res.ok || !json.data) throw new Error(json.error?.message ?? `HTTP ${res.status}`);
  return json.data;
}

export const shopFaults = {
  state: () => call("GET"),
  inject: (kind: FaultKind) => call("POST", kind === "latency" ? { kind, delay_ms: 2000 } : { kind }),
  release: () => call("DELETE"),
};
