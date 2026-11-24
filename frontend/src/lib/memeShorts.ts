// "요즘 유행·밈"의 쇼츠 검색(shorts_search) 순수 계산. 숫자·정렬·큰 채널 판정은 서버 값 그대로.

import type { MemeShortsSearch, MemeShortsVideo } from "./data.ts";

/** 서버 순서(구독자 대비 조회수 내림차순)를 지키며 작은 채널 / 구독자 10만 이상 채널로 나눈다 */
export function splitShorts(items: MemeShortsVideo[] | undefined): { small: MemeShortsVideo[]; big: MemeShortsVideo[] } {
  const small: MemeShortsVideo[] = [];
  const big: MemeShortsVideo[] = [];
  for (const v of items ?? []) (v.big_channel ? big : small).push(v);
  return { small, big };
}

/** "구독자 대비 12.3배" (모르면 null) */
export function perSubscriberLabel(v: Pick<MemeShortsVideo, "views_per_subscriber">): string | null {
  const r = v.views_per_subscriber;
  if (r == null || !Number.isFinite(r)) return null;
  return `구독자 대비 ${r.toLocaleString("ko-KR", { maximumFractionDigits: 1, minimumFractionDigits: 1 })}배`;
}

/** 안내 한 줄: 키워드가 없으면 설정 안내, 모르면 서버 이유, 영상이 있으면 null */
export function shortsNote(s: MemeShortsSearch): string | null {
  if (s.status === "not_set") return "회사 밈 키워드가 없어요. 아래에서 키워드를 넣으면 다음 날부터 찾아요.";
  if (s.status === "unknown") return s.reason ?? "쇼츠 검색 결과를 모르는 상태예요.";
  return s.items.length === 0 ? (s.reason ?? "찾은 쇼츠가 없어요.") : null;
}
