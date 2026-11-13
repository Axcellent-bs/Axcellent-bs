// 장애 사과·보상 글 초안 카드의 "이 초안으로 진행": 이미지 생성 확인 → 이미지 붙이기 → 게시 요청(승인 대기) 문구.
// 숫자·상태는 마케팅 서버가 준 값만 쓴다(지어내지 않는다). 순수 계산만 한다(`npm test`가 그대로 돈다).
// 첫 클릭은 게시 권한이 아니라 이미지 생성 진행 확인이다. 게시는 같은 카드의 승인 패널에서 사람이 승인해야 한다.
import { NGINX_BLOCKED_CODE, NGINX_BLOCKED_MESSAGE } from "./apiError.ts";
import {
  estimateBlockKo,
  mediaResultKo,
  publishResultKo,
  PUBLISH_EXPIRY_MIN,
  type BriefMediaAnswer,
  type MediaEstimate,
  type MediaResult,
  type PreparedMedia,
  type PublishAnswer,
  type PublishResult,
  type PublishState,
} from "./briefMedia.ts";

/** 카드 진행 상태(마케팅 `list_outage_sns_drafts`의 `media_flow`). 옛 서버는 이 칸이 없다 */
export interface OutageMediaFlow {
  draft_id: string;
  /** not_started | draft_ready(이미지 준비됨) | check_rejected */
  stage: "not_started" | "draft_ready" | "check_rejected" | string;
  /** true일 때만 "이 초안으로 진행" 버튼을 둔다 */
  can_proceed: boolean;
  reason_code: string | null;
  estimate: MediaEstimate | null;
  prepared: PreparedMedia | null;
  publish: PublishState | null;
}

export interface OutageMediaAnswer {
  status: string;
  outcome: string | null;
  output: { stage?: string; review_id?: string; media_flow?: OutageMediaFlow | null } | null;
}

/** 진행할 수 없는 이유(비용이 나기 전에 서버가 막은 것) */
const OUTAGE_BLOCK_KO: Record<string, string> = {
  already_merged: "다른 초안에 병합된 초안이라 진행할 수 없습니다. 대표 초안에서 진행해 주세요.",
  publish_history: "이미 게시 요청 이력이 있는 초안이라 새로 진행할 수 없습니다.",
  check_failed: "AI 표시·금지어 검사를 통과하지 못한 초안이라 진행할 수 없습니다.",
  video_not_supported: "영상 초안은 아직 진행할 수 없습니다. 이번에는 이미지만 지원합니다.",
  media_attached: "이미 이미지가 붙은 초안입니다.",
  not_outage_draft: "장애 사과·보상 초안이 아니라 진행할 수 없습니다.",
  draft_not_found: "초안을 찾을 수 없습니다. 새로고침해 주세요.",
  estimate_unavailable: "예상 비용을 계산할 수 없어 진행할 수 없습니다.",
  storage_error: "저장소를 읽을 수 없어 진행하지 않았습니다. 잠시 뒤 다시 시도해 주세요.",
  post_tools_unavailable: "서버에 이미지 처리 도구가 없어 만들지 않았습니다. 비용은 나지 않았습니다.",
};

export function outageBlockKo(code: string | null | undefined): string {
  return (code && OUTAGE_BLOCK_KO[code]) || estimateBlockKo(code ?? null);
}

/** 확인 단계에 덧붙일 고정 안내(비용 줄은 `estimateLines`) */
export const OUTAGE_CONFIRM_NOTES: string[] = [
  "초안 글은 바뀌지 않고, 사과 분위기의 이미지만 새로 만들어 붙입니다. 이미지에는 글자가 들어가지 않습니다.",
  "이미지 생성 후에도 게시는 사람 승인이 한 번 더 필요합니다.",
  `승인하지 않고 ${PUBLISH_EXPIRY_MIN}분이 지나면 게시 요청은 만료되고 이미지 비용만 남습니다.`,
  "만든 이미지에는 AI 표시가 들어갑니다.",
];

/** 이미지 만들기 응답을 화면 문구로. 서버 문장을 쓰지 않는다 */
export function outageMediaResultKo(answer: OutageMediaAnswer): MediaResult {
  const code = answer.outcome ?? "";
  if (answer.status === "failed" && OUTAGE_BLOCK_KO[code]) return { kind: "failed", text: OUTAGE_BLOCK_KO[code] };
  const shaped: BriefMediaAnswer = { status: answer.status, outcome: answer.outcome, output: { stage: answer.output?.stage, review_id: answer.output?.review_id } };
  const result = mediaResultKo(shaped);
  if (result.kind === "ready" && answer.outcome !== "already_prepared") return { kind: "ready", text: "이미지가 준비됐습니다. 이어서 게시 요청을 보냅니다." };
  return result;
}

/** 이미지가 준비됐으면 사람의 추가 클릭 없이 바로 게시 요청(승인 대기)을 보낸다. 그 밖에는 멈춘다 */
export const shouldRequestPublish = (answer: OutageMediaAnswer): boolean =>
  answer.status === "completed" && answer.output?.stage === "draft_ready";

const OUTAGE_PUBLISH_FAIL_KO: Record<string, string> = {
  not_outage_draft: "장애 사과·보상 초안이 아니라 게시를 요청할 수 없습니다.",
  draft_not_found: "초안을 찾을 수 없습니다. 새로고침해 주세요.",
  not_prepared: "게시할 수 있는 이미지가 아직 없습니다. 먼저 이 초안으로 진행해 주세요.",
};

/** 게시 요청 응답을 화면 문구로(승인 대기까지만. 게시되지 않았음을 항상 알린다) */
export function outagePublishResultKo(answer: PublishAnswer): PublishResult {
  const code = answer.outcome ?? "";
  if (answer.status === "failed" && OUTAGE_PUBLISH_FAIL_KO[code]) return { kind: "failed", text: OUTAGE_PUBLISH_FAIL_KO[code] };
  return publishResultKo(answer);
}

/** 연결 오류(HTTP 상태)를 화면 문구로. 서버가 보낸 문장은 쓰지 않는다. 앞단(nginx) 차단은 공용 안내를 쓴다 */
export function outageHttpErrorKo(status: number | undefined, code?: string | null): string {
  if (code === NGINX_BLOCKED_CODE) return NGINX_BLOCKED_MESSAGE;
  if (status === 401 || status === 403) return "진행할 권한이 없습니다. 사람 토큰 설정(또는 서버의 이 경로 허용)을 확인해 주세요.";
  if (status === 404) return "초안을 찾을 수 없거나 서버가 아직 이 기능을 지원하지 않습니다.";
  if (status === 400 || status === 422) return "요청을 서버가 받아들이지 않았습니다. 새로고침한 뒤 다시 시도해 주세요.";
  if (status === 502) return "마케팅 AI에 연결할 수 없습니다. 마케팅 서버가 켜져 있는지 확인해 주세요.";
  if (status === 504) return "시간 안에 끝나지 않았습니다. 이미지 생성이 계속되고 있을 수 있으니 1~2분 뒤 새로고침해 확인해 주세요. 다시 눌러도 같은 이미지를 두 번 만들지 않습니다.";
  return "진행하지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}
