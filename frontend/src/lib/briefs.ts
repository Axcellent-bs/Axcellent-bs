import { ApiError } from "./apiError";
import { writeAuth } from "./api";
import { DASHBOARD_API_URL, RELAY_API_URL, RELAY_VIEW_TOKEN } from "./env";

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
}

/** SNS·미디어로 넘긴 상태. 마케팅 서버가 알려 주는 값만 쓴다(옛 서버는 이 칸이 없을 수 있다) */
export interface SnsHandoff {
  status: "not_handed" | "handed" | "failed";
  draft_id: string | null;
  media_request: SnsMediaRequest | null;
  reason_code: string | null;
  handed_at: string | null;
  draft_state: "caption_only" | "media_attached" | "missing" | null;
}

/** 받은 기획안. 지금 마케팅이 아는 상태는 `received`(받음)뿐이다 */
export interface StoredBrief {
  sns_handoff?: SnsHandoff;
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

export interface HandoffAnswer { status: string; outcome: string | null; output: { outcome: string; sns_handoff: SnsHandoff; campaign_id: string | null } }

/** 기획안을 SNS·미디어로 넘긴다(dashboard-api, 사람 토큰 필요). 미디어 생성·게시는 하지 않고 초안만 만든다 */
async function handoff(proposalId: string): Promise<HandoffAnswer> {
  const path = `/api/dashboard/marketing/briefs/${encodeURIComponent(proposalId)}/sns-handoff`;
  const res = await fetch(`${DASHBOARD_API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...writeAuth("POST", path) },
    body: JSON.stringify({ media_kind: "image" }),
    cache: "no-store",
  });
  const json = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(json?.error?.message ?? `요청 실패 (${res.status})`, res.status);
  return json.data as HandoffAnswer;
}

/** shop-api 읽기 중계가 받는 목록 limit 상한(`ax_shop_api/marketing_client.py`의 `LIST_LIMIT_MAX`). 넘기면 400 "limit은 1~100이어야 한다" */
export const BRIEFS_LIST_MAX = 100;
/** 목록 limit을 중계가 받는 1~BRIEFS_LIST_MAX 정수로 맞춘다 */
export const briefsListLimit = (limit: number): number =>
  Number.isFinite(limit) ? Math.min(Math.max(Math.trunc(limit), 1), BRIEFS_LIST_MAX) : BRIEFS_LIST_MAX;

export const briefs = {
  list: (limit = 50) => get<BriefList>(`/api/shop/admin/marketing/briefs?limit=${briefsListLimit(limit)}`),
  handoff,
};

export const HANDOFF_REASON_KO: Record<string, string> = {
  price_not_found: "이미지 단가표에 없는 조합이라 넘기지 않았습니다",
  model_not_configured: "이미지 모델이 설정되지 않아 넘기지 않았습니다",
  price_table_error: "단가표를 읽을 수 없어 넘기지 않았습니다",
  unknown_scene_template: "장면 템플릿을 찾을 수 없습니다",
  caption_too_long: "캡션이 너무 길어 초안을 만들 수 없습니다",
  forbidden_phrase: "참여를 조건으로 거는 말이 있어 초안을 만들 수 없습니다",
};
export const handoffReasonKo = (code: string | null) => (code ? HANDOFF_REASON_KO[code] ?? `넘기지 못했습니다 (${code})` : "");

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
