// 우리 키 사용량 예산(마케팅 `get_media_cost_limits`·`lower_media_cost_limits`·`raise_media_cost_limits`,
// dashboard-api `/marketing/cost-limits*` 중계). 순수 계산이라 `npm test`가 그대로 돈다.
// 원화 단가표·건당 금액 상한은 없다. 예산은 공급자 단위 그대로의 월 사용량이다:
// 영상 초·이미지 장·BGM 곡. 종류마다 단위가 달라 합치지 않고, 남은 비율(%)도 종류별로 따로 본다.
// 우리 키(서버 키)에만 걸고 고객사 키(`key_owner=customer`)는 예산을 안 건다.
// 값은 서버가 준 것만 쓴다. 화면 검사는 안내용이고 최종 판단은 서버가 다시 한다.
// - 낮추기(하향): 주의사항 확인 뒤 바로 적용(A). 올리기(상향): 다른 사람 승인(C), 승인 뒤 같은 요청을 다시 보내면 적용
// - 한 요청에 올리는 칸과 낮추는 칸을 섞지 않는다
// - 하향 셀프 승인 스위치(options.lower_self_approve)가 켜져 있으면 확인창 뒤 바로 적용, 꺼져 있으면 낮추기도 다른 사람 승인(보류)
// - 안전 정책 값(영상 길이) 자체를 바꾸는 일은 별도 정책 창(costPolicy.ts)에서 한다

export type MediaKind = "video" | "image" | "audio";
export type UsageUnit = "seconds" | "images" | "tracks";
export type LimitField = "video_seconds_monthly" | "image_count_monthly" | "audio_count_monthly";
export const KINDS: readonly MediaKind[] = ["video", "image", "audio"];
export const LIMIT_FIELDS: readonly LimitField[] = ["video_seconds_monthly", "image_count_monthly", "audio_count_monthly"];
export const FIELD_OF: Record<MediaKind, LimitField> = { video: "video_seconds_monthly", image: "image_count_monthly", audio: "audio_count_monthly" };
export const KIND_KO: Record<MediaKind, string> = { video: "영상", image: "이미지", audio: "BGM" };
export const UNIT_KO: Record<UsageUnit, string> = { seconds: "초", images: "장", tracks: "곡" };
export const UNIT_OF: Record<MediaKind, UsageUnit> = { video: "seconds", image: "images", audio: "tracks" };

export interface UsageRow { unit: UsageUnit; used: number; limit: number | null; remaining_pct: number | null }
export type LimitSource = "ui" | "env" | "none";
export interface CostLimitsValues {
  video_seconds_monthly: number | null;
  image_count_monthly: number | null;
  audio_count_monthly: number | null;
  video_source: LimitSource;
  image_source: LimitSource;
  audio_source: LimitSource;
  /** server = 우리 키(예산을 건다), customer = 고객사 키(예산을 안 건다) */
  key_owner: "server" | "customer";
  quota_applies: boolean;
  updated_by: string | null;
  updated_at: string | null;
}
export interface CostBounds { min: number; max: Record<LimitField, number> }
export type PolicyScope = "video_duration";
export interface PolicyItem {
  key: string; label: string; value: number; unit: "krw" | "s"; op: string; scope: PolicyScope; note: string; max_allowed: number | null;
  default: number | null; min: number | null; absolute_max: number | null; source: "file" | "default";
}
export type ReviewStatus = "pending" | "approved" | "rejected" | "expired" | "unknown";
export type LimitPayload = Partial<Record<LimitField, number>>;
export interface LimitRequestRow {
  id: string; kind: "raise" | "lower"; requested_by: string; requested_at: string; reason: string;
  payload: LimitPayload;
  review_status: ReviewStatus;
}
export interface LimitHistoryRow { at: string; by: string; action: "lower" | "raise" | "option"; field: string; old: number | null; new: number | null; reason: string }
export interface CostLimitsState {
  month: string;
  usage: Partial<Record<MediaKind, UsageRow>>;
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

export const FIELD_KO: Record<LimitField, string> = {
  video_seconds_monthly: "영상 월 예산(초)", image_count_monthly: "이미지 월 예산(장)", audio_count_monthly: "BGM 월 예산(곡)",
};
export const FIELD_NAME_KO: Record<string, string> = {
  ...FIELD_KO, lower_self_approve: "하향 셀프 승인",
};

/** 사용량을 단위와 함께: 120초, 8장, 1곡 */
export const amountKo = (n: number | null | undefined, unit: UsageUnit): string => (typeof n === "number" ? `${n.toLocaleString("ko-KR")}${UNIT_KO[unit]}` : "미설정");

/** 남은 비율 한 줄. 예산이 없으면(고객사 키·미설정) 비율을 지어내지 않는다 */
export const remainingKo = (row: UsageRow | null | undefined): string =>
  row && row.remaining_pct !== null ? `남은 사용량 ${row.remaining_pct}%` : "남은 비율을 알 수 없음";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const numOrNull = (v: unknown): number | null => (isNum(v) ? v : null);
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);
const rec = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

