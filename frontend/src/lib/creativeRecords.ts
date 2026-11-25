import type { InstagramPostState } from "./instagramPost";
import type { YoutubeState } from "./youtube";
import type { ThumbnailsState } from "./creativeThumbnails";
import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";
import type { CharacterSheetResult } from "./characterSheet";
import type { StoryboardResult } from "./storyboard";
import type { CreativeScript } from "./creativeScript";
import type { OverlayState, PlayPart } from "./videoOverlay";
import { videoRequestBody, type RegenerateResult, type VideoQcState } from "./videoQcView";
import type { CreativeVideoSummary } from "./creativeVideo";
import { boardImagePath } from "./storyboardSvg";
import { keyframeRedrawPath, type KeyframePart, type KeyframesState } from "./keyframes";

// 마케팅 › 제작 기록. 이미지 시트를 만들 때마다 남은 기록과 이어 만든 스토리보드를 읽는다.
// dashboard-api GET /marketing/creative-records(목록) · /marketing/creative-records/{id}(상세)
// → 마케팅 `creative_records_list`·`creative_record_get`(read). 원본 이미지는 저장되지 않고 작은 미리보기만 있다.

export interface CreativeRecordSummary {
  record_id: string;
  created_at: string;
  summary_ko: string;
  note?: string | null;
  story?: string | null;
  thumbnail_base64?: string | null;
  storyboard_title_ko?: string | null;
  shot_count: number;
  storyboard_at?: string | null;
  /** 단계별 상태(sheet_image·board_image·video·final_video·youtube·thumbnails) */
  render?: Record<string, string>;
  /** 대본 장면 수(없으면 0)·저장 시각(11/23) */
  script_scenes?: number;
  script_at?: string | null;
}

export type RenderPartKey = "sheet_image" | "board_image" | "video" | "final_video" | "motion_video";
export interface RenderPart {
  status?: string;
  error?: string | null;
  asset?: string;
  sha256?: string;
  review_id?: string | null;
  request_id?: string;
  at?: string;
  /** "upload"면 사람이 직접 올린 파일 */
  source?: string;
  /** 올린 파일 이름 */
  label?: string | null;
  /** 올린 사람 */
  by?: string | null;
  /** 영상 칸의 요약(모델·길이·비율·참조·예상 크레딧). 아직 없을 수 있다 */
  summary?: CreativeVideoSummary;
  /** 영상 거절 이유(11/26): review_timeout = 기한 지나 자동 취소, rejected = 사람 거절 */
  reason?: string | null;
  /** 영상 다시 승인 요청 횟수(처음 0) */
  attempt?: number;
  /** 승인 대기를 시작한 시각 */
  held_at?: string | null;
  /** 기술 검사 [영상 다시 만들기]가 거절·기한 지남·막힘으로 끝나 예전 영상으로 되돌렸을 때(11/26) */
  regenerate?: RegenerateResult | null;
}

export interface CreativeRecord {
  record_id: string;
  created_at: string;
  note?: string | null;
  story?: string | null;
  shots?: number | null;
  thumbnail_base64?: string | null;
  sheet: CharacterSheetResult;
  storyboard?: StoryboardResult | null;
  storyboard_at?: string | null;
  /** 영상 대본(11/23). 옛 기록은 없다 */
  script?: CreativeScript | null;
  /** Higgsfield 그림·영상 */
  render?: Partial<Record<RenderPartKey, RenderPart>> & { youtube?: YoutubeState; thumbnails?: ThumbnailsState; instagram?: InstagramPostState; overlay?: OverlayState; keyframes?: KeyframesState; qc?: VideoQcState; plan_review?: { status?: string } & Record<string, unknown> };
}

const PATH = "/api/dashboard/marketing/creative-records";
export const RECORD_ID = /^CR-[0-9a-f]{12}$/;
export const thumbSrc = (b64?: string | null) => (b64 ? `data:image/jpeg;base64,${b64}` : null);

async function get<T>(url: string, fallback: string): Promise<T> {
  const res = await fetch(`${DASHBOARD_API_URL}${url}`, { headers: { ...dashboardAuth() }, cache: "no-store" });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, fallback);
  return (parsed as { data: T }).data;
}

