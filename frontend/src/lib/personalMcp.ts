// 도구 연결 › 내 연결(개인 MCP 연결, 예진 결정 11/20)의 순수 계산.
// 내 AI 도구(Claude Code·Codex·Cursor)가 MCP로 AXcellent에 붙어 AI 직원에게 일을 시킨다. 모델·비용은 내 도구 몫.
// API: dashboard-api personal_mcp.py (`/api/dashboard/me/ai-connection`). 계약: ax_contracts.schemas.personal_mcp.
// 토큰 원문은 발급 응답에서만 받고(readIssued) 여기 상태에는 끝 4자리(`hint`)만 둔다. 값은 서버가 준 것만 쓴다.
// npm test(node --test)가 그대로 돌도록 실행 import가 없다.

export type McpAgentId =
  | "orchestrator" | "support" | "finance" | "data" | "hr" | "marketing" | "planning" | "dev" | "infra" | "archive";

/** 고를 수 있는 AI 직원(계약 PERSONAL_MCP_AGENTS 순서). 이름은 서비스 목록(src/demo/services.ts)과 같게 */
export const MCP_AGENTS: readonly { id: McpAgentId; name: string }[] = [
  { id: "orchestrator", name: "총괄 AI" },
  { id: "support", name: "고객지원 AI" },
  { id: "finance", name: "재무 AI" },
  { id: "data", name: "데이터분석 AI" },
  { id: "hr", name: "인사 AI" },
  { id: "marketing", name: "마케팅 AI" },
  { id: "planning", name: "기획 AI" },
  { id: "dev", name: "개발 AI" },
  { id: "infra", name: "인프라 AI" },
  { id: "archive", name: "아카이브 AI" },
];
const IDS = MCP_AGENTS.map((a) => a.id);
const isAgent = (v: unknown): v is McpAgentId => typeof v === "string" && (IDS as string[]).includes(v);
const ordered = (xs: McpAgentId[]) => IDS.filter((id) => xs.includes(id));

export interface McpSettings { agents: McpAgentId[]; monthly_share_percent: number | null; updated_at: string | null }
export interface McpUsage {
  month: string | null; recording: boolean; used_tokens: number; budget_tokens: number | null; share_percent: number | null;
  limit_tokens: number | null; percent_of_budget: number | null; percent_of_limit: number | null; partial: boolean;
}
export interface PersonalMcp {
  company: { id: string; name: string } | null;
  state: string; // connected | not_issued | no_company
  can_save: boolean;
  token: { issued: boolean; hint: string; issued_at: string | null };
  settings: McpSettings;
  usage: McpUsage | null;
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : d);
const num = (v: unknown, d = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : d);
const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);

export function readMcpSettings(v: unknown): McpSettings {
  const s = isObj(v) ? v : {};
  return {
    agents: ordered(Array.isArray(s.agents) ? s.agents.filter(isAgent) : []),
    monthly_share_percent: numOrNull(s.monthly_share_percent),
    updated_at: strOrNull(s.updated_at),
  };
}

export function readMine(v: unknown): PersonalMcp | null {
  if (!isObj(v) || typeof v.state !== "string" || !isObj(v.settings)) return null;
  const t = isObj(v.token) ? v.token : {};
  const u = isObj(v.usage) ? v.usage : null;
  const c = v.company;
  return {
    company: isObj(c) && typeof c.id === "string" ? { id: c.id, name: str(c.name, c.id) } : null,
    state: v.state,
    can_save: v.can_save === true,
    token: { issued: t.issued === true, hint: str(t.hint), issued_at: strOrNull(t.issued_at) },
    settings: readMcpSettings(v.settings),
    usage: u ? {
      month: strOrNull(u.month), recording: u.recording === true, used_tokens: num(u.used_tokens),
      budget_tokens: numOrNull(u.budget_tokens), share_percent: numOrNull(u.share_percent),
      limit_tokens: numOrNull(u.limit_tokens), percent_of_budget: numOrNull(u.percent_of_budget),
      percent_of_limit: numOrNull(u.percent_of_limit), partial: u.partial === true,
    } : null,
  };
}

/** 발급·다시 발급 응답. 원문은 `axm_`으로 시작할 때만 받는다(화면에 한 번만 보이고 버린다) */
export function readIssued(v: unknown): { token: string; hint: string } | null {
  if (!isObj(v) || typeof v.token !== "string" || !v.token.startsWith("axm_")) return null;
  return { token: v.token, hint: str(v.hint, v.token.slice(-4)) };
}

export type Tone = "ok" | "wait" | "bad" | "off";
export function mineStateView(m: PersonalMcp): { tone: Tone; label: string } {
  if (m.state === "no_company") return { tone: "off", label: "회사 없음" };
  if (m.token.issued) return { tone: "ok", label: `연결됨 ••••${m.token.hint}` };
  return { tone: "off", label: "연결 안 됨" };
}

