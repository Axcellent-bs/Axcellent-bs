import { useId, useState, type ReactNode } from 'react'
import { cx } from '../lib/cx'
import { Icon } from './Icon'

export interface CollapsibleProps {
  title: ReactNode
  /** 제목 옆 개수. DejaFix처럼 'N건'으로 표시한다 */
  count?: number
  /** 제목 옆 흐린 설명 */
  meta?: ReactNode
  defaultOpen?: boolean
  id?: string
  children: ReactNode
}

/** 눌러서 펼치고 접는 구역 (DejaFix 현황 화면의 목록 묶음) */
export function Collapsible({ title, count, meta, defaultOpen = false, id, children }: CollapsibleProps) {
  const [open, setOpen] = useState(defaultOpen)
  const bodyId = useId()
  return (
    <div className={cx('collapsible', open && 'open')} id={id}>
      <button
        type="button"
        className="collapsible-head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={bodyId} // DejaFix 대비 예외: 펼치는 영역과 버튼을 스크린리더에 연결
      >
        <span className="chev">
          <Icon name="chevron" size={16} />
        </span>
        <span className="collapsible-title">{title}</span>
        {count !== undefined && <span className="collapsible-title">{count}건</span>}
        {meta && <span className="collapsible-meta">{meta}</span>}
      </button>
      {open && <div id={bodyId}>{children}</div>}
    </div>
  )
}
