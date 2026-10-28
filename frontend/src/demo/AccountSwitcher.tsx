import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { ACCOUNTS, homeOf, servicesOf, useSession } from './session'
import { useEnabledServices } from './services'

// 데모 전용: 예시 직원 계정을 바꿔 가며 배정된 서비스 화면을 보여 준다. 실제 제품에는 없다.
export function AccountSwitcher() {
  const { account, signIn, signOut } = useSession()
  const navigate = useNavigate()
  const enabled = useEnabledServices()
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
  const pick = (id: string) => {
    const a = signIn(id)
    setOpen(false)
    if (a) navigate(homeOf(a, enabled))
  }

  return (
    <div ref={box} className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-haspopup="menu" aria-expanded={open}
        className="flex items-center gap-2 rounded-full border border-ink-100 bg-surface py-1 pl-1 pr-3 text-sm hover:bg-ink-100">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white">
          {account.role.slice(0, 1)}
        </span>
        <span className="font-medium">{account.role}</span>
        <span className="text-ink-500" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div role="menu" className="card absolute right-0 top-11 z-50 w-72 border border-ink-100 p-2">
          <p className="px-3 pb-1 pt-2 text-xs text-ink-500">프로필 전환 (데모)</p>
          {ACCOUNTS.map((a) => (
            <button key={a.id} role="menuitemradio" aria-checked={a.id === account.id} type="button" onClick={() => pick(a.id)}
              className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-sm hover:bg-ink-100 ${a.id === account.id ? 'bg-brand-50' : ''}`}>
              <span>
                <span className="block font-medium">{a.role}</span>
                <span className="block text-xs text-ink-500">{servicesOf(a, enabled).map((s) => s.name).join(' · ') || a.email}</span>
              </span>
              {a.id === account.id && <span className="text-brand-600" aria-hidden="true">●</span>}
            </button>
          ))}
          <div className="mt-1 border-t border-ink-100 pt-1">
            <button type="button" onClick={() => { signOut(); navigate('/login') }}
              className="w-full rounded-xl px-3 py-2 text-left text-sm text-ink-700 hover:bg-ink-100">로그아웃</button>
          </div>
        </div>
      )}
    </div>
  )
}
