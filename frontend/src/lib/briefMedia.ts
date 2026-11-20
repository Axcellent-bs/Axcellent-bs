// 기획안 "미디어 만들기"의 응답 모양과 화면 문구. 숫자·상태는 마케팅 서버가 준 값만 쓴다(지어내지 않는다).
// 이 파일은 순수 계산만 한다(화면·환경 값을 가져오지 않음): `npm test`(node --test)에서 그대로 돈다.
import { safePermalink } from "./manualPublish.ts";
import { NGINX_BLOCKED_CODE, NGINX_BLOCKED_MESSAGE } from "./apiError.ts";

export type UsageUnit = "seconds" | "images" | "tracks";
/** 사용량 한 건. 단위는 공급자 단위 그대로(영상 초·이미지 장·BGM 곡)이고, 토큰은 응답에 있을 때만 */
export interface MediaUsage { unit: UsageUnit; amount: number; input_tokens: number | null; output_tokens: number | null }
/** 그 종류의 이번 달 사용량·예산·남은 %. 고객사 키면 `applies`가 false라 예산이 없다 */
export interface MediaQuota { unit: UsageUnit; used: number; limit: number | null; remaining_pct: number | null; applies: boolean }

/** 지금 요청 초안으로 만들 때의 예상 사용량과 이번 달 예산. 마케팅이 조회 때 장부에서 계산한다(원화 없음) */
export interface MediaEstimate {
  status: "ok" | "blocked";
  reason_code: string | null;
  media_kind: string | null;
  /** 예상 사용량. 계산할 수 없으면 null */
  usage: MediaUsage | null;
  /** 이미 끝난 생성이라 다시 만들어도 사용량이 늘지 않는다 */
  settled: boolean;
  /** estimate = 요청한 만큼으로 센 예상, mock = 목 공급자(테스트 전용), ledger = 생성 장부의 확정값 */
  basis: "estimate" | "mock" | "ledger" | null;
  /** 이번 달 사용량·예산·남은 %. 옛 서버는 없다 */
  quota: MediaQuota | null;
  /** 영상만. 길이 입력용: 상한은 운영자가 서버 설정(MARKETING_VIDEO_MAX_DURATION_S)으로 정한 값이다 */
  video?: VideoEstimate | null;
  /** 영상 소리를 아직 정하지 않았을 때만. "배경음악 따로 만들기"의 추가 사용량(BGM 한 곡). 옛 서버는 없다 */
  bgm?: BgmEstimate | null;
}

export interface BgmEstimate {
  /** BGM 한 곡의 예상 사용량. 계산할 수 없으면 null */
  usage: MediaUsage | null;
  /** 이미 만든 곡이라 새 사용량이 없다 */
  settled: boolean;
  reason_code: string | null;
}

/** 사람이 영상 소리를 정한 기록(감사). 한 번 정하면 바꾸지 않는다 */
export interface AudioDecision {
  choice: "keep" | "bgm";
  decided_at: string | null;
  had_source_audio: boolean;
  confirmed_listened: boolean | null;
  bgm_usage?: MediaUsage | null;
}

/** 만든 영상의 소리 상태. 이미지·옛 기록은 없다 */
export interface AudioState {
  /** source = 공급자가 만든 소리를 그대로 둠, off = 무음(예전 설정), bgm_overlay = 예전 묶음 흐름 */
  mode: string | null;
  /** 공급자가 만든 소리가 파일에 들어 있다 */
  has_source_audio: boolean;
  /** clip_concat_muted = 이어 붙인 영상이라 소리를 뺐다 */
  note?: string | null;
  /** 소리가 있어 사람이 듣고 정해야 게시 요청을 할 수 있다 */
  needs_decision: boolean;
  decision: AudioDecision | null;
}

export interface VideoEstimate {
  min_s: number;
  /** 운영자가 정한 길이 상한(초). 서버 설정값 */
  max_s: number;
  /** 코드의 절대 상한(초). 설정은 이 값을 넘을 수 없다 */
  absolute_max_s: number;
  /** 지금 요청 초안의 길이(초) */
  current_s: number | null;
  /** 이 길이(초)를 넘기면 시작 전에 경고창을 띄운다(서버 설정 MARKETING_VIDEO_WARN_DURATION_S). 화면 안내용이며 안전 관문이 아니다. 옛 서버는 없다 */
  warn_duration_s?: number;
  /** 길이(초) → 이어 붙일 클립 수. 만들 수 없는 길이는 없다. 옛 서버는 없다 */
  clips_by_duration?: Record<string, number>;
}

/** 지금 요청 초안으로 만든 미디어 초안(AI 표시를 입히고 검사까지 한 결과) */
export interface PreparedMedia {
  post_draft_id: string | null;
  stage: "draft_ready" | "check_rejected" | string;
  /** image | reel */
  media_kind: string | null;
  asset_ref: string | null;
  asset_sha256: string | null;
  /** 만들 때 센 사용량. 옛 기록은 없다 */
  usage?: MediaUsage | null;
  /** 파일 위에 입힌 AI 표시 문구 */
  label_text: string | null;
  check: { verdict: string | null; publishable: boolean | null; problems: string[] };
  prepared_at: string | null;
  /** 영상만. 소리 상태와 사람의 결정. 옛 서버·이미지는 없다 */
  audio?: AudioState | null;
}

