import { call } from "./companyConnection";
import type { SaveBody } from "./renderSettings";

// dashboard-api `marketing/render-settings`. 응답은 다른 대시보드 API처럼 `{data: ...}`로 감싸서 온다(call이 벗긴다)
const PATH = "/api/dashboard/marketing/render-settings";

export const renderSettingsApi = {
  get: () => call<unknown>("GET", PATH),
  save: (body: SaveBody) => call<unknown>("POST", `${PATH}/save`, body),
  clear: () => call<unknown>("POST", `${PATH}/clear`),
};
