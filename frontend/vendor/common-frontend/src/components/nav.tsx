import type { ReactNode } from 'react'
import type { IconName } from './Icon'

/** AppShell 메뉴 한 칸 */
export interface NavItem {
  href: string
  label: string
  icon?: IconName
  /** 현재 페이지면 true (라우터에서 계산해 넘긴다) */
  active?: boolean
  /** 오른쪽 숫자 배지. 0·undefined면 숨긴다 */
  badge?: number | string
  /** '준비 중' 표시 */
  soon?: boolean
}

/**
 * 링크를 그리는 함수. 기본은 <a href>.
 * React Router 예: ({ item, className, children }) => <Link to={item.href} className={className}>{children}</Link>
 */
export type LinkRenderer = (args: { item: NavItem; className: string; children: ReactNode }) => ReactNode

export const defaultLink: LinkRenderer = ({ item, className, children }) => (
  <a href={item.href} className={className} aria-current={item.active ? 'page' : undefined}>
    {children}
  </a>
)
