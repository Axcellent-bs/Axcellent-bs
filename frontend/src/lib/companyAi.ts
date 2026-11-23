// 회사 AI 연결 화면(시스템 관리 › 회사 연결 › 공통 AI 도구 연결, 도구 연결 › AI 연결)의 순수 계산.
// 설계: docs/superpowers/specs/2026-11-20-company-ai-connection-design.md, API: dashboard-api company_ai.py.
// 값은 서버가 준 것만 쓴다. 키 원문은 이 파일 어디에도 오지 않는다(끝 4자리 `hint`만).
// 공급자 판별 규칙은 서버(공통 LLM 호출부, 설계 5장)에 아직 없다. 그래서 화면은 서버의 `provider`를 그대로 쓰고,
// 모델 이름 앞부분(claude-·gpt-·gemini-)은 "짐작" 표시에만 쓴다. 저장할 때는 주소가 있으면 openai_compatible, 없으면 auto.
// npm test(node --test)가 그대로 돌도록 실행 import가 없다.

export type AiState = "connected" | "no_key" | "trial" | "trial_exhausted" | "budget_exhausted" | "no_company";
export type Tone = "ok" | "wait" | "bad" | "off";
export type KeyName =
  | "LLM_API_KEY" | "DEV_CODER_LLM_API_KEY" | "MARKETING_VIDEO_API_KEY"
  // SNS 계정 연결(회사마다 다름, 11/23)
  | "MARKETING_IG_ACCESS_TOKEN" | "MARKETING_IG_USER_ID" | "MARKETING_YOUTUBE_REFRESH_TOKEN" | "MARKETING_HIGGSFIELD_API_KEY"
  // 인스타 로그인 연결(Meta 통합 로그인, 60일 장기 토큰, 서버가 연장한다 11/23)
  | "MARKETING_IG_OAUTH_REFRESH_TOKEN"
  // Higgsfield 계정 연결(원격 MCP OAuth, 회사 구독 크레딧, 11/23)
  | "MARKETING_HIGGSFIELD_MCP_REFRESH_TOKEN";
export const KEY_NAMES: readonly KeyName[] = [
  "LLM_API_KEY", "DEV_CODER_LLM_API_KEY", "MARKETING_VIDEO_API_KEY",
  "MARKETING_IG_ACCESS_TOKEN", "MARKETING_IG_USER_ID", "MARKETING_YOUTUBE_REFRESH_TOKEN", "MARKETING_HIGGSFIELD_API_KEY",
  "MARKETING_HIGGSFIELD_MCP_REFRESH_TOKEN",
  "MARKETING_IG_OAUTH_REFRESH_TOKEN",
];

export type CoderMode = "company" | "separate";
export interface CompanyAiSettings {
  model: string;
  provider: string;
  base_url: string;
  monthly_budget_tokens: number | null;
  /** 월 예산의 몇 %에 이르면 AI 호출을 멈출지(1~100). null이면 100 */
  monthly_limit_percent: number | null;
  coder: { mode: CoderMode; model: string; base_url: string };
  video: { provider: string; enabled: boolean };
  embed_model: string;
  embed_base_url: string;
  updated_at: string | null;
  updated_by: string;
}
export interface KeyRow { name: KeyName; has_key: boolean; hint: string; updated_at: string | null; updated_by: string | null }
export interface KeyRequest { id: string; op: string; name: KeyName; hint: string; reason: string; requested_by: string; requested_at: string }
export interface HistoryEntry {
  at: string; by: string; action: string; name?: string; hint?: string; changed?: string[]; single_admin?: boolean; requested_by?: string;
}
export interface CompanyAiAdmin {
  company: { id: string; name: string } | null;
  secret_store: { enabled: boolean; reason: string };
  can_save: boolean;
  settings: CompanyAiSettings;
  keys: Record<KeyName, KeyRow>;
  requests: KeyRequest[];
  approval: { needed: boolean; admins: number };
  usage_recording: boolean;
  history: HistoryEntry[];
}
export interface MonthUsage {
  used_tokens: number; budget_tokens: number | null; percent: number | null;
  /** 서버가 준 상한 %(없으면 100)와 상한 토큰(예산 × %, 예산이 없으면 null) */
  limit_percent?: number; limit_tokens?: number | null;
}
export interface AiStatus {
  company: { id: string; name: string } | null;
  state: string;
  model: string;
  has_key: boolean;
  month: string | null;
  usage: (MonthUsage & { recording: boolean; partial: boolean }) | null;
  trial: { available: boolean; limit_tokens: number; used_tokens: number; percent: number | null } | null;
  video: { enabled: boolean; provider: string };
  coder: { mode: CoderMode };
  notices: HistoryEntry[];
}
export interface UsageDepartment {
  department: string; input_tokens: number; output_tokens: number; calls: number; total_tokens: number;
  percent_of_budget: number | null; share_percent: number | null;
}
export interface AiUsage {
  month: string | null; recording: boolean; total_tokens: number; budget_tokens: number | null; percent: number | null;
  partial: boolean; departments: UsageDepartment[];
}

