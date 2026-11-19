// 영상 비용·정책 설정(마케팅 `get_media_cost_limits`·`lower_media_cost_limits`·`raise_media_cost_limits`·
// `request_cost_policy_change`, dashboard-api `/marketing/cost-limits*` 중계). 순수 계산이라 `npm test`가 그대로 돈다.
// 값은 서버가 준 것만 쓴다. 화면 검사는 안내용이고 최종 판단은 서버가 다시 한다.
// - 낮추기(하향): 주의사항 확인 뒤 바로 적용(A). 올리기(상향): 다른 사람 승인(C), 승인 뒤 같은 요청을 다시 보내면 적용
// - 한 요청에 올리는 칸과 낮추는 칸을 섞지 않는다
// - 하향 셀프 승인 스위치(options.lower_self_approve)가 켜져 있으면 확인창 뒤 바로 적용, 꺼져 있으면 낮추기도 다른 사람 승인(보류)
// - 안전 정책 값 자체를 올리는 일은 별도 정책 창(costPolicy.ts)에서 한다

export interface CostLimitsValues {
  video_per_item_krw: number | null;
  video_source: "ui" | "env_video" | "env_item" | "none";
  monthly_budget_krw: number | null;
  monthly_source: "ui" | "env" | "none";
  image_audio_per_item_krw: number | null;
  updated_by: string | null;
  updated_at: string | null;
}
export interface CostBounds { min_krw: number; video_item_max_krw: number; monthly_max_krw: number }
export type PolicyScope = "video_item" | "image_item" | "audio_item" | "daily_all_media" | "video_duration";
export interface PolicyItem {
  key: string; label: string; value: number; unit: "krw" | "s"; op: string; scope: PolicyScope; note: string; max_allowed: number | null;
  default: number | null; min: number | null; absolute_max: number | null; source: "file" | "default";
}
export type ReviewStatus = "pending" | "approved" | "rejected" | "expired" | "unknown";
export interface LimitRequestRow {
  id: string; kind: "raise" | "lower"; requested_by: string; requested_at: string; reason: string;
  payload: { video_per_item_krw?: number; monthly_budget_krw?: number };
  review_status: ReviewStatus;
}
export interface LimitHistoryRow { at: string; by: string; action: "lower" | "raise" | "option"; field: string; old: number | null; new: number | null; reason: string }
export interface CostLimitsState {
  month: string;
  month_total_krw: number | null;
  limits: CostLimitsValues;
  bounds: CostBounds;
  policy: { available: boolean; items: PolicyItem[] };
  options: { lower_self_approve: boolean; updated_by: string | null; updated_at: string | null };
  requests: LimitRequestRow[];
  history: LimitHistoryRow[];
  viewer: { can_edit: boolean; can_edit_policy: boolean };
}
export interface CostLimitsAnswer {
  status: string;
  outcome: string | null;
  output: Record<string, unknown> | null;
}

export type LimitField = "video_per_item_krw" | "monthly_budget_krw";
export const FIELD_KO: Record<LimitField, string> = { video_per_item_krw: "영상 건당 상한", monthly_budget_krw: "월 예산" };
export const FIELD_NAME_KO: Record<string, string> = {
  ...FIELD_KO, lower_self_approve: "하향 셀프 승인",
};

export const krw = (n: number | null | undefined): string => (typeof n === "number" ? `${n.toLocaleString("ko-KR")}원` : "미설정");

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const numOrNull = (v: unknown): number | null => (isNum(v) ? v : null);
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);
const rec = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

const SCOPES: PolicyScope[] = ["video_item", "image_item", "audio_item", "daily_all_media", "video_duration"];
const STATUSES: ReviewStatus[] = ["pending", "approved", "rejected", "expired", "unknown"];

