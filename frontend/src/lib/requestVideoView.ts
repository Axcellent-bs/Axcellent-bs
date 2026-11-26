// 의뢰로 만든 영상(홍보 영상 의뢰 wr_… → SNS 초안 reel)의 순수 계산. node --test (requestVideoView.test.ts).
// 환경 값(env)에 기대지 않는 것만 둔다. 읽기·주소는 lib/requestVideos.ts.
// 화면은 제작 기록(/marketing/records)의 작업 목록과 상세에 둔다(11/26, 탭을 없앴다. ?request=wr_…는 그 작업 상세).
// 영상마다 회사 유튜브·인스타 릴스 올리기(사람 승인 C)를 할 수 있다. 대화방 의뢰 카드에서도 같은 칸을 연다(11/26).
import type { WorkRequestRow } from "./workRequests.ts";
import type { InstagramPostState } from "./instagramPost.ts";
import type { YoutubeState } from "./youtube.ts";

/** 백엔드 계약: GET /api/dashboard/marketing/request-videos 의 items 한 건 */
export interface RequestVideo {
  post_draft_id: string;
  request_id: string;
  context_id: string | null;
  media_id: string | null;
  media_kind: "video";
  duration_s: number | null;
  caption_ko: string | null;
  created_at: string;
  /** AI 라벨을 입힌 파일이 있나. false면 원본은 보여 주지 않는다 */
  labeled: boolean;
  /** labeled일 때만 값이 있다 (서버가 이 파일만 내준다). 화면은 이 값 대신 draft 번호로 주소를 만든다 */
  preview_path: string | null;
  /** draft_ready | check_rejected | label_missing (백엔드 request_video._item). label_missing이면 labeled:false */
  status: string;
  /** 이 영상의 유튜브 올리기 상태(제작 기록 render.youtube와 같은 모양). 아직 없으면 null */
  youtube?: YoutubeState | null;
  /** 이 영상의 인스타 릴스 올리기 상태(제작 기록 render.instagram과 같은 모양). 아직 없으면 null */
  instagram?: InstagramPostState | null;
  /** 썸네일 후보 3장(라벨 입힌 영상에서 뽑은 AI 표시 포함 그림). 라벨 없는 영상은 빈 목록 */
  covers?: RequestVideoCover[];
  /** 고른 후보, 고르지 않았으면 첫 후보(기본값). 후보가 없으면 null */
  cover_media_id?: string | null;
  /** 사람이 직접 골랐나(false면 첫 후보가 기본값) */
  cover_picked?: boolean;
  /** 승인 대기·승인됨(게시 전)인 게시·유튜브·인스타 요청이 있나. 있으면 썸네일을 바꾸기 전에 확인을 묻는다 */
  approval_waiting?: boolean;
  /** 후보를 만들지 못한 사유(옛 영상 채우기, 백엔드 request_video_cover.backfill). 없으면 null */
  covers_error?: string | null;
}

/** 썸네일 후보를 만들지 못한 사유 (서버 ffmpeg 프레임 뽑기) */
export const COVER_ERROR_KO: Record<string, string> = {
  ffmpeg_missing: "서버에 ffmpeg가 없어 썸네일 후보를 만들지 못했어요(서버에 ffmpeg 설치 필요, 설치하면 10분 안에 다시 시도해요)",
  ffprobe_missing: "서버에 ffprobe가 없어 영상 길이를 읽지 못해 썸네일 후보를 만들지 못했어요(ffmpeg 패키지 설치 필요)",
  duration_unknown: "영상 길이를 읽지 못해 썸네일 후보를 만들지 못했어요",
  frame_grab_failed: "영상에서 그림을 뽑지 못해 썸네일 후보를 만들지 못했어요",
  label_missing: "AI 표시가 입혀진 영상이 아니라 썸네일 후보를 만들지 않았어요",
  draft_missing: "서버 미디어 폴더 설정이 없어 썸네일 후보를 만들지 못했어요",
};
export const coverErrorKo = (code?: string | null): string | null =>
  code ? COVER_ERROR_KO[code] ?? `썸네일 후보를 만들지 못했어요 (${code})` : null;

