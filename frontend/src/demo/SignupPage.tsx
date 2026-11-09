import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { AuthAside } from './LoginPage'
import { homeOf, registerAccount, useSession } from './session'
import { useCompany } from './company'
import { memberLinked, thisPcIp, useLinks } from './members'
import { DeptPicker } from './DeptPicker'

// 일반회원 가입. 관리자(회사 계정)는 운영자에게 받은 키로 따로 가입한다(/setup).
// 이 PC IP나 이메일이 회사에 연결돼 있으면 그 회사 회원으로 바로 들어가고, 아니면 "연결 전"으로 가입해
// 관리자가 회원 연결(/system/members)에서 묶어 줄 때까지 기다린다.
// 데모라 계정은 이 브라우저에만 저장하고 비밀번호는 저장하지 않는다(session.tsx).
export default function SignupPage() {
  const { signIn } = useSession()
  const company = useCompany()
  const links = useLinks()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [dept, setDept] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [agree, setAgree] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [waiting, setWaiting] = useState(false)

  const ip = thisPcIp()
  const linkedTo = company && memberLinked(email, ip, links) ? company.name : null

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return setError('이름을 입력하세요.')
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) return setError('회사 이메일 형식이 아닙니다.')
    if (!dept.trim()) return setError('부서를 고르세요.')
    if (password.length < 8) return setError('비밀번호는 8자 이상이어야 합니다.')
    if (password !== confirm) return setError('비밀번호 확인이 일치하지 않습니다.')
    if (!agree) return setError('이용 약관과 개인정보 처리에 동의해야 가입할 수 있습니다.')
    try {
      const a = registerAccount({ name, email, dept })
      if (!linkedTo) return setWaiting(true)
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
            <h1 className="mt-6 text-2xl font-semibold">회원가입</h1>
          </div>

          <div className="grid grid-cols-2 rounded-xl bg-ink-100 p-1 text-sm" role="tablist" aria-label="가입 유형">
            <span role="tab" aria-selected="true" className="rounded-lg bg-surface py-2 text-center font-semibold shadow-sm">일반회원</span>
            <Link to="/setup" role="tab" aria-selected="false" className="rounded-lg py-2 text-center text-ink-500 hover:text-ink-900">관리자 (회사 계정)</Link>
          </div>

          <dl className="rounded-xl border border-ink-100 bg-surface px-3 py-2 text-sm">
            <dt className="text-xs text-ink-500">가입할 회사</dt>
            <dd className="mt-0.5 flex items-center justify-between gap-2">
              <span className="font-medium">{linkedTo ?? '연결 전'}</span>
              <span className="font-mono text-xs text-ink-500">이 PC {ip}</span>
            </dd>
          </dl>

          {waiting ? (
            <div className="space-y-3 rounded-2xl bg-amber-50 px-4 py-4 text-sm text-amber-800">
              <p className="font-semibold">가입 완료 · 회사 연결 대기</p>
              <p>회사 관리자가 계정이나 이 PC를 회사에 연결하면 로그인할 수 있어요.</p>
              <Link to="/login" className="btn-primary w-full py-2.5">로그인 화면으로</Link>
            </div>
          ) : (
            <form onSubmit={submit} className="space-y-3" noValidate>
              <label className="block space-y-1 text-sm">
                <span className="text-ink-700">이름</span>
                <input className="field" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
              </label>
              <label className="block space-y-1 text-sm">
                <span className="text-ink-700">회사 이메일</span>
                <input className="field" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" placeholder="name@company.com" />
              </label>
              <div className="space-y-1 text-sm">
                <span className="text-ink-700">부서</span>
                <DeptPicker value={dept} onChange={setDept} />
              </div>
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
              <button className="btn-primary w-full py-2.5">가입하기</button>
            </form>
          )}

          <p className="text-center text-sm text-ink-500">
            이미 계정이 있나요? <Link to="/login" className="font-semibold text-brand-600 hover:underline">로그인</Link>
          </p>
        </div>
      </section>
    </div>
  )
}
