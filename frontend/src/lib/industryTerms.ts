// 업종 선택지와 업종별 낱말. 회사 업종에 맞춰 "상품"·"주문"을 부르는 말을 고른다.
// 서버의 `ax_agent_template.industry_terms`와 같은 표다(이름·낱말을 같이 고친다). 코드에 한 회사(서점)의 말을 박지 않으려고 둔다.
// 모르는 업종·빈 값은 기본 낱말(상품·주문). 예전에 자유롭게 적은 업종도 낱말이 들어 있으면 맞춘다.
export type IndustryTerms = { item: string; order: string }

export const DEFAULT_TERMS: IndustryTerms = { item: '상품', order: '주문' }

const TABLE: { name: string; words: string[]; terms: IndustryTerms }[] = [
  { name: '서점·출판', words: ['서점', '출판', '도서', '책'], terms: { item: '책', order: '주문' } },
  { name: '여행사', words: ['여행', '투어', '숙박', '항공'], terms: { item: '여행 상품', order: '예약' } },
  { name: '교육', words: ['교육', '강의', '학원', '에듀'], terms: { item: '강의', order: '수강 신청' } },
  { name: '카페·음식', words: ['카페', '음식', '식당', '베이커리'], terms: { item: '메뉴', order: '주문' } },
  { name: '병원', words: ['병원', '의원', '클리닉'], terms: { item: '진료', order: '예약' } },
  { name: '온라인 쇼핑몰', words: ['쇼핑몰', '이커머스', '커머스'], terms: DEFAULT_TERMS },
  { name: '오프라인 유통', words: ['유통', '마트', '매장'], terms: DEFAULT_TERMS },
  { name: '제조', words: ['제조', '공장'], terms: DEFAULT_TERMS },
]

/** 업종 선택지 (연결 키 발급·회사 정보 화면). 마지막은 "기타" */
export const INDUSTRIES: string[] = [...TABLE.map((r) => r.name), '기타']

/** 업종에 맞는 낱말. 선택지 이름이 같으면 그 줄, 아니면 낱말이 들어 있는 첫 줄, 없으면 기본 */
export function termsFor(industry: string | null | undefined): IndustryTerms {
  const text = (industry ?? '').split(/\s+/).filter(Boolean).join(' ')
  if (!text) return DEFAULT_TERMS
  const exact = TABLE.find((r) => r.name === text)
  if (exact) return exact.terms
  return TABLE.find((r) => r.words.some((w) => text.includes(w)))?.terms ?? DEFAULT_TERMS
}
