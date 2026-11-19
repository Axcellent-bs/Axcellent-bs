// 계정·로그인 API (dashboard-api `accounts.py`, 사람별 토큰 설계 1·2단계).
// 비밀번호는 요청 본문으로만 보내고 어디에도 저장·기록하지 않는다. 토큰은 humanSession.ts가 sessionStorage에 둔다.
import { DASHBOARD_API_URL } from "./env";
import {
  getSession, readLoginResponse, readLoginStatus,
  type LoginOutcome, type ServerAccount, type ServerRole,
} from "./humanSession";

const BASE = `${DASHBOARD_API_URL}/api/dashboard/auth`;

async function send(path: string, init: RequestInit & { session?: boolean } = {}): Promise<{ status: number; body: unknown }> {
  const token = init.session ? getSession()?.token : undefined;
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  const body: unknown = await res.json().catch(() => undefined);
  return { status: res.status, body };
}

const errorMessage = (status: number, body: unknown, fallback: string) => {
  const m = (body as { error?: { message?: unknown } } | null)?.error?.message;
  return typeof m === "string" && m ? m : `${fallback} (${status})`;
};

/** 서버 로그인이 켜져 있나. 못 읽으면(옛 서버·연결 실패) false → 데모 로그인 */
export async function fetchLoginEnabled(): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 5000);
    const { status, body } = await send("/status", { signal: ctrl.signal }).finally(() => clearTimeout(t));
    return readLoginStatus(status, body);
  } catch {
    return false;
  }
}

export async function serverLogin(email: string, password: string): Promise<LoginOutcome> {
  try {
    const { status, body } = await send("/login", { method: "POST", body: JSON.stringify({ email: email.trim(), password }) });
    return readLoginResponse(status, body);
  } catch {
    return { kind: "error", message: "로그인 서버에 닿지 않았어요. 잠시 뒤 다시 해 주세요." };
  }
}

/** 서버의 끊은 토큰 목록에 올린다. 실패해도 화면에서는 로그아웃한다(토큰은 만료되면 어차피 못 쓴다) */
export async function serverLogout(): Promise<void> {
  if (!getSession()) return;
  try {
    await send("/logout", { method: "POST", session: true });
  } catch {
    /* 서버에 닿지 않아도 화면 세션은 지운다 */
  }
}

/** 세션이 아직 살아 있나(`GET /auth/me`). 401이면 끝난 세션(로그아웃·계정 삭제), 연결 실패는 판단하지 않는다(null) */
export async function checkSession(): Promise<boolean | null> {
  try {
    const { status } = await send("/me", { session: true });
    if (status === 401) return false;
    return status >= 200 && status < 300 ? true : null;
  } catch {
    return null;
  }
}

export async function listAccounts(): Promise<ServerAccount[]> {
  const { status, body } = await send("/accounts", { session: true });
  if (status < 200 || status >= 300) throw new Error(errorMessage(status, body, "계정 목록을 읽지 못했어요"));
  const rows = (body as { data?: unknown } | null)?.data;
  return Array.isArray(rows) ? (rows as ServerAccount[]) : [];
}

export type NewAccount = { name: string; email: string; role: ServerRole; password: string };

/** 계정 만들기: 운영자는 모든 역할, 고객사 관리자는 일반회원만(서버가 다시 본다) */
export async function createAccount(x: NewAccount): Promise<ServerAccount> {
  const { status, body } = await send("/accounts", {
    method: "POST",
    session: true,
    body: JSON.stringify({ name: x.name.trim(), email: x.email.trim(), role: x.role, password: x.password }),
  });
  if (status < 200 || status >= 300) throw new Error(errorMessage(status, body, "계정을 만들지 못했어요"));
  return (body as { data: ServerAccount }).data;
}
