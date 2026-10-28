/** 현재 테마에서 CSS 변수의 실제 값을 읽는다. 캔버스 차트처럼 var()를 못 쓰는 곳에서 쓴다. */
export function cssVar(name: string): string {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  } catch {
    return ''
  }
}
