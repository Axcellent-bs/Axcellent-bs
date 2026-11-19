// 서버 로그인 세션 (사람별 토큰 설계 2단계, docs/superpowers/specs/2026-11-19-human-session-tokens-design.md).
// - 로그인 응답의 세션 토큰(`axs1.`)을 sessionStorage에 둔다(설계 2절). 탭을 닫으면 사라지고 번들에는 들어가지 않는다
// - 사람 토큰을 붙이는 곳(env.ts의 SAFETY_HUMAN_TOKEN)은 세션이 있으면 세션 토큰을, 없으면 공용 토큰을 쓴다
// - 만료 시각이 지나면 세션을 버린다. 서버가 503 LOGIN_DISABLED(세션 비밀값 없음)를 주면 데모 로그인으로 들어간다(1~3단계)
// 비밀번호는 여기 두지 않고, 토큰은 콘솔·주소에 남기지 않는다. 순수 계산은 `npm test`가 그대로 돈다.
import { isNginxBlocked } from "./apiError.ts";

export type ServerRole = "operator" | "admin" | "member";

/** 열람 담당 영역 (dashboard-api `accounts.py`의 READER_AREAS, 화면 `demo/members.ts`의 READER_AREAS id와 같다) */
export const READER_AREA_IDS = ["hr", "finance"] as const;
export type ReaderAreaId = (typeof READER_AREA_IDS)[number];

/** 서버 계정 (dashboard-api `accounts.py`의 공개 칸. 해시는 오지 않는다) */
export interface ServerAccount {
  id: string;
  name: string;
  email: string;
  role: ServerRole;
  created_at?: string | null;
  /** 부서 (관리자가 회원·서버 관리에서 정한다). 없으면 null */
  dept?: string | null;
  /** 확인 권한: 위험한 요청을 승인할 수 있다 */
  approver?: boolean;
  /** 열람 담당: 인사·재무 AI의 다른 사람 대화 요약을 본다 */
  readers?: ReaderAreaId[];
}

/**
 * 서버가 준 계정 한 줄을 공개 칸만 골라 읽는다(허용 목록. 해시 같은 다른 칸이 와도 버린다).
 * 모양이 틀리면 null. 프로필 칸은 왔을 때만 넣는다.
 */
export function readServerAccount(v: unknown): ServerAccount | null {
  if (!v || typeof v !== "object") return null;
  const a = v as Record<string, unknown>;
  if (typeof a.id !== "string" || typeof a.name !== "string" || typeof a.email !== "string") return null;
  if (typeof a.role !== "string" || !ROLES.includes(a.role)) return null;
  const out: ServerAccount = {
    id: a.id, name: a.name, email: a.email, role: a.role as ServerRole,
    created_at: typeof a.created_at === "string" ? a.created_at : null,
  };
  if (a.dept === null || typeof a.dept === "string") out.dept = a.dept || null;
  if (typeof a.approver === "boolean") out.approver = a.approver;
  if (Array.isArray(a.readers)) out.readers = READER_AREA_IDS.filter((r) => (a.readers as unknown[]).includes(r));
  return out;
}

export interface HumanSession {
  token: string;
  /** 만료 시각 (유닉스 초, 서버 `expires_at`) */
  expiresAt: number;
  account: ServerAccount;
}

/** 브라우저 저장소 중 이 모듈이 쓰는 부분 (테스트에서 바꿔 끼운다) */
export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export const SESSION_KEY = "axcellent-session";
const ROLES: readonly string[] = ["operator", "admin", "member"];

const nowSeconds = () => Math.floor(Date.now() / 1000);

export const isExpired = (s: HumanSession, now = nowSeconds()) => s.expiresAt <= now;

/** 저장한 값을 읽어 모양이 맞는지 본다. 깨졌거나 만료됐으면 null */
export function parseSession(raw: string | null, now = nowSeconds()): HumanSession | null {
  if (!raw) return null;
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!v || typeof v !== "object") return null;
  const o = v as { token?: unknown; expiresAt?: unknown; account?: unknown };
  const account = readServerAccount(o.account);
  const ok =
    typeof o.token === "string" && o.token.startsWith("axs1.") &&
    typeof o.expiresAt === "number" && Number.isFinite(o.expiresAt) && !!account;
  if (!ok || !account) return null;
  const s: HumanSession = { token: o.token as string, expiresAt: o.expiresAt as number, account };
  return isExpired(s, now) ? null : s;
}

/** 로그인 응답(`{token, expires_at, account}`)을 세션으로. 모양이 다르면 null */
export function sessionFromLogin(data: unknown, now = nowSeconds()): HumanSession | null {
  const d = (data ?? {}) as { token?: unknown; expires_at?: unknown; account?: unknown };
  return parseSession(JSON.stringify({ token: d.token, expiresAt: d.expires_at, account: d.account }), now);
}

/** 사람 토큰 고르기: 만료 전 세션이 있으면 세션 토큰, 없으면 공용 토큰(옮기는 동안, 설계 3절) */
export function pickHumanToken(session: HumanSession | null, shared: string, now = nowSeconds()): string {
  return session && !isExpired(session, now) ? session.token : shared;
}

// ---- 지금 세션 (이 탭) ----

const browserStore = (): KeyValueStore | null => {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
};

let store: KeyValueStore | null = browserStore();
let current: HumanSession | null = (() => {
  try {
    return parseSession(store?.getItem(SESSION_KEY) ?? null);
  } catch {
    return null;
  }
})();
const listeners = new Set<() => void>();

export const getSession = (): HumanSession | null => (current && !isExpired(current) ? current : null);

