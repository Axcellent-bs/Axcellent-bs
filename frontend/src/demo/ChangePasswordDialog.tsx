import { useEffect, useState, type FormEvent } from 'react'
import { useSession } from './session'
import { changePassword } from '@/lib/authApi'
import { MIN_PASSWORD_LENGTH, passwordChangeProblem } from '@/lib/humanSession'

// 비밀번호 바꾸기 (서버 로그인 계정만, dashboard-api `POST /auth/password`).
// 운영자가 만든 임시 비밀번호를 받은 사람이 자기 비밀번호로 바꾼다. 성공하면 서버가 이 계정의 다른 세션을 모두 끊고
// 새 세션을 주므로 그걸로 바꿔 든다. 비밀번호는 입력칸 상태에만 두고 저장·기록하지 않는다.
export function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const { signInServer } = useSession()
  const [form, setForm] = useState({ current: '', next: '', confirm: '' })
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const problem = passwordChangeProblem(form)
    if (problem) return setError(problem)
    setBusy(true)
    const outcome = await changePassword(form.current, form.next)
    setBusy(false)
    if (outcome.kind === 'error') return setError(outcome.message)
    signInServer(outcome.session)
    setForm({ current: '', next: '', confirm: '' })
    setError(null)
    setDone(true)
  }

  const field = (key: keyof typeof form, label: string, autoComplete: string) => (
    <label className="block text-sm">
      <span className="mb-1 block text-ink-700">{label}</span>
      <input className="field w-full" type="password" value={form[key]} autoComplete={autoComplete}
        onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
    </label>
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose} role="presentation">
      <div role="dialog" aria-modal="true" aria-label="비밀번호 바꾸기" className="card w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-center justify-between">
          <h2 className="card-title">비밀번호 바꾸기</h2>
          <button type="button" className="btn-ghost px-2 py-1" onClick={onClose} aria-label="닫기">✕</button>
        </div>
        {done ? (
          <div className="space-y-4">
            <p role="status" className="text-sm text-emerald-700">비밀번호를 바꿨어요. 다른 기기·탭의 로그인은 끊겼고, 이 화면은 그대로 써요.</p>
            <div className="flex justify-end"><button type="button" className="btn-primary px-5" onClick={onClose}>닫기</button></div>
          </div>
        ) : (
          <form onSubmit={(e) => void submit(e)} className="space-y-3">
            <p className="text-xs text-ink-500">바꾸면 이 계정으로 로그인한 다른 기기·탭은 모두 로그아웃돼요. 새 비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이에요.</p>
            {field('current', '지금 비밀번호', 'current-password')}
            {field('next', '새 비밀번호', 'new-password')}
            {field('confirm', '새 비밀번호 확인', 'new-password')}
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost px-4" onClick={onClose}>취소</button>
              <button className="btn-primary px-5" disabled={busy}>{busy ? '바꾸는 중…' : '바꾸기'}</button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
