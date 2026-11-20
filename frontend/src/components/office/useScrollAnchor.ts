import { useCallback, useLayoutEffect, useRef, type RefObject } from "react";
import { restoreScroll, scrollAnchor, type RowBox, type ScrollAnchor } from "@/lib/chatView";

// 대화 칸 스크롤 밀림 막기 (AI 오피스 단톡방 OfficeChat · AI 대화방 칸 RoomPane).
// 맨 아래에 붙어 있지 않을 때는 화면 맨 위에 보이는 말풍선(data-chat-id)과 그 위치를 기억하고,
// 다시 그린 뒤(useLayoutEffect) 같은 말풍선이 같은 위치에 오게 scrollTop을 고친다. 사라졌으면 다음 말풍선을 쓴다.
// 붙어 있을 때(pinned)는 부르는 쪽이 지금처럼 맨 아래로 간다. 계산은 lib/chatView.ts

function rowsOf(box: HTMLElement): RowBox[] {
  const base = box.getBoundingClientRect().top - box.scrollTop;
  return [...box.querySelectorAll<HTMLElement>("[data-chat-id]")].map((el) => {
    const r = el.getBoundingClientRect();
    return { id: el.dataset.chatId ?? "", top: r.top - base, height: r.height };
  });
}

/**
 * deps가 바뀌어 다시 그린 뒤 보던 줄을 제자리에. 반환한 remember()는 사용자가 스크롤할 때(onScroll) 부른다.
 * pinned.current가 참이면 아무것도 하지 않는다
 */
export function useScrollAnchor(box: RefObject<HTMLElement | null>, pinned: RefObject<boolean>, deps: readonly unknown[]): () => void {
  const anchor = useRef<ScrollAnchor | null>(null);
  const remember = useCallback(() => {
    const el = box.current;
    anchor.current = el && !pinned.current ? scrollAnchor(rowsOf(el), el.scrollTop) : null;
  }, [box, pinned]);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el || pinned.current) { anchor.current = null; return; }
    const top = restoreScroll(anchor.current, rowsOf(el));
    if (top !== null && Math.abs(top - el.scrollTop) > 0.5) el.scrollTop = top;
    anchor.current = scrollAnchor(rowsOf(el), el.scrollTop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return remember;
}
