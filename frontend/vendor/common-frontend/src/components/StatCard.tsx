import type { ReactNode } from 'react'
import { cx } from '../lib/cx'

export interface StatCardProps {
  label: ReactNode
  /** null·undefined면 '확인 불가'로 표시한다 (0으로 바꾸지 않는다) */
  value: ReactNode | null | undefined
  /** 숫자 뒤 작은 글씨 (건, 대, % …) */
  unit?: string
  hint?: ReactNode
  /** 라벨 앞 점 색. CSS 색 값 (예: 'var(--danger)') */
  dot?: string
  onClick?: () => void
  className?: string
}

/** 숫자 하나를 크게 보여주는 카드 (DejaFix 현황 KPI 카드). 여러 개는 <div className="grid kpi">로 묶는다. */
export function StatCard({ label, value, unit, hint, dot, onClick, className }: StatCardProps) {
  const known = value !== null && value !== undefined
  const body = (
    <>
      <div className="kpi-label">
        {dot && <i className="dot" style={{ background: dot }} aria-hidden="true" />}
        {label}
      </div>
      <div className="kpi-value">
        {known ? value : <span className="card-sub">확인 불가</span>}
        {known && unit && <small>{unit}</small>}
      </div>
      {hint && <div className="kpi-hint">{hint}</div>}
    </>
  )
  return onClick ? (
    <button type="button" className={cx('card kpi-card', className)} onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className={cx('card kpi-card', className)}>{body}</div>
  )
}
