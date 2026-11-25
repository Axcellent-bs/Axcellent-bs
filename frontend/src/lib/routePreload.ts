/** 처음 연 주소의 페이지 코드를 미리 받는다 (11/25 첫 화면 성능).
 *
 * 업무 페이지는 `lazy()`로 나눠 받는데, 로그인·회사 확인이 끝나야 그 페이지를 그리므로 페이지 코드 받기가
 * 세션 확인 뒤로 밀린다. 같은 `import()`를 앱 시작 때 한 번 불러 두면 둘이 나란히 돈다.
 * 받기에 실패해도 조용히 둔다(그 페이지를 그릴 때 lazy가 다시 받고, 실패를 그때 보여 준다) */
export type RoutePreload = readonly [prefix: string, load: () => Promise<unknown>]

/** `pathname`에 맞는 첫 항목(앞에 둔 것이 먼저)의 코드를 받기 시작한다. 받기 시작한 접두어를 돌려준다 */
export function preloadRoute(pathname: string, table: readonly RoutePreload[]): string | null {
  const hit = table.find(([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`))
  if (!hit) return null
  hit[1]().catch(() => undefined)
  return hit[0]
}
