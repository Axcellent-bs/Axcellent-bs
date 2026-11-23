import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";

// AXcellent 유튜브 채널 연결 상태와 제작 기록 영상 올리기(2026-11-23).
// - GET /marketing/youtube/status → 마케팅 `youtube_status`(read). 키 값은 오지 않는다
// - POST /marketing/creative-records/{id}/youtube/draft → AI 제목·설명·태그 초안 또는 사람이 고친 값 저장
// - POST /marketing/creative-records/{id}/youtube → 초안대로 올리기를 사람 승인(C publish_youtube)에 올린다
// - POST /marketing/request-videos/{post_draft_id}/youtube/draft·/youtube → 의뢰로 만든 영상(wr_…)도 같은 흐름.
//   올리는 파일은 늘 AI 표시를 입힌 영상이라 올릴 영상(part)을 고르지 않는다

export type YoutubeConnection = "ok" | "missing_keys" | "invalid_credentials" | "error" | string;
export type YoutubePrivacy = "private" | "unlisted" | "public";

export interface YoutubeStatus {
  connection: YoutubeConnection;
  channel?: string | null;
  checked_at: string;
}

/** 제작 기록의 render.youtube */
export interface YoutubeState {
  status?: "draft" | "awaiting_approval" | "uploading" | "done" | "failed" | "rejected" | string;
  title?: string;
  description?: string;
  tags?: string[];
  privacy?: YoutubePrivacy;
  /** 제작 기록은 video·final_video, 의뢰로 만든 영상은 늘 labeled_video(AI 표시 입힌 파일) */
  part?: "video" | "final_video" | "labeled_video" | null;
  source?: "ai" | "template" | "human_edit";
  review_id?: string | null;
  video_id?: string;
  url?: string;
  error?: string | null;
  at?: string;
  /** 어느 캠페인(cmp_…) 영상인지(11/23, 선택). 재무 캠페인 결과의 유튜브 조회수가 이 값으로 영상을 찾는다 */
  campaign_id?: string | null;
}

export interface YoutubeDraftEdits {
  regenerate?: boolean;
  title?: string;
  description?: string;
  tags?: string[];
  privacy?: YoutubePrivacy;
  part?: "video" | "final_video";
  /** ""면 캠페인 연결을 푼다. 올린 뒤에도 바꿀 수 있다 */
  campaign_id?: string;
}

const BASE = "/api/dashboard/marketing";

async function readJson<T>(res: Response, fallback: string): Promise<T> {
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, fallback);
  return (parsed as { data: T }).data;
}

export async function fetchYoutubeStatus(): Promise<YoutubeStatus> {
  const res = await fetch(`${DASHBOARD_API_URL}${BASE}/youtube/status`, { headers: { ...dashboardAuth() }, cache: "no-store" });
  return readJson<YoutubeStatus>(res, "유튜브 연결 상태를 읽지 못했어요");
}

export async function saveYoutubeDraft(recordId: string, edits: YoutubeDraftEdits): Promise<YoutubeState> {
  const res = await fetch(`${DASHBOARD_API_URL}${BASE}/creative-records/${encodeURIComponent(recordId)}/youtube/draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: JSON.stringify(edits),
    cache: "no-store",
  });
  return (await readJson<{ youtube: YoutubeState }>(res, "유튜브 초안을 저장하지 못했어요")).youtube;
}

export async function requestYoutubeUpload(recordId: string): Promise<YoutubeState> {
  const res = await fetch(`${DASHBOARD_API_URL}${BASE}/creative-records/${encodeURIComponent(recordId)}/youtube`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: "{}",
    cache: "no-store",
  });
  return (await readJson<{ youtube: YoutubeState }>(res, "유튜브 올리기 요청을 보내지 못했어요")).youtube;
}

export type RequestVideoYoutubeEdits = Omit<YoutubeDraftEdits, "part">;

const requestVideoPath = (postDraftId: string) => `${DASHBOARD_API_URL}${BASE}/request-videos/${encodeURIComponent(postDraftId)}/youtube`;

/** 의뢰로 만든 영상의 AI 제목·설명·태그 초안 또는 사람이 고친 값 저장 (올리지 않는다) */
export async function saveRequestVideoYoutubeDraft(postDraftId: string, edits: RequestVideoYoutubeEdits): Promise<YoutubeState> {
  const res = await fetch(`${requestVideoPath(postDraftId)}/draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: JSON.stringify(edits),
    cache: "no-store",
  });
  return (await readJson<{ youtube: YoutubeState }>(res, "유튜브 초안을 저장하지 못했어요")).youtube;
}

/** 의뢰로 만든 영상(AI 표시 입힌 파일)의 유튜브 올리기를 사람 승인(C)에 올린다 */
export async function requestRequestVideoYoutubeUpload(postDraftId: string): Promise<YoutubeState> {
  const res = await fetch(requestVideoPath(postDraftId), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: "{}",
    cache: "no-store",
  });
  return (await readJson<{ youtube: YoutubeState }>(res, "유튜브 올리기 요청을 보내지 못했어요")).youtube;
}

export const YOUTUBE_ERROR_KO: Record<string, string> = {
  youtube_not_connected: "이 회사 유튜브 채널이 아직 연결되지 않았어요(SNS 계정 연결에서 연결)",
  video_not_ready: "올릴 영상(Higgsfield 영상이나 직접 올린 완성 영상)이 아직 없어요",
  draft_missing: "제목·설명 초안을 먼저 만들어 주세요",
  invalid_credentials: "유튜브 연결 키가 거절됐어요(다시 연결 필요)",
  interrupted: "서버가 다시 켜져 올리기가 중단됐어요",
  video_changed: "승인받은 뒤 영상 파일이 바뀌어 올리지 않았어요. 다시 요청해 주세요",
};
