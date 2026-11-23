// 인쇄(PDF) 직전 이미지 미리 받기 (트렌드 분석 보고서 "PDF로 저장", components/data/TrendReportView.tsx). node --test (printImages.test.ts)
// 썸네일이 loading="lazy"라 화면 밖 이미지를 받지 않은 채 빈 칸으로 찍혔다(11/23 TR-3F0D PDF).
// 인쇄 전에 보고서 안 img를 eager로 바꾸고, 모두 load/error될 때까지(최대 3초) 기다린다. 못 받은 이미지는 인쇄에서 숨긴다(PRINT_IMG_FAILED)

export const PRINT_IMG_WAIT_MS = 3_000;
/** 못 받은 이미지에 붙이는 클래스 (styles.css가 인쇄에서 숨긴다) */
export const PRINT_IMG_FAILED = "print-img-failed";

/** 브라우저 img에서 쓰는 것만 (시험은 가짜 객체) */
export interface ImgLike {
  loading: string;
  complete: boolean;
  naturalWidth: number;
  classList: { add(c: string): void; remove(c: string): void };
  addEventListener(type: "load" | "error", fn: () => void, opts?: { once?: boolean }): void;
  removeEventListener(type: "load" | "error", fn: () => void): void;
}

type Timer = (fn: () => void, ms: number) => unknown;
type ClearTimer = (t: unknown) => void;

/** 다 받았는지(성공·실패 어느 쪽이든 끝났는지) */
const settled = (img: ImgLike) => img.complete;
const broken = (img: ImgLike) => img.complete && img.naturalWidth === 0;

/**
 * img를 모두 eager로 바꾸고 load/error를 기다린다. maxMs가 지나면 기다리지 않는다.
 * 끝나면 못 받은 것(완료됐는데 크기 0, 또는 시간 안에 안 끝난 것)에 PRINT_IMG_FAILED를 붙이고 그 수를 돌려준다
 */
export function waitForImages(
  imgs: readonly ImgLike[], maxMs = PRINT_IMG_WAIT_MS,
  setTimer: Timer = (fn, ms) => setTimeout(fn, ms), clearTimer: ClearTimer = (t) => clearTimeout(t as ReturnType<typeof setTimeout>),
): Promise<{ failed: number; timedOut: boolean }> {
  for (const img of imgs) { img.classList.remove(PRINT_IMG_FAILED); if (img.loading === "lazy") img.loading = "eager"; }
  return new Promise((resolve) => {
    let left = imgs.filter((i) => !settled(i)).length;
    let finished = false;
    const unhooks: (() => void)[] = [];
    const finish = (timedOut: boolean) => {
      if (finished) return;
      finished = true;
      clearTimer(timer);
      for (const u of unhooks) u();
      let failed = 0;
      for (const img of imgs) if (broken(img) || !settled(img)) { img.classList.add(PRINT_IMG_FAILED); failed += 1; }
      resolve({ failed, timedOut });
    };
    const timer = left > 0 ? setTimer(() => finish(true), maxMs) : null;
    if (left === 0) { finish(false); return; }
    for (const img of imgs) {
      if (settled(img)) continue;
      const done = () => { left -= 1; if (left <= 0) finish(false); };
      img.addEventListener("load", done, { once: true });
      img.addEventListener("error", done, { once: true });
      unhooks.push(() => { img.removeEventListener("load", done); img.removeEventListener("error", done); });
    }
  });
}
