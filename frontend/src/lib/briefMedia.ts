// 기획안 "미디어 만들기"의 응답 모양과 화면 문구. 숫자·상태는 마케팅 서버가 준 값만 쓴다(지어내지 않는다).
// 이 파일은 순수 계산만 한다(화면·환경 값을 가져오지 않음): `npm test`(node --test)에서 그대로 돈다.
import { NGINX_BLOCKED_CODE, NGINX_BLOCKED_MESSAGE } from "./apiError.ts";

/** 지금 요청 초안으로 만들 때의 예상 비용·상한. 마케팅이 조회 때 단가표·장부에서 계산한다 */
export interface MediaEstimate {
  status: "ok" | "blocked";
  reason_code: string | null;
  media_kind: string | null;
  /** 예상 비용(원). 계산할 수 없으면 null */
  cost_krw: number | null;
  /** 이미 끝난 생성이라 다시 만들어도 비용이 나지 않는다 */
  settled: boolean;
  /** price_table = 단가표, mock = 목 공급자(테스트 전용), ledger = 생성 장부의 확정액 */
  basis: "price_table" | "mock" | "ledger" | null;
  per_item_limit_krw: number | null;
  monthly_budget_krw: number | null;
  month_total_krw: number | null;
}

/** 지금 요청 초안으로 만든 미디어 초안(AI 표시를 입히고 검사까지 한 결과) */
export interface PreparedMedia {
  post_draft_id: string | null;
  stage: "draft_ready" | "check_rejected" | string;
  /** image | reel */
  media_kind: string | null;
  asset_ref: string | null;
  asset_sha256: string | null;
  cost_krw: number | null;
  /** 파일 위에 입힌 AI 표시 문구 */
  label_text: string | null;
  check: { verdict: string | null; publishable: boolean | null; problems: string[] };
  prepared_at: string | null;
}

export interface BriefMediaOutput {
  stage?: string;
  reason?: string;
  review_id?: string;
  hold_reason?: string;
  sns_handoff?: { prepared?: PreparedMedia | null; media_estimate?: MediaEstimate | null };
}
export interface BriefMediaAnswer { status: string; outcome: string | null; output: BriefMediaOutput | null }

const won = (n: number) => `${n.toLocaleString("ko-KR")}원`;

/** 만들 수 없는 이유(비용이 나기 전에 서버가 막은 것) */
const BLOCK_KO: Record<string, string> = {
  fields_missing: "미디어 요청에 미정 필드가 남아 있습니다. 받은 기획안 탭에서 필드를 정해 주세요.",
  carousel_items_undecided: "캐러셀은 장마다 무엇을 그릴지 정하는 단계가 아직 없어 여기서 만들 수 없습니다. 받은 기획안 탭에서 종류를 이미지나 영상으로 바꿔 주세요.",
  bgm_fields_required: "서버가 배경음 모드라 영상에 배경음 설정이 필요합니다. 이 화면에서는 아직 정할 수 없습니다.",
  unknown_media_kind: "미디어 종류를 알 수 없습니다. 받은 기획안 탭에서 종류를 다시 저장해 주세요.",
  cost_over_single: "예상 비용이 건당 상한을 넘어 만들 수 없습니다.",
  budget_exceeded: "이달 미디어 비용 상한을 넘게 되어 만들 수 없습니다.",
  price_not_found: "단가표에 없는 조합이라 만들 수 없습니다.",
  price_table_error: "단가표를 읽을 수 없어 만들 수 없습니다.",
  model_not_configured: "서버에 미디어 모델이 설정되지 않아 만들 수 없습니다.",
  provider_adapter_missing: "서버에 설정된 미디어 공급자를 쓸 수 없습니다.",
  fx_not_configured: "서버에 환율이 설정되지 않아 비용을 계산할 수 없습니다.",
  fx_max_age_not_configured: "서버에 환율 유효 기간이 설정되지 않아 비용을 계산할 수 없습니다.",
  fx_stale: "서버의 환율 기준일이 오래되어 비용을 계산할 수 없습니다.",
  cost_limit_not_configured: "서버에 비용 상한이 설정되지 않아 만들 수 없습니다.",
  prompt_rejected: "안전 규칙에 걸리는 필드 값이 있어 만들 수 없습니다. 받은 기획안 탭에서 필드를 바꿔 주세요.",
  storage_error: "비용 장부를 읽을 수 없어 만들 수 없습니다.",
};

/** 만들기를 눌렀는데 만들지 못한 이유 */
const FAIL_KO: Record<string, string> = {
  ...BLOCK_KO,
  not_handed: "아직 SNS·미디어로 넘기지 않은 기획안입니다.",
  in_progress: "이미 만드는 중입니다. 잠시 뒤 새로고침해 주세요.",
  forbidden_phrase: "기획안 글에 참여를 조건으로 거는 말이 있어 초안을 만들 수 없습니다.",
  caption_too_long: "캡션이 너무 길어 초안을 만들 수 없습니다.",
  post_tools_unavailable: "서버에 영상·이미지 처리 도구가 없어 만들지 않았습니다. 비용은 나지 않았습니다.",
  ffmpeg_not_found: "서버에 영상 처리 도구가 없어 만들지 않았습니다. 비용은 나지 않았습니다.",
  media_dir_not_configured: "서버에 미디어 폴더가 설정되지 않아 만들지 않았습니다.",
  media_dir_mismatch: "서버의 미디어 폴더 설정이 맞지 않아 만들지 않았습니다.",
  convert_failed: "미디어는 만들었지만 게시 규격으로 바꾸지 못했습니다. 다시 누르면 새 비용 없이 이어서 시도합니다.",
  label_failed: "미디어는 만들었지만 AI 표시를 입히지 못했습니다. 다시 누르면 새 비용 없이 이어서 시도합니다.",
  execute_rejected: "안전 장치가 이 요청을 거절했습니다.",
  execute_failed: "안전 장치에서 요청을 처리하지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
  rejected_by_review: "검토에서 거절되어 만들지 않았습니다.",
  TIMEOUT: "안전 장치 확인이 제때 끝나지 않았습니다. 잠시 뒤 다시 시도해 주세요.",
};