export interface BriefMediaOutput {
  stage?: string;
  reason?: string;
  review_id?: string;
  hold_reason?: string;
  sns_handoff?: { prepared?: PreparedMedia | null; media_estimate?: MediaEstimate | null };
}
export interface BriefMediaAnswer { status: string; outcome: string | null; output: BriefMediaOutput | null }

const UNIT_KO: Record<UsageUnit, string> = { seconds: "초", images: "장", tracks: "곡" };
const amountText = (n: number, unit: UsageUnit) => `${n.toLocaleString("ko-KR")}${UNIT_KO[unit]}`;

/** 사용량 한 줄: 15초, 1장. 토큰이 있으면(Gemini) 같이 보여 준다. 원화는 없다 */
export function usageKo(u: MediaUsage | null | undefined): string {
  if (!u) return "사용량을 읽을 수 없음";
  const tokens = u.input_tokens != null || u.output_tokens != null
    ? ` (토큰 입력 ${u.input_tokens?.toLocaleString("ko-KR") ?? "?"} · 출력 ${u.output_tokens?.toLocaleString("ko-KR") ?? "?"})`
    : "";
  return `${amountText(u.amount, u.unit)}${tokens}`;
}

/** 이번 달 예산 한 줄. 고객사 키면 예산을 안 건다고 밝히고, 예산이 없으면 비율을 지어내지 않는다 */
export function quotaKo(q: MediaQuota | null | undefined): string {
  if (!q) return "이번 달 사용량을 읽을 수 없음";
  if (!q.applies) return `이번 달 사용량 ${amountText(q.used, q.unit)} (고객사 키라 예산을 걸지 않습니다)`;
  if (q.limit === null) return `이번 달 사용량 ${amountText(q.used, q.unit)} / 월 예산 설정을 읽을 수 없음`;
  return `이번 달 사용량 ${amountText(q.used, q.unit)} / 월 예산 ${amountText(q.limit, q.unit)}${q.remaining_pct !== null ? ` · 남은 사용량 ${q.remaining_pct}%` : ""}`;
}

/** 만들 수 없는 이유(공급자를 부르기 전에 서버가 막은 것) */
const BLOCK_KO: Record<string, string> = {
  fields_missing: "미디어 요청에 미정 필드가 남아 있습니다. 받은 기획안 탭에서 필드를 정해 주세요.",
  carousel_items_undecided: "캐러셀은 장마다 무엇을 그릴지 정하는 단계가 아직 없어 여기서 만들 수 없습니다. 받은 기획안 탭에서 종류를 이미지나 영상으로 바꿔 주세요.",
  bgm_fields_required: "서버가 배경음 모드라 영상에 배경음 설정이 필요합니다. 이 화면에서는 아직 정할 수 없습니다.",
  unknown_media_kind: "미디어 종류를 알 수 없습니다. 받은 기획안 탭에서 종류를 다시 저장해 주세요.",
  video_max_duration_invalid: "서버의 영상 길이 상한 설정(MARKETING_VIDEO_MAX_DURATION_S)이 1~90초 숫자가 아니라 만들 수 없습니다. 운영자가 설정을 고쳐야 합니다.",
  duration_over_limit: "고른 길이가 운영자가 정한 영상 길이 상한을 넘어 만들 수 없습니다.",
  duration_under_min: "고른 길이가 공급자의 최소 길이보다 짧아 만들 수 없습니다.",
  long_video_unsupported: "이 영상 공급자는 한 번에 만들 수 있는 길이를 넘는 영상을 이어 붙일 수 없어 만들 수 없습니다. 더 짧은 길이를 고르세요.",
  quota_exceeded: "이달 사용량 예산(우리 키)을 넘게 되어 만들 수 없습니다.",
  quota_not_configured: "서버에 이 종류의 월 사용량 예산이 설정되지 않아 만들 수 없습니다.",
  model_not_configured: "서버에 미디어 모델이 설정되지 않아 만들 수 없습니다.",
  provider_adapter_missing: "서버에 설정된 미디어 공급자를 쓸 수 없습니다.",
  prompt_rejected: "안전 규칙에 걸리는 필드 값이 있어 만들 수 없습니다. 받은 기획안 탭에서 필드를 바꿔 주세요.",
  storage_error: "사용량 장부를 읽을 수 없어 만들 수 없습니다.",
};

/** 만들기를 눌렀는데 만들지 못한 이유 */
const FAIL_KO: Record<string, string> = {
  ...BLOCK_KO,
  not_handed: "아직 SNS·미디어로 넘기지 않은 기획안입니다.",
  in_progress: "이미 만드는 중입니다. 잠시 뒤 새로고침해 주세요.",
  forbidden_phrase: "기획안 글에 참여를 조건으로 거는 말이 있어 초안을 만들 수 없습니다.",
  caption_too_long: "캡션이 너무 길어 초안을 만들 수 없습니다.",
  post_tools_unavailable: "서버에 영상·이미지 처리 도구가 없어 만들지 않았습니다. 공급자는 부르지 않았습니다.",
  ffmpeg_not_found: "서버에 영상 처리 도구가 없어 만들지 않았습니다. 공급자는 부르지 않았습니다.",
  media_dir_not_configured: "서버에 미디어 폴더가 설정되지 않아 만들지 않았습니다.",
  media_dir_mismatch: "서버의 미디어 폴더 설정이 맞지 않아 만들지 않았습니다.",
  convert_failed: "미디어는 만들었지만 게시 규격으로 바꾸지 못했습니다. 다시 누르면 새 사용량 없이 이어서 시도합니다.",
  label_failed: "미디어는 만들었지만 AI 표시를 입히지 못했습니다. 다시 누르면 새 사용량 없이 이어서 시도합니다.",
  execute_rejected: "안전 장치가 이 요청을 거절했습니다.",
  execute_failed: "안전 장치에서 요청을 처리하지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
  rejected_by_review: "검토에서 거절되어 만들지 않았습니다.",
  TIMEOUT: "안전 장치 확인이 제때 끝나지 않았습니다. 잠시 뒤 다시 시도해 주세요.",
};

