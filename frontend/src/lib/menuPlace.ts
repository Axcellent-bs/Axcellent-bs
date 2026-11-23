// 방 목록 ⋯ 메뉴를 어디에 열지 (RoomMenu, 11/23). 순수 계산만 한다: `npm test`(node --test)에서 그대로 돈다.
// 메뉴는 document.body에 position: fixed로 그린다(방 줄·스크롤 상자에 가리거나 잘리지 않게).
// - 오른쪽 끝을 ⋯ 버튼 오른쪽 끝에 맞춘다(창 밖으로 나가면 안쪽으로 당긴다)
// - 버튼 아래 공간이 메뉴 높이보다 작으면 위로 연다

export interface Box { top: number; bottom: number; right: number }
export interface Place { top: number; left: number; up: boolean }

export const MENU_WIDTH = 224; // w-56
export const MENU_GAP = 4; // 버튼과 메뉴 사이 (mt-1)
export const EDGE = 8; // 창 가장자리 여백

/** 그리기 전 메뉴 높이 어림값: 위아래 여백 + 항목(설명 줄이 있으면 더 높다). 그린 뒤에는 실제 높이로 다시 잰다 */
export const menuHeight = (items: readonly { sub?: string }[]) => 8 + items.reduce((h, it) => h + (it.sub ? 44 : 30), 0);

export function menuPlace(button: Box, view: { width: number; height: number }, height: number, width = MENU_WIDTH): Place {
  const below = view.height - button.bottom - MENU_GAP - EDGE;
  const up = below < height;
  const top = up ? Math.max(EDGE, button.top - MENU_GAP - height) : button.bottom + MENU_GAP;
  const left = Math.min(Math.max(EDGE, button.right - width), Math.max(EDGE, view.width - width - EDGE));
  return { top, left, up };
}
