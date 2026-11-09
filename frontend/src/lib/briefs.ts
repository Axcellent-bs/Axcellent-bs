import { ApiError } from "./apiError";
import { RELAY_API_URL, RELAY_VIEW_TOKEN } from "./env";

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

/** 받은 기획안. 지금 마케팅이 아는 상태는 `received`(받음)뿐이다 */
export interface StoredBrief {
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

export const briefs = {
  list: (limit = 50) => get<BriefList>(`/api/shop/admin/marketing/briefs?limit=${limit}`),
};

/** 연결 오류를 화면 문구로 */
export function briefsError(error: { status?: number; message: string }): string {
  if (error.status === 502) return "마케팅 AI에 연결할 수 없습니다. 마케팅 서버가 켜져 있는지 확인해 주세요.";
  if (error.status === 504) return "마케팅 AI가 제때 응답하지 않았습니다. 잠시 뒤 다시 시도해 주세요.";
  if (error.status === 403 || error.status === 401) return "받은 기획안을 읽을 권한이 없습니다. 열람 토큰 설정을 확인해 주세요.";
  return error.message;
}
