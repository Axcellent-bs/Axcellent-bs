// 사용량·안전 정책 창(dashboard-api `/api/dashboard/cost-policy*`, 안전 서버가 값·승인·적용을 맡는다). 순수 계산이라 `npm test`가 그대로 돈다.
// - 값은 서버가 준 것만 보여 준다. 화면 검사는 안내용이고 서버가 다시 한다(하한~절대 상한, 현재 값과 같으면 거절).
// - 변경 요청은 사람 승인(요청자와 다른 사람)이 필요하고, 승인 뒤 '적용'을 눌러야 파일에 반영된다. 안전 서버는 재시작 없이 다시 읽는다.
// - 이 창의 응답은 `{status, outcome, output}`으로 싸지 않은 일반 data다.

// 원화 비용 규칙(건당·하루 합계)은 없앴다. 고칠 수 있는 안전 정책은 영상 길이 하나다
export type PolicyScope = "video_duration";
export const POLICY_SCOPES: PolicyScope[] = ["video_duration"];

export interface PolicyRule {
  key: string; label: string; scope: PolicyScope; unit: "krw" | "s"; op: string;
  default: number; value: number; min: number; absolute_max: number;
  source: "file" | "default"; updated_by: string | null; updated_at: string | null;
}
export type RequestStatus = "pending" | "approved" | "rejected" | "expired";
export interface PolicyRequest {
  review_id: string; status: RequestStatus; requested_at: string | null; decided_by: string | null;
  rule_key: string; requested_value: number; requested_by: string; reason: string;
}
export interface PolicyHistoryRow {
  at: string; key: string; old: number | null; new: number | null; requested_by: string; approved_by: string; review_id: string; reason: string;
}
export interface CostPolicyState {
  editable: boolean;
  items: PolicyRule[];
  requests: PolicyRequest[];
  history: PolicyHistoryRow[];
  viewer: { can_edit_policy: boolean };
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : d);
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);
const rec = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const STATUSES: RequestStatus[] = ["pending", "approved", "rejected", "expired"];

/** 서버가 준 정책 창 data를 읽는다. 핵심 칸이 틀린 줄은 버리고, 목록 자체가 없으면 null */
export function readCostPolicy(data: unknown): CostPolicyState | null {
  const o = rec(data);
  if (!o || !Array.isArray(o.items)) return null;
  const items = o.items.flatMap((x): PolicyRule[] => {
    const r = rec(x);
    if (!r || typeof r.key !== "string" || !isNum(r.value) || !isNum(r.default) || !isNum(r.min) || !isNum(r.absolute_max)) return [];
    return [{
      key: r.key, label: str(r.label, r.key), scope: POLICY_SCOPES.includes(r.scope as PolicyScope) ? (r.scope as PolicyScope) : "video_duration",
      unit: r.unit === "s" ? "s" : "krw", op: str(r.op), default: r.default, value: r.value, min: r.min, absolute_max: r.absolute_max,
      source: r.source === "file" ? "file" : "default", updated_by: strOrNull(r.updated_by), updated_at: strOrNull(r.updated_at),
    }];
  });
  const requests = (Array.isArray(o.requests) ? o.requests : []).flatMap((x): PolicyRequest[] => {
    const r = rec(x);
    if (!r || typeof r.review_id !== "string" || typeof r.rule_key !== "string" || !isNum(r.requested_value)) return [];
    return [{
      review_id: r.review_id, status: STATUSES.includes(r.status as RequestStatus) ? (r.status as RequestStatus) : "pending",
      requested_at: strOrNull(r.requested_at), decided_by: strOrNull(r.decided_by), rule_key: r.rule_key, requested_value: r.requested_value,
      requested_by: str(r.requested_by), reason: str(r.reason),
    }];
  });
  const history = (Array.isArray(o.history) ? o.history : []).flatMap((x): PolicyHistoryRow[] => {
    const r = rec(x);
    if (!r || typeof r.at !== "string" || typeof r.key !== "string") return [];
    return [{
      at: r.at, key: r.key, old: isNum(r.old) ? r.old : null, new: isNum(r.new) ? r.new : null, requested_by: str(r.requested_by),
      approved_by: str(r.approved_by), review_id: str(r.review_id), reason: str(r.reason),
    }];
  });
  return { editable: o.editable === true, items, requests, history, viewer: { can_edit_policy: rec(o.viewer)?.can_edit_policy === true } };
}

export const unitKo = (unit: "krw" | "s") => (unit === "s" ? "초" : "원");
export const valueKo = (value: number | null, unit: "krw" | "s"): string =>
  value === null ? "-" : unit === "s" ? `${value}초` : `${value.toLocaleString("ko-KR")}원`;
export const labelOf = (items: readonly PolicyRule[], key: string): string => items.find((i) => i.key === key)?.label ?? key;
export const sourceKo = (r: Pick<PolicyRule, "source">) => (r.source === "file" ? "변경 파일 값" : "기본값(안전 정책 파일)");

/** 입력 글자를 정수로. 비어 있으면 null, 정수가 아니면 NaN */
export function parseValue(text: string): number | null {
  const t = text.replace(/[,\s]/g, "");
  if (!t) return null;
  return /^\d+$/.test(t) ? Number(t) : Number.NaN;
}

