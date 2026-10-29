import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { AuthAside } from './LoginPage'
import { homeOf, registerAccount, useSession } from './session'
import { useCompany } from './company'

// 회원가입. 가입하면 그 계정으로 바로 로그인한다.
// 회사 연결 전 첫 가입은 시스템 관리자(회사 연결을 맡는다), 연결 뒤에는 그 회사 직원 계정이다.
// 데모라 계정은 이 브라우저에만 저장하고 비밀번호는 저장하지 않는다(session.tsx).
export default function SignupPage() {
  const { signIn } = useSession()
  const company = useCompany()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [agree, setAgree] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return setError('이름을 입력하세요.')
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) return setError('회사 이메일 형식이 아닙니다.')
    if (password.length < 8) return setError('비밀번호는 8자 이상이어야 합니다.')
    if (password !== confirm) return setError('비밀번호 확인이 일치하지 않습니다.')
    if (!agree) return setError('이용 약관과 개인정보 처리에 동의해야 가입할 수 있습니다.')
    try {
      const a = registerAccount({ name, email, role })
      const signed = signIn(a.id)
      if (signed) navigate(homeOf(signed), { replace: true })
    } catch (x) {
      setError(x instanceof Error ? x.message : '가입하지 못했습니다.')
    }
  }

  return (
    <div className="grid min-h-screen bg-app text-ink-900 lg:grid-cols-[1.1fr_1fr]">
      <AuthAside />

      <section className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm space-y-8">
          <div>
            <Link to="/" className="text-sm text-ink-500 hover:text-ink-900">← 서비스 둘러보기</Link>
            <p className="mt-6 text-sm text-ink-500">{company ? `${company.name} 워크스페이스` : 'AXcellent'}</p>
            <h1 className="mt-1 text-2xl font-semibold">회원가입</h1>
          </div>

          <dl className="grid grid-cols-2 gap-2 text-sm">
            <div className="rounded-xl border border-ink-100 bg-surface px-3 py-2">
              <dt className="text-xs text-ink-500">가입할 회사</dt>
              <dd className="mt-0.5 font-medium">{company?.name ?? '연결 전'}</dd>
            </div>
            <div className="rounded-xl border border-ink-100 bg-surface px-3 py-2">
              <dt className="text-xs text-ink-500">계정 권한</dt>
              <dd className="mt-0.5 font-medium">{company ? '직원' : '시스템 관리자'}</dd>
            </div>
          </dl>

          <form onSubmit={submit} className="space-y-3" noValidate>
            <label className="block space-y-1 text-sm">
              <span className="text-ink-700">이름</span>
              <input className="field" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
            </label>
            <label className="block space-y-1 text-sm">
              <span className="text-ink-700">회사 이메일</span>
              <input className="field" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" placeholder="name@company.com" />
            </label>
            <label className="block space-y-1 text-sm">
              <span className="text-ink-700">직무 <span className="text-ink-500">(선택)</span></span>
              <input className="field" value={role} onChange={(e) => setRole(e.target.value)} autoComplete="organization-title" placeholder={company ? '예: CS 상담' : '시스템 관리자'} />
            </label>
            <label className="block space-y-1 text-sm">
              <span className="text-ink-700">비밀번호</span>
              <input className="field" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" placeholder="8자 이상" />
            </label>
            <label className="block space-y-1 text-sm">
              <span className="text-ink-700">비밀번호 확인</span>
              <input className="field" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
            </label>
            <label className="flex items-start gap-2 pt-1 text-sm text-ink-700">
              <input type="checkbox" className="mt-0.5" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
              <span>이용 약관과 개인정보 처리에 동의합니다</span>
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button className="btn-primary w-full py-2.5">가입하고 시작하기</button>
          </form>

          <p className="text-center text-sm text-ink-500">
            이미 계정이 있나요? <Link to="/login" className="font-semibold text-brand-600 hover:underline">로그인</Link>
          </p>
        </div>
      </section>
    </div>
  )
}
