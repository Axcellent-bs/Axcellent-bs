import { call, type StoredConnection } from "./companyConnection";
import type { SiteMapping, SiteResource } from "./siteMapping";

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
}

const PATH = "/api/dashboard/site-adapter";

export const siteAdapter = {
  get: () => call<SiteAdapterState>("GET", PATH),
  propose: (input: ProposeInput) => call<SiteProposal>("POST", `${PATH}/propose`, input),
  save: (base_url: string, mapping: SiteMapping, token?: string, tenant?: string) =>
    call<SiteAdapterState>("POST", `${PATH}/save`, { base_url, mapping, token: token || undefined, tenant: tenant || undefined }),
};

