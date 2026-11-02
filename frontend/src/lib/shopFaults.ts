import { RELAY_API_URL, RELAY_VIEW_TOKEN } from "./env";

// 고객사 쇼핑몰(캣북스, shop-api)의 결제 장애 상태 (SHOP-06). AI 대화방이 읽기만 한다.
// 장애를 넣고 빼는 건 화면이 아니라 운영자 명령이다 (backend/scripts/shop_fault.sh).
// 관리자(admin) 토큰은 backend/scripts/run_web.sh 가 VITE_SHOP_ADMIN_TOKEN으로 넘긴다.

export interface FaultState { active: boolean; delay_ms: number; failing: boolean; degraded: boolean }

async function state(): Promise<FaultState> {
  const res = await fetch(`${RELAY_API_URL}/api/shop/admin/faults`, {
    headers: { Authorization: `Bearer ${RELAY_VIEW_TOKEN}` },
    cache: "no-store",
  });
  const json = (await res.json().catch(() => ({}))) as { data?: FaultState; error?: { message?: string } };
  if (!res.ok || !json.data) throw new Error(json.error?.message ?? `HTTP ${res.status}`);
  return json.data;
}

export const shopFaults = { state };
