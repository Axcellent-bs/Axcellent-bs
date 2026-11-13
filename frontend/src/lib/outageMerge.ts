import { NGINX_BLOCKED_CODE, NGINX_BLOCKED_MESSAGE } from "./apiError.ts";

// 장애 사과·보상 글 초안의 '유사 초안 병합' 화면 문구. 서버가 준 숫자·id만 쓰고 경로·내부 주소는 적지 않는다.

export interface MergeGroup {
  representative: string;
  merged_from: string[];
  representative_caption: string;
  trigger: string | null;
}
export interface MergeSkip { id: string; reason: string }
export interface MergeAnswer {
  dry_run: boolean;
  groups: MergeGroup[];
  merged_count: number;
  remaining_count: number;
  skipped: MergeSkip[];
}

export const SKIP_REASON_KO: Record<string, string> = {
  not_found: "초안을 찾을 수 없음",
  not_outage_draft: "장애 사과·보상 초안이 아님",
  already_merged: "이미 병합됨",
  media_attached: "미디어가 붙어 있어 건드리지 않음",
  check_failed: "AI 표시·금지어 검사를 통과하지 못해 건드리지 않음",
  publish_in_progress_or_done: "게시 요청·게시 이력이 있어 건드리지 않음",
  publish_state_unknown: "게시 상태를 읽지 못해 건드리지 않음",
};
export const skipReasonKo = (reason: string) => SKIP_REASON_KO[reason] ?? "병합하지 않음";

/** 병합 호출이 실패했을 때 화면 문구. 서버 문장은 쓰지 않는다 */
export function mergeHttpErrorKo(status: number | undefined, code?: string | null): string {
  if (code === NGINX_BLOCKED_CODE) return NGINX_BLOCKED_MESSAGE;
  if (status === 401 || status === 403) return "병합할 권한이 없습니다. 사람 토큰 설정(또는 서버의 이 경로 허용)을 확인해 주세요.";
  if (status === 400 || status === 422) return "요청을 서버가 받아들이지 않았습니다. 새로고침한 뒤 다시 시도해 주세요.";
  if (status === 502) return "마케팅 AI에 연결할 수 없습니다. 마케팅 서버가 켜져 있는지 확인해 주세요.";
  if (status === 504) return "마케팅 AI가 제때 응답하지 않았습니다. 잠시 뒤 새로고침해 확인해 주세요. 다시 눌러도 같은 결과입니다.";
  return "초안을 병합하지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}

/** 미리보기·결과 한 줄 요약 */
export function mergeSummaryKo(a: MergeAnswer): string {
  if (a.groups.length === 0) return a.dry_run ? "병합할 비슷한 초안이 없습니다." : "병합된 초안이 없습니다.";
  const head = `${a.groups.length}묶음에서 ${a.merged_count}건을 대표 초안 아래로 접습니다.`;
  return a.dry_run ? `${head} 병합 뒤 목록에는 ${a.remaining_count}건이 남습니다.` : `${a.groups.length}묶음, ${a.merged_count}건을 병합했습니다. 목록에는 ${a.remaining_count}건이 남았습니다.`;
}

/** 미리보기에서 본 그대로만 저장하도록, 확인 때 보낼 초안 번호(대표 + 접히는 원본) */
export const previewDraftIds = (a: MergeAnswer): string[] => a.groups.flatMap((g) => [g.representative, ...g.merged_from]);
