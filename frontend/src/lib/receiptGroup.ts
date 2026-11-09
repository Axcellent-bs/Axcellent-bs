// 영수증 사진을 한 영수증의 쪽으로 묶을 때 무엇을 묶을지 고른다. 화면(ReceiptAnalysisCard)과 분리한 순수 함수다.

/** 묶을 수 있는 항목: 사진 한 장이거나, 이미 묶어 둔 여러 쪽 */
export interface Groupable { pages: number }

/**
 * 쪽 수 한도(maxPages) 안에서 앞에서부터 항목째로 고른다. 한도를 넘는 항목은 건너뛰고(남기고)
 * 뒤 항목을 이어서 본다 — 한도를 넘는 항목에서 멈추면 뒤에 묶을 수 있는 사진이 있어도 못 묶는다.
 * 묶음은 두 항목 이상일 때만 의미가 있다(한 항목뿐이면 이미 묶인 그대로라 바꿀 것이 없다).
 */
export function pickGroup<T extends Groupable>(list: T[], maxPages: number): { taken: T[]; pages: number; ok: boolean } {
  const taken: T[] = [];
  let pages = 0;
  for (const item of list) {
    if (pages + item.pages > maxPages) continue;
    taken.push(item);
    pages += item.pages;
  }
  return { taken, pages, ok: taken.length >= 2 };
}
