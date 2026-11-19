// 키 관리 화면(dashboard-api `/api/dashboard/secrets*`)의 순수 계산. 값은 어디에도 없다: 서버는 값을 돌려주지 않고
// 화면은 '설정됨'과 끝 4자리 이하만 보여 준다. `npm test`가 그대로 돈다.
// - 값은 입력칸에서 전송하는 순간까지만 쓰고, 전송 뒤 곧바로 비운다(상태·로그에 남기지 않는다).

export const TENANT_RE = /^[a-z0-9][a-z0-9_-]{0,39}$/;
export const NAME_RE = /^[A-Z][A-Z0-9_]{2,63}$/;
export const MIN_VALUE = 8;
export const MAX_VALUE = 4096;

export interface SecretRow {
  tenant: string; name: string; configured: boolean; hint: string; version: number; updated_by: string; updated_at: string;
}
export interface SecretRequest {
  id: string; op: "add" | "replace" | "delete"; tenant: string; name: string; hint: string; fingerprint: string;
  reason: string; requested_by: string; requested_at: string; review_id: string | null;
}
export interface SecretHistoryRow { at: string; by: string; action: string; tenant: string; name: string; review_id: string | null }
export interface SecretState {
  enabled: boolean; canEdit: boolean; myTenants: string[];
  tenants: { tenant: string; secrets: SecretRow[] }[];
  requests: SecretRequest[]; history: SecretHistoryRow[]; suggested: string[];
}

const rec = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : d);
const OPS = ["add", "replace", "delete"];

/** 서버 응답을 읽는다. 허용 칸만 고르고(값·암호문 같은 칸이 와도 버린다) 모양이 틀리면 null */
export function readSecretState(data: unknown): SecretState | null {
  const o = rec(data);
  const viewer = rec(o?.viewer);
  if (!o || !viewer || !Array.isArray(o.tenants)) return null;
  const tenants = o.tenants.flatMap((t) => {
    const r = rec(t);
    if (!r || typeof r.tenant !== "string" || !Array.isArray(r.secrets)) return [];
    const tenant = r.tenant;
    const secrets = r.secrets.flatMap((s): SecretRow[] => {
      const x = rec(s);
      if (!x || typeof x.name !== "string") return [];
      return [{ tenant, name: x.name, configured: x.configured === true, hint: str(x.hint), version: typeof x.version === "number" ? x.version : 1, updated_by: str(x.updated_by), updated_at: str(x.updated_at) }];
    });
    return [{ tenant, secrets }];
  });
  const requests = (Array.isArray(o.requests) ? o.requests : []).flatMap((x): SecretRequest[] => {
    const r = rec(x);
    if (!r || typeof r.id !== "string" || typeof r.tenant !== "string" || typeof r.name !== "string" || !OPS.includes(str(r.op))) return [];
    return [{ id: r.id, op: r.op as SecretRequest["op"], tenant: r.tenant, name: r.name, hint: str(r.hint), fingerprint: str(r.fingerprint), reason: str(r.reason), requested_by: str(r.requested_by), requested_at: str(r.requested_at), review_id: typeof r.review_id === "string" ? r.review_id : null }];
  });
  const history = (Array.isArray(o.history) ? o.history : []).flatMap((x): SecretHistoryRow[] => {
    const r = rec(x);
    if (!r || typeof r.at !== "string" || typeof r.action !== "string") return [];
    return [{ at: r.at, by: str(r.by), action: r.action, tenant: str(r.tenant), name: str(r.name), review_id: typeof r.review_id === "string" ? r.review_id : null }];
  });
  return {
    enabled: viewer.enabled === true, canEdit: viewer.can_edit === true,
    myTenants: Array.isArray(viewer.tenants) ? viewer.tenants.filter((t): t is string => typeof t === "string") : [],
    tenants, requests, history, suggested: Array.isArray(o.suggested_names) ? o.suggested_names.filter((n): n is string => typeof n === "string") : [],
  };
}

/** 화면에 보이는 값: '설정됨 (…끝글자)' (끝 4자리 이하). 값이 없으면 '미설정' */
export const hintKo = (row: Pick<SecretRow, "configured" | "hint"> | undefined): string =>
  row?.configured ? `설정됨${row.hint ? ` (…${row.hint.slice(-4)})` : ""}` : "미설정";

/** 입력 검사(안내용, 서버가 다시 한다). 값이 틀려도 값 자체는 문구에 넣지 않는다 */
export function valueProblem(value: string): string | null {
  if (value.length < MIN_VALUE) return `값은 ${MIN_VALUE}자 이상이어야 해요.`;
  if (value.length > MAX_VALUE) return `값은 ${MAX_VALUE}자 이하여야 해요.`;
  if (value !== value.trim()) return "값 앞뒤에 공백이 있어요.";
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(value)) return "줄바꿈·제어 문자는 넣을 수 없어요.";
  return null;
}
export const nameProblem = (name: string): string | null => (NAME_RE.test(name) ? null : "키 이름은 영대문자·숫자·_ 3~64자예요 (예: MARKETING_VIDEO_API_KEY).");