/** 저장·발급을 막는 이유(없으면 null). 서버가 409로 막는 경우를 미리 보인다 */
export function mineBlockReason(m: PersonalMcp): string | null {
  if (!m.company) return "회사 연결에서 회사를 먼저 정해야 해요. 관리자에게 말해 주세요.";
  if (!m.can_save) return "AI 설정을 둘 곳이 없어요 (서버 설정 AX_COMPANY_AI_DIR 필요).";
  return null;
}

const n = (x: number) => x.toLocaleString("en-US");
const pct = (p: number) => (p < 10 ? `${Math.round(p * 10) / 10}%` : `${Math.round(p)}%`);
export function shareUsageKo(u: McpUsage | null): string {
  if (!u) return "사용량을 받지 못했어요";
  if (!u.recording) return "사용량 기록이 꺼져 있어 셀 수 없어요 (서버 설정 AX_LLM_USAGE_DB 필요)";
  if (u.budget_tokens === null || u.percent_of_budget === null) {
    return `이번 달 ${n(u.used_tokens)} 토큰 · 회사 월 예산이 없어 % 한도는 걸리지 않아요`;
  }
  const ofBudget = `회사 예산의 ${pct(u.percent_of_budget)}`;
  if (u.limit_tokens === null || u.percent_of_limit === null) return `이번 달 ${n(u.used_tokens)} 토큰 · ${ofBudget} · 내 몫 제한 없음`;
  return `이번 달 ${n(u.used_tokens)} / ${n(u.limit_tokens)} 토큰 (내 몫의 ${pct(u.percent_of_limit)}) · ${ofBudget}`;
}

export interface McpSettingsBody { agents: McpAgentId[]; monthly_share_percent: number | null }
/** 저장 본문(서버 SettingsIn). 내 몫 칸이 1~100 정수가 아니면 "invalid", 비우면 제한 없음 */
export function shareBody(f: { share: string; agents: McpAgentId[] }): McpSettingsBody | "invalid" {
  const t = f.share.trim().replace(/%$/, "").trim();
  let share: number | null = null;
  if (t) {
    if (!/^\d+$/.test(t)) return "invalid";
    share = Number(t);
    if (share < 1 || share > 100) return "invalid";
  }
  return { agents: ordered(f.agents), monthly_share_percent: share };
}

export const toggleAgent = (cur: McpAgentId[], id: McpAgentId): McpAgentId[] =>
  cur.includes(id) ? cur.filter((x) => x !== id) : ordered([...cur, id]);

// ---- 연결 방법 (도구별 복사용 설정). 서버 이름은 예전 개발자 PC 연결과 같은 axcellent-dev ----

export type McpClient = "claude" | "codex" | "cursor";
export const MCP_CLIENTS: { id: McpClient; label: string }[] = [
  { id: "claude", label: "Claude Code" },
  { id: "codex", label: "Codex" },
  { id: "cursor", label: "Cursor" },
];
/** 설정에 넣는 토큰 자리. 원문은 발급 상자에서 복사해 이 자리에 넣는다(설정 글에 원문을 싣지 않는다) */
export const TOKEN_PLACEHOLDER = "axm_…";
export const MCP_SERVER_NAME = "axcellent-dev";
// Codex·Cursor는 토큰을 설정 파일이 아니라 환경변수에서 읽는다
export const TOKEN_ENV = "AXCELLENT_MCP_TOKEN";

export function mcpSetup(client: McpClient, url: string): { label: string; note?: string; text: string }[] {
  const setEnv = { label: "토큰을 환경변수로", note: "Git Bash · 다시 켠 뒤 적용", text: `setx ${TOKEN_ENV} "${TOKEN_PLACEHOLDER}"` };
  if (client === "claude") {
    return [
      { label: "MCP 서버 붙이기", note: "터미널", text: `claude mcp add --transport http ${MCP_SERVER_NAME} ${url} --header "Authorization: Bearer ${TOKEN_PLACEHOLDER}"` },
      { label: "확인", text: "claude mcp list" },
    ];
  }
  if (client === "codex") {
    return [
      setEnv,
      { label: "MCP 서버 붙이기", text: `codex mcp add ${MCP_SERVER_NAME} --url ${url} --bearer-token-env-var ${TOKEN_ENV}` },
      { label: "확인", text: "codex mcp list" },
    ];
  }
  const json = { mcpServers: { [MCP_SERVER_NAME]: { url, headers: { Authorization: `Bearer \${env:${TOKEN_ENV}}` } } } };
  return [setEnv, { label: "MCP 서버 붙이기", note: "~/.cursor/mcp.json", text: JSON.stringify(json, null, 2) }];
}
