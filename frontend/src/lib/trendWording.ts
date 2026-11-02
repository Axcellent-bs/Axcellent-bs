// 트렌드 카드의 쉬운 문구 (표시용 상수). 이 파일의 숫자는 문구를 고르는 데만 쓴다.
// 급상승·평범·판정 불가 판정은 서버(데이터 AI trend_scan)가 정한다. 서버 급상승 기준은 z 3.2다
// (backend/domains/data/README.md "trend_scan"). 아래 구간은 그 위를 사람이 읽기 쉽게 말로 나눈 것뿐이다.

/** 변화 정도: z(평소 흔들림 대비) 구간 → 말. 큰 구간부터 본다 */
export const STRENGTH_BANDS: { min: number; label: string }[] = [
  { min: 6, label: "매우 이례적" },
  { min: 4, label: "이례적" },
  { min: 3.2, label: "조금 이례적" },
];

/** 급상승 카드의 변화 정도. z가 없으면 null. 서버 기준이 바뀌어 3.2 아래 급상승이 와도 가장 낮은 말을 쓴다 */
export function strengthLabel(z: number | null | undefined): string | null {
  if (typeof z !== "number" || !Number.isFinite(z)) return null;
  return (STRENGTH_BANDS.find((b) => z >= b.min) ?? STRENGTH_BANDS[STRENGTH_BANDS.length - 1]).label;
}

/** 급상승인데 배수가 이보다 작으면: 평소에 아주 일정한 키워드라 작은 배수도 드문 변화 */
export const STEADY_RISING_RATIO = 2;
export const STEADY_RISING_NOTE = "평소 검색량이 아주 일정한 키워드라 이 정도도 드문 변화예요";

/** 평범인데 배수가 이 이상이면: 원래 크게 오르내리는 키워드라 큰 배수도 평소 범위 */
export const SWINGY_NORMAL_RATIO = 1.5;
export const SWINGY_NORMAL_NOTE = "원래 크게 오르내리는 키워드예요";

/** 판정 불가 문구 (뒤에 서버 이유를 그대로 붙인다) */
export const UNKNOWN_TREND_TEXT = "자료가 부족해 판단할 수 없어요";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** 판정과 배수로 덧붙일 한 줄. 없으면 null */
export function ratioNote(status: string, ratio: number | null | undefined): string | null {
  if (!isNum(ratio)) return null;
  if (status === "rising" && ratio < STEADY_RISING_RATIO) return STEADY_RISING_NOTE;
  if (status === "normal" && ratio >= SWINGY_NORMAL_RATIO) return SWINGY_NORMAL_NOTE;
  return null;
}