export function estimateBlockKo(code: string | null): string {
  return (code && BLOCK_KO[code]) || `지금은 만들 수 없습니다${code ? ` (${code})` : ""}.`;
}

const BASIS_KO: Record<string, string> = {
  estimate: "요청한 만큼으로 센 예상",
  mock: "목 공급자(테스트 전용)",
  ledger: "생성 장부의 확정값",
};

/** 확인 단계에 보여 줄 사용량 줄. 서버가 준 값만 쓰고 없는 값은 "읽을 수 없음"으로 둔다(원화 없음) */
export function estimateLines(e: MediaEstimate, seconds?: number | null): string[] {
  const lines: string[] = [];
  const basis = e.basis ? ` (${BASIS_KO[e.basis] ?? e.basis})` : "";
  if (e.video && seconds != null && !e.settled && durationClips(e.video, seconds) != null) lines.push(`영상 ${seconds}초 사용 예정${basis}`);
  else if (e.usage == null) lines.push("예상 사용량을 계산할 수 없습니다.");
  else if (e.settled) lines.push(`이미 만든 미디어가 있어 다시 만들어도 사용량이 늘지 않습니다 (만들 때 ${usageKo(e.usage)}).`);
  else lines.push(`예상 사용량 ${usageKo(e.usage)}${basis}`);
  lines.push(quotaKo(e.quota));
  return lines;
}

/** 고른 길이로 이어 붙일 클립 수. 서버 표에 없으면(만들 수 없는 길이) null. 옛 서버는 표가 없어 null */
export function durationClips(v: VideoEstimate, seconds: number): number | null {
  const clips = v.clips_by_duration?.[String(seconds)];
  return typeof clips === "number" ? clips : null;
}

/** 입력한 길이를 정수로 바꿔 서버가 알려 준 범위(최소~운영자 상한)로 맞춘다. 숫자가 아니면 null */
export function clampDuration(v: VideoEstimate, raw: number): number | null {
  if (!Number.isFinite(raw)) return null;
  return Math.min(v.max_s, Math.max(v.min_s, Math.round(raw)));
}

/** 이 길이로는 만들 수 없는 이유(서버가 준 클립 표·월 예산과 비교). 만들 수 있으면 null */
export function durationProblemKo(e: MediaEstimate, seconds: number): string | null {
  if (!e.video || e.settled) return null;
  if (e.video.clips_by_duration && durationClips(e.video, seconds) == null) return `${seconds}초 길이는 이 공급자로 만들 수 없습니다. 다른 길이를 고르세요.`;
  const q = e.quota;
  if (q && q.applies && q.limit !== null && q.used + seconds > q.limit) {
    return `${seconds}초는 이달 영상 예산(${amountText(q.limit, q.unit)})을 넘게 되어 만들 수 없습니다. 남은 사용량은 ${amountText(Math.max(0, q.limit - q.used), q.unit)}입니다.`;
  }
  return null;
}

/** 긴 영상 경고창을 띄워야 하는 길이인가. 서버가 경고 길이를 알려 줬고 고른 길이가 그보다 길 때만 true(같으면 경고 없음) */
export function needsLongWarning(v: VideoEstimate | null | undefined, seconds: number | null | undefined): boolean {
  if (!v || seconds == null || typeof v.warn_duration_s !== "number") return false;
  return seconds > v.warn_duration_s;
}

export interface LongWarning { title: string; lines: string[]; notes: string[]; confirmLabel: string; cancelLabel: string }

/** 경고창 문구. 숫자는 서버가 준 값만 쓰고, 없으면 읽을 수 없다고 적는다(지어내지 않는다) */
export function longWarningKo(e: MediaEstimate, seconds: number): LongWarning {
  const v = e.video;
  const clips = v?.clips_by_duration?.[String(seconds)];
  const lines = [
    `선택한 길이: ${seconds}초${typeof v?.warn_duration_s === "number" ? ` (경고 기준 ${v.warn_duration_s}초보다 깁니다)` : ""}`,
    typeof clips === "number" ? `이어 붙일 클립 수: ${clips}개` : "이어 붙일 클립 수를 읽을 수 없음",
    `사용 예정: ${amountText(seconds, "seconds")}${e.basis ? ` (${BASIS_KO[e.basis] ?? e.basis})` : ""}`,
    quotaKo(e.quota),
  ];
  return {
    title: "긴 영상을 만들까요?",
    lines,
    notes: [
      "길이가 길수록 장면이나 인물이 이어지지 않을 수 있습니다. 짧은 영상을 여러 개 만들어 이어 붙이기 때문입니다.",
      "사용량은 길이만큼 늘어납니다.",
      "우리 키로 만들 때 이달 영상 예산을 넘으면 만들기 전에 막힙니다.",
    ],
    confirmLabel: "계속",
    cancelLabel: "길이 줄이기",
  };
}

