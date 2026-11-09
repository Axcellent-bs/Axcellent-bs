import type { ReactNode } from 'react'

export interface ProgressBarProps {
  /** 왼쪽 이름 */
  label: ReactNode
  /** null·undefined면 막대를 채우지 않고 '확인 불가' (0으로 바꾸지 않는다) */
  value: number | null | undefined
  max?: number
}

const pct = (v: number, max: number) => (max > 0 ? Math.round((Math.min(Math.max(v, 0), max) / max) * 100) : 0)

/** 이름 | 막대 | 값 한 줄. DejaFix 마크업: .bar-row > span + .bar-track > .bar-fill + span
    막대 색 var(--primary)와 퍼센트 표시는 DejaFix에 없는 기본값으로, 템플릿 작성 시 Claude가 정했다. */
export function ProgressBar({ label, value, max = 100 }: ProgressBarProps) {
  const known = typeof value === 'number' && Number.isFinite(value)
  return (
    <div className="bar-row">
      <span>{label}</span>
      <div className="bar-track">
        {known && <div className="bar-fill" style={{ width: `${pct(value, max)}%`, background: 'var(--primary)' }} />}
      </div>
      <span>{known ? `${pct(value, max)}%` : <span className="card-sub">확인 불가</span>}</span>
    </div>
  )
}
