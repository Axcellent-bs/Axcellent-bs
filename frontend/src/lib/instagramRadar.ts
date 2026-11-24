import { call } from "./companyConnection";
import type { AgeBand, AudienceGender, RadarKind } from "./data";

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
  /** 계정별 대상층 태그 (계정 이름 → 태그). 목록에 없는 계정은 태그 없음 (옛 서버에는 없음) */
  meta?: Record<string, RadarMetaEntry>;
  /** 이 숫자보다 팔로워가 적은 밈·크리에이터 계정은 제외 (기본 5만) */
  min_followers?: number;
  meta_editable?: boolean;
}

export interface RadarMetaEntry {
  kind?: RadarKind;
  ages?: AgeBand[];
  genders?: AudienceGender[];
  topic?: string | null;
  tag_source?: "ai" | "human";
  followers?: number | null;
}

export interface RadarMetaInput {
  account: string;
  kind: RadarKind;
  ages: AgeBand[];
  genders: AudienceGender[];
  topic: string | null;
}

const PATH = "/api/dashboard/instagram-radar";

export const instagramRadar = {
  get: () => call<RadarState>("GET", PATH),
  save: (accounts: string[], memeAccounts?: string[]) =>
    call<RadarState>("POST", `${PATH}/save`, memeAccounts ? { accounts, meme_accounts: memeAccounts } : { accounts }),
  clear: () => call<RadarState>("POST", `${PATH}/clear`),
  /** 계정 하나의 대상층 태그를 사람이 고친다 (tag_source가 human이 된다). 422 INVALID_HANDLE · 404 NOT_IN_RADAR · 409 META_NOT_EDITABLE */
  saveMeta: (input: RadarMetaInput) => call<RadarState>("POST", `${PATH}/meta`, input),
};

/** 줄·쉼표·공백으로 나눠 계정 이름만 남긴다(`@`는 서버가 뗀다) */
export const splitAccounts = (text: string): string[] =>
  text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
