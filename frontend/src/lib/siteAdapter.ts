import { call, type StoredConnection } from "./companyConnection";
import type { SiteMapping, SiteResource } from "./siteMapping";
import type { InboxMap, RecentWrite, SiteWritesState, WriteAction, WriteMap, WritePath } from "./siteWrites";

export type { ResourceMapping, SiteMapping, SiteResource } from "./siteMapping";
export { preview, unfilled } from "./siteMapping";

// 회사 연결 › 다른 형식 API: 고객사 자기 API를 사이트 변환기(dashboard-api `/site-adapter`)로 잇는다.
// dashboard-api `site_adapter/setup.py`. 둘러보기(짝 제안) → 사람이 짝 확인 → 연결(첫 쪽을 실제로 바꿔 본 뒤 저장).
// 키는 서버의 변환기 폴더에만 저장되고 다시 내려오지 않는다(token_saved만 온다). 읽기·쓰기 모두 사람 토큰(dashboardAuth).

export interface SiteSample {
  count: number;
  keys: string[];
  first: Record<string, unknown> | null;
}

export interface SiteProposal {
  /** 고객사 openapi.json에서 찾은 GET 목록 경로 (없으면 빈 목록) */
  paths: string[];
  samples: Record<SiteResource, SiteSample> | null;
  mapping: SiteMapping | null;
  /** 고객사 openapi.json의 쓰기 경로 후보 (POST·PUT·PATCH·DELETE) */
  write_paths?: WritePath[];
  /** `claims_path`를 주면 신청 첫 쪽 칸 이름·첫 줄 */
  claims_sample?: SiteSample | null;
}

export interface SiteAdapterState {
  configured: boolean;
  mapping: SiteMapping | null;
  token_saved: boolean;
  connection: StoredConnection | null;
  counts?: Record<SiteResource, number>;
}

export interface ProposeInput {
  base_url: string;
  token?: string;
  auth_header?: string;
  auth_scheme?: string;
  orders_path?: string;
  products_path?: string;
  claims_path?: string;
}

const PATH = "/api/dashboard/site-adapter";

// 주문·상품 짝 저장은 이 네 칸만 보낸다. 쓰기 짝·받은함 짝은 사이트 쓰기 칸이 따로 저장한다
// (불러온 짝에 섞여 온 옛 writes·inboxes로 새 값을 덮어쓰지 않게. 서버도 안 보낸 칸은 그대로 둔다)
const readPart = (m: SiteMapping): SiteMapping => ({ version: m.version, auth_header: m.auth_header, auth_scheme: m.auth_scheme, resources: m.resources });

export const siteAdapter = {
  get: () => call<SiteAdapterState>("GET", PATH),
  propose: (input: ProposeInput) => call<SiteProposal>("POST", `${PATH}/propose`, input),
  save: (base_url: string, mapping: SiteMapping, token?: string, tenant?: string) =>
    call<SiteAdapterState>("POST", `${PATH}/save`, { base_url, mapping: readPart(mapping), token: token || undefined, tenant: tenant || undefined }),
};

// 회사 연결 › 사이트 쓰기 (W4): dashboard-api `site_adapter/write_setup.py`. 로그인 세션의 회사 줄만.
// 쓰기(POST)는 운영자·관리자만. 돈이 걸린 동작(환불·쿠폰)은 confirm_money가 있어야 켜진다.
export const siteWrites = {
  get: () => call<SiteWritesState>("GET", `${PATH}/writes`),
  recent: () => call<{ items: RecentWrite[]; limit: number }>("GET", `${PATH}/writes/recent`),
  saveMap: (action: WriteAction, write: WriteMap | null) => call<SiteWritesState>("POST", `${PATH}/writes/map`, { action, write }),
  saveInbox: (map: InboxMap | null) => call<SiteWritesState>("POST", `${PATH}/inboxes/map`, { inbox: "claims", map }),
  switch: (action: WriteAction, on: boolean, confirm_money = false) =>
    call<SiteWritesState>("POST", `${PATH}/writes/switch`, { action, on, confirm_money }),
  clearDenied: (action: WriteAction) => call<SiteWritesState>("POST", `${PATH}/writes/denied/clear`, { action }),
};

