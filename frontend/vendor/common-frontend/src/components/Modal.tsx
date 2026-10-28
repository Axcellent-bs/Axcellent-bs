import { useEffect, useId, useRef, type ReactNode } from 'react'
import { cx } from '../lib/cx'
import { Icon } from './Icon'

export interface ModalProps {
  open: boolean
  /** Esc, 바깥(배경) 클릭, × 버튼 모두 이것을 부른다 */
  onClose: () => void
  title: ReactNode
  children: ReactNode
  /** 아래 버튼 줄 */
  footer?: ReactNode
  wide?: boolean
  /** false면 배경 클릭으로 닫히지 않는다 (입력 중인 폼 등) */
  dismissable?: boolean
}

/** [신규] 기본 <dialog> 기반 모달. 포커스 가두기·Esc·배경은 브라우저가 처리한다. 모양은 DejaFix 토큰을 따른다. */
export function Modal({ open, onClose, title, children, footer, wide, dismissable = true }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const pressedOnBackdrop = useRef(false)

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      className={cx('modal', wide && 'wide')}
      aria-labelledby={titleId}
      onCancel={(e) => {
        // Esc: 브라우저가 직접 닫지 않게 막고 부모 상태로 닫는다
        e.preventDefault()
        onClose()
      }}
      onMouseDown={(e) => {
        pressedOnBackdrop.current = e.target === e.currentTarget
      }}
      onClick={(e) => {
        // 안에서 눌러 바깥에서 뗀 경우(글자 드래그 등)는 닫지 않는다
        if (dismissable && pressedOnBackdrop.current && e.target === e.currentTarget) onClose()
        pressedOnBackdrop.current = false
      }}
    >
      <div className="modal-inner">
        <div className="modal-head">
          <h2 className="modal-title" id={titleId}>
            {title}
          </h2>
          <button type="button" className="btn small" onClick={onClose} aria-label="닫기">
            <Icon name="close" size={14} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </dialog>
  )
}
