// 트렌드 분석 의뢰 "직접 정하기"의 프리셋. 값은 백엔드 eco_beauty.toml과 같다 (프리셋을 고르면 칸에 미리 넣는다).
// 프리셋을 바꾸면 백엔드 설정 파일과 같이 바꾼다.
import type { TrendReportGender } from "./data.ts";

export interface TrendReportPreset {
  id: string;
  label: string;
  product: string;
  target_gender: TrendReportGender | null;
  target_ages: string[];
  format: string;
  competitors: string[];
  keywords: string[];
}

export const TREND_REPORT_PRESETS: TrendReportPreset[] = [
  {
    id: "eco_beauty",
    label: "친환경 화장품",
    product: "친환경 화장품 브랜드 인지도",
    target_gender: "f",
    target_ages: ["20", "30"],
    format: "인스타 릴스 30초",
    competitors: ["toun28_official", "beplain_official", "abib.official", "mixsoon_official", "purito_official", "amuse", "sidmool"],
    keywords: ["리필 화장품", "비건 선크림", "클린뷰티", "제로웨이스트", "비건 화장품"],
  },
];
