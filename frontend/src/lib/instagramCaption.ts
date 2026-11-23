// 인스타 릴스 캡션 칸의 순수 계산(InstagramPanel). 해시태그 줄·AI 생성 문구는 서버가 다시 붙인다.

export const INSTAGRAM_HASHTAG_MAX = 5;

/** "#AI직원 AI, 릴스" → ["AI직원", "AI", "릴스"] (공백·쉼표로 나누고 앞의 #은 뺀다, 겹치면 하나, 최대 5개) */
export function parseHashtags(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(/[\s,]+/)) {
    const word = raw.replace(/^#+/, "").trim();
    if (word && !out.includes(word)) out.push(word);
  }
  return out.slice(0, INSTAGRAM_HASHTAG_MAX);
}
