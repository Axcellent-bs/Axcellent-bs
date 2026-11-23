import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";
import type { SavedCharacter } from "./requestCharacter";

// 회사 대표 캐릭터 (11/23). dashboard-api /marketing/brand-characters → 마케팅 brand_characters_list·mark·unmark.
// 사람 토큰이 필요하고, 로그인한 회사의 것만 보인다. 새 저장소 없이 제작 기록(CR-…)에 표시를 단다(회사당 최대 3개, 마지막이 기본).

const PATH = "/api/dashboard/marketing/brand-characters";

async function send<T>(url: string, method: string, body: unknown, fallback: string): Promise<T> {
  const res = await fetch(`${DASHBOARD_API_URL}${url}`, {
    method,
    headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...dashboardAuth() },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, fallback);
  return (parsed as { data: T }).data;
}

export interface BrandCharacter extends SavedCharacter {
  marked_by?: string | null;
  rights_confirmed_by?: string | null;
  rights_confirmed_at?: string | null;
}

export const listBrandCharacters = () =>
  send<{ items: BrandCharacter[] }>(PATH, "GET", undefined, "대표 캐릭터를 읽지 못했어요").then((d) => d.items);

/** 대표 캐릭터로 표시한다. 사람이 권리를 확인했을 때만 부른다(서버도 rights_confirmed 없으면 422) */
export const markBrandCharacter = (recordId: string) =>
  send<{ record_id: string; marked: boolean; dropped: string | null; items: BrandCharacter[] }>(
    `${PATH}/${encodeURIComponent(recordId)}`, "POST", { rights_confirmed: true }, "대표 캐릭터로 저장하지 못했어요");

export const unmarkBrandCharacter = (recordId: string) =>
  send<{ record_id: string; marked: boolean }>(`${PATH}/${encodeURIComponent(recordId)}`, "DELETE", undefined, "표시를 떼지 못했어요");
