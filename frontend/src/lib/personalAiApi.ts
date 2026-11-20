// 개인 AI 연결 호출(dashboard-api personal_ai.py). 로그인 세션 토큰만 쓴다(공용·에이전트 토큰은 서버가 403).
// 계정은 서버가 세션에서 꺼낸다. 여기서 계정을 보내는 칸은 없다. 키 값은 요청 본문으로만 나가고 어디에도 남기지 않는다.
// nginx는 이 경로에 GET·HEAD·POST만 연다(지우기도 POST .../key/delete).
import { failureError } from "./apiError";
import { DASHBOARD_API_URL } from "./env";
import { getSession } from "./humanSession";
import { readMine, readPersonalSettings, type PersonalAi, type PersonalBody, type PersonalSettings } from "./personalAi";

const ME = `${DASHBOARD_API_URL}/api/dashboard/me/ai-connection`;

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

export async function getMyAi(): Promise<PersonalAi> {
  const m = readMine(await call(ME));
  if (!m) throw new Error("서버 응답을 읽지 못했어요.");
  return m;
}
export async function saveMyAiSettings(body: PersonalBody): Promise<{ settings: PersonalSettings; changed: string[] }> {
  const d = (await call(`${ME}/settings`, body)) as { settings?: unknown; changed?: unknown };
  return { settings: readPersonalSettings(d.settings), changed: Array.isArray(d.changed) ? d.changed.filter((x): x is string => typeof x === "string") : [] };
}
const hintOf = (d: unknown) => (typeof (d as { hint?: unknown } | null)?.hint === "string" ? (d as { hint: string }).hint : "");
export const putMyAiKey = async (value: string): Promise<string> => hintOf(await call(`${ME}/key`, { value }));
export const deleteMyAiKey = async (): Promise<void> => { await call(`${ME}/key/delete`, {}); };
