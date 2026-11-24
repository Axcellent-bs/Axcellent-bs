import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";

// 마케팅 › SNS·미디어 › Higgsfield 연결 상태. dashboard-api GET /marketing/higgsfield/status →
// 마케팅 `higgsfield_status`(read). 서버가 Higgsfield에 가벼운 읽기 요청을 보내 키가 받아들여지는지 본다.
// 키 값은 오지 않는다. 서버는 결과를 20초 동안 기억한다.
// 회사가 Higgsfield 계정을 연결했으면(원격 MCP OAuth) source=mcp이고 connection은 그 연결 상태,
// credits·plan은 그 계정의 남은 크레딧·구독 요금제다(모르면 null).

export type HiggsfieldConnection = "ok" | "invalid_key" | "unreachable" | "not_configured";

export interface HiggsfieldStatus {
  key_configured: boolean;
  connection: HiggsfieldConnection;
  http_status?: number | null;
  image_model?: string | null;
  checked_at: string;
  message_ko: string;
  source?: "mcp" | "api_key" | null;
  credits?: number | null;
  plan?: string | null;
  /** 11/24 실패 사유 코드(network·http_<n>·tool_error·client_error…). 성공·옛 서버는 없음 */
  reason_code?: string | null;
  /** 11/24 Higgsfield가 준 문장(주소·토큰 모양 가림, 120자). 없으면 null */
  detail_ko?: string | null;
}

const PATH = "/api/dashboard/marketing/higgsfield/status";

export async function fetchHiggsfieldStatus(): Promise<HiggsfieldStatus> {
  const res = await fetch(`${DASHBOARD_API_URL}${PATH}`, { headers: { ...dashboardAuth() }, cache: "no-store" });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, "Higgsfield 연결 상태를 읽지 못했어요");
  return (parsed as { data: HiggsfieldStatus }).data;
}
