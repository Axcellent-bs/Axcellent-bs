import type { ReactNode } from 'react'
import { cx } from '../lib/cx'
import { Icon, type IconName } from './Icon'

/** DejaFix .note(기본) / .note.warn */
export type NoticeTone = 'info' | 'warn'

export interface NoticeProps {
  tone?: NoticeTone
  /** 앞 아이콘 (DejaFix 화면은 bell) */
  icon?: IconName
  children: ReactNode
  className?: string
}

/** 화면 안 안내 띠 (.note) */
export function Notice({ tone = 'info', icon, children, className }: NoticeProps) {
  return (
    <div className={cx('note', tone === 'warn' && 'warn', className)} role={tone === 'warn' ? 'alert' : undefined}>
      {icon && <Icon name={icon} size={16} />}
      <span>{children}</span>
    </div>
  )
}