// ---- 읽기 (모르는 칸은 버린다) ----

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : d);
const num = (v: unknown, d = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : d);
const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);
const company = (v: unknown) => (isObj(v) && typeof v.id === "string" ? { id: v.id, name: str(v.name, v.id) } : null);
const coderMode = (v: unknown): CoderMode => (v === "separate" ? "separate" : "company");

function readHistory(v: unknown): HistoryEntry | null {
  if (!isObj(v) || typeof v.action !== "string") return null;
  const e: HistoryEntry = { at: str(v.at), by: str(v.by), action: v.action };
  if (typeof v.name === "string") e.name = v.name;
  if (typeof v.hint === "string") e.hint = v.hint;
  if (Array.isArray(v.changed)) e.changed = v.changed.filter((x): x is string => typeof x === "string");
  if (v.single_admin === true) e.single_admin = true;
  if (typeof v.requested_by === "string") e.requested_by = v.requested_by;
  return e;
}
const list = <T>(v: unknown, f: (x: unknown) => T | null): T[] => (Array.isArray(v) ? v.map(f).filter((x): x is T => x !== null) : []);

export function readSettings(v: unknown): CompanyAiSettings {
  const s = isObj(v) ? v : {};
  const c = isObj(s.coder) ? s.coder : {};
  const vid = isObj(s.video) ? s.video : {};
  return {
    model: str(s.model), provider: str(s.provider, "auto") || "auto", base_url: str(s.base_url),
    monthly_budget_tokens: numOrNull(s.monthly_budget_tokens),
    monthly_limit_percent: numOrNull(s.monthly_limit_percent),
    coder: { mode: coderMode(c.mode), model: str(c.model), base_url: str(c.base_url) },
    video: { provider: str(vid.provider), enabled: vid.enabled === true },
    embed_model: str(s.embed_model), embed_base_url: str(s.embed_base_url),
    updated_at: strOrNull(s.updated_at), updated_by: str(s.updated_by),
  };
}

export function readCompanyAi(v: unknown): CompanyAiAdmin | null {
  if (!isObj(v) || !isObj(v.secret_store) || !isObj(v.settings)) return null;
  const rows = Array.isArray(v.keys) ? v.keys.filter(isObj) : [];
  const keys = Object.fromEntries(KEY_NAMES.map((name) => {
    const r = rows.find((x) => x.name === name);
    return [name, { name, has_key: r?.has_key === true, hint: str(r?.hint), updated_at: strOrNull(r?.updated_at), updated_by: strOrNull(r?.updated_by) }];
  })) as Record<KeyName, KeyRow>;
  const requests = list(v.requests, (r): KeyRequest | null =>
    isObj(r) && typeof r.id === "string" && KEY_NAMES.includes(r.name as KeyName)
      ? { id: r.id, op: str(r.op), name: r.name as KeyName, hint: str(r.hint), reason: str(r.reason), requested_by: str(r.requested_by), requested_at: str(r.requested_at) }
      : null);
  const ap = isObj(v.approval) ? v.approval : {};
  return {
    company: company(v.company),
    secret_store: { enabled: v.secret_store.enabled === true, reason: str(v.secret_store.reason) },
    can_save: v.can_save === true,
    settings: readSettings(v.settings),
    keys,
    requests,
    approval: { needed: ap.needed === true, admins: num(ap.admins) },
    usage_recording: v.usage_recording === true,
    history: list(v.history, readHistory),
  };
}

