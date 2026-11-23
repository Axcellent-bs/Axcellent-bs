import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";

// 마케팅 › SNS·미디어 › Higgsfield 연결 상태. dashboard-api GET /marketing/higgsfield/status →
// 마케팅 `higgsfield_status`(read). 서버가 Higgsfield에 가벼운 읽기 요청을 보내 키가 받아들여지는지 본다.
// 키 값은 오지 않는다. 서버는 결과를 20초 동안 기억한다.

export type HiggsfieldConnection = "ok" | "invalid_key" | "unreachable" | "not_configured";

export interface HiggsfieldStatus {
  key_configured: boolean;
  connection: HiggsfieldConnection;
  http_status?: number | null;
  image_model?: string | null;
  checked_at: string;
  message_ko: string;
}

const PATH = "/api/dashboard/marketing/higgsfield/status";

export async function fetchHiggsfieldStatus(): Promise<HiggsfieldStatus> {
  const res = await fetch(`${DASHBOARD_API_URL}${PATH}`, { headers: { ...dashboardAuth() }, cache: "no-store" });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, "Higgsfield 연결 상태를 읽지 못했어요");
  return (parsed as { data: HiggsfieldStatus }).data;
}
