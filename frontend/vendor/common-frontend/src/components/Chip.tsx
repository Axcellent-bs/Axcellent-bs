import type { ReactNode } from 'react'
import { cx } from '../lib/cx'

/** DejaFix의 범용 칩 색 3종 (.chip.neutral / .green / .amber) */
export type ChipTone = 'neutral' | 'green' | 'amber'

export interface ChipProps {
  tone?: ChipTone
  title?: string
  className?: string
  children: ReactNode
}

export function Chip({ tone = 'neutral', title, className, children }: ChipProps) {
  return (
    <span className={cx('chip', tone, className)} title={title}>
      {children}
    </span>
  )
}

/** 칩 여러 개를 줄바꿈 가능하게 묶는다 (.chips) */
export function Chips({ children }: { children: ReactNode }) {
  return <span className="chips">{children}</span>
}
