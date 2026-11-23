// 제작 기록 영상 썸네일의 순수 계산(env에 기대지 않는다). node --test (creativeThumbnails.test.ts). 읽기·주소는 creativeThumbnails.ts.

// 제작 기록 영상의 썸네일 후보 3장(Higgsfield로 만든 그림, AI 표시 포함) → 사람이 고르기 (2026-11-23).
// 상태는 제작 기록 render.thumbnails. 영상이 생기면 기록을 읽을 때 만들기 요청이 저절로 한 번 걸리고(사람 결정 대기),
// 승인되면 3장을 만든다. 고르지 않으면 첫 장이 기본값이고 유튜브 올리기 승인에 같이 들어간다.
// dashboard-api: POST …/creative-records/{id}/thumbnails(만들기 요청) · POST …/thumbnail(고르기) · GET …/thumbnails/{media_id}(그림)

export const THUMB_ID = /^thm_[0-9a-f]{12}$/;
export type ThumbKind = "closeup" | "scene" | "moment";

export interface ThumbCandidate {
  media_id: string;
  kind?: ThumbKind | string;
  sha256?: string;
  is_ai_generated?: boolean;
}

export interface ThumbnailsState {
  status?: "requesting" | "awaiting_approval" | "running" | "done" | "failed" | "blocked" | "unavailable" | "rejected" | string;
  error?: string | null;
  errors?: string[];
  candidates?: ThumbCandidate[];
  /** 사람이 고른 후보. 없으면 첫 후보가 기본값 */
  chosen?: string | null;
  cover_rev?: number;
  aspect?: string;
  route?: "mcp" | "api";
  round?: number;
  review_id?: string | null;
}

export const KIND_KO: Record<string, string> = { closeup: "캐릭터 클로즈업", scene: "핵심 장면", moment: "액션·감정 순간" };
export const THUMB_PENDING = new Set(["requesting", "awaiting_approval", "running"]);
export const THUMB_STATUS_KO: Record<string, string> = {
  requesting: "요청 중", awaiting_approval: "사람 결정 대기", running: "만드는 중", done: "3장 준비됨",
  failed: "실패", blocked: "막힘", unavailable: "만들 수 없음", rejected: "거절됨",
};
export const THUMB_ERROR_KO: Record<string, string> = {
  not_connected: "Higgsfield 계정 연결도 키도 없어 만들지 않았어요. SNS 계정 연결에서 Higgsfield를 연결해 주세요",
  video_not_ready: "영상(Higgsfield 영상이나 직접 올린 완성 영상)이 생기면 만들 수 있어요",
  model_not_configured: "Higgsfield 이미지 모델이 서버에 설정되지 않았어요",
  credits_insufficient: "Higgsfield 계정의 크레딧이 3장 만들기에 모자라요",
  higgsfield_reconnect_needed: "Higgsfield 계정 연결이 끊겼어요. SNS 계정 연결에서 다시 연결해 주세요",
  higgsfield_not_connected: "Higgsfield 계정 연결이 없어요(승인 기다리는 사이 끊겼을 수 있어요)",
  quota_exceeded: "이번 달 이미지 사용량 예산을 넘었어요",
  quota_not_configured: "이미지 월 사용량 예산이 설정되지 않았어요",
  cover_locked: "이미 올린 영상이라 썸네일을 바꿀 수 없어요",
  cover_not_candidate: "후보에 없는 썸네일이에요",
  interrupted: "서버가 다시 켜져 중단됐어요",
  gate_rejected: "사람 결정에서 거절됐어요",
};
export const THUMB_CONFIRM_KO = "썸네일을 바꾸면 유튜브·인스타 올리기 승인을 다시 받아야 해요.";
export const thumbErrorText = (code?: string | null) => (code ? THUMB_ERROR_KO[code] ?? code : null);

export interface ThumbCell {
  media_id: string;
  kind: string;
  /** 지금 정해진 썸네일(고른 것, 없으면 첫 장) */
  chosen: boolean;
  /** 눌러 둔 것(아직 안 눌렀으면 정해진 것) */
  selected: boolean;
}

/** 후보 칸. 형식 밖 값은 버리고, 서버가 고른 값이 후보 밖이면 첫 장을 정해진 것으로 본다 */
export function thumbCells(s: ThumbnailsState | null | undefined, selected: string | null): ThumbCell[] {
  const list = (s?.candidates ?? []).filter((c) => THUMB_ID.test(c.media_id));
  const chosen = list.some((c) => c.media_id === s?.chosen) ? (s?.chosen as string) : list[0]?.media_id ?? null;
  const pick = selected && list.some((c) => c.media_id === selected) ? selected : chosen;
  return list.map((c) => ({ media_id: c.media_id, kind: KIND_KO[c.kind ?? ""] ?? "", chosen: c.media_id === chosen, selected: c.media_id === pick }));
}

/** [이걸로 정하기]: 다른 후보를 눌렀거나, 아직 사람이 안 골랐을 때(기본값 확정)만 */
export function canCommitThumb(cells: ThumbCell[], picked: boolean): boolean {
  const target = cells.find((c) => c.selected);
  return !!target && (!target.chosen || !picked);
}

/** 바꾸기 전 확인: 유튜브·인스타 승인 대기 중이고 다른 후보로 바꿀 때만 */
export function needsThumbConfirm(cells: ThumbCell[], youtubeStatus?: string | null, instagramStatus?: string | null): boolean {
  const target = cells.find((c) => c.selected);
  const waiting = youtubeStatus === "awaiting_approval" || instagramStatus === "awaiting_approval";
  return waiting && !!target && !target.chosen;
}

/** 올린 뒤에는 잠긴다(고르기·다시 만들기 모두). 인스타 릴스 표지로 올리는 중·올린 뒤도 같다 */
export const thumbsLocked = (youtubeStatus?: string | null, instagramStatus?: string | null) =>
  youtubeStatus === "uploading" || youtubeStatus === "done" || instagramStatus === "posting" || instagramStatus === "done";

/** 지금 정해진 썸네일(고른 것, 없으면 첫 장). 릴스 표지 미리보기에 쓴다. 후보가 없으면 null */
export function chosenThumb(s: ThumbnailsState | null | undefined): { media_id: string; picked: boolean } | null {
  const cell = thumbCells(s, null).find((c) => c.chosen);
  return cell ? { media_id: cell.media_id, picked: s?.chosen === cell.media_id } : null;
}

/** 만들기 버튼 글: 후보가 있으면 다시 만들기. 진행 중이거나 영상이 없으면 null(버튼 없음) */
export function thumbAction(s: ThumbnailsState | null | undefined, hasVideo: boolean, locked: boolean): "make" | "remake" | null {
  if (!hasVideo || locked || THUMB_PENDING.has(s?.status ?? "")) return null;
  return (s?.candidates?.length ?? 0) > 0 ? "remake" : "make";
}

/** 목록 칩 글: render 단계 상태 중 thumbnails. 없으면 null */
export function thumbChip(status?: string | null): string | null {
  if (!status) return null;
  if (status === "done") return "썸네일 3장";
  return `썸네일 ${THUMB_STATUS_KO[status] ?? status}`;
}