/** 서버가 준 상태를 읽는다. 모양이 틀린 줄은 버리고, 핵심 칸(한도·범위)이 없으면 null */
export function readCostLimits(output: unknown): CostLimitsState | null {
  const o = rec(output);
  const l = rec(o?.limits);
  const b = rec(o?.bounds);
  if (!o || !l || !b || !isNum(b.min_krw) || !isNum(b.video_item_max_krw) || !isNum(b.monthly_max_krw)) return null;
  const pick = <T extends string>(v: unknown, ok: readonly T[], fallback: T): T => (ok.includes(v as T) ? (v as T) : fallback);
  const policyRaw = rec(o.policy);
  const items = (Array.isArray(policyRaw?.items) ? policyRaw.items : []).flatMap((x): PolicyItem[] => {
    const r = rec(x);
    if (!r || typeof r.key !== "string" || typeof r.label !== "string" || !isNum(r.value)) return [];
    return [{
      key: r.key, label: r.label, value: r.value, unit: r.unit === "s" ? "s" : "krw", op: typeof r.op === "string" ? r.op : "",
      scope: pick(r.scope, SCOPES, "video_item"), note: typeof r.note === "string" ? r.note : "", max_allowed: numOrNull(r.max_allowed),
      default: numOrNull(r.default), min: numOrNull(r.min), absolute_max: numOrNull(r.absolute_max), source: r.source === "file" ? "file" : "default",
    }];
  });
  const requests = (Array.isArray(o.requests) ? o.requests : []).flatMap((x): LimitRequestRow[] => {
    const r = rec(x);
    if (!r || typeof r.id !== "string" || (r.kind !== "raise" && r.kind !== "lower")) return [];
    const p = rec(r.payload) ?? {};
    const payload: LimitRequestRow["payload"] = {};
    if (isNum(p.video_per_item_krw)) payload.video_per_item_krw = p.video_per_item_krw;
    if (isNum(p.monthly_budget_krw)) payload.monthly_budget_krw = p.monthly_budget_krw;
    return [{
      id: r.id, kind: r.kind, requested_by: typeof r.requested_by === "string" ? r.requested_by : "", requested_at: typeof r.requested_at === "string" ? r.requested_at : "",
      reason: typeof r.reason === "string" ? r.reason : "", payload, review_status: pick(r.review_status, STATUSES, "unknown"),
    }];
  });
  const history = (Array.isArray(o.history) ? o.history : []).flatMap((x): LimitHistoryRow[] => {
    const r = rec(x);
    if (!r || typeof r.at !== "string" || (r.action !== "lower" && r.action !== "raise" && r.action !== "option")) return [];
    return [{ at: r.at, by: typeof r.by === "string" ? r.by : "", action: r.action, field: typeof r.field === "string" ? r.field : "", old: numOrNull(r.old), new: numOrNull(r.new), reason: typeof r.reason === "string" ? r.reason : "" }];
  });
  return {
    month: typeof o.month === "string" ? o.month : "",
    month_total_krw: numOrNull(o.month_total_krw),
    limits: {
      video_per_item_krw: numOrNull(l.video_per_item_krw),
      video_source: pick(l.video_source, ["ui", "env_video", "env_item", "none"] as const, "none"),
      monthly_budget_krw: numOrNull(l.monthly_budget_krw),
      monthly_source: pick(l.monthly_source, ["ui", "env", "none"] as const, "none"),
      image_audio_per_item_krw: numOrNull(l.image_audio_per_item_krw),
      updated_by: strOrNull(l.updated_by), updated_at: strOrNull(l.updated_at),
    },
    bounds: { min_krw: b.min_krw, video_item_max_krw: b.video_item_max_krw, monthly_max_krw: b.monthly_max_krw },
    policy: { available: policyRaw?.available === true, items },
    options: { lower_self_approve: rec(o.options)?.lower_self_approve === true, updated_by: strOrNull(rec(o.options)?.updated_by), updated_at: strOrNull(rec(o.options)?.updated_at) },
    requests, history,
    viewer: { can_edit: rec(o.viewer)?.can_edit === true, can_edit_policy: rec(o.viewer)?.can_edit_policy === true },
  };
}

export const SOURCE_KO = {
  ui: "화면에서 설정", env_video: "서버 환경변수(영상 전용)", env_item: "서버 환경변수(이미지·BGM과 같은 값)", env: "서버 환경변수", none: "설정 안 됨",
} as const;

// ---- 변경 방향·검사 ----

export type Direction = "up" | "down" | "same";
export interface Draft { video_per_item_krw: number | null; monthly_budget_krw: number | null }
export const directionOf = (current: number | null, next: number | null): Direction =>
  next === null || next === current ? "same" : current === null || next > current ? "up" : "down";

