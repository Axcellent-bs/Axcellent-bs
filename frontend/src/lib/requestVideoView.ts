// 의뢰로 만든 영상(홍보 영상 의뢰 wr_… → SNS 초안 reel)의 순수 계산. node --test (requestVideoView.test.ts).
// 환경 값(env)에 기대지 않는 것만 둔다. 읽기·주소는 lib/requestVideos.ts.
// 화면은 제작 기록(/marketing/records?view=videos)의 '의뢰로 만든 영상' 탭에 둔다(11/23, 제작 기록과 같이 본다).
// 영상마다 회사 유튜브 올리기(사람 승인 C)를 할 수 있다. Instagram 게시 승인은 기획안 카드의 승인 패널 그대로다.
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

/** 유튜브 칸을 보여 줄 수 있나: AI 표시를 입힌 영상(재생되는 영상)만 올릴 수 있다 */
export const canUploadToYoutube = (v: Pick<RequestVideo, "labeled" | "preview_path" | "status">): boolean => videoPane(v).kind === "play";