/** 요청 입력 검사(안내용). 문제가 없으면 null, 있으면 이유 한 줄 */
export function requestProblem(rule: PolicyRule | undefined, value: number | null, reason: string): string | null {
  if (!rule) return "항목을 골라 주세요.";
  if (value === null) return "바라는 값을 적어 주세요.";
  if (!Number.isInteger(value)) return "바라는 값은 숫자로 적어 주세요.";
  if (value < rule.min || value > rule.absolute_max) {
    return `${rule.label}은 ${valueKo(rule.min, rule.unit)}~${valueKo(rule.absolute_max, rule.unit)} 안에서만 정할 수 있습니다(이 범위는 코드 상수라 승인으로도 넘길 수 없습니다).`;
  }
  if (value === rule.value) return "지금 값과 같습니다.";
  if (!reason.trim()) return "사유를 적어 주세요.";
  if (reason.trim().length > 200) return "사유는 200자 이내로 적어 주세요.";
  return null;
}

export function requestStatusKo(s: RequestStatus): string {
  switch (s) {
    case "pending": return "승인 대기";
    case "approved": return "승인됨 - 적용 버튼으로 반영";
    case "rejected": return "거절";
    case "expired": return "만료";
  }
}
/** 승인됐고 아직 적용(이력에 같은 review_id) 안 된 요청만 적용할 수 있다 */
export const isApplied = (state: Pick<CostPolicyState, "history">, r: PolicyRequest) => state.history.some((h) => h.review_id === r.review_id);
export const canApply = (state: CostPolicyState, r: PolicyRequest) => r.status === "approved" && !isApplied(state, r);
/** 보여 줄 요청: 거절·만료·이미 적용된 것은 이력으로 넘어가 숨긴다 */
export const openRequests = (state: CostPolicyState): PolicyRequest[] =>
  state.requests.filter((r) => (r.status === "pending" || r.status === "approved") && !isApplied(state, r));

export function requestLineKo(items: readonly PolicyRule[], r: PolicyRequest): string {
  const rule = items.find((i) => i.key === r.rule_key);
  return `${rule?.label ?? r.rule_key}: ${valueKo(rule?.value ?? null, rule?.unit ?? "krw")} → ${valueKo(r.requested_value, rule?.unit ?? "krw")}`;
}
export function historyLineKo(items: readonly PolicyRule[], h: PolicyHistoryRow): string {
  const rule = items.find((i) => i.key === h.key);
  const unit = rule?.unit ?? "krw";
  return `${rule?.label ?? h.key}: ${valueKo(h.old, unit)} → ${valueKo(h.new, unit)} (요청 ${h.requested_by || "?"}, 승인 ${h.approved_by || "?"}${h.reason ? `, ${h.reason}` : ""})`;
}

const CODES: Record<string, string> = {
  POLICY_FILE_NOT_CONFIGURED: "서버에 정책 변경 파일이 설정되지 않아 지금은 요청할 수 없습니다. 설치 담당자가 먼저 설정해야 합니다.",
  UNKNOWN_POLICY_RULE: "바꿀 수 없는 정책 항목입니다.",
  VALUE_OUT_OF_RANGE: "허용 범위(하한~절대 상한) 밖의 값입니다.",
  NO_CHANGE: "지금 값과 같습니다.",
  NOT_APPROVED: "아직 승인되지 않았습니다. 요청자와 다른 사람이 승인 화면에서 승인한 뒤 눌러 주세요.",
  ALREADY_APPLIED: "이미 적용된 요청입니다.",
  POLICY_EDITOR_REQUIRED: "정책 변경 권한이 없는 계정입니다. 운영자나 정책 담당 관리자가 회원·서버 관리에서 권한을 켜야 합니다.",
  HUMAN_ONLY: "사람 계정으로 로그인해야 합니다. 에이전트·공용 토큰은 정책을 바꿀 수 없습니다.",
  SAFETY_UNREACHABLE: "안전 서버에 닿지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
};

export function policyHttpErrorKo(status: number | undefined, code?: string | null): string {
  if (code && CODES[code]) return CODES[code];
  if (status === 401) return "로그인이 필요합니다. 다시 로그인해 주세요.";
  if (status === 403) return "운영자·관리자로 로그인한 계정만 볼 수 있습니다.";
  if (status === 404) return "서버가 아직 이 기능을 지원하지 않습니다.";
  if (status === 400 || status === 422) return "입력을 서버가 받아들이지 않았습니다.";
  return "정책 서버에 닿지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}

/** 요청을 만든 응답(안전 서버 ExecuteDecision)을 문구로. 보류가 정상이다 */
export function requestResultKo(data: unknown): { kind: "held" | "failed"; text: string; reviewId: string | null } {
  const d = rec(data);
  if (d?.status === "held") {
    return { kind: "held", text: "요청을 올렸습니다. 요청자와 다른 사람이 승인 화면에서 승인한 뒤, 이 창에서 '승인됨 → 적용'을 눌러야 반영됩니다.", reviewId: typeof d.review_id === "string" ? d.review_id : null };
  }
  return { kind: "failed", text: `요청이 접수되지 않았습니다${typeof d?.status === "string" ? ` (${d.status})` : ""}.`, reviewId: null };
}