/** 썸네일 후보 한 장 (백엔드 request_video_cover.public) */
export interface RequestVideoCover {
  media_id: string;
  /** 영상에서 뽑은 시각(초) */
  at_s: number | null;
}

export const REQUEST_ID = /^wr_[0-9a-f]{10}$/;
export const isRequestId = (v: string | null | undefined): v is string => !!v && REQUEST_ID.test(v);

/** 홍보 영상 의뢰의 영상 제작 단계: 마케팅 generate_media (의뢰 종류 promo_video의 "make" 단계) */
export const VIDEO_MAKE_SKILL = "generate_media";
export const REQUEST_VIDEOS_PATH = "/marketing/records?view=videos";

/** 영상 열기 링크: 영상 제작 단계가 완료일 때만 `/marketing/records?view=videos&request=<wr_…>`, 아니면 null */
export function requestVideoLink(r: Pick<WorkRequestRow, "request_id" | "task" | "plan" | "steps">): string | null {
  if (!isRequestId(r.request_id)) return null;
  if (r.task && r.task !== "promo_video") return null;
  const make = r.plan.find((p) => p.skill === VIDEO_MAKE_SKILL && p.dept === "marketing");
  if (!make || r.steps[make.step_id]?.status !== "completed") return null;
  return `${REQUEST_VIDEOS_PATH}&request=${encodeURIComponent(r.request_id)}`;
}

/** 쿼리 ?request= 값이 의뢰 번호 형식이면 그 번호, 아니면 null (형식 밖 값은 서버에 보내지 않는다) */
export const requestFilterOf = (param: string | null | undefined): string | null => (isRequestId(param) ? param : null);

export const durationKo = (s: number | null | undefined) => (s == null || !Number.isFinite(s) || s <= 0 ? "길이 미정" : `${Math.round(s)}초`);

const STATUS_KO: Record<string, string> = { draft: "초안", merged: "합쳐짐", pending_approval: "게시 승인 대기", approved: "승인됨", published: "게시됨", rejected: "거절됨", draft_ready: "초안 준비됨", check_rejected: "점검에서 거절됨", label_missing: "AI 표시 아직 없음" };
export const statusKo = (s: string | null | undefined) => (s ? STATUS_KO[s] ?? s : "상태 미정");

export type VideoPane =
  | { kind: "play" }
  | { kind: "waiting"; text: string };

/** 칸 표시: 라벨 입힌 영상이면 재생, 아니면 안내(원본은 어떤 경우에도 보여 주지 않는다) */
export function videoPane(v: Pick<RequestVideo, "labeled" | "preview_path" | "status">): VideoPane {
  if (v.status !== "label_missing" && v.labeled && v.preview_path) return { kind: "play" };
  return { kind: "waiting", text: "AI 표시(라벨)를 입히는 중이거나 아직 없어요. 라벨이 입혀진 영상만 여기서 볼 수 있어요." };
}

/** 의뢰 필터가 있으면 그 의뢰 영상을 맨 앞으로 (필터는 서버에도 보낸다) */
export const sortForRequest = (items: RequestVideo[], requestId: string | null): RequestVideo[] =>
  requestId ? [...items].sort((a, b) => Number(b.request_id === requestId) - Number(a.request_id === requestId)) : items;

export const VIDEOS_EMPTY_KO = "아직 영상이 없어요. 홍보 영상 의뢰가 끝나면 여기에 나타나요.";
export const AI_LABEL_NOTE_KO = "이 영상은 AI가 만들었어요. AI 기본법에 따라 영상 파일과 게시 글에 AI 생성 표시가 들어가며, 게시는 사람 승인(C 등급)을 거쳐요.";

// ---- 썸네일 후보 3장 → 사람이 고르기 (11/23)

