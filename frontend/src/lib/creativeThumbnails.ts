import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";
import { THUMB_ID, type ThumbnailsState } from "./creativeThumbnailView";

// 제작 기록 영상 썸네일의 읽기·요청 (순수 계산은 creativeThumbnailView.ts).
// dashboard-api: POST …/creative-records/{id}/thumbnails(만들기 요청, 사람 결정 C) · POST …/thumbnail(고르기)
// · GET …/thumbnails/{media_id}(후보 그림, 사람 토큰)

export * from "./creativeThumbnailView";

const PATH = "/api/dashboard/marketing/creative-records";

/** 후보 그림 주소(사람 토큰이 필요해 useAuthedMedia로 읽는다). 형식 밖 값이면 null */
export const thumbUrl = (recordId: string, mediaId: string): string | null =>
  THUMB_ID.test(mediaId) ? `${DASHBOARD_API_URL}${PATH}/${encodeURIComponent(recordId)}/thumbnails/${mediaId}` : null;

async function post<T>(url: string, body: unknown, fallback: string): Promise<T> {
  const res = await fetch(`${DASHBOARD_API_URL}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, fallback);
  return (parsed as { data: T }).data;
}

/** 후보 3장 만들기를 사람 결정에 올린다(C). 승인 전에는 만들지 않는다 */
export const requestThumbnails = (recordId: string, regenerate: boolean) =>
  post<{ record_id: string; thumbnails: ThumbnailsState }>(`${PATH}/${encodeURIComponent(recordId)}/thumbnails`, { regenerate }, "썸네일 만들기를 요청하지 못했어요");

export interface ThumbPicked { record_id: string; cover_media_id: string; changed: boolean; cover_rev: number; approvals_reset: string[] }

/** 후보 중 하나를 고른다. 유튜브 승인 대기 중이면 그 승인이 풀린다 */
export const selectThumbnail = (recordId: string, mediaId: string) =>
  post<ThumbPicked>(`${PATH}/${encodeURIComponent(recordId)}/thumbnail`, { media_id: mediaId }, "썸네일을 정하지 못했어요");
