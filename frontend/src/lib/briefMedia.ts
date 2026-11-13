// 기획안 "미디어 만들기"의 응답 모양과 화면 문구. 숫자·상태는 마케팅 서버가 준 값만 쓴다(지어내지 않는다).
// 이 파일은 순수 계산만 한다(가져오기 없음): `npm test`(node --test)에서 그대로 돈다.

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
export function mediaHttpErrorKo(status: number | undefined): string {
  if (status === 401 || status === 403) return "미디어를 만들 권한이 없습니다. 사람 토큰 설정(또는 서버의 이 경로 허용)을 확인해 주세요.";
  if (status === 404) return "기획안을 찾을 수 없거나 서버가 아직 이 기능을 지원하지 않습니다.";
  if (status === 400 || status === 422) return "요청을 서버가 받아들이지 않았습니다. 새로고침한 뒤 다시 시도해 주세요.";
  if (status === 502) return "마케팅 AI에 연결할 수 없습니다. 마케팅 서버가 켜져 있는지 확인해 주세요.";
  if (status === 504) return "시간 안에 끝나지 않았습니다. 생성이 계속되고 있을 수 있으니 1~2분 뒤 새로고침해 확인해 주세요. 다시 눌러도 같은 미디어를 두 번 만들지 않습니다.";
  return "미디어를 만들지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}