export function readStatus(v: unknown): AiStatus | null {
  if (!isObj(v) || typeof v.state !== "string") return null;
  const u = isObj(v.usage) ? v.usage : null;
  const t = isObj(v.trial) ? v.trial : null;
  const vid = isObj(v.video) ? v.video : {};
  return {
    company: company(v.company),
    state: v.state,
    model: str(v.model),
    has_key: isObj(v.key) && v.key.has_key === true,
    month: strOrNull(v.month),
    usage: u ? {
      recording: u.recording === true, used_tokens: num(u.used_tokens), budget_tokens: numOrNull(u.budget_tokens), percent: numOrNull(u.percent),
      limit_percent: num(u.limit_percent, 100), limit_tokens: numOrNull(u.limit_tokens), partial: u.partial === true,
    } : null,
    trial: t ? { available: t.available === true, limit_tokens: num(t.limit_tokens), used_tokens: num(t.used_tokens), percent: numOrNull(t.percent) } : null,
    video: { enabled: vid.enabled === true, provider: str(vid.provider) },
    coder: { mode: coderMode(isObj(v.coder) ? v.coder.mode : undefined) },
    notices: list(v.notices, readHistory),
  };
}

export function readUsage(v: unknown): AiUsage | null {
  if (!isObj(v)) return null;
  return {
    month: strOrNull(v.month), recording: v.recording === true, total_tokens: num(v.total_tokens), budget_tokens: numOrNull(v.budget_tokens),
    percent: numOrNull(v.percent), partial: v.partial === true,
    departments: list(v.departments, (d): UsageDepartment | null =>
      isObj(d) && typeof d.department === "string"
        ? {
          department: d.department, input_tokens: num(d.input_tokens), output_tokens: num(d.output_tokens), calls: num(d.calls),
          total_tokens: num(d.total_tokens), percent_of_budget: numOrNull(d.percent_of_budget), share_percent: numOrNull(d.share_percent),
        }
        : null),
  };
}

// ---- 공급자 ----

// 표시용 짐작만. 실제 판별은 서버(공통 LLM 호출부) 몫이다
const GUESS: [RegExp, string][] = [[/^claude-/i, "Anthropic"], [/^(gpt-|o\d)/i, "OpenAI"], [/^gemini-/i, "Google"]];
const guess = (model: string) => GUESS.find(([r]) => r.test(model.trim()))?.[1] ?? null;

export function providerView(model: string, provider: string, baseUrl: string): { label: string; needsUrl: boolean } {
  if (provider === "openai_compatible") return { label: "OpenAI 호환 주소", needsUrl: true };
  if (provider && provider !== "auto") return { label: provider, needsUrl: false };
  if (!model.trim()) return { label: "모델 이름을 넣으면 공급자를 보여 줘요", needsUrl: !!baseUrl.trim() };
  const g = guess(model);
  if (!g) return { label: "목록에 없는 모델 · OpenAI 호환 주소를 넣어 주세요", needsUrl: true };
  return { label: `${g} (모델 이름으로 짐작)`, needsUrl: !!baseUrl.trim() };
}

export function providerToSave(_model: string, baseUrl: string, current: string): string {
  if (current && current !== "auto" && current !== "openai_compatible") return current;
  return baseUrl.trim() ? "openai_compatible" : "auto";
}

/** 저장 본문(서버 `SettingsIn`): 받는 칸만. updated_at·updated_by는 서버가 채운다 */
export type SettingsBody = Omit<CompanyAiSettings, "updated_at" | "updated_by">;
export function settingsBody(
  cur: CompanyAiSettings,
  patch: Partial<Pick<CompanyAiSettings, "model" | "base_url" | "monthly_budget_tokens" | "monthly_limit_percent" | "coder" | "video">>,
): SettingsBody {
  const model = (patch.model ?? cur.model).trim();
  const base_url = (patch.base_url ?? cur.base_url).trim();
  const coder = patch.coder ?? cur.coder;
  const video = patch.video ?? cur.video;
  return {
    model,
    provider: providerToSave(model, base_url, cur.provider),
    base_url,
    monthly_budget_tokens: patch.monthly_budget_tokens !== undefined ? patch.monthly_budget_tokens : cur.monthly_budget_tokens,
    monthly_limit_percent: patch.monthly_limit_percent !== undefined ? patch.monthly_limit_percent : cur.monthly_limit_percent,
    coder: { mode: coder.mode, model: coder.model.trim(), base_url: coder.base_url.trim() },
    video: { provider: video.provider.trim(), enabled: video.enabled },
    embed_model: cur.embed_model,
    embed_base_url: cur.embed_base_url,
  };
}