const SCOPES: PolicyScope[] = ["video_duration"];
const STATUSES: ReviewStatus[] = ["pending", "approved", "rejected", "expired", "unknown"];
const UNITS: UsageUnit[] = ["seconds", "images", "tracks"];

/** 서버가 준 상태를 읽는다. 모양이 틀린 줄은 버리고, 핵심 칸(한도·범위)이 없으면 null */
export function readCostLimits(output: unknown): CostLimitsState | null {
  const o = rec(output);
  const l = rec(o?.limits);
  const b = rec(o?.bounds);
  const max = rec(b?.max);
  if (!o || !l || !b || !max || !isNum(b.min) || !LIMIT_FIELDS.every((f) => isNum(max[f]))) return null;
  const pick = <T extends string>(v: unknown, ok: readonly T[], fallback: T): T => (ok.includes(v as T) ? (v as T) : fallback);
  const policyRaw = rec(o.policy);
  const items = (Array.isArray(policyRaw?.items) ? policyRaw.items : []).flatMap((x): PolicyItem[] => {
    const r = rec(x);
    if (!r || typeof r.key !== "string" || typeof r.label !== "string" || !isNum(r.value)) return [];
    return [{
      key: r.key, label: r.label, value: r.value, unit: r.unit === "krw" ? "krw" : "s", op: typeof r.op === "string" ? r.op : "",
      scope: pick(r.scope, SCOPES, "video_duration"), note: typeof r.note === "string" ? r.note : "", max_allowed: numOrNull(r.max_allowed),
      default: numOrNull(r.default), min: numOrNull(r.min), absolute_max: numOrNull(r.absolute_max), source: r.source === "file" ? "file" : "default",
    }];
  });
  const requests = (Array.isArray(o.requests) ? o.requests : []).flatMap((x): LimitRequestRow[] => {
    const r = rec(x);
    if (!r || typeof r.id !== "string" || (r.kind !== "raise" && r.kind !== "lower")) return [];
    const p = rec(r.payload) ?? {};
    const payload: LimitPayload = {};
    for (const f of LIMIT_FIELDS) if (isNum(p[f])) payload[f] = p[f] as number;
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
  const usageRaw = rec(o.usage);
  const usage: CostLimitsState["usage"] = {};
  for (const kind of KINDS) {
    const r = rec(usageRaw?.[kind]);
    if (r && isNum(r.used)) {
      usage[kind] = { unit: pick(r.unit, UNITS, UNIT_OF[kind]), used: r.used, limit: numOrNull(r.limit), remaining_pct: numOrNull(r.remaining_pct) };
    }
  }
  return {
    month: typeof o.month === "string" ? o.month : "",
    usage,
    limits: {
      video_seconds_monthly: numOrNull(l.video_seconds_monthly),
      image_count_monthly: numOrNull(l.image_count_monthly),
      audio_count_monthly: numOrNull(l.audio_count_monthly),
      video_source: pick(l.video_source, ["ui", "env", "none"] as const, "none"),
      image_source: pick(l.image_source, ["ui", "env", "none"] as const, "none"),
      audio_source: pick(l.audio_source, ["ui", "env", "none"] as const, "none"),
      key_owner: l.key_owner === "customer" ? "customer" : "server",
      quota_applies: l.quota_applies !== false,
      updated_by: strOrNull(l.updated_by), updated_at: strOrNull(l.updated_at),
    },
    bounds: { min: b.min, max: Object.fromEntries(LIMIT_FIELDS.map((f) => [f, max[f] as number])) as Record<LimitField, number> },
    policy: { available: policyRaw?.available === true, items },
    options: { lower_self_approve: rec(o.options)?.lower_self_approve === true, updated_by: strOrNull(rec(o.options)?.updated_by), updated_at: strOrNull(rec(o.options)?.updated_at) },
    requests, history,
    viewer: { can_edit: rec(o.viewer)?.can_edit === true, can_edit_policy: rec(o.viewer)?.can_edit_policy === true },
  };
}

export const SOURCE_KO = { ui: "화면에서 설정", env: "서버 환경변수", none: "설정 안 됨" } as const;
export const sourceOf = (l: CostLimitsValues, kind: MediaKind): LimitSource => l[`${kind}_source` as const];

/** 키 주인 한 줄. 고객사 키면 예산이 걸리지 않는다는 점을 밝힌다 */
export const keyOwnerKo = (l: CostLimitsValues): string =>
  l.quota_applies
    ? "우리 키(서버 키)로 만드는 중이라 예산이 걸립니다. 고객사 키로 바꾸면 예산은 걸리지 않습니다."
    : "고객사 키로 만드는 중이라 예산이 걸리지 않습니다. 사용량만 기록합니다.";

// ---- 변경 방향·검사 ----

export type Direction = "up" | "down" | "same";
export type Draft = Record<LimitField, number | null>;
export const emptyDraft = (): Draft => ({ video_seconds_monthly: null, image_count_monthly: null, audio_count_monthly: null });
export const directionOf = (current: number | null, next: number | null): Direction =>
  next === null || next === current ? "same" : current === null || next > current ? "up" : "down";

/** 입력 글자를 정수로. 비어 있으면 null(바꾸지 않음), 정수가 아니면 NaN */
export function parseAmount(text: string): number | null {
  const t = text.replace(/[,\s]/g, "");
  if (!t) return null;
  return /^\d+$/.test(t) ? Number(t) : Number.NaN;
}

export interface Change { field: LimitField; from: number | null; to: number; direction: "up" | "down" }
/** 현재 값과 다른 칸만 */
export function changesOf(state: CostLimitsState, draft: Draft): Change[] {
  const out: Change[] = [];
  for (const f of LIMIT_FIELDS) {
    const to = draft[f];
    const from = state.limits[f];
    const d = directionOf(from, to);
    if (d !== "same" && to !== null && Number.isInteger(to)) out.push({ field: f, from, to, direction: d });
  }
  return out;
}
export const isMixed = (changes: readonly Change[]) => changes.some((c) => c.direction === "up") && changes.some((c) => c.direction === "down");

const kindOfField = (f: LimitField): MediaKind => KINDS.find((k) => FIELD_OF[k] === f) as MediaKind;
const unitOfField = (f: LimitField): UsageUnit => UNIT_OF[kindOfField(f)];

export interface DraftCheck { errors: string[]; mixed: boolean; warnings: string[]; changes: Change[] }

/** 서버 규칙을 그대로 따라 미리 보여 주는 검사(안내용). 오류가 있으면 보내지 않는다 */
export function checkDraft(state: CostLimitsState, draft: Draft): DraftCheck {
  const errors: string[] = [];
  const warnings: string[] = [];
  const { bounds } = state;
  for (const f of LIMIT_FIELDS) {
    const v = draft[f];
    if (v === null) continue;
    const unit = unitOfField(f);
    if (!Number.isInteger(v)) { errors.push(`${FIELD_KO[f]}는 숫자로 적어 주세요.`); continue; }
    if (v < bounds.min) errors.push(`${FIELD_KO[f]}는 ${amountKo(bounds.min, unit)} 이상이어야 합니다(0으로는 막을 수 없습니다).`);
    if (v > bounds.max[f]) errors.push(`${FIELD_KO[f]}는 코드 절대 상한 ${amountKo(bounds.max[f], unit)}을 넘을 수 없습니다.`);
    const used = state.usage[kindOfField(f)]?.used;
    if (typeof used === "number" && v < used) {
      warnings.push(`${FIELD_KO[f]} ${amountKo(v, unit)}이 이번 달 사용량 ${amountKo(used, unit)}보다 낮아 새 생성이 이번 달 내내 막힙니다.`);
    }
  }
  const changes = changesOf(state, draft);
  const mixed = isMixed(changes);
  if (mixed) errors.push("올리는 칸과 낮추는 칸을 한 번에 바꿀 수 없습니다. 하나씩 나눠서 요청해 주세요.");
  return { errors, mixed, warnings, changes };
}

/** 낮추기가 어느 길로 가는지(스위치 상태) 한 줄 */
export const lowerPathKo = (selfApprove: boolean): string =>
  selfApprove
    ? "낮추기: 주의사항을 확인하면 바로 적용됩니다(하향 셀프 승인 허용 중)."
    : "낮추기: 하향 셀프 승인이 꺼져 있어 낮추기도 요청자와 다른 담당자의 승인이 필요합니다.";

/** 낮추기 확인창 문구. 줄 목록 */
export function loweringSummary(changes: readonly Change[], warnings: readonly string[]): string[] {
  const lines = changes.map((c) => `${FIELD_KO[c.field]}: ${amountKo(c.from, unitOfField(c.field))} → ${amountKo(c.to, unitOfField(c.field))}`);
  lines.push("지금부터 새로 시작하는 생성에 적용됩니다. 이미 진행 중인 생성은 영향이 없습니다.");
  lines.push("예산이 낮아지면 지금까지 만들어지던 미디어가 '월 예산 초과'로 막힐 수 있습니다.");
  lines.push("다시 올리려면 다른 담당자의 승인이 필요합니다.");
  return [...lines, ...warnings];
}

// ---- 서버 응답 문구 ----

export type LimitResult = { kind: "done" | "held" | "failed"; text: string; needsConfirm?: boolean; reviewId?: string | null; warnings?: string[] };

const DONE_KO: Record<string, string> = {
  lowered: "예산을 낮췄습니다. 지금부터 새로 시작하는 생성에 적용됩니다.",
  raised: "예산을 올렸습니다. 지금부터 새로 시작하는 생성에 적용됩니다.",
  option_saved: "하향 셀프 승인 설정을 저장했습니다.",
};
const FAIL_KO: Record<string, string> = {
  invalid_input: "입력이 맞지 않습니다.",
  reason_required: "사유를 적어 주세요.",
  no_change: "바꿀 값이 현재 값과 같습니다.",
  mixed_direction: "올리는 칸과 낮추는 칸을 한 번에 바꿀 수 없습니다. 하나씩 나눠서 요청해 주세요.",
  not_a_decrease: "낮추는 요청이 아닙니다. 올릴 때는 '상향 요청'을 쓰세요.",
  not_an_increase: "올리는 요청이 아닙니다. 낮출 때는 '낮추기'를 쓰세요.",
  deprecated_krw_limit: "원화 상한은 없어졌습니다. 영상 초·이미지 장·BGM 곡 예산으로 정해 주세요.",
  limit_store_error: "서버가 설정을 저장하지 못했습니다.",
  rejected_by_review: "승인 담당이 거절했습니다.",
};

const num = (o: Record<string, unknown> | null, k: string): number | undefined => (o && isNum(o[k]) ? (o[k] as number) : undefined);

export function warningKo(w: unknown): string {
  const r = rec(w);
  if (r?.code === "quota_below_month_usage") {
    const kind = KINDS.find((k) => k === r.media_kind);
    const unit = UNITS.find((u) => u === r.unit) ?? (kind ? UNIT_OF[kind] : "seconds");
    return `${kind ? KIND_KO[kind] : "미디어"} 예산 ${amountKo(num(r, "limit"), unit)}이 이번 달 사용량 ${amountKo(num(r, "month_used"), unit)}보다 낮아 새 생성이 막힙니다.`;
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
  if (code === "over_absolute_max") {
    const field = String(out?.field);
    const unit = (LIMIT_FIELDS as readonly string[]).includes(field) ? unitOfField(field as LimitField) : "seconds";
    return { kind: "failed", text: `${FIELD_NAME_KO[field] ?? "값"}이 코드 절대 상한 ${amountKo(num(out, "max"), unit)}을 넘습니다.` };
  }
  if (code === "below_minimum") return { kind: "failed", text: `${num(out, "min") ?? 1} 이상으로 적어 주세요(0으로는 막을 수 없습니다).` };
  return { kind: "failed", text: FAIL_KO[code] ?? `처리하지 못했습니다${code ? ` (${code})` : ""}` };
}

export function limitHttpErrorKo(status: number | undefined, code?: string | null): string {
  if (status === 401) return "로그인이 필요합니다. 다시 로그인해 주세요.";
  if (status === 403) {
    if (code === "LIMIT_EDITOR_REQUIRED") return "예산 변경 권한이 없는 계정입니다. 관리자가 회원 관리에서 권한을 켜야 합니다.";
    return "운영자·관리자로 로그인한 계정만 볼 수 있습니다.";
  }
  if (status === 404) return "서버가 아직 이 기능을 지원하지 않습니다.";
  if (status === 400 || status === 422) return "입력을 서버가 받아들이지 않았습니다.";
  return "사용량 예산 서버에 닿지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
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
  const parts = LIMIT_FIELDS.flatMap((f) => (r.payload[f] !== undefined ? [`${FIELD_KO[f]} ${amountKo(r.payload[f], unitOfField(f))}`] : []));
  return `${r.kind === "raise" ? "상향" : "하향"} 요청: ${parts.join(", ") || "?"}`;
}

export function historyLineKo(h: LimitHistoryRow): string {
  if (h.action === "option") {
    return `하향 셀프 승인 ${h.new === 1 ? "켬" : "끔"}${h.reason ? ` (${h.reason})` : ""}`;
  }
  const verb = h.action === "lower" ? "낮춤" : "올림";
  return `${FIELD_NAME_KO[h.field] ?? h.field} ${verb}: ${h.old === null ? "미설정" : h.old} → ${h.new === null ? "미설정" : h.new}${h.reason ? ` (${h.reason})` : ""}`;
}

/** 정책 줄 값(영상 길이): 초면 초, 원이면 원(예전 서버) */
export const policyValueKo = (p: PolicyItem) => (p.unit === "s" ? `${p.value}초` : `${p.value.toLocaleString("ko-KR")}원`);
