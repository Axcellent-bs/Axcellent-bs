import { call } from "./companyConnection";

// 회사 연결 › 인스타 레이더 계정 (dashboard-api `instagram_radar.py`). 데이터 AI 인스타 레이더가 읽는 계정 목록.
// 화면에서 저장한 목록이 회사 설정 파일(trends.toml [instagram] radar_accounts)보다 먼저다. 쓰기는 사람 토큰(POST).
export interface RadarState {
  accounts: string[];
  /** screen: 화면에서 저장 · tenant: 회사 설정 파일 · none: 없음 */
  source: "screen" | "tenant" | "none";
  /** 회사 설정 파일에 적힌 계정 수 */
  tenant_count: number;
  max: number;
  editable: boolean;
}

const PATH = "/api/dashboard/instagram-radar";

export const instagramRadar = {
  get: () => call<RadarState>("GET", PATH),
  save: (accounts: string[]) => call<RadarState>("POST", `${PATH}/save`, { accounts }),
  clear: () => call<RadarState>("POST", `${PATH}/clear`),
};

/** 줄·쉼표·공백으로 나눠 계정 이름만 남긴다(`@`는 서버가 뗀다) */
export const splitAccounts = (text: string): string[] =>
  text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