/** 저장을 막는 이유(없으면 null). 서버가 409로 막는 경우를 미리 보인다 */
export function saveBlockReason(a: CompanyAiAdmin): string | null {
  if (!a.company) return "회사 연결에서 회사를 먼저 정해 주세요.";
  if (!a.secret_store.enabled) return a.secret_store.reason || "키 저장소가 꺼져 있어요.";
  if (!a.can_save) return "회사 AI 설정을 둘 곳이 없어요 (서버 설정 AX_COMPANY_AI_DIR 필요).";
  return null;
}

// ---- 숫자·문구 ----

const BUDGET_MAX = 10 ** 13;
export function parseBudget(text: string): number | null | "invalid" {
  const t = text.trim().replace(/,/g, "");
  if (!t) return null;
  if (!/^\d+$/.test(t)) return "invalid";
  const n = Number(t);
  return n <= BUDGET_MAX ? n : "invalid";
}

/** 상한 % 칸: 비우면 null(100으로 본다), 1~100 정수만 */
export function parseLimitPercent(text: string): number | null | "invalid" {
  const t = text.trim().replace(/%$/, "").trim();
  if (!t) return null;
  if (!/^\d+$/.test(t)) return "invalid";
  const n = Number(t);
  return n >= 1 && n <= 100 ? n : "invalid";
}

export const tokensKo = (n: number) => n.toLocaleString("en-US");
/** 사용량 막대 상한선 문구. 예산이 없어 상한이 없으면 null */
export function limitLineKo(u: MonthUsage | null): string | null {
  if (!u || u.limit_tokens == null || u.budget_tokens == null) return null;
  return `${u.limit_percent ?? 100}%(${tokensKo(u.limit_tokens)} 토큰)에서 멈춰요`;
}
export function percentKo(p: number | null): string {
  if (p === null) return "—";
  // 10% 아래는 소수 한 자리까지(0.4%·8.4%), 그 위는 정수
  return p < 10 ? `${Math.round(p * 10) / 10}%` : `${Math.round(p)}%`;
}
export function budgetKo(u: MonthUsage | null): string {
  if (!u) return "이번 달 사용량을 받지 못했어요";
  if (u.budget_tokens === null) return `예산 없음 · 이번 달 ${tokensKo(u.used_tokens)} 토큰 사용`;
  return `이번 달 사용 ${percentKo(u.percent)} (${tokensKo(u.used_tokens)} / ${tokensKo(u.budget_tokens)} 토큰)`;
}

const STATES: Record<AiState, { tone: Tone; label: string; detail: string }> = {
  connected: { tone: "ok", label: "연결됨", detail: "회사 AI 키로 AI 직원이 일해요." },
  trial: { tone: "wait", label: "체험 중", detail: "회사 AI 키가 없어 체험 토큰으로 돌아요. 체험 한도를 다 쓰면 멈춰요." },
  no_key: { tone: "bad", label: "키 없음", detail: "회사 AI 키가 없어 AI 직원이 일하지 못해요." },
  trial_exhausted: { tone: "bad", label: "체험 한도 다 씀", detail: "체험 토큰을 다 썼어요. 회사 AI 키를 넣어야 다시 일해요." },
  budget_exhausted: { tone: "bad", label: "예산 다 씀", detail: "이번 달 예산을 다 써서 멈췄어요. 예산을 올리거나 다음 달을 기다려요." },
  no_company: { tone: "off", label: "회사 없음", detail: "회사 연결에서 회사를 먼저 정해야 해요." },
};
export function stateView(state: string): { tone: Tone; label: string; detail: string } {
  return STATES[state as AiState] ?? { tone: "off", label: `알 수 없는 상태 (${state})`, detail: "" };
}

export function videoView(hasKey: boolean, enabled: boolean): { tone: Tone; label: string } {
  if (!hasKey) return { tone: "wait", label: "꺼짐 · 키 필요" };
  return enabled ? { tone: "ok", label: "켜짐" } : { tone: "off", label: "꺼짐" };
}

