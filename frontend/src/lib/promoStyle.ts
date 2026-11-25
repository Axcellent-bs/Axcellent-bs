import { call } from "./companyConnection";
import type { PromoState } from "./promoStyleView";

// 마케팅 › 브랜드(/marketing/brand): 홍보 영상 방향 API (dashboard-api `promo_style.py`, 11/24).
// 보기는 회원 모두, 저장·되돌리기는 회사 관리자만(서버도 403). 화면 문구·계산은 promoStyleView.ts
const PATH = "/api/dashboard/promo-style";

export const promoStyleApi = {
  get: () => call<PromoState>("GET", PATH),
  // body는 promoStyleView.saveBody가 만든다 (새 칸이 없는 서버면 예전과 같은 두 칸)
  save: (body: Record<string, string>) => call<PromoState>("POST", `${PATH}/save`, body),
  clear: () => call<PromoState>("POST", `${PATH}/clear`),
};
