// 영상 AI 공급자 목록·선택·커스텀 등록 호출(dashboard-api). 오류는 상태 코드만 담아 던진다.
import { statusOnlyError } from "./apiError";
import { writeAuth } from "./api";
import { DASHBOARD_API_URL } from "./env";
import type { VideoProvidersAnswer } from "./videoProviders";

const BASE = "/api/dashboard/marketing/video-providers";

async function post(path: string, body: unknown): Promise<VideoProvidersAnswer> {
  const res = await fetch(`${DASHBOARD_API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...writeAuth("POST", path) },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const json = await res.json().catch(() => null);
  if (!res.ok || !json?.data) throw statusOnlyError(json, res.status);
  return json.data as VideoProvidersAnswer;
}

export async function getVideoProviders(): Promise<VideoProvidersAnswer> {
  const res = await fetch(`${DASHBOARD_API_URL}${BASE}`, { cache: "no-store" });
  const json = await res.json().catch(() => null);
  if (!res.ok || !json?.data) throw statusOnlyError(json, res.status);
  return json.data as VideoProvidersAnswer;
}
export const selectVideoProvider = (provider: string, model: string) => post(`${BASE}/select`, { provider, model });
export const saveCustomVideoProvider = (spec: Record<string, unknown>) => post(`${BASE}/custom`, { spec });
export const deleteCustomVideoProvider = (id: string) => post(`${BASE}/custom/delete`, { id });
