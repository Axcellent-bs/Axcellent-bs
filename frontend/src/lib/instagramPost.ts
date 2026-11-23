// 제작 기록 영상·의뢰로 만든 영상을 회사 인스타그램에 릴스로 올리기(2026-11-23). 유튜브 올리기와 같은 흐름이다.
// - POST /marketing/creative-records/{id}/instagram/draft → AI 캡션·해시태그 초안 또는 사람이 고친 값 저장
// - POST /marketing/creative-records/{id}/instagram → 초안대로 올리기를 사람 승인(C publish_instagram_video)에 올린다
// - POST /marketing/request-videos/{post_draft_id}/instagram/draft·/instagram → 의뢰로 만든 영상도 같은 흐름
// 올라가는 파일은 늘 AI 표시를 입힌 영상이고(제작 기록은 요청할 때 서버가 입힌다), 캡션 끝 AI 생성 문구는 지워지지 않는다.
import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";

/** 제작 기록 render.instagram · 의뢰 영상 인스타 상태 */
export interface InstagramPostState {
  status?: "draft" | "awaiting_approval" | "posting" | "done" | "failed" | "rejected" | string;
  caption?: string;
  hashtags?: string[];
  source?: "ai" | "template" | "human_edit";
  /** 제작 기록은 video·final_video, 의뢰로 만든 영상은 늘 labeled_video */
  part?: "video" | "final_video" | "labeled_video" | null;
  review_id?: string | null;
  media_id?: string;
  permalink?: string | null;
  published_at?: string;
  error?: string | null;
  at?: string;
}

export interface InstagramDraftEdits {
  regenerate?: boolean;
  caption?: string;
  hashtags?: string[];
  part?: "video" | "final_video";
}

const BASE = `${DASHBOARD_API_URL}/api/dashboard/marketing`;

async function post(url: string, body: unknown, fallback: string): Promise<InstagramPostState> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, fallback);
  return ((parsed as { data?: { instagram?: InstagramPostState } } | undefined)?.data?.instagram) ?? {};
}

const recordPath = (id: string) => `${BASE}/creative-records/${encodeURIComponent(id)}/instagram`;
const requestPath = (id: string) => `${BASE}/request-videos/${encodeURIComponent(id)}/instagram`;

export const saveInstagramDraft = (recordId: string, edits: InstagramDraftEdits) =>
  post(`${recordPath(recordId)}/draft`, edits, "인스타 캡션 초안을 저장하지 못했어요");
export const requestInstagramPost = (recordId: string) =>
  post(recordPath(recordId), {}, "인스타 올리기 요청을 보내지 못했어요");
export const saveRequestVideoInstagramDraft = (postDraftId: string, { part: _part, ...edits }: InstagramDraftEdits) =>
  post(`${requestPath(postDraftId)}/draft`, edits, "인스타 캡션 초안을 저장하지 못했어요");
export const requestRequestVideoInstagramPost = (postDraftId: string) =>
  post(requestPath(postDraftId), {}, "인스타 올리기 요청을 보내지 못했어요");

/** 게시물 주소는 instagram.com의 https만 링크로 */
export const safePermalink = (url?: string | null): string | null =>
  url && /^https:\/\/(www\.)?instagram\.com\//.test(url) ? url : null;

export const INSTAGRAM_STATUS_KO: Record<string, string> = {
  draft: "초안", awaiting_approval: "사람 승인 대기", posting: "올리는 중", done: "올림", failed: "실패", rejected: "거절됨",
};

export const INSTAGRAM_ERROR_KO: Record<string, string> = {
  instagram_not_connected: "이 회사 인스타그램이 아직 연결되지 않았어요(SNS 계정 연결에서 연결)",
  video_not_ready: "올릴 영상이 아직 없어요",
  draft_missing: "캡션 초안을 먼저 만들어 주세요",
  label_failed: "영상에 AI 표시를 입히지 못했어요(서버의 ffmpeg·글꼴 확인 필요)",
  media_dir_not_configured: "서버에 미디어 폴더 설정이 없어요",
  media_dir_mismatch: "영상이 미디어 폴더 밖에 있어 올릴 수 없어요",
  video_changed: "승인받은 뒤 영상이나 캡션이 바뀌어 올리지 않았어요. 다시 요청해 주세요",
  interrupted: "서버가 다시 켜져 올리기가 중단됐어요",
  publish_halted: "계정 제한·한도 때문에 SNS 게시가 멈춰 있어요(SNS·미디어에서 확인)",
  publish_paused: "자동 게시가 꺼져 있어요(SNS·미디어 › 게시 스위치)",
  daily_limit_reached: "24시간 게시 한도에 닿았어요. 잠시 뒤 다시 요청해 주세요",
  publish_response_lost: "인스타 응답을 받지 못했어요. 인스타 앱에서 올라갔는지 확인해 주세요(다시 보내지 않았어요)",
  ig_token_invalid: "인스타 토큰이 거절됐어요(다시 연결 필요)",
  ig_video_format: "인스타가 영상 규격을 받지 않았어요(릴스 규격 확인)",
  ig_media_url_unreachable: "인스타가 영상 주소에 닿지 못했어요(미디어 호스팅 설정 확인)",
  gate_rejected: "사람이 거절했어요",
  cover_changed: "표지(썸네일)가 바뀌어 다시 승인이 필요해요. 다시 요청해 주세요",
  cover_missing: "고른 썸네일 파일이 기록과 달라요. 썸네일을 다시 골라 주세요",
};

export const instagramErrorKo = (code?: string | null): string =>
  (code && INSTAGRAM_ERROR_KO[code]) || code || "올리지 못했어요";
