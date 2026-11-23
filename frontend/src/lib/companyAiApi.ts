// 회사 AI 설정·AI 연결 호출(dashboard-api company_ai.py). 로그인 세션 토큰만 쓴다(공용 토큰은 서버가 403).
// 키 값은 요청 본문으로만 나가고 어디에도 남기지 않는다. 실패하면 서버 문장(error.message)과 코드를 그대로 담아 던진다.
import { ApiError, failureError } from "./apiError";
import {
  readCompanyAi, readSettings, readStatus, readUsage,
  type AiStatus, type AiUsage, type CompanyAiAdmin, type CompanyAiSettings, type KeyName, type SettingsBody,
} from "./companyAi";
import { DASHBOARD_API_URL } from "./env";
import { getSession } from "./humanSession";

const ADMIN = `${DASHBOARD_API_URL}/api/dashboard/company-ai`;
const VIEW = `${DASHBOARD_API_URL}/api/dashboard/ai-connection`;

async function call(url: string, body?: unknown): Promise<unknown> {
  const token = getSession()?.token;
  const res = await fetch(url, {
    method: body === undefined ? "GET" : "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const json: unknown = await res.json().catch(() => null);
  const data = (json as { data?: unknown } | null)?.data;
  if (!res.ok || data === undefined) throw failureError(json, res.status, `요청 실패 (${res.status})`);
  return data;
}

const must = <T>(v: T | null): T => {
  if (v === null) throw new Error("서버 응답을 읽지 못했어요.");
  return v;
};

export const getCompanyAi = async (): Promise<CompanyAiAdmin> => must(readCompanyAi(await call(ADMIN)));
export async function saveCompanyAiSettings(body: SettingsBody): Promise<{ settings: CompanyAiSettings; changed: string[] }> {
  const d = (await call(`${ADMIN}/settings`, body)) as { settings?: unknown; changed?: unknown };
  return { settings: readSettings(d.settings), changed: Array.isArray(d.changed) ? d.changed.filter((x): x is string => typeof x === "string") : [] };
}

export type KeyResult = { applied: boolean; op: string; hint: string };
const keyResult = (d: unknown): KeyResult => {
  const o = (d ?? {}) as { applied?: unknown; op?: unknown; hint?: unknown; request?: { hint?: unknown } };
  const hint = typeof o.hint === "string" ? o.hint : typeof o.request?.hint === "string" ? o.request.hint : "";
  return { applied: o.applied === true, op: typeof o.op === "string" ? o.op : "", hint };
};
export const putCompanyAiKey = async (name: KeyName, value: string, reason = ""): Promise<KeyResult> =>
  keyResult(await call(`${ADMIN}/keys/${encodeURIComponent(name)}`, { value, reason }));
export const deleteCompanyAiKey = async (name: KeyName, reason = ""): Promise<KeyResult> =>
  keyResult(await call(`${ADMIN}/keys/${encodeURIComponent(name)}/delete`, { reason }));
export const approveKeyRequest = (id: string) => call(`${ADMIN}/requests/${encodeURIComponent(id)}/approve`, {});
export const rejectKeyRequest = (id: string) => call(`${ADMIN}/requests/${encodeURIComponent(id)}/reject`, {});

export const getAiStatus = async (): Promise<AiStatus> => must(readStatus(await call(`${VIEW}/status`)));
export const getAiUsage = async (): Promise<AiUsage> => must(readUsage(await call(`${VIEW}/usage`)));

/** 화면에 보일 실패 이유: 서버 문장 + 오류 코드 */
export function errText(e: unknown): string {
  if (e instanceof ApiError) return `${e.message}${e.code ? ` (${e.code})` : ""}`;
  return e instanceof Error ? e.message : String(e);
}

// SNS 계정 연결(마케팅 › SNS·미디어). 유튜브는 구글 동의 화면으로 보내고, 돌아오면 화면이 `complete`를 불러
// (연결을 시작한 계정으로 로그인한 상태에서만) 서버가 이 회사 키 저장소에 넣는다
const YT = `${DASHBOARD_API_URL}/api/dashboard/marketing/youtube`;
export async function youtubeConnectUrl(): Promise<string> {
  const d = (await call(`${YT}/connect`, {})) as { url?: unknown };
  if (typeof d.url !== "string" || !d.url.startsWith("https://accounts.google.com/")) throw new Error("구글 연결 주소를 받지 못했어요.");
  return d.url;
}
export const youtubeComplete = (code: string, state: string) => call(`${YT}/complete`, { code, state });
export const youtubeDisconnect = () => call(`${YT}/disconnect`, {});