/** '고객사,고객사' 입력을 목록으로 (공백·중복 제거) */
export const parseTenants = (text: string): string[] => [...new Set(text.split(/[,\s]+/).map((t) => t.trim()).filter(Boolean))];
export const tenantListProblem = (text: string): string | null =>
  parseTenants(text).every((t) => TENANT_RE.test(t)) ? null : "고객사 ID는 영소문자·숫자·-·_ (40자 이하)예요.";

const ACTION_KO: Record<string, string> = {
  request_add: "추가 요청", request_replace: "교체 요청", request_delete: "삭제 요청",
  add: "추가", replace: "교체", delete: "삭제", migrate: "이전(.env → DB)", rewrap: "마스터 키 재암호화",
};
export const historyLineKo = (h: SecretHistoryRow): string => `${h.by} · ${h.tenant}/${h.name} · ${ACTION_KO[h.action] ?? h.action}`;
export const requestLineKo = (r: SecretRequest): string => `${r.tenant}/${r.name} ${r.op === "delete" ? "삭제" : "교체"} 요청${r.hint ? ` (새 값 …${r.hint})` : ""}`;

/** 승인 대기 중인 교체·삭제 요청 */
export const openRequests = (s: SecretState): SecretRequest[] => s.requests.filter((r) => r.op !== "add");
/** 승인 화면에서 승인했는지는 안전 서버가 안다. 이 버튼은 눌러서 확인한다(승인 전이면 보류로 돌아온다) */
export const canTryApply = (s: SecretState, r: SecretRequest): boolean => s.canEdit && s.myTenants.includes(r.tenant) && !!r.review_id;

const CODES: Record<string, string> = {
  SECRET_EDITOR_REQUIRED: "키 담당 권한이 있는 계정만 바꿀 수 있어요. 운영자가 회원·서버 관리에서 켜야 해요.",
  TENANT_NOT_ASSIGNED: "담당 고객사가 아니에요.",
  SECRET_STORE_DISABLED: "키 저장소가 꺼져 있어요(서버가 env 모드). 서버 설정이 먼저 필요해요.",
  SECRET_STORE_UNAVAILABLE: "키 저장소를 열지 못했어요. 서버 담당자에게 알려 주세요.",
  ALREADY_EXISTS: "이미 있는 키예요. 바꾸려면 '교체 요청'을 써 주세요.",
  NOT_FOUND: "그런 키가 없어요.",
  INVALID_VALUE: "값이 맞지 않아요 (8~4096자, 앞뒤 공백·줄바꿈 없음).",
  INVALID_NAME: "키 이름 형식이 맞지 않아요.",
  INVALID_TENANT: "고객사 ID 형식이 맞지 않아요.",
  REQUEST_NOT_FOUND: "그런 요청이 없어요(이미 적용됐을 수 있어요).",
  REQUEST_EXPIRED: "요청이 오래돼 만료됐어요. 다시 요청해 주세요.",
  REQUEST_MISMATCH: "요청 종류가 맞지 않아요.",
};
export function secretHttpErrorKo(status: number | undefined, code?: string | null): string {
  if (code && CODES[code]) return CODES[code];
  if (status === 401) return "로그인이 필요해요. 다시 로그인해 주세요.";
  if (status === 403) return "운영자·관리자로 로그인한 계정만 볼 수 있어요.";
  if (status === 404) return "서버가 아직 이 기능을 지원하지 않아요.";
  if (status === 405) return "서버가 이 요청 경로를 허용하지 않아요. 서버 허용 경로(nginx) 추가가 필요해요.";
  return "키 서버에 닿지 못했어요. 잠시 뒤 다시 시도해 주세요.";
}

/** 쓰기 응답을 문구로. 값은 응답에 없다. held = 다른 사람의 승인을 기다림 */
export function writeResultKo(data: unknown): { kind: "done" | "held" | "failed"; text: string; reviewId: string | null } {
  const result = rec(rec(data)?.result);
  const outcome = str(result?.outcome);
  const out = rec(result?.output);
  if (outcome === "applied") return { kind: "done", text: "저장했어요. 값은 다시 볼 수 없고 교체만 할 수 있어요.", reviewId: null };
  if (outcome === "held") {
    return { kind: "held", text: "요청을 올렸어요. 요청자와 다른 담당자가 승인 화면에서 승인한 뒤 '승인됨 → 적용'을 눌러야 바뀌어요.", reviewId: typeof out?.review_id === "string" ? out.review_id : null };
  }
  return { kind: "failed", text: `처리되지 않았어요${outcome ? ` (${outcome})` : ""}.`, reviewId: null };
}
