import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { kindOf, useSession } from './session'

// 오른쪽 위 계정 메뉴. 계정 전환은 없고, 누가 어떤 역할(운영자·관리자·일반회원)로 들어와 있는지와 로그아웃만 둔다.
export function AccountSwitcher() {
  const { account, signOut } = useSession()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  if (!account) return null

  return (
    <div ref={box} className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-haspopup="menu" aria-expanded={open}
        className="flex items-center gap-2 rounded-full border border-ink-100 bg-surface py-1 pl-1 pr-3 text-sm hover:bg-ink-100">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white">
          {account.name.slice(0, 1)}
        </span>
        <span className="font-medium">{account.name}</span>
        <span className="chip bg-ink-100 text-ink-700">{kindOf(account)}</span>
        <span className="text-ink-500" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div role="menu" className="card absolute right-0 top-11 z-50 w-72 border border-ink-100 p-2">
          <div className="px-3 py-2 text-sm">
            <p className="font-medium">{account.name} <span className="text-xs font-normal text-ink-500">{kindOf(account)}{account.admin ? ' · 회사 계정' : ''}</span></p>
            <p className="text-xs text-ink-500">{account.email}</p>
          </div>
          <div className="mt-1 border-t border-ink-100 pt-1">
            <button type="button" role="menuitem" onClick={() => { const op = account.operator; signOut(); navigate(op ? '/control/login' : '/') }}
              className="w-full rounded-xl px-3 py-2 text-left text-sm text-ink-700 hover:bg-ink-100">로그아웃</button>
          </div>
        </div>
      )}
    </div>
  )
}