/**
 * '시작'을 눌렀을 때의 관문(화면 안내일 뿐 안전 관문이 아니다: 서버가 길이 상한·예산을 따로 막는다).
 * 경고 길이를 넘으면 `ask`로 사람 확인을 받고, 확인하지 않으면 `send`를 부르지 않는다.
 * 부른 쪽이 `send(confirmedLong)`으로 요청을 보낸다. 보냈으면 true.
 */
export async function gateLongStart(
  e: MediaEstimate | null | undefined,
  seconds: number | null | undefined,
  ask: () => Promise<boolean>,
  send: (confirmedLong: boolean) => Promise<void>,
): Promise<boolean> {
  if (!needsLongWarning(e?.video, seconds)) { await send(false); return true; }
  if (!(await ask())) return false;
  await send(true);
  return true;
}

/** 길이 입력 옆 안내. 상한은 운영자가 정한 값이라는 점을 밝힌다 */
export const durationNoteKo = (v: VideoEstimate): string =>
  `영상 길이 상한 ${v.max_s}초는 운영자가 서버 설정(MARKETING_VIDEO_MAX_DURATION_S)으로 정한 값입니다(최대 ${v.absolute_max_s}초까지만 정할 수 있습니다). 공급자가 한 번에 만드는 길이보다 길면 여러 클립을 이어 붙여 만들고, 길이만큼 사용량이 늘어납니다.`;

export const isReel = (kind: string | null | undefined) => kind === "video" || kind === "reel";
/** 걸리는 시간 안내(영상은 생성이 길다) */
export const waitKo = (kind: string | null | undefined) =>
  isReel(kind) ? "영상은 만드는 데 약 1분 걸립니다. 이 화면을 닫지 말고 기다려 주세요." : "이미지를 만들고 있습니다. 잠시 기다려 주세요.";

export type MediaResultKind = "ready" | "check_rejected" | "held" | "working" | "failed";
export interface MediaResult { kind: MediaResultKind; text: string }

/** 만들기 응답을 화면 문구로. 서버 오류 문장을 그대로 쓰지 않는다 */
export function mediaResultKo(answer: BriefMediaAnswer): MediaResult {
  const stage = answer.output?.stage;
  if (answer.status === "completed" && stage === "draft_ready") {
    return { kind: "ready", text: answer.outcome === "already_prepared" ? "이미 준비된 초안이 있어 새로 만들지 않았습니다." : "초안이 준비됐습니다." };
  }
  if (answer.status === "completed" && stage === "check_rejected") {
    return { kind: "check_rejected", text: "미디어는 만들었지만 초안 검사에 걸렸습니다. 이 초안은 게시할 수 없습니다." };
  }
  if (answer.status === "input-required") {
    return { kind: "held", text: "안전 장치가 이 생성을 사람 확인으로 보류했습니다. 아래 '사람 결정 대기' 목록에 나오며, 승인된 뒤 다시 눌러 주세요. 아직 공급자는 부르지 않았습니다." };
  }
  if (answer.status === "working") return { kind: "working", text: "안전 장치가 확인하는 중입니다. 잠시 뒤 다시 눌러 주세요." };
  const code = answer.outcome ?? "";
  return { kind: "failed", text: FAIL_KO[code] ?? `미디어를 만들지 못했습니다. 잠시 뒤 다시 시도해 주세요${code ? ` (${code})` : ""}.` };
}

