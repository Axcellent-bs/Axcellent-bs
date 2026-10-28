import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { OPERATORS, homeOf, useSession } from '@/demo/session'

// 관제 센터 로그인: AXcellent 운영팀(우리 개발자) 전용 입구. 고객사 로그인(/login)과 따로 있고, 고객사 계정으로는 못 들어온다.
// 데모라 비밀번호는 확인하지 않는다.
const WORDMARK = `${import.meta.env.BASE_URL}axcellent-wordmark.png`

export default function OperatorLogin() {
  const { signIn } = useSession()
  const navigate = useNavigate()
  const [email, setEmail] = useState(OPERATORS[0].email)
  const [password, setPassword] = useState('demo')
  const [error, setError] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const op = OPERATORS.find((x) => x.email === email.trim().toLowerCase())
    if (!op) return setError('AXcellent 운영팀 계정이 아니에요. 고객사 계정은 고객사 로그인에서 들어가요.')
    if (!password) return setError('비밀번호를 입력하세요.')
    const a = signIn(op.id)
    if (a) navigate(homeOf(a), { replace: true })
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#1d1d3a] px-6 py-12">
      <div className="w-full max-w-sm space-y-8">
        <div className="space-y-3 text-center">
          <img src={WORDMARK} alt="AXcellent" className="mx-auto h-9 w-auto" />
          <p className="text-sm text-white/60">운영팀 전용 · 관제 센터</p>
        </div>
        <form onSubmit={submit} className="space-y-3 bg-surface p-6">
          <h1 className="text-lg font-semibold">관제 센터 로그인</h1>
          <label className="block space-y-1 text-sm">
            <span className="text-ink-700">운영팀 이메일</span>
            <input className="field" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="text-ink-700">비밀번호</span>
            <input className="field" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </label>
          {error && <p className="text-sm text-red-700">{error}</p>}
          <button className="btn-primary w-full py-2.5">로그인</button>
          <p className="pt-1 text-xs text-ink-500">고객사 화면에는 들어가지 않아요. 고객사에서 받는 건 상태 신호뿐이에요.</p>
        </form>
      </div>
    </div>
  )
}
