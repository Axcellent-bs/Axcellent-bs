import type { Validation } from "./types";

export class ApiError extends Error {
  /** `validation`: 검증 결과(있을 때). `code`: 서버 오류 코드(`{error: {code}}`), 없으면 비어 있다 */
  status: number;
  validation?: Validation;
  code?: string | null;
  constructor(message: string, status: number, validation?: Validation, code?: string | null) {
    super(message);
    this.status = status;
    this.validation = validation;
    this.code = code;
  }
}

/** 앞단 서버(nginx)가 허용 경로가 아니라서 막은 요청의 내부 구분 코드 */
export const NGINX_BLOCKED_CODE = "nginx_blocked";
export const NGINX_BLOCKED_MESSAGE = "서버가 이 요청 경로를 허용하지 않습니다. 관리자에게 서버 허용 경로(nginx) 추가를 요청해 주세요.";

/**
 * 앞단 서버가 막은 응답인지: 403/405인데 본문이 JSON 객체가 아니다(nginx 기본 오류는 HTML).
 * 앱이 만든 403은 `{error:{code,message}}`·`{detail}` 같은 JSON 객체라 해당하지 않는다.
 * `body`는 본문을 JSON으로 읽은 결과(못 읽었으면 undefined/null).
 */
export function isNginxBlocked(status: number, body: unknown): boolean {
  if (status !== 403 && status !== 405) return false;
  return !(body !== null && typeof body === "object" && !Array.isArray(body));
}

/**
 * 실패 응답을 오류로: 앞단 차단이면 안내 문구 + `nginx_blocked`, 아니면 서버 문장(`error.message`),
 * 없으면 `fallback`(기본 `요청 실패 (상태)`)을 쓴다. 경로·주소는 문구에 넣지 않는다.
 */
export function failureError(body: unknown, status: number, fallback?: string): ApiError {
  if (isNginxBlocked(status, body)) return new ApiError(NGINX_BLOCKED_MESSAGE, status, undefined, NGINX_BLOCKED_CODE);
  const e = (body as { error?: { code?: string; message?: string } } | null | undefined)?.error;
  return new ApiError(e?.message ?? fallback ?? `요청 실패 (${status})`, status, undefined, e?.code);
}

/** 서버 문장을 쓰지 않는 호출용: 앞단 차단이면 `nginx_blocked`, 아니면 상태 코드만 담는다(문구는 호출 쪽 `…HttpErrorKo`) */
export function statusOnlyError(body: unknown, status: number): ApiError {
  return isNginxBlocked(status, body) ? failureError(body, status) : new ApiError(`요청 실패 (${status})`, status);
}
