// 개인 MCP 연결 호출(dashboard-api personal_mcp.py). 로그인 세션 토큰만 쓴다(공용·에이전트 토큰은 서버가 403).
// 계정은 서버가 세션에서 꺼낸다. 여기서 계정을 보내는 칸은 없다. 발급한 토큰 원문은 돌려주기만 하고 어디에도 남기지 않는다.
// nginx는 이 경로에 GET·HEAD·POST만 연다(끊기도 POST .../token/delete).
import { failureError } from "./apiError";
import { DASHBOARD_API_URL } from "./env";
import { getSession } from "./humanSession";
import { readIssued, readMcpSettings, readMine, type McpSettings, type McpSettingsBody, type PersonalMcp } from "./personalMcp";

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

export async function getMyMcp(): Promise<PersonalMcp> {
  const m = readMine(await call(ME));
  if (!m) throw new Error("서버 응답을 읽지 못했어요.");
  return m;
}

async function issued(path: string): Promise<{ token: string; hint: string }> {
  const got = readIssued(await call(`${ME}/${path}`, {}));
  if (!got) throw new Error("발급 응답을 읽지 못했어요.");
  return got;
}
/** 발급. 원문은 이 응답에만 있다 */
export const issueMcpToken = () => issued("token");
/** 다시 발급. 옛 토큰은 바로 쓸 수 없다 */
export const reissueMcpToken = () => issued("token/reissue");
/** 끊기 */
export const disconnectMcp = async (): Promise<void> => { await call(`${ME}/token/delete`, {}); };

export async function saveMyMcpSettings(body: McpSettingsBody): Promise<{ settings: McpSettings; changed: string[] }> {
  const d = (await call(`${ME}/settings`, body)) as { settings?: unknown; changed?: unknown };
  return { settings: readMcpSettings(d.settings), changed: Array.isArray(d.changed) ? d.changed.filter((x): x is string => typeof x === "string") : [] };
}
