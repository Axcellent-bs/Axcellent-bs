import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";
import { COVER_ID, isRequestId, type RequestVideo } from "./requestVideoView";

// 마케팅 › SNS·미디어 › 의뢰로 만든 영상. dashboard-api GET /marketing/request-videos (목록) ·
// /marketing/sns-drafts/{post_draft_id}/media/preview (라벨 입힌 mp4만). 둘 다 로그인 세션 토큰(dashboardAuth)만 쓴다.
// 미리보기는 <video src>에 헤더를 못 붙이므로 useAuthedMedia(fetch → blob:)로 읽는다.

const LIST_PATH = "/api/dashboard/marketing/request-videos";
const DRAFT_ID = /^[A-Za-z0-9_.:-]{1,80}$/;

export const requestVideosUrl = (requestId: string | null, limit = 20) =>
  `${DASHBOARD_API_URL}${LIST_PATH}?${requestId && isRequestId(requestId) ? `request_id=${encodeURIComponent(requestId)}&` : ""}limit=${limit}`;

/** 라벨 입힌 영상 주소. 번호 형식이 아니면 null */
export const requestVideoPreviewUrl = (postDraftId: string): string | null =>
  DRAFT_ID.test(postDraftId) ? `${DASHBOARD_API_URL}/api/dashboard/marketing/sns-drafts/${encodeURIComponent(postDraftId)}/media/preview` : null;

/** 썸네일 후보 그림 주소(AI 표시 포함 JPEG). 번호 형식이 아니면 null */
export const requestVideoCoverUrl = (postDraftId: string, mediaId: string): string | null =>
  DRAFT_ID.test(postDraftId) && COVER_ID.test(mediaId)
    ? `${DASHBOARD_API_URL}/api/dashboard/marketing/request-videos/${encodeURIComponent(postDraftId)}/covers/${mediaId}`
    : null;

export interface CoverPicked {
  post_draft_id: string;
  cover_media_id: string;
  changed: boolean;
  cover_rev: number;
  /** 무효가 된 승인: instagram · youtube */
  approvals_reset: string[];
}

/** 썸네일 고르기. 사람 토큰. 후보 밖 값은 422, 이미 올라간 영상은 409 */
export async function selectRequestVideoCover(postDraftId: string, mediaId: string): Promise<CoverPicked> {
  const res = await fetch(`${DASHBOARD_API_URL}/api/dashboard/marketing/request-videos/${encodeURIComponent(postDraftId)}/cover`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: JSON.stringify({ media_id: mediaId }),
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, "썸네일을 정하지 못했어요");
  return (parsed as { data: CoverPicked }).data;
}

/** 목록 읽기. 서버에 아직 없으면(404) 빈 목록으로 본다 */
export async function listRequestVideos(requestId: string | null, limit = 20): Promise<RequestVideo[]> {
  const res = await fetch(requestVideosUrl(requestId, limit), { headers: { ...dashboardAuth() }, cache: "no-store" });
  if (res.status === 404) return [];
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, "의뢰로 만든 영상을 읽지 못했어요");
  const items = (parsed as { data?: { items?: unknown } } | undefined)?.data?.items;
  return Array.isArray(items) ? (items as RequestVideo[]) : [];
}
