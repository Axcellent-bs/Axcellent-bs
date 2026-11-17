import { DASHBOARD_API_URL } from "./env";

// 고객사 쇼핑몰(캣북스, shop-api)의 결제 장애 상태 (SHOP-06). AI 대화방이 읽기만 한다.
// 장애를 넣고 빼는 건 화면이 아니라 운영자 명령이다 (backend/scripts/shop_fault.sh → 캣북스 레포 스크립트).
// 판매 서버는 dashboard-api가 대신 읽는다(`/api/dashboard/shop/faults`, 서버 쪽 열람 토큰).

export interface FaultState { active: boolean; delay_ms: number; failing: boolean; degraded: boolean }

async function state(): Promise<FaultState> {
  const res = await fetch(`${DASHBOARD_API_URL}/api/dashboard/shop/faults`, { cache: "no-store" });
  const json = (await res.json().catch(() => ({}))) as { data?: FaultState; error?: { message?: string } };
  if (!res.ok || !json.data) throw new Error(json.error?.message ?? `HTTP ${res.status}`);
  return json.data;
}

export const shopFaults = { state };
