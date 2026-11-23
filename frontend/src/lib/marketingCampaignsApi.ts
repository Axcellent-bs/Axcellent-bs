import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";
import type { MarketingCampaign } from "./campaignLinks";

// 마케팅이 시작한 캠페인 대장(GET /api/dashboard/marketing/campaigns → 마케팅 `list_marketing_campaigns`, read).
// 유튜브 칸의 캠페인 고르기와 결과 저장 칸 미리 채우기가 같이 쓴다. 여러 칸이 같은 목록을 쓰므로 한 번만 읽는다.

let pending: Promise<MarketingCampaign[]> | null = null;

async function load(): Promise<MarketingCampaign[]> {
  let res: Response;
  try {
    res = await fetch(`${DASHBOARD_API_URL}/api/dashboard/marketing/campaigns?limit=100`, { cache: "no-store", headers: { ...dashboardAuth() } });
  } catch {
    throw new Error("화면 중계 서버(dashboard-api)에 연결하지 못했습니다.");
  }
  const json = (await res.json().catch(() => null)) as { data?: { items?: MarketingCampaign[] } } | null;
  if (!res.ok || !json?.data) throw failureError(json, res.status);
  return json.data.items ?? [];
}

/** 캠페인 대장. `fresh`면 다시 읽는다(캠페인을 막 시작했을 때). 실패하면 다음 호출이 다시 읽는다 */
export function marketingCampaigns(fresh = false): Promise<MarketingCampaign[]> {
  if (!DASHBOARD_API_URL) return Promise.resolve([]);
  if (fresh || pending === null) {
    pending = load().catch((e: unknown) => { pending = null; throw e; });
  }
  return pending;
}