/** 입력 글자를 정수로. 비어 있으면 null(바꾸지 않음), 정수가 아니면 NaN */
export function parseKrw(text: string): number | null {
  const t = text.replace(/[,\s]/g, "");
  if (!t) return null;
  return /^\d+$/.test(t) ? Number(t) : Number.NaN;
}

export interface Change { field: LimitField; from: number | null; to: number; direction: "up" | "down" }
/** 현재 값과 다른 칸만 */
export function changesOf(state: CostLimitsState, draft: Draft): Change[] {
  const cur: Record<LimitField, number | null> = { video_per_item_krw: state.limits.video_per_item_krw, monthly_budget_krw: state.limits.monthly_budget_krw };
  const out: Change[] = [];
  for (const f of ["video_per_item_krw", "monthly_budget_krw"] as const) {
    const to = draft[f];
    const d = directionOf(cur[f], to);
    if (d !== "same" && to !== null && Number.isInteger(to)) out.push({ field: f, from: cur[f], to, direction: d });
  }
  return out;
}
export const isMixed = (changes: readonly Change[]) => changes.some((c) => c.direction === "up") && changes.some((c) => c.direction === "down");

export const policyCeilingText = (policyValue: number) => `정책 상한은 ${krw(policyValue)}이라 그 이상은 정책 변경(관리자 승인)이 먼저 필요합니다.`;

/** 영상 건당 정책 항목 */
export const videoPolicy = (state: CostLimitsState): PolicyItem | undefined => state.policy.items.find((p) => p.key === "video_cost_single");

export interface DraftCheck { errors: string[]; policyCeiling: number | null; mixed: boolean; warnings: string[]; changes: Change[] }

/** 서버 규칙을 그대로 따라 미리 보여 주는 검사(안내용). 오류가 있으면 보내지 않는다 */
export function checkDraft(state: CostLimitsState, draft: Draft): DraftCheck {
  const errors: string[] = [];
  const warnings: string[] = [];
  let policyCeiling: number | null = null;
  const { bounds } = state;
  for (const f of ["video_per_item_krw", "monthly_budget_krw"] as const) {
    const v = draft[f];
    if (v === null) continue;
    if (!Number.isInteger(v)) { errors.push(`${FIELD_KO[f]}는 숫자(원)로 적어 주세요.`); continue; }
    if (v < bounds.min_krw) errors.push(`${FIELD_KO[f]}는 ${krw(bounds.min_krw)} 이상이어야 합니다(0원으로는 막을 수 없습니다).`);
  }
  const video = draft.video_per_item_krw;
  if (video !== null && Number.isInteger(video)) {
    if (video > bounds.video_item_max_krw) errors.push(`영상 건당 상한은 코드 절대 상한 ${krw(bounds.video_item_max_krw)}을 넘을 수 없습니다.`);
    const pol = videoPolicy(state);
    if (pol && pol.max_allowed !== null && video > pol.max_allowed) {
      policyCeiling = pol.value;
      errors.push(policyCeilingText(pol.value));
    }
  }
  const monthly = draft.monthly_budget_krw;
  if (monthly !== null && Number.isInteger(monthly) && monthly > bounds.monthly_max_krw) errors.push(`월 예산은 코드 절대 상한 ${krw(bounds.monthly_max_krw)}을 넘을 수 없습니다.`);
  const effVideo = video ?? state.limits.video_per_item_krw;
  const effMonthly = monthly ?? state.limits.monthly_budget_krw;
  if (effVideo !== null && effMonthly !== null && Number.isInteger(effVideo) && Number.isInteger(effMonthly) && effMonthly < effVideo) {
    errors.push(`월 예산(${krw(effMonthly)})이 영상 건당 상한(${krw(effVideo)})보다 작을 수 없습니다.`);
  }
  if (monthly !== null && Number.isInteger(monthly) && state.month_total_krw !== null && monthly < state.month_total_krw) {
    warnings.push(`월 예산 ${krw(monthly)}이 이번 달 누적 ${krw(state.month_total_krw)}보다 낮아 새 생성이 이번 달 내내 막힙니다.`);
  }
  const changes = changesOf(state, draft);
  const mixed = isMixed(changes);
  if (mixed) errors.push("올리는 칸과 낮추는 칸을 한 번에 바꿀 수 없습니다. 하나씩 나눠서 요청해 주세요.");
  return { errors, policyCeiling, mixed, warnings, changes };
}