/** 동의 화면에서 돌아온 주소의 `#` 뒤(`<prefix>_code`·`<prefix>_state`). 둘 다 있어야 마치기를 부른다 */
function readOauthReturn(hash: string, prefix: "youtube" | "higgsfield" | "instagram"): { code: string; state: string } | null {
  const p = new URLSearchParams(hash.replace(/^#/, ""));
  const code = p.get(`${prefix}_code`) ?? "";
  const state = p.get(`${prefix}_state`) ?? "";
  return code && state && code.length <= 2000 && state.length <= 2000 ? { code, state } : null;
}
export const readYoutubeReturn = (hash: string) => readOauthReturn(hash, "youtube");
export const readHiggsfieldReturn = (hash: string) => readOauthReturn(hash, "higgsfield");
export const readInstagramReturn = (hash: string) => readOauthReturn(hash, "instagram");

/** 회사 인스타 로그인 연결(GET /marketing/instagram/connection). 토큰 값은 오지 않는다 */
export interface InstagramAccount {
  app_configured: boolean;
  connected: boolean;
  /** 받은 토큰 종류: ig(graph.instagram.com) · fb(graph.facebook.com) */
  kind: "ig" | "fb" | null;
  username: string | null;
  profile_url: string | null;
  expires_at: string | null;
  refresh_error: string | null;
  needs_reconnect: boolean;
  user_id_mismatch: boolean;
  connected_by: string | null;
}
export function readInstagramAccount(v: unknown): InstagramAccount | null {
  if (!isObj(v) || typeof v.connected !== "boolean") return null;
  const profile = strOrNull(v.profile_url);
  return {
    app_configured: v.app_configured === true,
    connected: v.connected,
    kind: v.kind === "ig" || v.kind === "fb" ? v.kind : null,
    username: strOrNull(v.username),
    profile_url: profile && /^https:\/\/www\.instagram\.com\//.test(profile) ? profile : null,
    expires_at: strOrNull(v.expires_at),
    refresh_error: strOrNull(v.refresh_error),
    needs_reconnect: v.needs_reconnect === true,
    user_id_mismatch: v.user_id_mismatch === true,
    connected_by: strOrNull(v.connected_by),
  };
}

/** 회사 Higgsfield 계정 연결(GET /marketing/higgsfield/connection). 토큰 값은 오지 않는다 */
export interface HiggsfieldAccount {
  app_configured: boolean;
  connected: boolean;
  email: string | null;
  connected_by: string | null;
  connected_at: string | null;
}
export function readHiggsfieldAccount(v: unknown): HiggsfieldAccount | null {
  if (!isObj(v) || typeof v.connected !== "boolean") return null;
  return {
    app_configured: v.app_configured === true,
    connected: v.connected,
    email: strOrNull(v.email),
    connected_by: strOrNull(v.connected_by),
    connected_at: strOrNull(v.connected_at),
  };
}

export const keyHintKo = (hint: string) => (hint ? `••••${hint}` : "—");
export const KEY_LABEL: Record<KeyName, string> = {
  LLM_API_KEY: "회사 AI 키", DEV_CODER_LLM_API_KEY: "코딩 에이전트 키", MARKETING_VIDEO_API_KEY: "영상 생성 키",
  MARKETING_IG_ACCESS_TOKEN: "인스타 토큰", MARKETING_IG_USER_ID: "인스타 계정 ID", MARKETING_YOUTUBE_REFRESH_TOKEN: "유튜브 채널 연결", MARKETING_HIGGSFIELD_API_KEY: "Higgsfield 키",
  MARKETING_HIGGSFIELD_MCP_REFRESH_TOKEN: "Higgsfield 계정 연결",
  MARKETING_IG_OAUTH_REFRESH_TOKEN: "인스타 로그인 연결",
};
const keyLabel = (name?: string) => (name && KEY_LABEL[name as KeyName]) || name || "키";
const OP_LABEL: Record<string, string> = { add: "등록", replace: "교체", delete: "삭제" };
const FIELD_LABEL: Record<string, string> = {
  model: "모델", provider: "공급자", base_url: "주소", monthly_budget_tokens: "월 예산", monthly_limit_percent: "상한 %", coder: "코딩 에이전트", video: "영상 생성",
  embed_model: "임베딩 모델", embed_base_url: "임베딩 주소",
};

/** 본인 요청에는 승인 버튼이 없다(서버도 403 SELF_APPROVAL로 막는다) */
export const canApprove = (r: KeyRequest, myName: string) => r.requested_by !== myName;

export function keyResultKo(r: { applied: boolean; op: string; hint: string }, admins: number): string {
  const saved = r.hint ? ` 저장된 키 ${keyHintKo(r.hint)}` : "";
  if (r.applied && r.op === "add") return `처음 등록이라 바로 적용했어요.${saved}`;
  if (r.applied && admins <= 1) {
    return r.op === "delete" ? "관리자가 한 명뿐이라 바로 지웠고 회원 모두에게 알렸어요." : `관리자가 한 명뿐이라 바로 바꿨고 회원 모두에게 알렸어요.${saved}`;
  }
  if (r.applied) return `적용했어요.${saved}`;
  return r.op === "delete"
    ? "삭제 요청을 남겼어요. 다른 관리자가 승인하면 적용돼요."
    : `교체 요청을 남겼어요(${keyHintKo(r.hint)}). 다른 관리자가 승인하면 적용돼요.`;
}

export function historyLineKo(e: HistoryEntry): string {
  const who = e.by || "누군가";
  const hint = e.hint ? ` ${keyHintKo(e.hint)}` : "";
  const m = /^(key|request)_(add|replace|delete)$/.exec(e.action);
  if (m) {
    const what = `${keyLabel(e.name)} ${OP_LABEL[m[2]]}${m[1] === "request" ? " 요청" : ""}${hint}`;
    const tail = e.single_admin ? " (관리자 한 명이라 바로 적용)" : e.requested_by && m[1] === "key" ? ` (${e.requested_by} 요청 승인)` : "";
    return `${who} · ${what}${tail}`;
  }
  if (e.action === "reject") return `${who} · ${keyLabel(e.name)} 요청 거절`;
  if (e.action === "settings") return `${who} · 설정 변경: ${(e.changed ?? []).map((c) => FIELD_LABEL[c] ?? c).join(", ") || "—"}`;
  return `${who} · ${e.action}`;
}

export const whenKo = (iso: string) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime())
    ? d.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : "—";
};

