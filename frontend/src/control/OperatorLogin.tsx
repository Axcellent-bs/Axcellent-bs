import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { OPERATORS, homeOf, useSession } from '@/demo/session'
import { DemoLoginNotice, ExpiredNotice } from '@/demo/LoginPage'
import { serverLogin } from '@/lib/authApi'

// 관제 센터 로그인: AXcellent 운영팀(우리 개발자) 전용 입구. 고객사 로그인(/login)과 따로 있고, 고객사 계정으로는 못 들어온다.
// 서버 로그인이 켜져 있으면 서버가 비밀번호를 확인하고 운영자 계정만 들인다. 꺼져 있으면(503 LOGIN_DISABLED) 데모 운영자로 들어간다.
const WORDMARK = `${import.meta.env.BASE_URL}axcellent-wordmark.webp`

export default function OperatorLogin() {
  const { signIn, signInServer, fallBackToDemo, mode, expired } = useSession()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (mode !== 'demo') {
      if (!email.trim() || !password) return setError('이메일과 비밀번호를 입력하세요.')
      setBusy(true)
      const r = await serverLogin(email, password)
      setBusy(false)
      if (r.kind === 'ok') {
        if (r.session.account.role !== 'operator') return setError('운영자 계정이 아니에요. 고객사 계정(관리자·일반회원)은 고객사 로그인에서 들어가요.')
        setPassword('')
        const a = signInServer(r.session)
        return navigate(homeOf(a), { replace: true })
      }
      if (r.kind === 'error') return setError(r.message)
      fallBackToDemo() // 서버 로그인이 꺼져 있다 → 데모 운영자로
    }
    const op = OPERATORS.find((x) => x.email === email.trim().toLowerCase())
    if (!op) return setError('운영자 계정이 아니에요. 고객사 계정(관리자·일반회원)은 고객사 로그인에서 들어가요.')
    if (!password) return setError('비밀번호를 입력하세요.')
    const a = signIn(op.id)
    if (a) navigate(homeOf(a), { replace: true })
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#1d1d3a] px-6 py-12">
      <div className="w-full max-w-sm space-y-8">
        <div className="space-y-3 text-center">
          <img src={WORDMARK} alt="AXcellent" className="mx-auto h-9 w-auto" />
          <p className="text-sm text-white/60">운영자 전용 · 관제 센터</p>
        </div>
        <form onSubmit={(e) => void submit(e)} className="space-y-3 bg-surface p-6">
          <h1 className="text-lg font-semibold">관제 센터 로그인</h1>
          <ExpiredNotice expired={expired} />
          <DemoLoginNotice mode={mode} />
          <label className="block space-y-1 text-sm">
            <span className="text-ink-700">운영자 이메일</span>
            <input className="field" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="text-ink-700">비밀번호</span>
            <input className="field" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </label>
          {error && <p className="text-sm text-red-700">{error}</p>}
          <button className="btn-primary w-full py-2.5" disabled={busy}>{busy ? '확인하는 중…' : '로그인'}</button>
          <p className="pt-1 text-xs text-ink-500">고객사 화면에는 들어가지 않아요. 고객사에서 받는 건 상태 신호뿐이에요.</p>
        </form>
      </div>
    </div>
  )
}
