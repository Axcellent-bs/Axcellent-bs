import { ApiError } from "./apiError";
import { writeAuth } from "./api";
import { DASHBOARD_API_URL, RELAY_API_URL, RELAY_VIEW_TOKEN } from "./env";
import type { BriefMediaAnswer, MediaEstimate, PreparedMedia, PublishAnswer, PublishState } from "./briefMedia";

// 마케팅이 기획에서 받은 기획안 읽기. 화면은 마케팅을 직접 부르지 않고 shop-api 관리 읽기 중계(GET만)를 부른다.
// 응답 모양은 ax-backend `marketing_agent/briefs.py`(`StoredBrief`)와 계약 `CampaignBriefInput` 기준이다.
// 토큰은 환경변수(개발용)에서만 오고 브라우저 저장소에는 두지 않는다.

export interface BriefFact { label: string; value: string; source: string; link: string | null }
export interface BriefTarget { label: string; value: string; measure: string | null }

export interface BriefBody {
  proposal_id: string;
  insight_id: string | null;
  title: string;
  idea: string;
  why: string;
  facts: BriefFact[];
  target: BriefTarget | null;
  products: string[];
  format: string | null;
  approved_by: string;
  note: string | null;
  body_md: string | null;
}

/** 미디어 요청 초안. 생성은 사람이 시작하므로 `auto_generate`는 항상 false다 */
export interface SnsMediaRequest {
  media_kind: string;
  scene_template_id: string;
  resolution: string;
  quality: string | null;
  duration_s: number | null;
  media_ref: string;
  fields: Record<string, string>;
  missing_fields: string[];
  next_steps: string[];
  auto_generate: boolean;
  // 아래는 자동 추천·관리자 선택 기록이다. 옛 서버·옛 기록에는 없다
  /** 종류를 누가 정했나. auto_fallback = 추천 종류를 서버가 만들 수 없어 이미지로 넘김 */
  kind_source?: "auto" | "auto_fallback" | "admin" | null;
  recommended_media_kind?: string | null;
  recommendation_reason?: string | null;
  recommendation_confidence?: string | null;
  recommendation_blocked?: string | null;
  /** 필드 값의 출처: brief(기획안 근거) | default(기본값) | admin(관리자) */
  field_sources?: Record<string, string>;
  field_reasons?: Record<string, string>;
  /** 고를 수 있는 값. null이면 짧은 단어를 직접 적는다 */
  field_options?: Record<string, string[] | null>;
  field_max_len?: Record<string, number>;
}

export type MediaKindChoice = "auto" | "image" | "video" | "carousel";

export interface MediaFieldNote { field: string; value: string | null; source: "brief" | "default" | "undecided"; reason: string }

/** 마케팅 AI가 기획안 글에서 규칙으로 계산한 추천. 화면은 받은 값만 보여 준다 */
export interface MediaRecommendation {
  media_kind: "image" | "video" | "carousel";
  rule: string;
  reason: string;
  confidence: "high" | "low";
  fields: Record<string, string>;
  field_notes: MediaFieldNote[];
}

/** SNS·미디어로 넘긴 상태. 마케팅 서버가 알려 주는 값만 쓴다(옛 서버는 이 칸이 없을 수 있다) */
export interface SnsHandoff {
  status: "not_handed" | "handed" | "failed";
  draft_id: string | null;
  media_request: SnsMediaRequest | null;
  reason_code: string | null;
  handed_at: string | null;
  draft_state: "caption_only" | "media_attached" | "missing" | null;
  /** 지금 요청 초안으로 만들 때의 예상 비용·상한. 옛 서버는 이 칸이 없다 */
  media_estimate?: MediaEstimate | null;
  /** 지금 요청 초안으로 만든 미디어 초안. 없거나 요청 초안이 바뀌었으면 null */
  prepared?: PreparedMedia | null;
  /** 준비된 초안의 게시 요청(승인 대기) 상태. 옛 서버는 이 칸이 없다 */
  publish?: PublishState | null;
}

