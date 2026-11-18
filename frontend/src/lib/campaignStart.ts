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
