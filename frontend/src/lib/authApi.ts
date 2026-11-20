// 계정·로그인 API (dashboard-api `accounts.py`, 사람별 토큰 설계 1·2단계).
// 비밀번호는 요청 본문으로만 보내고 어디에도 저장·기록하지 않는다. 토큰은 humanSession.ts가 sessionStorage에 둔다.
import { DASHBOARD_API_URL } from "./env";
import {
  getSession, readCompanyPickResponse, readLoginResponse, readLoginStatus, readPasswordChangeResponse, readServerAccount,
  type CompanyPickOutcome, type LoginOutcome, type PasswordChangeOutcome, type ServerAccount, type ServerRole,
} from "./humanSession";
import { readAccountList, updateBody, type AccountPatch } from "./memberAdmin";

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

/** alive의 data: 응답 본문 그대로(회사 칸 `company_list`·`company_id`는 humanSession.withCompanies가 읽는다) */
export type MeOutcome = { kind: "alive"; account: ServerAccount; data: unknown } | { kind: "ended" } | { kind: "unknown" };

/**
 * 내 계정(`GET /auth/me`): 세션이 살아 있으면 서버의 지금 계정 값(역할·부서·확인 권한·열람 담당).
 * 401이면 끝난 세션(로그아웃·계정 삭제·역할 변경), 연결 실패·못 읽은 응답은 판단하지 않는다(unknown)
 */
export async function fetchMe(): Promise<MeOutcome> {
  try {
    const { status, body } = await send("/me", { session: true });
    if (status === 401) return { kind: "ended" };
    if (status < 200 || status >= 300) return { kind: "unknown" };
    const data = (body as { data?: { account?: unknown } } | null)?.data;
    const account = readServerAccount(data?.account);
    return account ? { kind: "alive", account, data } : { kind: "unknown" };
  } catch {
    return { kind: "unknown" };
  }
}

/** 계정 목록: 운영자는 모든 계정, 고객사 관리자는 고객사 계정(admin·member)만 (서버가 고른다) */
export async function listAccounts(): Promise<ServerAccount[]> {
  const { status, body } = await send("/accounts", { session: true });
  const r = readAccountList(status, body);
  if (!r.ok) throw new Error(r.message);
  return r.rows;
}

/** 역할·이름·부서·확인 권한·열람 담당 바꾸기. 역할이 바뀌면 서버가 그 계정의 기존 세션을 끊는다 */
export async function updateAccount(accountId: string, patch: AccountPatch): Promise<ServerAccount> {
  const payload = updateBody(patch);
  if (!payload) throw new Error("바꿀 칸이 없어요.");
  const { status, body } = await send(`/accounts/${encodeURIComponent(accountId)}/update`, {
    method: "POST",
    session: true,
    body: JSON.stringify(payload),
  });
  if (status < 200 || status >= 300) throw new Error(errorMessage(status, body, "계정을 바꾸지 못했어요"));
  const row = readServerAccount((body as { data?: unknown } | null)?.data);
  if (!row) throw new Error("응답을 읽지 못했어요. 목록을 다시 읽어 주세요.");
  return row;
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

/** 내 비밀번호 바꾸기. 성공하면 서버가 이 계정의 기존 세션을 모두 끊고 새 세션을 준다 */
export async function changePassword(current: string, next: string): Promise<PasswordChangeOutcome> {
  try {
    const { status, body } = await send("/password", {
      method: "POST",
      session: true,
      body: JSON.stringify({ current_password: current, new_password: next }),
    });
    return readPasswordChangeResponse(status, body);
  } catch {
    return { kind: "error", message: "서버에 닿지 않았어요. 잠시 뒤 다시 해 주세요." };
  }
}

/** 비밀번호 초기화(운영자: 모든 계정, 고객사 관리자: 일반회원만). 그 계정의 기존 세션은 서버가 끊는다 */
export async function resetPassword(accountId: string, newPassword: string): Promise<ServerAccount> {
  const { status, body } = await send(`/accounts/${encodeURIComponent(accountId)}/reset-password`, {
    method: "POST",
    session: true,
    body: JSON.stringify({ new_password: newPassword }),
  });
  if (status < 200 || status >= 300) throw new Error(errorMessage(status, body, "비밀번호를 초기화하지 못했어요"));
  return (body as { data: ServerAccount }).data;
}

/** 지금 회사 바꾸기(여러 회사 설계 1-8): 서버가 그 계정 소속 회사로만 세션 토큰을 다시 서명해 준다 */
export async function switchCompany(companyId: string): Promise<CompanyPickOutcome> {
  try {
    const { status, body } = await send("/company", { method: "POST", session: true, body: JSON.stringify({ company_id: companyId }) });
    return readCompanyPickResponse(status, body);
  } catch {
    return { kind: "error", message: "서버에 닿지 않았어요. 잠시 뒤 다시 해 주세요." };
  }
}
