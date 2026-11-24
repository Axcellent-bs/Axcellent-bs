import { call } from "./companyConnection";

// 회사 연결 › 유튜브 밈 키워드 (dashboard-api `youtube_meme_keywords.py`). 하루 1번 이 키워드로 최근 7일 쇼츠를 찾는다.
// 화면에서 저장한 목록이 회사 설정 파일보다 먼저다. 쓰기는 사람 토큰(POST).
export interface MemeKeywordsState {
  keywords: string[];
  /** screen: 화면에서 저장 · tenant: 회사 설정 파일 · none: 없음 */
  source: "screen" | "tenant" | "none";
  /** 회사 설정 파일에 적힌 키워드 수 */
  tenant_count: number;
  max: number;
  editable: boolean;
}

const PATH = "/api/dashboard/youtube-meme-keywords";

export const youtubeMemeKeywords = {
  get: () => call<MemeKeywordsState>("GET", PATH),
  save: (keywords: string[]) => call<MemeKeywordsState>("POST", `${PATH}/save`, { keywords }),
  clear: () => call<MemeKeywordsState>("POST", `${PATH}/clear`),
};

/** 줄 단위로 나눈다 (키워드 안의 공백은 살린다) */
export const splitKeywords = (text: string): string[] =>
  text.split(/[\r\n,]+/).map((s) => s.trim()).filter(Boolean);
