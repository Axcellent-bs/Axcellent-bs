import { useCallback, useRef, useState } from "react";
import { loadHidden, saveHidden, withHidden, withoutHidden } from "@/lib/hiddenRooms";

// 목록에서 숨긴 방. 저장이 막혀도 이번 화면에서는 숨겨진다 (상태가 먼저, 저장은 시도만).
export function useHiddenRooms() {
  const [ids, setIds] = useState<string[]>(() => loadHidden());
  const cur = useRef(ids);
  const apply = useCallback((next: string[]) => { cur.current = next; setIds(next); saveHidden(next); }, []);
  const hide = useCallback((id: string) => apply(withHidden(cur.current, id)), [apply]);
  const unhide = useCallback((id: string) => apply(withoutHidden(cur.current, id)), [apply]);
  const isHidden = useCallback((id: string) => ids.includes(id), [ids]);
  return { ids, hide, unhide, isHidden };
}