export function estimateBlockKo(code: string | null): string {
  return (code && BLOCK_KO[code]) || `지금은 만들 수 없습니다${code ? ` (${code})` : ""}.`;
}

const BASIS_KO: Record<string, string> = {
  price_table: "단가표 기준 추정",
  mock: "목 공급자 고정 비용(테스트 전용)",
  ledger: "생성 장부의 확정액",
};

/** 확인 단계에 보여 줄 비용 줄. 서버가 준 값만 쓰고 없는 값은 "읽을 수 없음"으로 둔다 */
export function estimateLines(e: MediaEstimate): string[] {
  const lines: string[] = [];
  if (e.cost_krw == null) lines.push("예상 비용을 계산할 수 없습니다.");
  else if (e.settled) lines.push(`이미 만든 미디어가 있어 다시 만들어도 새 비용이 나지 않습니다 (만들 때 든 비용 ${won(e.cost_krw)}).`);
  else lines.push(`예상 비용 ${won(e.cost_krw)}${e.basis ? ` (${BASIS_KO[e.basis] ?? e.basis})` : ""}`);
  lines.push(e.per_item_limit_krw != null ? `건당 상한 ${won(e.per_item_limit_krw)}` : "건당 상한 설정을 읽을 수 없음");
  const month = e.month_total_krw != null ? `이달 누적 ${won(e.month_total_krw)}` : "이달 누적을 읽을 수 없음";
  lines.push(`${month} / ${e.monthly_budget_krw != null ? `월 상한 ${won(e.monthly_budget_krw)}` : "월 상한 설정을 읽을 수 없음"}`);
  return lines;
}

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
    return { kind: "held", text: "안전 장치가 이 생성을 사람 확인으로 보류했습니다. 아래 '사람 결정 대기' 목록에 나오며, 승인된 뒤 다시 눌러 주세요. 아직 비용은 나지 않았습니다." };
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
export type PublishStatus = "not_requested" | "held" | "publishing" | "published" | "rejected" | "expired" | "failed" | "unknown";

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
}

export interface PublishAnswer {
  status: string;
  outcome: string | null;
  output: { publish?: PublishState | null; review_id?: string } | null;
}

export const PUBLISH_EXPIRY_MIN = 10;

/** 게시 요청을 할 수 없거나 실패한 이유(코드별 문구). 서버 문장은 쓰지 않는다 */
const PUBLISH_FAIL_KO: Record<string, string> = {
  not_handed: "아직 SNS·미디어로 넘기지 않은 기획안입니다.",
  not_prepared: "게시할 수 있는 미디어 초안이 없습니다. 먼저 미디어를 만들어 주세요.",
  check_not_passed: "초안 검사를 통과하지 못해 게시를 요청할 수 없습니다.",
  media_kind_unsupported: "캐러셀은 이 화면에서 게시를 요청할 수 없습니다.",
  draft_missing: "서버에서 초안을 찾을 수 없어 요청하지 않았습니다.",
  draft_changed: "초안 내용이 만든 뒤 바뀌어 요청하지 않았습니다. 미디어를 다시 만들어 주세요.",
  daily_limit_reached: "24시간 게시 상한에 도달해 지금은 요청할 수 없습니다. 나중에 다시 시도해 주세요.",
  publish_halted: "게시가 중단된 상태라 요청할 수 없습니다. 담당자 확인이 필요합니다.",
  limits_config_error: "서버의 게시 한도 설정을 읽을 수 없어 요청하지 않았습니다.",
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
  adapter_unavailable: "서버에 Instagram 게시 설정이 없어 게시하지 못했습니다.",
};

export const publishReasonKo = (code: string | null | undefined): string =>
  (code && PUBLISH_FAIL_KO[code]) || `게시를 요청하지 못했습니다. 잠시 뒤 다시 시도해 주세요${code ? ` (${code})` : ""}.`;

export interface PublishLine { tone: "info" | "ok" | "warn" | "bad"; text: string }

/** 서버가 준 게시 요청 상태를 화면 한 줄로. 요청 전(not_requested)이면 null */
export function publishStatusLine(p: PublishState | null | undefined): PublishLine | null {
  if (!p) return null;
  switch (p.status) {
    case "held":
      return { tone: "info", text: `게시 승인 대기 중입니다. 사람이 ${PUBLISH_EXPIRY_MIN}분 안에 승인해야 게시되고, 시간이 지나면 만료됩니다.` };
    case "publishing":
      return { tone: "info", text: "승인됐습니다. Instagram에 게시하는 중입니다." };
    case "published":
      return { tone: "ok", text: "Instagram에 게시됐습니다." };
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
    return { kind: "requested", text: `게시 요청을 보냈습니다. 아직 게시되지 않았습니다. 사람이 ${PUBLISH_EXPIRY_MIN}분 안에 승인 화면에서 진행을 눌러야 합니다.` };
  }
  if (answer.status === "completed" && answer.outcome === "already_requested") {
    return { kind: "already", text: "이미 요청된 건입니다. 새로 요청하지 않았습니다." };
  }
  return { kind: "failed", text: publishReasonKo(answer.outcome) };
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
