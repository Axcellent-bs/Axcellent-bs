import type { ReactNode } from 'react'
import { cx } from '../lib/cx'

export interface CardProps {
  title?: ReactNode
  sub?: ReactNode
  /** 주면 카드 전체가 버튼이 된다 (button.card) */
  onClick?: () => void
  as?: 'div' | 'section' | 'article'
  className?: string
  children?: ReactNode
}

/** DejaFix 카드: .card > .card-head > h2.card-title + .card-sub */
export function Card({ title, sub, onClick, as: Tag = 'div', className, children }: CardProps) {
  const head =
    title || sub ? (
      <div className="card-head">
        {title && <h2 className="card-title">{title}</h2>}
        {sub && <span className="card-sub">{sub}</span>}
      </div>
    ) : null
  if (onClick) {
    return (
      <button type="button" className={cx('card', className)} onClick={onClick}>
        {head}
        {children}
      </button>
    )
  }
  return (
    <Tag className={cx('card', className)}>
      {head}
      {children}
    </Tag>
  )
}