/** 낮추기가 어느 길로 가는지(스위치 상태) 한 줄 */
export const lowerPathKo = (selfApprove: boolean): string =>
  selfApprove
    ? "낮추기: 주의사항을 확인하면 바로 적용됩니다(하향 셀프 승인 허용 중)."
    : "낮추기: 하향 셀프 승인이 꺼져 있어 낮추기도 요청자와 다른 담당자의 승인이 필요합니다.";

/** 낮추기 확인창 문구. 줄 목록 */
export function loweringSummary(changes: readonly Change[], warnings: readonly string[]): string[] {
  const lines = changes.map((c) => `${FIELD_KO[c.field]}: ${krw(c.from)} → ${krw(c.to)}`);
  lines.push("지금부터 새로 시작하는 영상 생성에 적용됩니다. 이미 진행 중인 생성은 영향이 없습니다.");
  lines.push("상한이 낮아지면 지금까지 만들어지던 영상이 '건당 상한 초과'로 막힐 수 있습니다.");
  lines.push("다시 올리려면 다른 담당자의 승인이 필요합니다.");
  return [...lines, ...warnings];
}

// ---- 서버 응답 문구 ----

export type LimitResult = { kind: "done" | "held" | "failed"; text: string; policyKey?: string; policyKrw?: number; needsConfirm?: boolean; reviewId?: string | null; warnings?: string[] };

const DONE_KO: Record<string, string> = {
  lowered: "상한을 낮췄습니다. 지금부터 새로 시작하는 생성에 적용됩니다.",
  raised: "상한을 올렸습니다. 지금부터 새로 시작하는 생성에 적용됩니다.",
  option_saved: "하향 셀프 승인 설정을 저장했습니다.",
};
const FAIL_KO: Record<string, string> = {
  invalid_input: "입력이 맞지 않습니다.",
  reason_required: "사유를 적어 주세요.",
  no_change: "바꿀 값이 현재 값과 같습니다.",
  mixed_direction: "올리는 칸과 낮추는 칸을 한 번에 바꿀 수 없습니다. 하나씩 나눠서 요청해 주세요.",
  not_a_decrease: "낮추는 요청이 아닙니다. 올릴 때는 '상향 요청'을 쓰세요.",
  not_an_increase: "올리는 요청이 아닙니다. 낮출 때는 '낮추기'를 쓰세요.",
  policy_unavailable: "안전 정책 값을 읽지 못해 지금은 저장할 수 없습니다. 잠시 뒤 다시 시도해 주세요.",
  limit_store_error: "서버가 설정을 저장하지 못했습니다.",
  rejected_by_review: "승인 담당이 거절했습니다.",
};

const num = (o: Record<string, unknown> | null, k: string): number | undefined => (o && isNum(o[k]) ? (o[k] as number) : undefined);

export function warningKo(w: unknown): string {
  const r = rec(w);
  if (r?.code === "monthly_below_month_total") {
    return `월 예산 ${krw(num(r, "monthly_krw"))}이 이번 달 누적 ${krw(num(r, "month_total_krw"))}보다 낮아 새 생성이 막힙니다.`;
  }
  return "확인이 필요한 주의사항이 있습니다.";
}