export function onSessionChange(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function setSession(next: HumanSession | null): void {
  current = next;
  try {
    if (next) store?.setItem(SESSION_KEY, JSON.stringify(next));
    else store?.removeItem(SESSION_KEY);
  } catch {
    /* 저장이 막힌 브라우저는 이 화면 동안만 기억한다 */
  }
  listeners.forEach((l) => l());
}

/** 테스트용: 저장소를 바꿔 끼우고 거기서 다시 읽는다 */
export function setStoreForTests(next: KeyValueStore | null): void {
  store = next;
  current = parseSession(next?.getItem(SESSION_KEY) ?? null);
}

// ---- 로그인 응답 해석 ----

export type LoginOutcome =
  | { kind: "ok"; session: HumanSession }
  /** 서버 로그인이 꺼져 있다(세션 비밀값 없음·로그인 경로가 아직 안 열림) → 데모 로그인 */
  | { kind: "disabled" }
  | { kind: "error"; message: string };

/** 로그인이 꺼져 있다고 보는 응답: 503 LOGIN_DISABLED, 앞단 nginx가 아직 막은 경로, 옛 서버에 경로가 없음 */
export function loginDisabled(status: number, body: unknown): boolean {
  const code = (body as { error?: { code?: unknown } } | null)?.error?.code;
  if (status === 503 && code === "LOGIN_DISABLED") return true;
  if (isNginxBlocked(status, body)) return true;
  return status === 404 && code === undefined;
}

export function readLoginResponse(status: number, body: unknown, now = nowSeconds()): LoginOutcome {
  if (loginDisabled(status, body)) return { kind: "disabled" };
  if (status >= 200 && status < 300) {
    const data = (body as { data?: unknown } | null)?.data;
    const session = sessionFromLogin(data, now);
    return session ? { kind: "ok", session } : { kind: "error", message: "로그인 응답을 읽지 못했어요." };
  }
  const message = (body as { error?: { message?: unknown } } | null)?.error?.message;
  if (typeof message === "string" && message) return { kind: "error", message };
  if (status === 401) return { kind: "error", message: "이메일이나 비밀번호가 맞지 않아요" };
  return { kind: "error", message: `로그인하지 못했어요 (${status})` };
}

/** `GET /auth/status` 응답: 서버 로그인이 켜져 있다고 분명히 말할 때만 true. 못 읽으면 데모 로그인 */
export function readLoginStatus(status: number, body: unknown): boolean {
  if (status < 200 || status >= 300) return false;
  return (body as { data?: { login_enabled?: unknown } } | null)?.data?.login_enabled === true;
}

export const MIN_PASSWORD_LENGTH = 10;
/** 서버 계정 만들기 전 화면 검사 (서버도 같은 규칙으로 다시 본다) */
export function accountInputProblem(x: { name: string; email: string; password: string; confirm?: string }): string | null {
  if (!x.name.trim()) return "이름을 입력하세요.";
  if (!/^[^@\s:]+@[^@\s:]+\.[^@\s:]+$/.test(x.email.trim())) return "이메일 형식이 아니에요.";
  if (x.password.length < MIN_PASSWORD_LENGTH) return `비밀번호는 ${MIN_PASSWORD_LENGTH}자 이상이어야 해요.`;
  if (x.confirm !== undefined && x.password !== x.confirm) return "비밀번호 확인이 일치하지 않아요.";
  return null;
}

// ---- 비밀번호 바꾸기·초기화 (dashboard-api `/auth/password`, `/auth/accounts/{id}/reset-password`) ----

/** 비밀번호 바꾸기 전 화면 검사 (서버도 길이·현재와 같은지 다시 본다) */
export function passwordChangeProblem(x: { current: string; next: string; confirm: string }): string | null {
  if (!x.current) return "지금 비밀번호를 입력하세요.";
  if (x.next.length < MIN_PASSWORD_LENGTH) return `새 비밀번호는 ${MIN_PASSWORD_LENGTH}자 이상이어야 해요.`;
  if (x.next === x.current) return "새 비밀번호는 지금 비밀번호와 달라야 해요.";
  if (x.next !== x.confirm) return "새 비밀번호 확인이 일치하지 않아요.";
  return null;
}

export type PasswordChangeOutcome = { kind: "ok"; session: HumanSession } | { kind: "error"; message: string };

/** 비밀번호 바꾸기 응답: 성공이면 새 세션(기존 세션은 서버가 모두 끊었다) */
export function readPasswordChangeResponse(status: number, body: unknown, now = nowSeconds()): PasswordChangeOutcome {
  if (status >= 200 && status < 300) {
    const session = sessionFromLogin((body as { data?: unknown } | null)?.data, now);
    return session ? { kind: "ok", session } : { kind: "error", message: "응답을 읽지 못했어요. 다시 로그인해 주세요." };
  }
  const message = (body as { error?: { message?: unknown } } | null)?.error?.message;
  if (typeof message === "string" && message) return { kind: "error", message };
  if (status === 401) return { kind: "error", message: "지금 비밀번호가 맞지 않아요." };
  return { kind: "error", message: `비밀번호를 바꾸지 못했어요 (${status})` };
}

/** 이 사람이 그 계정의 비밀번호를 초기화할 수 있나: 운영자는 모든 계정, 고객사 관리자는 일반회원만. 자기 계정은 "비밀번호 바꾸기"로 */
export function canResetPassword(actor: { id: string; role: ServerRole }, target: { id: string; role: ServerRole }): boolean {
  if (actor.id === target.id) return false;
  if (actor.role === "operator") return true;
  return actor.role === "admin" && target.role === "member";
}