/** 받은 기획안. 지금 마케팅이 아는 상태는 `received`(받음)뿐이다 */
export interface StoredBrief {
  sns_handoff?: SnsHandoff;
  /** 옛 서버는 이 칸이 없다 */
  media_recommendation?: MediaRecommendation | null;
  campaign_id: string;
  proposal_id: string;
  status: "received";
  received_at: string;
  brief: BriefBody;
}

export interface BriefList { items: StoredBrief[]; total: number; unknown_reason: string | null }

export const briefsEnabled = Boolean(RELAY_VIEW_TOKEN && RELAY_API_URL);

export const STATUS_STEPS = ["받음", "캠페인 시작", "게시", "성과"] as const;
export const briefStatusKo = (_status: StoredBrief["status"]): (typeof STATUS_STEPS)[number] => "받음";

/** 출처 링크는 http(s)만 연다. 그 밖의 주소(javascript: 등)는 글자로만 둔다 */
export function safeLink(link: string | null | undefined): string | null {
  if (!link) return null;
  try {
    const url = new URL(link);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

async function get<T>(path: string): Promise<T> {
  if (!briefsEnabled) throw new ApiError("받은 기획안 읽기 연결이 꺼져 있습니다", 403);
  const res = await fetch(`${RELAY_API_URL}${path}`, { headers: { Authorization: `Bearer ${RELAY_VIEW_TOKEN}` }, cache: "no-store" });
  let body: { data?: T; error?: { message?: string } } = {};
  try { body = await res.json(); } catch { /* 본문이 JSON이 아님 */ }
  if (!res.ok || body.data === undefined) throw new ApiError(body.error?.message ?? `요청 실패 (${res.status})`, res.status);
  return body.data;
}

export interface HandoffAnswer {
  status: string;
  outcome: string | null;
  output: { outcome: string; sns_handoff: SnsHandoff; campaign_id: string | null; reason_code?: string | null; field?: string | null; field_code?: string | null };
}

export interface HandoffOptions {
  /** auto = 마케팅 AI 추천을 따른다(기본). 종류를 고르면 관리자 선택이 우선한다 */
  mediaKind?: MediaKindChoice;
  /** 관리자가 정한 필드 값. 추천 값을 덮어쓴다 */
  fields?: Record<string, string>;
  /** 이미 넘긴 기획안의 미디어 요청 초안을 다시 만든다 */
  revise?: boolean;
}

/**
 * 기획안을 SNS·미디어로 넘긴다(dashboard-api, 사람 토큰 필요). 미디어 생성·게시는 하지 않고 초안만 만든다.
 * 종류·필드는 마케팅 AI가 기획안에서 추천해 채우고, 관리자가 고른 값이 있으면 그것이 우선한다.
 */
async function handoff(proposalId: string, options: HandoffOptions = {}): Promise<HandoffAnswer> {
  const payload: Record<string, unknown> = { media_kind: options.mediaKind ?? "auto", autofill: true };
  if (options.fields && Object.keys(options.fields).length > 0) payload.fields = options.fields;
  if (options.revise) payload.revise = true;
  const path = `/api/dashboard/marketing/briefs/${encodeURIComponent(proposalId)}/sns-handoff`;
  const res = await fetch(`${DASHBOARD_API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...writeAuth("POST", path) },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  const json = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(json?.error?.message ?? `요청 실패 (${res.status})`, res.status);
  return json.data as HandoffAnswer;
}

const mediaPath = (proposalId: string) => `/api/dashboard/marketing/briefs/${encodeURIComponent(proposalId)}/media`;

/**
 * 넘긴 기획안의 저장된 미디어 요청(종류·필드)으로 미디어 초안을 만든다(dashboard-api, 사람 토큰 필요).
 * 종류·필드는 보내지 않는다(서버가 보관본에서 읽는다). 게시는 하지 않는다: 게시는 C 등급 사람 승인이다.
 * 영상은 1분쯤 걸린다. 오류는 서버 문장 없이 상태 코드만 담아 던진다(문구는 `mediaHttpErrorKo`).
 */
async function makeMedia(proposalId: string): Promise<BriefMediaAnswer> {
  const path = mediaPath(proposalId);
  const res = await fetch(`${DASHBOARD_API_URL}${path}`, { method: "POST", headers: writeAuth("POST", path), cache: "no-store" });
  const json = await res.json().catch(() => null);
  if (!res.ok || !json?.data) throw new ApiError(`요청 실패 (${res.status})`, res.status);
  return json.data as BriefMediaAnswer;
}

/**
 * 준비된 초안의 "게시 요청": 승인 대기 건(C 등급, 10분 만료)만 만든다. 게시하지 않는다.
 * 기획안 번호만 보낸다(글·파일은 서버가 보관본에서 읽는다). 오류는 상태 코드만 담아 던진다.
 */
async function requestPublish(proposalId: string): Promise<PublishAnswer> {
  const path = `/api/dashboard/marketing/briefs/${encodeURIComponent(proposalId)}/publish-request`;
  const res = await fetch(`${DASHBOARD_API_URL}${path}`, { method: "POST", headers: writeAuth("POST", path), cache: "no-store" });
  const json = await res.json().catch(() => null);
  if (!res.ok || !json?.data) throw new ApiError(`요청 실패 (${res.status})`, res.status);
  return json.data as PublishAnswer;
}

/** 준비된 미디어 초안의 미리보기 주소(AI 표시를 입힌 결과 파일). `version`은 파일이 바뀌면 새로 받게 하는 값이다 */
export const mediaPreviewUrl = (proposalId: string, version: string | null) =>
  `${DASHBOARD_API_URL}${mediaPath(proposalId)}/preview${version ? `?v=${encodeURIComponent(version.slice(0, 12))}` : ""}`;

/** shop-api 읽기 중계가 받는 목록 limit 상한(`ax_shop_api/marketing_client.py`의 `LIST_LIMIT_MAX`). 넘기면 400 "limit은 1~100이어야 한다" */
export const BRIEFS_LIST_MAX = 100;
/** 목록 limit을 중계가 받는 1~BRIEFS_LIST_MAX 정수로 맞춘다 */
export const briefsListLimit = (limit: number): number =>
  Number.isFinite(limit) ? Math.min(Math.max(Math.trunc(limit), 1), BRIEFS_LIST_MAX) : BRIEFS_LIST_MAX;

/** 장애 사과·보상 SNS 글 초안(마케팅 `list_outage_sns_drafts`). 초안일 뿐 게시된 글이 아니다 */
export interface OutageSnsDraft {
  post_draft_id: string;
  trigger: "apology_notice" | "compensation" | null;
  context_id: string;
  source_ref: string;
  caption: string;
  media_kind: string;
  media_attached: boolean;
  created_at: string;
}
export interface OutageSnsDraftList { items: OutageSnsDraft[]; total: number; unknown_reason: string | null }
export const OUTAGE_TRIGGER_KO: Record<string, string> = { apology_notice: "사과 공지", compensation: "보상 쿠폰 지급" };
export const outageTriggerKo = (trigger: string | null) => (trigger ? OUTAGE_TRIGGER_KO[trigger] ?? trigger : "확인할 수 없음");

export const briefs = {
  list: (limit = 50) => get<BriefList>(`/api/shop/admin/marketing/briefs?limit=${briefsListLimit(limit)}`),
  outageSnsDrafts: (limit = 20) => get<OutageSnsDraftList>(`/api/shop/admin/marketing/outage-sns-drafts?limit=${briefsListLimit(limit)}`),
  handoff,
  makeMedia,
  requestPublish,
};

export const HANDOFF_REASON_KO: Record<string, string> = {
  price_not_found: "이미지 단가표에 없는 조합이라 넘기지 않았습니다",
  model_not_configured: "이미지 모델이 설정되지 않아 넘기지 않았습니다",
  price_table_error: "단가표를 읽을 수 없어 넘기지 않았습니다",
  unknown_scene_template: "장면 템플릿을 찾을 수 없습니다",
  caption_too_long: "캡션이 너무 길어 초안을 만들 수 없습니다",
  forbidden_phrase: "참여를 조건으로 거는 말이 있어 초안을 만들 수 없습니다",
  invalid_field: "쓸 수 없는 필드 값이 있습니다",
};
export const handoffReasonKo = (code: string | null) => (code ? HANDOFF_REASON_KO[code] ?? `넘기지 못했습니다 (${code})` : "");

export const MEDIA_KIND_KO: Record<string, string> = { image: "이미지", video: "영상", carousel: "캐러셀" };
export const mediaKindKo = (kind: string | null | undefined) => (kind ? MEDIA_KIND_KO[kind] ?? kind : "확인할 수 없음");
export const MEDIA_FIELD_KO: Record<string, string> = { season: "계절", color_theme: "색감", category: "책 분류" };
export const mediaFieldKo = (name: string) => MEDIA_FIELD_KO[name] ?? name;
export const FIELD_SOURCE_KO: Record<string, string> = { brief: "자동 · 기획안 근거", default: "자동 · 기본값", admin: "관리자 선택", undecided: "미정" };
export const KIND_SOURCE_KO: Record<string, string> = { auto: "자동 선택", auto_fallback: "자동 선택 · 이미지로 대체", admin: "관리자 선택" };
/** 필드 값 검사에 걸린 이유(마케팅 `prompts.py`의 닫힌 집합) */
export const FIELD_CODE_KO: Record<string, string> = {
  value_not_allowed: "고를 수 있는 값이 아닙니다",
  free_text: "짧은 단어(한글·영문·숫자)만 쓸 수 있습니다",
  pii: "개인정보나 고객 원문처럼 보이는 말은 쓸 수 없습니다",
  text_request: "글자를 그려 달라는 말은 쓸 수 없습니다",
  real_person_or_brand: "실존 인물·브랜드 이름은 쓸 수 없습니다",
  field_not_allowed: "이 장면 템플릿에 없는 필드입니다",
  field_value_invalid: "값이 비어 있습니다",
};

/** 종류·필드 바꾸기가 막혔을 때 화면 문구. 막히지 않았으면 null */
export function reviseProblemKo(output: HandoffAnswer["output"]): string | null {
  if (output.outcome !== "revise_rejected" && output.outcome !== "failed") return null;
  const code = output.reason_code ?? output.sns_handoff.reason_code;
  if (code === "invalid_field" && output.field) {
    return `${mediaFieldKo(output.field)}: ${FIELD_CODE_KO[output.field_code ?? ""] ?? "쓸 수 없는 값입니다"}`;
  }
  return handoffReasonKo(code) || "바꾸지 못했습니다";
}

export const DRAFT_STATE_KO: Record<string, string> = {
  caption_only: "캡션 초안만 있음 (미디어 없음)",
  media_attached: "미디어가 붙은 초안",
  missing: "초안 저장소에 없음 (다시 넘기면 복원)",
};
export const draftStateKo = (state: string | null) => (state ? DRAFT_STATE_KO[state] ?? state : "확인할 수 없음");

/** 연결 오류를 화면 문구로 */
export function briefsError(error: { status?: number; message: string }): string {
  if (error.status === 502) return "마케팅 AI에 연결할 수 없습니다. 마케팅 서버가 켜져 있는지 확인해 주세요.";
  if (error.status === 504) return "마케팅 AI가 제때 응답하지 않았습니다. 잠시 뒤 다시 시도해 주세요.";
  if (error.status === 403 || error.status === 401) return "받은 기획안을 읽을 권한이 없습니다. 열람 토큰 설정을 확인해 주세요.";
  return error.message;
}
