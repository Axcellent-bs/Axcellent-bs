import { useCallback, useRef, useState } from 'react'
import { useDismiss } from '../hooks/useDismiss'
import { Icon } from './Icon'

export interface UserMenuItem {
  label: string
  onSelect: () => void
}

export interface UserMenuProps {
  name: string
  /** 이름 위 작은 글씨 (역할·소속) */
  role?: string
  /** 펼친 메뉴의 버튼들 (DejaFix: 로그아웃 하나) */
  items: UserMenuItem[]
}

/** 상단바 오른쪽 사용자 칸: 역할(작게) 위에 이름, 누르면 메뉴. 1280px 이하에서는 아바타만 보인다. */
export function UserMenu({ name, role, items }: UserMenuProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(ref, open, close)

  return (
    <div className="usermenu" ref={ref}>
      <button
        type="button"
        className="usermenu-btn"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="user-avatar" aria-hidden="true">
          <Icon name="user" size={18} />
        </span>
        <span className="user-text">
          {role && <small>{role}</small>}
          <b>{name}</b>
        </span>
        <span className="user-caret" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && (
        <div className="usermenu-pop" role="menu">
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              className="btn"
              onClick={() => {
                setOpen(false)
                it.onSelect()
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
