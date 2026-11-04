// 내 사람 토큰: 승인·머지·재무 결정에 쓰는 사람 토큰을 이 브라우저에만 넣는다 (lib/env.ts).
// 여러 사람이 쓰는 서버는 화면 빌드에 토큰을 넣지 않으므로, 사람마다 자기 토큰을 여기 넣는다.
// 토큰 값은 다시 보여 주지 않는다. 누구 토큰인지는 개발 서비스 /me가 돌려주는 이름으로 보여 준다.
import { useEffect, useRef, useState } from 'react'
import { HUMAN_TOKEN_FROM, setMyHumanToken } from '@/lib/env'
import { useMe } from '@/lib/planning'

const LABEL = { mine: '내 토큰', build: '공용 토큰', none: '토큰 없음' } as const

export function HumanToken() {
  const me = useMe()
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])
  const tone = HUMAN_TOKEN_FROM === 'mine' ? 'bg-emerald-50 text-emerald-700' : HUMAN_TOKEN_FROM === 'build' ? 'bg-amber-50 text-amber-800' : 'bg-ink-100 text-ink-500'
  const who = me?.name ? ` · ${me.name}` : ''
  return (
    <div ref={box} className="relative">
      <button type="button" className={`chip ${tone}`} onClick={() => setOpen((v) => !v)} aria-expanded={open}
        title="승인·머지·재무 결정에 쓰는 사람 토큰">
        {LABEL[HUMAN_TOKEN_FROM]}{HUMAN_TOKEN_FROM !== 'none' && who}
      </button>
      {open && (
        <form className="absolute right-0 top-9 z-30 w-72 space-y-2 rounded-lg border border-ink-200 bg-surface p-3 shadow-lg"
          onSubmit={(e) => { e.preventDefault(); if (value.trim()) setMyHumanToken(value) }}>
          <label className="block text-xs font-medium text-ink-700" htmlFor="my-human-token">내 사람 토큰</label>
          <input id="my-human-token" type="password" autoComplete="off" spellCheck={false} value={value}
            onChange={(e) => setValue(e.target.value)} className="w-full rounded-md border border-ink-200 px-2 py-1.5 text-sm" placeholder="운영자에게 받은 토큰" />
          <div className="flex items-center gap-2">
            <button type="submit" className="btn-primary px-3 py-1 text-xs" disabled={!value.trim()}>넣기</button>
            {HUMAN_TOKEN_FROM === 'mine' && <button type="button" className="btn-ghost px-3 py-1 text-xs" onClick={() => setMyHumanToken(null)}>빼기</button>}
          </div>
        </form>
      )}
    </div>
  )
}