/** 응답을 화면 문구로. 보류(held)면 승인 방법을 알린다 */
export function limitResultKo(answer: CostLimitsAnswer): LimitResult {
  const out = answer.output;
  if (answer.status === "input-required" || answer.outcome === "held") {
    return { kind: "held", text: "다른 담당자의 승인이 필요합니다. 승인 화면에서 승인된 뒤 같은 요청을 다시 보내면 적용됩니다.", reviewId: typeof out?.review_id === "string" ? out.review_id : null };
  }
  if (answer.outcome && DONE_KO[answer.outcome]) return { kind: "done", text: DONE_KO[answer.outcome] };
  const code = answer.outcome ?? "";
  if (code === "confirmation_required") {
    const warnings = (Array.isArray(out?.warnings) ? out.warnings : []).map(warningKo);
    return { kind: "failed", text: "낮추기는 주의사항을 확인한 뒤에 적용됩니다.", needsConfirm: true, warnings };
  }
  if (code === "over_policy_ceiling") {
    const policy = num(out, "policy_krw");
    return {
      kind: "failed",
      text: policy !== undefined ? policyCeilingText(policy) : "정책 상한을 넘어 저장할 수 없습니다. 정책 변경(관리자 승인)이 먼저 필요합니다.",
      policyKey: typeof out?.policy_key === "string" ? out.policy_key : "video_cost_single", policyKrw: policy,
    };
  }
  if (code === "over_absolute_max") return { kind: "failed", text: `${FIELD_NAME_KO[String(out?.field)] ?? "값"}이 코드 절대 상한 ${krw(num(out, "max_krw"))}을 넘습니다.` };
  if (code === "below_minimum") return { kind: "failed", text: `${krw(num(out, "min_krw"))} 이상으로 적어 주세요(0원으로는 막을 수 없습니다).` };
  if (code === "monthly_below_item") return { kind: "failed", text: `월 예산(${krw(num(out, "monthly_krw"))})이 영상 건당 상한(${krw(num(out, "item_krw"))})보다 작을 수 없습니다.` };
  return { kind: "failed", text: FAIL_KO[code] ?? `처리하지 못했습니다${code ? ` (${code})` : ""}` };
}

export function limitHttpErrorKo(status: number | undefined, code?: string | null): string {
  if (status === 401) return "로그인이 필요합니다. 다시 로그인해 주세요.";
  if (status === 403) {
    if (code === "LIMIT_EDITOR_REQUIRED") return "비용 상한 변경 권한이 없는 계정입니다. 관리자가 회원 관리에서 권한을 켜야 합니다.";
    return "운영자·관리자로 로그인한 계정만 볼 수 있습니다.";
  }
  if (status === 404) return "서버가 아직 이 기능을 지원하지 않습니다.";
  if (status === 400 || status === 422) return "입력을 서버가 받아들이지 않았습니다.";
  return "비용 설정 서버에 닿지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}

// ---- 승인 요청 줄 ----

export function reviewStatusKo(r: LimitRequestRow): string {
  switch (r.review_status) {
    case "pending": return "승인 대기";
    case "approved": return "승인됨 - 같은 요청을 다시 눌러 적용";
    case "rejected": return "거절";
    case "expired": return "만료";
    default: return "승인 상태는 승인 화면에서 확인 - 승인됐다면 적용을 눌러 보세요";
  }
}
/**
 * 승인됨이면 같은 요청을 다시 보내 적용할 수 있다. 서버가 상태를 안 주면(unknown) 눌러 볼 수 있다:
 * 아직 승인 전이면 서버가 다시 보류로 답하고 아무것도 바뀌지 않는다
 */
export const canApply = (r: LimitRequestRow) => r.review_status === "approved" || r.review_status === "unknown";
/** 보여 줄 요청: 거절·만료는 숨긴다 */
export const visibleRequests = (rows: readonly LimitRequestRow[]) => rows.filter((r) => r.review_status !== "rejected" && r.review_status !== "expired");

export function requestSummaryKo(r: LimitRequestRow): string {
  const p = r.payload;
  const parts: string[] = [];
  if (p.video_per_item_krw !== undefined) parts.push(`영상 건당 ${krw(p.video_per_item_krw)}`);
  if (p.monthly_budget_krw !== undefined) parts.push(`월 예산 ${krw(p.monthly_budget_krw)}`);
  return `${r.kind === "raise" ? "상향" : "하향"} 요청: ${parts.join(", ") || "?"}`;
}

export function historyLineKo(h: LimitHistoryRow): string {
  if (h.action === "option") {
    return `하향 셀프 승인 ${h.new === 1 ? "켬" : "끔"}${h.reason ? ` (${h.reason})` : ""}`;
  }
  const verb = h.action === "lower" ? "낮춤" : "올림";
  return `${FIELD_NAME_KO[h.field] ?? h.field} ${verb}: ${h.old === null ? "미설정" : h.old} → ${h.new === null ? "미설정" : h.new}${h.reason ? ` (${h.reason})` : ""}`;
}

/** 정책 줄 값: 원이면 원, 초면 초 */
export const policyValueKo = (p: PolicyItem) => (p.unit === "s" ? `${p.value}초` : krw(p.value));