/** 연결 오류(HTTP 상태)를 화면 문구로. 서버가 보낸 문장은 쓰지 않는다 */
export function mediaHttpErrorKo(status: number | undefined, code?: string | null): string {
  if (code === NGINX_BLOCKED_CODE) return NGINX_BLOCKED_MESSAGE;
  if (status === 401 || status === 403) return "미디어를 만들 권한이 없습니다. 사람 토큰 설정(또는 서버의 이 경로 허용)을 확인해 주세요.";
  if (status === 404) return "기획안을 찾을 수 없거나 서버가 아직 이 기능을 지원하지 않습니다.";
  if (status === 400 || status === 422) return "요청을 서버가 받아들이지 않았습니다. 새로고침한 뒤 다시 시도해 주세요.";
  if (status === 502) return "마케팅 AI에 연결할 수 없습니다. 마케팅 서버가 켜져 있는지 확인해 주세요.";
  if (status === 504) return "시간 안에 끝나지 않았습니다. 생성이 계속되고 있을 수 있으니 1~2분 뒤 새로고침해 확인해 주세요. 다시 눌러도 같은 미디어를 두 번 만들지 않습니다.";
  return "미디어를 만들지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}

// ---- 게시 요청(C 등급 사람 승인 대기 건 만들기) ------------------------------------------
// 이 화면은 게시하지 않는다. 요청하면 서버가 승인 대기 건(10분 만료)만 만들고, 사람이 승인 화면에서
// 진행을 눌러야 게시된다. 글·파일은 서버가 보관본에서 읽는다(화면은 기획안 번호만 보낸다).

/** rejected = 사람이 거절, expired = 승인 시간(10분)이 지나 자동 거절(사람이 다시 요청할 수 있다) */
export type PublishStatus = "not_requested" | "held" | "publishing" | "manual_pending" | "published" | "rejected" | "expired" | "failed" | "unknown";

/** 서버가 승인 대기 저장소에서 읽어 준 게시 요청 상태. 옛 서버는 이 칸이 없다 */
export interface PublishState {
  status: PublishStatus;
  /** true일 때만 "게시 요청" 버튼을 둔다 */
  can_request: boolean;
  reason_code: string | null;
  review_id: string | null;
  failure_code: string | null;
  permalink: string | null;
  /** 게시될 글(서버 보관본 그대로) */
  caption: string | null;
  // 카드 안 승인 패널용. 옛 서버는 이 칸들이 없다(없으면 패널은 있는 만큼만 보여 준다)
  proposal_id?: string;
  /** 안전 장치 승인 마감 시각(ISO). 아직 모르면 null */
  expires_at?: string | null;
  /** 대시보드 API 상대 경로. 앞에 서버 주소를 붙여 쓴다 */
  preview_url?: string | null;
  preview_kind?: "image" | "video" | null;
  ai_label?: string | null;
  /** rejected일 때: human = 사람이 거절, unknown = 만료와 구분하지 못하는 옛 기록 */
  rejected_cause?: "human" | "unknown" | null;
  attempt?: number;
  /** true일 때만 "다시 요청" 버튼을 둔다(만료로 닫힌 건) */
  can_retry?: boolean;
  retry_reason_code?: string | null;
  // 수동 게시(자동 게시가 꺼진 동안 사람이 직접 올리고 링크를 기록). 해당할 때만 온다
  post_draft_id?: string;
  /** 승인됐고 사람이 올린 뒤 링크를 기록하기를 기다린다(status=manual_pending) */
  manual_pending?: boolean;
  /** 사람이 직접 올린 게시(링크를 사람이 기록했다) */
  manual?: boolean;
  posted_by?: string | null;
  published_at?: string | null;
  /** 승인 대기 중인데 자동 게시가 꺼져 있다(승인되면 수동 게시로 이어진다) */
  auto_publish_off?: boolean;
}

export interface PublishAnswer {
  status: string;
  outcome: string | null;
  output: { publish?: PublishState | null; review_id?: string; pause_reason?: string; auto_publish_off?: boolean; manual_available?: boolean; manual_pending?: boolean } | null;
}

export const PUBLISH_EXPIRY_MIN = 10;

/** 게시 요청을 할 수 없거나 실패한 이유(코드별 문구). 서버 문장은 쓰지 않는다 */
const PUBLISH_FAIL_KO: Record<string, string> = {
  not_handed: "아직 SNS·미디어로 넘기지 않은 기획안입니다.",
  not_prepared: "게시할 수 있는 미디어 초안이 없습니다. 먼저 미디어를 만들어 주세요.",
  audio_decision_required: "영상에 AI가 만든 소리가 들어 있습니다. 소리를 듣고 '이 영상 그대로 사용'이나 '배경음악 따로 만들기'를 먼저 골라 주세요.",
  check_not_passed: "초안 검사를 통과하지 못해 게시를 요청할 수 없습니다.",
  media_kind_unsupported: "캐러셀은 이 화면에서 게시를 요청할 수 없습니다.",
  draft_missing: "서버에서 초안을 찾을 수 없어 요청하지 않았습니다.",
  draft_changed: "초안 내용이 만든 뒤 바뀌어 요청하지 않았습니다. 미디어를 다시 만들어 주세요.",
  daily_limit_reached: "24시간 게시 상한에 도달해 지금은 요청할 수 없습니다. 나중에 다시 시도해 주세요.",
  publish_halted: "게시가 중단된 상태라 요청할 수 없습니다. 담당자 확인이 필요합니다.",
  limits_config_error: "서버의 게시 한도 설정을 읽을 수 없어 요청하지 않았습니다.",
  publish_paused: "자동 게시가 꺼져 있습니다. 초안과 미디어 생성은 계속 쓸 수 있습니다. 담당자 확인이 필요합니다.",
  publish_control_error: "서버의 자동 게시 스위치를 읽을 수 없어 안전하게 요청하지 않았습니다. 초안과 미디어 생성은 계속 쓸 수 있습니다. 담당자 확인이 필요합니다.",
  adapter_unavailable: "서버에 Instagram 게시 설정이 없어 요청하지 않았습니다. 승인을 받아도 게시할 수 없는 상태입니다.",
  execute_rejected: "안전 장치가 이 게시 요청을 거절했습니다.",
  execute_failed: "안전 장치에서 요청을 처리하지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
  storage_error: "게시 대기 저장소를 읽을 수 없어 요청하지 않았습니다. 게시되지 않았습니다.",
  invalid_input: "서버가 요청을 받아들이지 않았습니다. 새로고침한 뒤 다시 시도해 주세요.",
  brief_not_found: "기획안을 찾을 수 없습니다.",
  retry_not_expired: "승인 시간이 지나 닫힌 건만 다시 요청할 수 있습니다.",
  retry_rejected_by_human: "사람이 거절한 건이라 다시 요청할 수 없습니다.",
  retry_cause_unknown: "거절인지 만료인지 구분할 수 없는 옛 기록이라 다시 요청할 수 없습니다. 담당자에게 문의해 주세요.",
  retry_already_published: "같은 내용이 이미 게시돼 다시 요청할 수 없습니다.",
  retry_other_held: "이 내용의 다른 승인 대기 건이 있어 다시 요청할 수 없습니다. 그 건을 먼저 처리해 주세요.",
  TIMEOUT: "서버 응답이 늦어 결과를 알 수 없습니다. 새로고침해 상태를 확인해 주세요. 다시 눌러도 같은 요청을 두 번 만들지 않습니다.",
};

/** 승인 뒤 게시가 실패한 이유(대기 건의 failure_code) */
const PUBLISH_FAILURE_KO: Record<string, string> = {
  content_changed: "승인 뒤 내용이 바뀌어 게시하지 않았습니다. 새 승인이 필요합니다.",
  review_rejected: "사람이 거절했거나 승인 시간이 지났습니다.",
  review_timeout: "승인 시간이 지나 자동으로 닫혔습니다.",
  daily_limit_reached: "24시간 게시 상한에 도달해 게시하지 못했습니다.",
  publish_halted: "게시가 중단돼 게시하지 못했습니다.",
  publish_paused: "게시 도중 자동 게시가 꺼져 자동으로 올리지 않았습니다. 이 건은 닫혔습니다. 새로 요청하면 수동 게시로 안내합니다.",
  publish_control_error: "자동 게시 스위치를 읽을 수 없어 안전하게 게시하지 않았습니다. 담당자 확인이 필요합니다.",
  adapter_unavailable: "서버에 Instagram 게시 설정이 없어 게시하지 못했습니다.",
};

export const publishReasonKo = (code: string | null | undefined): string =>
  (code && PUBLISH_FAIL_KO[code]) || `게시를 요청하지 못했습니다. 잠시 뒤 다시 시도해 주세요${code ? ` (${code})` : ""}.`;

export interface PublishLine {
  tone: "info" | "ok" | "warn" | "bad";
  text: string;
  /** 게시된 건의 Instagram 링크(형식이 맞는 값만). 있으면 화면이 새 탭 링크로 건다 */
  link?: string;
}

/** 서버가 준 게시 요청 상태를 화면 한 줄로. 요청 전(not_requested)이면 null */
export function publishStatusLine(p: PublishState | null | undefined): PublishLine | null {
  if (!p) return null;
  switch (p.status) {
    case "held":
      return { tone: "info", text: `게시 승인 대기 중입니다. 사람이 ${PUBLISH_EXPIRY_MIN}분 안에 승인해야 게시되고, 시간이 지나면 만료됩니다.${p.auto_publish_off ? " 자동 게시가 꺼져 있어 승인되면 사람이 직접 올리는 수동 게시로 이어집니다." : ""}` };
    case "manual_pending":
      return { tone: "info", text: "승인됐습니다. 자동 게시가 꺼져 있어 사람이 Instagram에 직접 올린 뒤 게시물 링크를 기록해야 게시 완료로 닫힙니다." };
    case "publishing":
      return { tone: "info", text: "승인됐습니다. Instagram에 게시하는 중입니다." };
    case "published": {
      const link = safePermalink(p.permalink) ?? undefined;
      if (p.manual) return { tone: "ok", text: `직접 게시됨${p.posted_by ? ` (${p.posted_by} 기록)` : ""}. 링크는 사람이 입력한 값이며 시스템이 확인하지 않았습니다.`, link };
      return { tone: "ok", text: "Instagram에 게시됐습니다.", link };
    }
    case "rejected":
      return { tone: "warn", text: p.rejected_cause === "human"
        ? "사람이 거절했습니다. 이 초안은 다시 요청할 수 없습니다."
        : "거절됐거나 승인 시간이 지나 만료됐습니다(옛 기록이라 구분할 수 없습니다). 이 초안은 다시 요청할 수 없습니다." };
    case "expired":
      return { tone: "warn", text: `승인 시간(${PUBLISH_EXPIRY_MIN}분)이 지나 만료됐습니다. 게시되지 않았습니다.${p.can_retry ? " 필요하면 사람이 다시 요청할 수 있습니다." : ""}` };
    case "failed":
      return { tone: "bad", text: `게시되지 않았습니다. ${(p.failure_code && PUBLISH_FAILURE_KO[p.failure_code]) || `담당자 확인이 필요합니다${p.failure_code ? ` (${p.failure_code})` : ""}.`}` };
    case "unknown":
      return { tone: "warn", text: "게시 요청 상태를 읽을 수 없습니다. 잠시 뒤 새로고침해 주세요." };
    default:
      return null;
  }
}

export type PublishResultKind = "requested" | "already" | "failed";
export interface PublishResult { kind: PublishResultKind; text: string }

/** 게시 요청 응답을 화면 문구로 */
export function publishResultKo(answer: PublishAnswer): PublishResult {
  if (answer.status === "completed" && answer.outcome === "requested") {
    const manual = answer.output?.auto_publish_off ? " 자동 게시가 꺼져 있어 승인 뒤 사람이 직접 올리고 링크를 기록하는 수동 게시로 이어집니다." : "";
    return { kind: "requested", text: `게시 요청을 보냈습니다. 아직 게시되지 않았습니다. 사람이 ${PUBLISH_EXPIRY_MIN}분 안에 승인 화면에서 진행을 눌러야 합니다.${manual}` };
  }
  if (answer.status === "completed" && answer.outcome === "already_requested") {
    return { kind: "already", text: "이미 요청된 건입니다. 새로 요청하지 않았습니다." };
  }
  const base = publishReasonKo(answer.outcome);
  // 꺼 둔 사유는 담당자가 적은 짧은 글이다(비밀값 없음). 있을 때만 붙인다
  const why = answer.outcome === "publish_paused" ? answer.output?.pause_reason : null;
  return { kind: "failed", text: why ? `${base} 꺼 둔 사유: ${why}` : base };
}

/** 연결 오류(HTTP 상태)를 화면 문구로. 서버가 보낸 문장은 쓰지 않는다 */
export function publishHttpErrorKo(status: number | undefined, code?: string | null): string {
  if (code === NGINX_BLOCKED_CODE) return NGINX_BLOCKED_MESSAGE;
  if (status === 401 || status === 403) return "게시를 요청할 권한이 없습니다. 사람 토큰 설정(또는 서버의 이 경로 허용)을 확인해 주세요.";
  if (status === 404) return "기획안을 찾을 수 없거나 서버가 아직 이 기능을 지원하지 않습니다.";
  if (status === 400 || status === 422) return "요청을 서버가 받아들이지 않았습니다. 새로고침한 뒤 다시 시도해 주세요.";
  if (status === 502) return "마케팅 AI에 연결할 수 없습니다. 마케팅 서버가 켜져 있는지 확인해 주세요.";
  if (status === 504) return "시간 안에 끝나지 않았습니다. 새로고침해 상태를 확인해 주세요. 다시 눌러도 같은 요청을 두 번 만들지 않습니다.";
  return "게시를 요청하지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}

// ---- 카드 안 승인 패널 ------------------------------------------------------------------------

/** 마감까지 남은 초. 마감을 모르면 null(지어내지 않는다) */
export function remainingSeconds(expiresAt: string | null | undefined, nowMs: number): number | null {
  if (!expiresAt) return null;
  const t = Date.parse(expiresAt);
  return Number.isNaN(t) ? null : Math.floor((t - nowMs) / 1000);
}

/** 남은 시간 문구(1분 단위). 마감을 모르면 "남은 시간을 알 수 없음" */
export function remainingKo(seconds: number | null): string {
  if (seconds == null) return "남은 시간을 알 수 없습니다";
  if (seconds <= 0) return "승인 시간이 지났습니다";
  if (seconds < 60) return "1분 미만 남음";
  return `약 ${Math.ceil(seconds / 60)}분 남음`;
}

/** 승인 패널의 버튼을 켤지. 내용 확인 체크·승인자 이름·마감 전이어야 한다(마감을 모르면 서버 판단에 맡긴다) */
export function canDecideNow(o: { tokenSet: boolean; reviewed: boolean; approver: string | null; remaining: number | null; busy: boolean }): boolean {
  return o.tokenSet && o.reviewed && !!o.approver && !o.busy && !(o.remaining != null && o.remaining <= 0);
}

/** 승인·거절 호출 오류를 화면 문구로. 서버 문장은 쓰지 않는다 */
export function decideErrorKo(status: number | undefined, code: string | null | undefined): string {
  if (code === "SELF_APPROVAL") return "요청한 본인은 승인할 수 없습니다. 다른 사람이 눌러 주세요.";
  if (status === 409 || code === "ALREADY_DECIDED") return "이미 결정됐거나 승인 시간이 지난 건입니다. 새로고침해 확인해 주세요.";
  if (status === 404) return "보류 건을 찾을 수 없습니다. 승인 시간이 지나 정리됐을 수 있습니다. 새로고침해 확인해 주세요.";
  if (status === 401 || status === 403) return "사람 권한이 없어 처리하지 못했습니다. 사람 토큰 설정을 확인해 주세요.";
  if (status === 0) return "안전 장치 서버에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
  return "처리하지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}

/** 승인 화면 링크: 해당 건을 강조한다(`?review=<id>`). 링크를 여는 것만으로는 아무것도 승인되지 않는다 */
export const approvalLink = (reviewId: string | null | undefined): string =>
  reviewId ? `/marketing/campaign?review=${encodeURIComponent(reviewId)}` : "/marketing/campaign";

// ---- 영상 소리 선택(이 영상 그대로 사용 / 배경음악 따로 만들기) -----------------------------
// 영상은 공급자가 만든 소리를 그대로 두고 한 번만 만든다. 확인 단계에서 사람이 소리를 듣고 한 번 정한다.
// 비용·결정 기록은 서버가 준 값만 쓴다. 이 선택은 게시하지 않는다(게시는 C 등급 사람 승인).

export type AudioChoice = "keep" | "bgm";

/** 아직 소리를 정하지 않은 영상인가(결정 칸이 있고 비어 있을 때) */
export function audioPending(p: PreparedMedia | null | undefined): boolean {
  return !!p && isReel(p.media_kind) && !!p.audio && p.audio.mode === "source" && !p.audio.decision;
}

/** 결정할 수 있는 상태인가: 게시 요청이 이미 올라간 뒤에는 서버가 거절하므로 버튼을 두지 않는다 */
export function canChooseAudio(p: PreparedMedia | null | undefined, publishStatus: string | null | undefined): boolean {
  return audioPending(p) && (!publishStatus || publishStatus === "not_requested");
}

/** 영상 소리 안내: 소리가 있으면 끝까지 듣고 고르게 한다 */
export function audioIntroKo(a: AudioState): string[] {
  if (a.has_source_audio) {
    return [
      "이 영상에는 AI가 만든 소리(음악·목소리 등)가 들어 있습니다. 소리를 끝까지 들어 보고 고르세요.",
      "소리 안에 사람 목소리나 알아볼 만한 곡이 섞여 있으면 '배경음악 따로 만들기'를 고르세요. 소리도 AI가 만든 것이라 게시물에 AI 표시가 붙습니다.",
    ];
  }
  return [
    a.note === "clip_concat_muted"
      ? "여러 클립을 이어 붙인 영상이라 소리를 뺐습니다(클립마다 소리가 달라 이음새가 어긋나기 때문입니다)."
      : "이 영상에는 소리가 없습니다(영상 공급자가 소리를 만들지 않았습니다).",
    "소리 없이 써도 되고, 배경음악을 따로 만들어 넣을 수도 있습니다.",
  ];
}

/** "배경음악 따로 만들기" 추가 사용량 안내. 서버가 준 값만 쓴다(원화 없음) */
export function bgmUsageKo(b: BgmEstimate | null | undefined): string {
  if (!b) return "추가 사용량을 서버가 알려 주지 않았습니다.";
  if (b.settled) return "이미 만든 배경음악이 있어 사용량이 늘지 않습니다.";
  if (b.usage == null) return "추가 사용량을 계산할 수 없습니다.";
  return `추가 사용량 ${usageKo(b.usage)}. 영상은 다시 만들지 않아 영상 사용량은 늘지 않습니다.`;
}

/** 정해진 결정 한 줄(감사 기록을 화면에 보여 줄 때) */
export function audioDecisionKo(d: AudioDecision): string {
  const when = d.decided_at ? ` (${d.decided_at.slice(0, 16).replace("T", " ")} UTC)` : "";
  if (d.choice === "keep") return `소리 선택: 이 영상 그대로 사용${when}`;
  const used = d.bgm_usage ? ` · 배경음악 ${usageKo(d.bgm_usage)}` : "";
  return `소리 선택: 배경음악을 따로 만들어 입힘${when}${used}`;
}

const AUDIO_FAIL_KO: Record<string, string> = {
  ...FAIL_KO,
  audio_already_decided: "이미 소리를 정했습니다. 한 번 정하면 바꿀 수 없습니다.",
  publish_already_requested: "게시 요청이 이미 올라가 있어 소리를 바꿀 수 없습니다.",
  not_prepared: "먼저 미디어를 만들어 주세요.",
  not_video: "영상만 소리를 정할 수 있습니다.",
  bgm_unavailable: "이 영상은 배경음악을 따로 만들 수 없습니다(소리를 둔 방식으로 만든 영상만 가능합니다).",
};

export interface AudioResult { kind: "kept" | "bgm_ready" | "check_rejected" | "held" | "working" | "failed"; text: string }

/** 소리 선택 응답을 화면 문구로. 서버 오류 문장을 그대로 쓰지 않는다 */
export function audioResultKo(answer: BriefMediaAnswer, choice: AudioChoice): AudioResult {
  if (answer.status === "completed" && answer.outcome === "audio_kept") {
    return { kind: "kept", text: "이 영상 그대로 쓰기로 정했습니다. 이제 게시를 요청할 수 있습니다." };
  }
  if (answer.status === "completed" && answer.output?.stage === "draft_ready") {
    return { kind: "bgm_ready", text: "배경음악을 입힌 영상이 준비됐습니다. 소리를 다시 한 번 들어 보세요. 이제 게시를 요청할 수 있습니다." };
  }
  if (answer.status === "completed" && answer.output?.stage === "check_rejected") {
    return { kind: "check_rejected", text: "배경음악은 입혔지만 초안 검사에 걸렸습니다. 이 초안은 게시할 수 없습니다." };
  }
  if (answer.status === "input-required") {
    return { kind: "held", text: "안전 장치가 배경음악 생성을 사람 확인으로 보류했습니다. 아래 '사람 결정 대기' 목록에서 승인된 뒤 다시 눌러 주세요. 영상은 그대로이고 아직 공급자는 부르지 않았습니다." };
  }
  if (answer.status === "working") return { kind: "working", text: "안전 장치가 확인하는 중입니다. 잠시 뒤 다시 눌러 주세요." };
  const code = answer.outcome ?? "";
  const fallback = choice === "bgm" ? "배경음악을 만들지 못했습니다. 영상은 그대로입니다. 잠시 뒤 다시 시도해 주세요" : "소리를 정하지 못했습니다. 잠시 뒤 다시 시도해 주세요";
  return { kind: "failed", text: AUDIO_FAIL_KO[code] ?? `${fallback}${code ? ` (${code})` : ""}.` };
}

/** 소리 선택 연결 오류(HTTP 상태)를 화면 문구로 */
export function audioHttpErrorKo(status: number | undefined, code?: string | null): string {
  if (code === NGINX_BLOCKED_CODE) return NGINX_BLOCKED_MESSAGE;
  if (status === 401 || status === 403) return "소리를 정할 권한이 없습니다. 사람 토큰 설정(또는 서버의 이 경로 허용)을 확인해 주세요.";
  if (status === 404) return "기획안을 찾을 수 없거나 서버가 아직 이 기능을 지원하지 않습니다.";
  if (status === 400 || status === 422) return "요청을 서버가 받아들이지 않았습니다. 새로고침한 뒤 다시 시도해 주세요.";
  if (status === 502) return "마케팅 AI에 연결할 수 없습니다. 마케팅 서버가 켜져 있는지 확인해 주세요.";
  if (status === 504) return "시간 안에 끝나지 않았습니다. 1~2분 뒤 새로고침해 확인해 주세요. 다시 눌러도 같은 배경음악을 두 번 만들지 않습니다.";
  return "소리를 정하지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}
