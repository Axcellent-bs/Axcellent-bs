// 마케팅 캠페인 대장(GET /api/dashboard/marketing/campaigns)을 화면에 쓰는 순수 함수 (2026-11-23).
// - 유튜브 칸: "어느 캠페인 영상인지" 고르기(비울 수 있다). 고른 값은 render.youtube.campaign_id에 남고,
//   재무로 가는 캠페인 결과에 그 영상의 유튜브 조회수가 실린다
// - 결과 저장 칸: 시작 때 적은 콘텐츠 카테고리·형식·제작비를 미리 채운다(사람이 고치면 고친 값)
// - 재무 화면: 자동 전송 결과의 광고비 출처(source)와 조회수 출처를 글로 보인다
// 숫자를 만들지 않는다. 서버가 준 값만 옮긴다.

export type ContentFormat = "shorts" | "reels";

/** 마케팅 `list_marketing_campaigns` 한 줄(시작 때 값 그대로) */
export interface MarketingCampaign {
  campaign_id: string;
  campaign_name: string;
  channel: string;
  budget_krw: number;
  started_on: string;
  ends_on?: string | null;
  status: string;
  content_category?: string | null;
  format?: ContentFormat | null;
  production_cost_krw?: number | null;
}

const STATUS_KO: Record<string, string> = {
  active: "진행 중", recorded_auto: "결과 자동 전송", recorded_human: "결과 저장됨", canceled: "취소", gave_up: "자동 전송 멈춤",
};

export function campaignStatusKo(status: string): string {
  return STATUS_KO[status] ?? status;
}

export interface CampaignOption { value: string; label: string }

/** 유튜브 칸의 캠페인 고르기 목록. 맨 앞은 "고르지 않음"(""), 저장된 값이 목록에 없으면 그대로 보인다 */
export function campaignOptions(items: MarketingCampaign[], current: string | null | undefined): CampaignOption[] {
  const out: CampaignOption[] = [{ value: "", label: "고르지 않음" }];
  for (const c of items) {
    const period = c.ends_on ? `${c.started_on}~${c.ends_on}` : `${c.started_on}~`;
    out.push({ value: c.campaign_id, label: `${c.campaign_name} · ${period} · ${campaignStatusKo(c.status)}` });
  }
  if (current && !items.some((c) => c.campaign_id === current)) out.push({ value: current, label: `${current} (목록에 없음)` });
  return out;
}

export interface ContentForm { category: string; format: ContentFormat | ""; production: string }

/** 결과 저장 칸에 미리 채울 값: 마케팅 대장(시작 때 값·기획안 값) → 재무 기록 순. 모르면 빈 칸 */
export function contentPrefill(
  started: Pick<MarketingCampaign, "content_category" | "format" | "production_cost_krw"> | null | undefined,
  recorded?: { content_category?: string | null; format?: ContentFormat | null; production_cost_krw?: number | null } | null,
): ContentForm {
  const category = started?.content_category ?? recorded?.content_category ?? "";
  const format = started?.format ?? recorded?.format ?? "";
  const cost = started?.production_cost_krw ?? recorded?.production_cost_krw ?? null;
  return { category, format, production: cost === null ? "" : String(cost) };
}

/** 결과 저장 요청에 더할 칸. 빈 칸은 보내지 않는다(서버가 시작 때 값을 잇는다) */
export function contentFields(f: ContentForm): { content_category?: string; format?: ContentFormat; production_cost_krw?: number } {
  return {
    ...(f.category.trim() ? { content_category: f.category.trim() } : {}),
    ...(f.format ? { format: f.format } : {}),
    ...(f.production.trim() !== "" ? { production_cost_krw: Number(f.production) } : {}),
  };
}

// 마케팅 자동 전송의 재무 source(campaign_auto.py). 값을 바꾸면 마케팅과 맞춘다
export const AUTO_BUDGET_SOURCE = "marketing.auto.approved_budget";
export const AUTO_AD_METRICS_SOURCE = "marketing.auto.ad_metrics";

/** 재무 손익 칸의 광고비 출처 표시. 사람이 저장한 결과는 표시하지 않는다(null) */
export function spendSourceBadge(source: string): { label: string; provisional: boolean; title: string } | null {
  if (source === AUTO_BUDGET_SOURCE) {
    return { label: "예산 기준(잠정)", provisional: true, title: "마케팅이 캠페인 종료 뒤 자동으로 보낸 결과입니다. 기간 광고 숫자가 없거나 광고비를 모르는 날이 있어 승인 예산을 광고비로 넣었습니다. 손익이 실제와 다를 수 있고, 사람이 실제 집행액으로 다시 저장하면 이 표시가 사라집니다." };
  }
  if (source === AUTO_AD_METRICS_SOURCE) {
    return { label: "광고 숫자 합계", provisional: false, title: "마케팅이 캠페인 종료 뒤 자동으로 보낸 결과입니다. 광고비는 캠페인 기간에 사람이 적은 광고 숫자(보고서 값)의 광고비 합계입니다. 사람이 다시 저장하면 그 값이 우선합니다." };
  }
  return null;
}

/** 조회수 출처 글. 영상 성과(데이터 7일)와 회사 유튜브를 나눠 적고, 둘 다 모르면 null */
export function viewSourcesText(r: { views?: number | null; views_point?: string | null; youtube_views?: number | null; youtube_views_at?: string | null }): string | null {
  const parts: string[] = [];
  if (r.views != null) parts.push(`영상 성과(데이터 ${r.views_point ?? "?"}) ${r.views.toLocaleString("ko-KR")}회`);
  if (r.youtube_views != null) parts.push(`회사 유튜브 ${r.youtube_views.toLocaleString("ko-KR")}회${r.youtube_views_at ? `(${r.youtube_views_at.slice(0, 16).replace("T", " ")} 읽음)` : ""}`);
  return parts.length ? parts.join(" + ") : null;
}

/** 카테고리 칸의 조회수 출처 줄. 유튜브가 없으면 null(옛 화면 그대로) */
export function categoryViewsBreakdown(row: { performance_views?: number; youtube_views?: number }): string | null {
  if (!row.youtube_views) return null;
  return `영상 성과 7일 ${(row.performance_views ?? 0).toLocaleString("ko-KR")} + 회사 유튜브 ${row.youtube_views.toLocaleString("ko-KR")}`;
}
