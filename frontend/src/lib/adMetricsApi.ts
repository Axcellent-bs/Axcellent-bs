import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";
import type { AdMetricsBody, AdMetricsView, CampaignLink, RecordAdMetricsAnswer } from "./adMetrics";

// 캠페인 광고 숫자·추적 링크(dashboard-api). 경로는 routes.py의
// GET/POST /api/dashboard/marketing/campaigns/{id}/ad-metrics, POST .../{id}/link.
// 적기는 사람 토큰만 된다(적은 사람은 서버가 토큰에서 채운다). 링크 만들기는 읽기다.

const base = (campaignId: string) => `${DASHBOARD_API_URL}/api/dashboard/marketing/campaigns/${encodeURIComponent(campaignId)}`;

async function send<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store", ...init, headers: { ...(init?.body ? { "Content-Type": "application/json" } : {}), ...dashboardAuth() } });
  } catch {
    throw new Error("화면 중계 서버(dashboard-api)에 연결하지 못했습니다.");
  }
  const json = (await res.json().catch(() => null)) as { data?: T } | null;
  if (!res.ok || !json || json.data === undefined) throw failureError(json, res.status);
  return json.data;
}

export const adMetricsEnabled = Boolean(DASHBOARD_API_URL);

export const adMetricsApi = {
  get: (campaignId: string) => send<AdMetricsView>(`${base(campaignId)}/ad-metrics`),
  record: (campaignId: string, body: AdMetricsBody) =>
    send<RecordAdMetricsAnswer>(`${base(campaignId)}/ad-metrics`, { method: "POST", body: JSON.stringify(body) }),
  link: (campaignId: string, body: { base_url: string; source: string; medium: string }) =>
    send<CampaignLink>(`${base(campaignId)}/link`, { method: "POST", body: JSON.stringify(body) }),
};
