// 시스템 관리 › 회사 연결 › 브랜드: 홍보 영상 방향 두 줄 (11/24 "재미 먼저, 브랜드는 끝에"). 순수 계산만 한다:
// `npm test`(node --test)에서 그대로 돈다. API는 promoStyle.ts.
// 회사별로 저장하고 data 트렌드 보고서가 같은 값을 읽는다. 의뢰 문장이 "책 소개 영상"처럼 제품을
// 직접 말하면 그 의뢰만 제품·업종 소개로 본다(서버 판정).
export type PromoStyle = "fun_first" | "product";
export type BrandExposure = "end_card" | "prop" | "caption";
export type PromoSource = "screen" | "tenant" | "default";

export interface PromoState {
  promo_style: PromoStyle;
  brand_exposure: BrandExposure;
  source: { promo_style: PromoSource; brand_exposure: PromoSource };
  defaults: { promo_style: PromoStyle; brand_exposure: BrandExposure };
  editable: boolean;
}

export interface PromoPick {
  promo_style: PromoStyle;
  brand_exposure: BrandExposure;
}

// 시안 문구 그대로
export const STYLE_ROW = {
  label: "홍보 영상 방향",
  hint: "회사마다 고르고, 의뢰 문장에 \"책 소개 영상\"처럼 쓰면 그 의뢰만 바뀌어요",
  options: [
    { value: "fun_first", label: "재미 먼저 (브랜드는 끝에)" },
    { value: "product", label: "제품·업종 소개" },
  ] as { value: PromoStyle; label: string }[],
};
export const EXPOSURE_ROW = {
  label: "브랜드 노출",
  hint: "재미 먼저일 때",
  options: [
    { value: "end_card", label: "마지막 2~3초 채널 이름·로고" },
    { value: "prop", label: "화면 속 소품으로 살짝" },
    { value: "caption", label: "캡션·프로필에만" },
  ] as { value: BrandExposure; label: string }[],
};

/** 지금 값이 어디서 왔는지 한 줄 (두 칸 중 화면 저장이 하나라도 있으면 화면 저장) */
export function sourceLine(s: Pick<PromoState, "source">): string {
  const all = [s.source.promo_style, s.source.brand_exposure];
  if (all.includes("screen")) return "이 화면에서 저장한 값이에요";
  if (all.includes("tenant")) return "회사 설정 파일 값이에요";
  return "기본값이에요 (재미 먼저 · 마지막에 채널 이름·로고)";
}

/** 화면 저장 값이 있나 ([회사 설정 파일 값으로 되돌리기]를 보이나) */
export const hasScreenValue = (s: Pick<PromoState, "source">) =>
  s.source.promo_style === "screen" || s.source.brand_exposure === "screen";

/** [저장]을 켤 수 있나: 고친 값이 지금 값과 다르고, 저장할 수 있는 서버이고, 저장 중이 아니다 */
export function canSave(s: Pick<PromoState, "promo_style" | "brand_exposure" | "editable">, pick: PromoPick, busy = false): boolean {
  return s.editable && !busy && (pick.promo_style !== s.promo_style || pick.brand_exposure !== s.brand_exposure);
}

/** 브랜드 노출 줄은 재미 먼저일 때만 고른다 (제품·업종 소개면 흐리게) */
export const exposureActive = (style: PromoStyle) => style === "fun_first";
