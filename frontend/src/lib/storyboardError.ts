import { ApiError, failureError, NGINX_BLOCKED_CODE } from "./apiError.ts";

// 스토리보드 호출이 실패했을 때 사용자에게 보일 문장. 서버가 준 한국어 사유(`error.message`)만 쓴다.
// 줄거리·캐릭터 문장 같은 입력은 이 문장에 섞지 않는다(서버 문장과 고정 문구만).

export const STORYBOARD_FAILED = "스토리보드를 만들지 못했어요";
const MAX_REASON = 200;

/** 서버 문장을 한 줄로 다듬는다. 문자열이 아니거나 비면 undefined */
export function serverReason(body: unknown): string | undefined {
  const message = (body as { error?: { message?: unknown } } | null | undefined)?.error?.message;
  if (typeof message !== "string") return undefined;
  const text = message.replace(/\s+/g, " ").trim().slice(0, MAX_REASON);
  return text || undefined;
}

/**
 * 실패 응답 → 오류. JSON 오류 본문이면 "스토리보드를 만들지 못했어요 — <서버 사유>",
 * 본문이 JSON이 아니면(프록시 오류 등) 고정 문구만. 앞단(nginx) 차단은 기존 안내 문구를 쓴다.
 */
export function storyboardFailure(body: unknown, status: number): ApiError {
  const base = failureError(body, status, STORYBOARD_FAILED);
  if (base.code === NGINX_BLOCKED_CODE) return base;
  const reason = serverReason(body);
  return new ApiError(reason ? `${STORYBOARD_FAILED} — ${reason}` : STORYBOARD_FAILED, status, undefined, base.code);
}
