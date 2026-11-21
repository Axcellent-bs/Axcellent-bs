import type { RadarState } from "./instagramRadar";

// 인스타 레이더 계정 목록 출처 글자 (dashboard-api instagram_radar.py `source`).
// 우선순위: 화면 저장 > 회사 설정 파일 > 자동(경쟁사 찾기로 확인한 계정) > 없음
export const RADAR_SOURCE: Record<RadarState["source"], string> = {
  screen: "화면에서 저장한 목록",
  tenant: "회사 설정 파일",
  auto: "자동 (경쟁사 찾기로 확인한 계정)",
  none: "없음",
};
