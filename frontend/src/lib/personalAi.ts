// 도구 연결 › 내 연결(개인 AI 연결, 예진 결정 11/20)의 순수 계산.
// API: dashboard-api personal_ai.py (`/api/dashboard/me/ai-connection`). 계약: ax_contracts.schemas.personal_ai.
// 나만 보고 나만 바꾼다. 키 원문은 이 파일 어디에도 오지 않는다(끝 4자리 `hint`만). 값은 서버가 준 것만 쓴다.
// npm test(node --test)가 그대로 돌도록 실행 import가 없다.

export type PersonalAgentId =
  | "orchestrator" | "support" | "finance" | "data" | "hr" | "marketing" | "planning" | "dev" | "infra" | "archive";

/** 고를 수 있는 AI 직원(계약 PERSONAL_AI_AGENTS 순서). 이름은 서비스 목록(src/demo/services.ts)과 같게 */
export const PERSONAL_AGENTS: readonly { id: PersonalAgentId; name: string }[] = [
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
const IDS = PERSONAL_AGENTS.map((a) => a.id);
const isAgent = (v: unknown): v is PersonalAgentId => typeof v === "string" && (IDS as string[]).includes(v);
const ordered = (xs: PersonalAgentId[]) => IDS.filter((id) => xs.includes(id));

export interface PersonalSettings {
  model: string; provider: string; base_url: string; monthly_limit_tokens: number | null; agents: PersonalAgentId[]; updated_at: string | null;
}
export interface PersonalUsage {
  month: string | null; recording: boolean; used_tokens: number; limit_tokens: number | null; percent: number | null; partial: boolean;
}
export interface PersonalAi {
  company: { id: string; name: string } | null;
  secret_store: { enabled: boolean; reason: string };
  state: string; // connected | no_key | no_company
  can_save: boolean;
  key: { has_key: boolean; hint: string; updated_at: string | null };
  settings: PersonalSettings;
  usage: PersonalUsage | null;
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : d);
const num = (v: unknown, d = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : d);
const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);

export function readPersonalSettings(v: unknown): PersonalSettings {
  const s = isObj(v) ? v : {};
  return {
    model: str(s.model), provider: str(s.provider, "auto") || "auto", base_url: str(s.base_url),
    monthly_limit_tokens: numOrNull(s.monthly_limit_tokens),
    agents: ordered(Array.isArray(s.agents) ? s.agents.filter(isAgent) : []),
    updated_at: strOrNull(s.updated_at),
  };
}

export function readMine(v: unknown): PersonalAi | null {
  if (!isObj(v) || typeof v.state !== "string" || !isObj(v.secret_store) || !isObj(v.settings)) return null;
  const k = isObj(v.key) ? v.key : {};
  const u = isObj(v.usage) ? v.usage : null;
  const c = v.company;
  return {
    company: isObj(c) && typeof c.id === "string" ? { id: c.id, name: str(c.name, c.id) } : null,
    secret_store: { enabled: v.secret_store.enabled === true, reason: str(v.secret_store.reason) },
    state: v.state,
    can_save: v.can_save === true,
    key: { has_key: k.has_key === true, hint: str(k.hint), updated_at: strOrNull(k.updated_at) },
    settings: readPersonalSettings(v.settings),
    usage: u ? {
      month: strOrNull(u.month), recording: u.recording === true, used_tokens: num(u.used_tokens),
      limit_tokens: numOrNull(u.limit_tokens), percent: numOrNull(u.percent), partial: u.partial === true,
    } : null,
  };
}

export type Tone = "ok" | "wait" | "bad" | "off";
export function mineStateView(m: PersonalAi): { tone: Tone; label: string } {
  if (m.state === "no_company") return { tone: "off", label: "회사 없음" };
  if (m.key.has_key) return { tone: "ok", label: `연결됨 ••••${m.key.hint}` };
  return { tone: "off", label: "연결 안 됨" };
}

/** 저장을 막는 이유(없으면 null). 서버가 409로 막는 경우를 미리 보인다 */
export function mineBlockReason(m: PersonalAi): string | null {
  if (!m.company) return "회사 연결에서 회사를 먼저 정해야 해요. 관리자에게 말해 주세요.";
  if (!m.secret_store.enabled) return m.secret_store.reason || "키 저장소가 꺼져 있어요.";
  if (!m.can_save) return "AI 설정을 둘 곳이 없어요 (서버 설정 AX_COMPANY_AI_DIR 필요).";
  return null;
}

const n = (x: number) => x.toLocaleString("en-US");
const pct = (p: number | null) => (p === null ? "—" : p < 10 ? `${Math.round(p * 10) / 10}%` : `${Math.round(p)}%`);
export function personalUsageKo(u: PersonalUsage | null): string {
  if (!u) return "사용량을 받지 못했어요";
  if (!u.recording) return "사용량 기록이 꺼져 있어 셀 수 없어요 (서버 설정 AX_LLM_USAGE_DB 필요)";
  if (u.limit_tokens === null) return `한도 없음 · 이번 달 내 키 ${n(u.used_tokens)} 토큰 사용`;
  return `이번 달 내 키 사용 ${pct(u.percent)} (${n(u.used_tokens)} / ${n(u.limit_tokens)} 토큰)`;
}

export interface PersonalBody { model: string; base_url: string; monthly_limit_tokens: number | null; agents: PersonalAgentId[] }
const LIMIT_MAX = 10 ** 13;
/** 저장 본문(서버 SettingsIn). 월 한도 칸이 0 이상 정수가 아니면 "invalid" */
export function personalBody(f: { model: string; base_url: string; limit: string; agents: PersonalAgentId[] }): PersonalBody | "invalid" {
  const t = f.limit.trim().replace(/,/g, "");
  let limit: number | null = null;
  if (t) {
    if (!/^\d+$/.test(t) || Number(t) > LIMIT_MAX) return "invalid";
    limit = Number(t);
  }
  return { model: f.model.trim(), base_url: f.base_url.trim(), monthly_limit_tokens: limit, agents: ordered(f.agents) };
}

export const toggleAgent = (cur: PersonalAgentId[], id: PersonalAgentId): PersonalAgentId[] =>
  cur.includes(id) ? cur.filter((x) => x !== id) : ordered([...cur, id]);
