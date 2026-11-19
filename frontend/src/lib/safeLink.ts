/**
 * 바깥 값으로 받은 링크 중 http·https만 연다 (href에 넣는 모든 바깥 주소는 이 함수를 거친다. 보안 리뷰 10번).
 * - 앞뒤 공백을 떼고 `http://`·`https://`로 시작해야 한다 (대소문자 무관). javascript:·data:·vbscript:·상대 경로·`//호스트`는 null
 * - 그다음 `new URL`로 읽어 프로토콜이 http:·https:인지 한 번 더 본다. 읽지 못하는 주소(`https://` 뿐 등)는 null
 * - 통과하면 받은 글자 그대로(공백만 뗀 것) 돌려준다
 * lib/briefs.ts·components/data/*는 이 함수를 다시 내보내거나 가져다 쓴다
 */
export const safeLink = (url: string | null | undefined): string | null => {
  const u = url?.trim();
  if (!u || !/^https?:\/\//i.test(u)) return null;
  try {
    const parsed = new URL(u);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? u : null;
  } catch {
    return null;
  }
};
