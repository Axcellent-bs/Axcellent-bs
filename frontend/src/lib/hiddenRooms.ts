// 대화방 목록에서 숨긴 방 (내 브라우저에서만 · 기록은 그대로). 대화 기록·AI 오피스가 같이 쓴다.
// 저장은 localStorage `ax.office.hiddenRooms`(방 id 배열). 읽기·쓰기가 막혀도(사생활 모드 등) 숨김 없이 정상 표시한다.
// 이 파일은 다른 모듈을 값으로 가져오지 않는다: `node --test`로 바로 시험한다 (hiddenRooms.test.ts).

export const HIDDEN_KEY = "ax.office.hiddenRooms";

/** 읽고 쓸 저장소. 기본은 window.localStorage (시험에서 바꿔 끼운다) */
export interface KeyStore { getItem(key: string): string | null; setItem(key: string, value: string): void }

const defaultStore = (): KeyStore | null => {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null; // 접근만 해도 막히는 브라우저 설정
  }
};

/** 숨긴 방 id. 저장소가 없거나 값이 깨졌거나 읽기가 막히면 빈 목록 */
export function loadHidden(store: KeyStore | null = defaultStore()): string[] {
  try {
    const raw = store?.getItem(HIDDEN_KEY);
    const v: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? [...new Set(v.filter((x): x is string => typeof x === "string" && x.length > 0))] : [];
  } catch {
    return [];
  }
}

/** 저장. 막히면 false (이번 화면에서만 숨김) */
export function saveHidden(ids: readonly string[], store: KeyStore | null = defaultStore()): boolean {
  try {
    if (!store) return false;
    store.setItem(HIDDEN_KEY, JSON.stringify([...new Set(ids)]));
    return true;
  } catch {
    return false;
  }
}

export const withHidden = (ids: readonly string[], id: string): string[] => (ids.includes(id) ? [...ids] : [...ids, id]);
export const withoutHidden = (ids: readonly string[], id: string): string[] => ids.filter((x) => x !== id);
