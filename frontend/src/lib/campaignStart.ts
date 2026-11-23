// 캠페인 시작 화면의 영상 캠페인 필수 입력(콘텐츠 카테고리·형식·제작비)과 종료일 검사.
// 가벼운 검사만 하고 최종 판단은 백엔드가 한다(영상 캠페인이 이 셋을 모르면 마케팅이 시작을 거절한다).

export type ContentFormat = "shorts" | "reels";

export interface StartForm {
  isVideo: boolean;
  category: string;
  format: ContentFormat | "";
  production: string; // 숫자만, 0 가능
  endsOn: string; // YYYY-MM-DD(KST), 비우면 자동 결과 전달 대상이 아님
}

/** 영상 캠페인이면 빠진 필수 칸 이름(한국어). 비어 있으면 시작할 수 있다 */
export function missingVideoFields(f: StartForm): string[] {
  if (!f.isVideo) return [];
  const out: string[] = [];
  if (f.category.trim() === "") out.push("콘텐츠 카테고리");
  if (f.format === "") out.push("형식");
  if (f.production.trim() === "") out.push("영상 제작비(없으면 0)");
  if (f.endsOn === "") out.push("종료일");
  return out;
}

/** 시작 요청에 더할 필드. 영상이면 campaign_kind·제작비·종료일까지 싣는다 */
export function startFields(f: StartForm): {
  campaign_kind?: "video"; content_category?: string; format?: ContentFormat; production_cost_krw?: number; ends_on?: string;
} {
  return {
    ...(f.isVideo ? { campaign_kind: "video" as const } : {}),
    ...(f.category.trim() ? { content_category: f.category.trim() } : {}),
    ...(f.format ? { format: f.format } : {}),
    ...(f.isVideo && f.production.trim() !== "" ? { production_cost_krw: Number(f.production) } : {}),
    ...(f.endsOn ? { ends_on: f.endsOn } : {}),
  };
}

// ---- 캠페인 번호 (2026-11-23) ----
// 받은 기획안으로 시작하면 마케팅 `briefs.campaign_id_for`와 같은 규칙(cmp_ + 제안 번호)으로 번호를 정한다.
// 광고 숫자 칸·데이터 `ad_campaign_of`가 같은 번호를 써서, 자동 결과 전송이 광고 숫자 합계를 찾는다.
// 기획안 없이 시작하면 예전처럼 cmp_<시각>이다. 같은 번호는 한 번만 시작한다(마케팅이 already_started로 거절).

export const CAMPAIGN_ID_PREFIX = "cmp_";

/** 받은 기획안의 캠페인 번호 */
export const campaignIdForProposal = (proposalId: string) => `${CAMPAIGN_ID_PREFIX}${proposalId.trim()}`;

/** 시작 요청에 싣는 번호. 기획안이 있으면 cmp_<proposal_id>, 없으면 cmp_<시각(ms)> */
export function startCampaignId(proposalId: string | null | undefined, nowMs: number): string {
  const id = (proposalId ?? "").trim();
  return id ? campaignIdForProposal(id) : `${CAMPAIGN_ID_PREFIX}${nowMs}`;
}

const STARTED_KO: Record<string, string> = {
  active: "진행 중", recorded_auto: "결과 자동 전송됨", recorded_human: "결과 저장됨", canceled: "취소됨", gave_up: "자동 전송 멈춤",
};

/** 이미 시작한 번호면 화면 안내(시작 버튼을 막는다). 처음이면 null. 최종 판단은 마케팅이 한다 */
export function startedConflict(campaignId: string, started: Readonly<Record<string, { status: string }>>): string | null {
  const hit = started[campaignId];
  if (!hit) return null;
  return `이 기획안 캠페인(${campaignId})은 이미 시작했어요(${STARTED_KO[hit.status] ?? hit.status}). 기획안 하나로는 한 번만 시작해요.`;
}
