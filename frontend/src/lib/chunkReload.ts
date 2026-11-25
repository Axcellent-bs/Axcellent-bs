// 배포 뒤 옛 코드 조각을 못 받을 때 (11/25 #1748로 lazy 조각이 늘었다. 프론트는 90초마다 배포된다)
// 열린 탭이 옛 조각 주소(assets/<이름>-<해시>.js)를 부르면 "Failed to fetch dynamically imported module"로 화면이 비므로,
// 한 번만 새로고침해 새 index.html·새 조각을 받는다. 같은 주소에서 60초 안에 또 실패하면 더 새로고침하지 않고
// 화면(ErrorBoundary)에 "새 버전이 나왔어요" + [새로고침] 버튼을 보여 준다 (자동 새로고침이 계속 도는 것 막기).

export const CHUNK_RELOAD_KEY = "ax:chunk-reload";
export const CHUNK_RELOAD_WINDOW_MS = 60_000;

export const CHUNK_RELOAD_TITLE = "새 버전이 나왔어요";
export const CHUNK_RELOAD_BODY = "화면 코드가 새로 배포돼서 이 부분을 받지 못했어요. 새로고침하면 새 버전으로 열려요.";
export const CHUNK_RELOAD_BUTTON = "새로고침";

const CHUNK_ERROR = [
  /Failed to fetch dynamically imported module/i, // Chrome
  /error loading dynamically imported module/i, // Firefox
  /Importing a module script failed/i, // Safari
  /Unable to preload CSS/i, // vite CSS 조각
  /Failed to load module script/i,
];

/** 코드 조각 받기 실패인가 (vite:preloadError의 payload, lazy()가 던진 에러) */
export function isChunkLoadError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const { name, message } = error as { name?: unknown; message?: unknown };
  if (name === "ChunkLoadError") return true;
  return typeof message === "string" && CHUNK_ERROR.some((re) => re.test(message));
}

type ReadStorage = Pick<Storage, "getItem" | "setItem">;

export interface ReloadEnv {
  /** sessionStorage를 돌려준다. 막힌 브라우저(사생활 모드·쿠키 차단)에서는 여는 것만으로 던질 수 있다 */
  storage: () => ReadStorage;
  href: string;
  now: number;
  reload: () => void;
}

/**
 * 조각 받기 실패 때 한 번 새로고침한다. 새로고침했으면 true.
 * - 같은 주소에서 60초 안에 이미 새로고침했으면 false (두 번째부터는 화면의 버튼으로)
 * - sessionStorage가 막혀 기록을 못 남기면 false: 기록 없이 새로고침하면 새로고침이 끝없이 돌 수 있어서다
 */
export function reloadOnce(env: ReloadEnv): boolean {
  let storage: ReadStorage;
  let raw: string | null;
  try {
    storage = env.storage();
    raw = storage.getItem(CHUNK_RELOAD_KEY);
  } catch {
    return false;
  }
  try {
    const last = raw ? (JSON.parse(raw) as { href?: unknown; at?: unknown }) : null;
    if (last && last.href === env.href && typeof last.at === "number" && env.now - last.at >= 0 && env.now - last.at < CHUNK_RELOAD_WINDOW_MS) {
      return false;
    }
  } catch {
    // 깨진 기록은 없는 것으로 본다
  }
  try {
    storage.setItem(CHUNK_RELOAD_KEY, JSON.stringify({ href: env.href, at: env.now }));
  } catch {
    return false;
  }
  env.reload();
  return true;
}

/** 브라우저에서 vite:preloadError를 받는다 (main.tsx). 새로고침할 때만 기본 동작(에러 던지기)을 막는다 */
export function installChunkReload(win: Window): void {
  win.addEventListener("vite:preloadError", (event) => {
    const reloaded = reloadOnce({
      storage: () => win.sessionStorage,
      href: win.location.href,
      now: Date.now(),
      reload: () => win.location.reload(),
    });
    // 새로고침하면 화면이 곧 바뀌므로 에러를 막는다. 안 하면 에러를 그대로 두어 ErrorBoundary가 [새로고침] 버튼을 보여 준다
    if (reloaded) event.preventDefault();
  });
}