// ---- AI 직원별 사용량 원그래프 ----

const AGENT: Record<string, string> = {
  orchestrator: "총괄 AI", support: "고객지원 AI", finance: "재무 AI", data: "데이터분석 AI", hr: "인사 AI", marketing: "마케팅 AI",
  planning: "기획 AI", dev: "개발 AI", infra: "인프라 AI", archive: "아카이브 AI", safety: "안전규칙 AI", memory: "기억 AI",
};
export const agentLabel = (dept: string) => (dept ? AGENT[dept] ?? dept : "부서 없음");

/** 색약도 구분되는 순서(파랑·주황·청록·노랑·분홍). 6번째부터는 회색 기타 */
export const PIE_COLORS = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"],
} as const;
export const OTHER_COLOR = "#a3a3b5";
export const PIE_TOP = 5;

export interface Slice { key: string; label: string; tokens: number; fraction: number; percent: number; color: string }

export function pieSlices(rows: UsageDepartment[], dark: boolean): Slice[] {
  const used = rows.filter((r) => r.total_tokens > 0).sort((a, b) => b.total_tokens - a.total_tokens || a.department.localeCompare(b.department));
  const total = used.reduce((a, r) => a + r.total_tokens, 0);
  if (!total) return [];
  const colors = dark ? PIE_COLORS.dark : PIE_COLORS.light;
  const make = (key: string, label: string, tokens: number, color: string): Slice =>
    ({ key, label, tokens, fraction: tokens / total, percent: Math.round((tokens * 1000) / total) / 10, color });
  const top = used.slice(0, PIE_TOP).map((r, i) => make(r.department, agentLabel(r.department), r.total_tokens, colors[i]));
  const rest = used.slice(PIE_TOP);
  if (rest.length) top.push(make("_other", `기타 ${rest.length}개`, rest.reduce((a, r) => a + r.total_tokens, 0), OTHER_COLOR));
  return top;
}

const pt = (cx: number, cy: number, rad: number, a: number) => `${(cx + rad * Math.cos(a)).toFixed(2)} ${(cy + rad * Math.sin(a)).toFixed(2)}`;

/** 고리 조각 하나의 SVG 경로(a0→a1, 라디안). 한 바퀴 전체면 반쪽 두 개로 닫는다 */
export function ringPath(cx: number, cy: number, R: number, r: number, a0: number, a1: number): string {
  if (a1 - a0 >= Math.PI * 2 - 1e-6) {
    const m = a0 + Math.PI;
    return `M${pt(cx, cy, R, a0)} A${R} ${R} 0 0 1 ${pt(cx, cy, R, m)} A${R} ${R} 0 0 1 ${pt(cx, cy, R, a0)} `
      + `M${pt(cx, cy, r, a0)} A${r} ${r} 0 0 0 ${pt(cx, cy, r, m)} A${r} ${r} 0 0 0 ${pt(cx, cy, r, a0)} Z`;
  }
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M${pt(cx, cy, R, a0)} A${R} ${R} 0 ${large} 1 ${pt(cx, cy, R, a1)} L${pt(cx, cy, r, a1)} A${r} ${r} 0 ${large} 0 ${pt(cx, cy, r, a0)} Z`;
}
