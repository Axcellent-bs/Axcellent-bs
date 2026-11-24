import { call } from "./companyConnection";

// 회사 연결 › 인스타 레이더 계정 (dashboard-api `instagram_radar.py`). 데이터 AI 인스타 레이더가 읽는 계정 목록.
// 화면에서 저장한 목록이 회사 설정 파일(trends.toml [instagram] radar_accounts)보다 먼저다. 쓰기는 사람 토큰(POST).
export interface RadarState {
  accounts: string[];
  /** screen: 화면에서 저장 · tenant: 회사 설정 파일 · auto: 경쟁사 찾기로 확인한 계정 · none: 없음 */
  source: "screen" | "tenant" | "auto" | "none";
  /** 회사 설정 파일에 적힌 계정 수 */
  tenant_count: number;
  max: number;
  editable: boolean;
  /** 밈·크리에이터 계정 (옛 서버에는 없음) */
  meme_accounts?: string[];
  meme_source?: "screen" | "tenant" | "none";
  meme_tenant_count?: number;
}

const PATH = "/api/dashboard/instagram-radar";

export const instagramRadar = {
  get: () => call<RadarState>("GET", PATH),
  save: (accounts: string[], memeAccounts?: string[]) =>
    call<RadarState>("POST", `${PATH}/save`, memeAccounts ? { accounts, meme_accounts: memeAccounts } : { accounts }),
  clear: () => call<RadarState>("POST", `${PATH}/clear`),
};

/** 줄·쉼표·공백으로 나눠 계정 이름만 남긴다(`@`는 서버가 뗀다) */
export const splitAccounts = (text: string): string[] =>
  text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
