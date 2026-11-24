import { call } from "./companyConnection";
import type { BrandExposure, PromoState, PromoStyle } from "./promoStyleView";

// 마케팅 › 브랜드(/marketing/brand): 홍보 영상 방향 API (dashboard-api `promo_style.py`, 11/24).
// 보기는 회원 모두, 저장·되돌리기는 회사 관리자만(서버도 403). 화면 문구·계산은 promoStyleView.ts
const PATH = "/api/dashboard/promo-style";

export const promoStyleApi = {
  get: () => call<PromoState>("GET", PATH),
  save: (promo_style: PromoStyle, brand_exposure: BrandExposure) =>
    call<PromoState>("POST", `${PATH}/save`, { promo_style, brand_exposure }),
  clear: () => call<PromoState>("POST", `${PATH}/clear`),
};