export const listCreativeRecords = (limit = 50) =>
  get<{ items: CreativeRecordSummary[] }>(`${PATH}?limit=${limit}`, "제작 기록을 읽지 못했어요").then((d) => d.items);

export const getCreativeRecord = (id: string) => get<CreativeRecord>(`${PATH}/${encodeURIComponent(id)}`, "제작 기록을 읽지 못했어요");

/** Higgsfield 결과 파일 주소(사람 토큰이 필요해 useAuthedMedia로 읽는다). `version`은 sha256 앞자리 */
export const assetUrl = (id: string, part: RenderPartKey | PlayPart | KeyframePart, version: string | null) =>
  `${DASHBOARD_API_URL}${PATH}/${encodeURIComponent(id)}/asset/${part}${version ? `?v=${encodeURIComponent(version.slice(0, 12))}` : ""}`;

/** 스토리보드 그림으로 영상 만들기를 사람 승인에 올린다(POST, C 등급). 승인 전에는 만들지 않는다.
 *  `regenerate`(11/26): 다 만든 영상이 기술 검사 확인 필요일 때만 서버가 새 승인 요청을 만든다 */
export async function requestCreativeVideo(id: string, opts?: { regenerate?: boolean }): Promise<{ record_id: string; video: RenderPart }> {
  const res = await fetch(`${DASHBOARD_API_URL}${PATH}/${encodeURIComponent(id)}/video`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: videoRequestBody(opts),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, "영상 요청을 보내지 못했어요");
  return (parsed as { data: { record_id: string; video: RenderPart } }).data;
}

/** [Higgsfield 그림으로 다시 그리기]: 스토리보드 그림만 만든다(POST, LLM 없음, 그림 크레딧·관문은 그대로).
 *  서버가 아직 이 경로를 모르거나 다른 회사 기록이면 404(ApiError.status) */
export async function redrawBoardImage(id: string): Promise<{ record_id: string; part: string; item: RenderPart }> {
  const res = await fetch(`${DASHBOARD_API_URL}${boardImagePath(id)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: "{}",
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, "그림을 요청하지 못했어요");
  return (parsed as { data: { record_id: string; part: string; item: RenderPart } }).data;
}

/** 키프레임 한 장 [다시 그리기] (POST, 그림 1장·그림 크레딧·관문 그대로). 결과 item은 render.keyframes.
 *  서버가 아직 이 경로를 모르거나 다른 회사 기록이면 404(ApiError.status) */
export async function redrawKeyframe(id: string, index: number): Promise<{ record_id: string; part: string; item: KeyframesState }> {
  const res = await fetch(`${DASHBOARD_API_URL}${keyframeRedrawPath(id, index)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: "{}",
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, "키프레임을 다시 그리지 못했어요");
  return (parsed as { data: { record_id: string; part: string; item: KeyframesState } }).data;
}

/** 자막을 고쳐서 다시 얹는다(POST). 서버가 아직 이 경로를 모르면 404(ApiError.status)로 온다 */
export async function reapplyOverlay(id: string, captions: { no: number; caption_ko: string }[]): Promise<OverlayState> {
  const res = await fetch(`${DASHBOARD_API_URL}${PATH}/${encodeURIComponent(id)}/overlay`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: JSON.stringify({ captions }),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, "자막을 다시 얹지 못했어요");
  const data = (parsed as { data?: { overlay?: OverlayState } & OverlayState } | undefined)?.data;
  return data?.overlay ?? data ?? { status: "running" };
}

/** 사람이 만든 그림(JPG·PNG)·영상(MP4)을 제작 기록 칸에 올린다(POST, 본문은 파일 그대로) */
export async function uploadCreativeFile(id: string, part: RenderPartKey, file: File): Promise<RenderPart> {
  const url = `${DASHBOARD_API_URL}${PATH}/${encodeURIComponent(id)}/upload/${part}?name=${encodeURIComponent(file.name.slice(0, 120))}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": file.type || "application/octet-stream", ...dashboardAuth() },
    body: file,
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, res.status === 413 ? "파일이 너무 커요" : "파일을 올리지 못했어요");
  return (parsed as { data: { item: RenderPart } }).data.item;
}
