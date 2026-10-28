import type { ReactNode } from 'react'

export interface EmptyStateProps {
  title: ReactNode
  /** 제목 아래 버튼·링크 (DejaFix: '현황으로 돌아가기' .btn 링크) */
  action?: ReactNode
}

/** 비었거나 찾을 수 없을 때 (DejaFix .empty > h2 + 버튼) */
export function EmptyState({ title, action }: EmptyStateProps) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      {action}
    </div>
  )
}
