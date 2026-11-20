import { useSyncExternalStore } from "react";
import { humanTokenSetNow, onHumanTokenChange } from "./humanToken";

/** 사람 토큰이 있는지(쓰기 버튼을 켤지). 로그인·로그아웃으로 켜짐·꺼짐이 바뀌면 다시 그린다 */
export const useHumanTokenSet = (): boolean => useSyncExternalStore(onHumanTokenChange, humanTokenSetNow, humanTokenSetNow);