export const COVER_CONFIRM_KO = "썸네일을 바꾸면 게시 승인을 다시 받아야 해요.";
export const VIDEO_NOT_FOUND_KO = "이 의뢰의 영상을 아직 찾지 못했어요. 영상 제작이 끝났는지 의뢰 진행을 확인해 주세요.";
export const COVER_ID = /^cov_[0-9a-f]{12}$/;

export interface CoverCell {
  media_id: string;
  /** "0:03" 같은 영상 속 시각 (모르면 빈 글자) */
  at: string;
  /** 지금 정해진 썸네일(체크 표시) */
  chosen: boolean;
  /** 화면에서 눌러 둔 후보(아직 [이걸로 정하기] 전) */
  selected: boolean;
}

const clock = (s: number | null | undefined) => {
  if (s == null || !Number.isFinite(s) || s < 0) return "";
  const whole = Math.floor(s);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
};

/** 후보 칸: 형식이 맞는 후보만, 지금 정해진 것은 chosen, 눌러 둔 것은 selected (기본은 정해진 것) */
export function coverCells(v: Pick<RequestVideo, "covers" | "cover_media_id">, selected: string | null): CoverCell[] {
  const covers = (v.covers ?? []).filter((c) => COVER_ID.test(c.media_id));
  const chosen = covers.some((c) => c.media_id === v.cover_media_id) ? v.cover_media_id : covers[0]?.media_id ?? null;
  const pick = selected && covers.some((c) => c.media_id === selected) ? selected : chosen;
  return covers.map((c) => ({ media_id: c.media_id, at: clock(c.at_s), chosen: c.media_id === chosen, selected: c.media_id === pick }));
}

/** [이걸로 정하기]를 누를 수 있나: 눌러 둔 후보가 지금 정해진 것과 다를 때, 또는 아직 사람이 고르지 않았을 때 */
export function canCommitCover(cells: CoverCell[], picked: boolean): boolean {
  const sel = cells.find((c) => c.selected);
  return !!sel && (!sel.chosen || !picked);
}

/** 바꾸기 전에 확인 상자를 띄우나: 다른 후보로 바꾸고, 승인 대기·승인된 요청이 있을 때 */
export function needsCoverConfirm(cells: CoverCell[], v: Pick<RequestVideo, "approval_waiting">): boolean {
  const sel = cells.find((c) => c.selected);
  return !!sel && !sel.chosen && v.approval_waiting === true;
}

/** ?request=로 들어왔을 때 표시할 카드: 그 의뢰의 가장 최근 영상 번호, 목록에 없으면 null */
export function focusDraftId(items: Pick<RequestVideo, "post_draft_id" | "request_id">[], requestId: string | null): string | null {
  if (!requestId) return null;
  return items.find((v) => v.request_id === requestId)?.post_draft_id ?? null;
}

/** 유튜브 칸을 보여 줄 수 있나: AI 표시를 입힌 영상(재생되는 영상)만 올릴 수 있다 */
export const canUploadToYoutube = (v: Pick<RequestVideo, "labeled" | "preview_path" | "status">): boolean => videoPane(v).kind === "play";

/** 대화방 의뢰 카드의 "어디에 올릴까요?" 버튼 (11/26). 채널마다 지금 상태 한 마디(아직 안 했으면 null) */
export type PublishChannel = "youtube" | "instagram";
const CHANNEL_STATUS_KO: Record<string, string> = {
  draft: "초안", awaiting_approval: "사람 승인 대기", uploading: "올리는 중", posting: "올리는 중",
  done: "올림", failed: "실패", rejected: "거절됨", blocked: "막힘",
};
export const PUBLISH_CHANNELS: { key: PublishChannel; label: string }[] = [
  { key: "youtube", label: "유튜브" }, { key: "instagram", label: "인스타그램 (릴스)" },
];
export function publishStatusKo(v: Pick<RequestVideo, "youtube" | "instagram">, channel: PublishChannel): string | null {
  const status = (channel === "youtube" ? v.youtube : v.instagram)?.status;
  return status ? CHANNEL_STATUS_KO[status] ?? status : null;
}
