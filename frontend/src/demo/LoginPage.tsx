import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { ACCOUNTS, homeOf, useSession } from './session'
import { useCompany } from './company'

// 사내망 안에서만 열리는 로그인 화면. 계정에 배정된 서비스에 따라 로그인 뒤 보이는 메뉴가 다르다.
// 데모라 비밀번호는 확인하지 않는다.
export default function LoginPage() {
  const { signIn } = useSession()
  const company = useCompany()
  const navigate = useNavigate()
  const [email, setEmail] = useState(ACCOUNTS[1].email)
  const [password, setPassword] = useState('demo')
  const [error, setError] = useState<string | null>(null)

  const enter = (id: string) => {
    const a = signIn(id)
    if (a) navigate(homeOf(a), { replace: true })
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const a = ACCOUNTS.find((x) => x.email === email.trim().toLowerCase())
    if (!a) return setError('등록되지 않은 계정입니다. 아래 데모 계정 중 하나를 고르세요.')
    if (!password) return setError('비밀번호를 입력하세요.')
    enter(a.id)
  }

  return (
    <div className="grid min-h-screen bg-app text-ink-900 lg:grid-cols-[1.1fr_1fr]">
      <section className="hidden flex-col justify-between bg-[#1d1d3a] p-12 text-white lg:flex">
        <div>
          <h1><img src={`${import.meta.env.BASE_URL}axcellent-wordmark.png`} alt="AXcellent" className="h-10 w-auto" /></h1>
          <p className="mt-1 text-sm text-white/70">사내 설치형 AX 솔루션</p>
        </div>
        <div className="space-y-6">
          <p className="text-3xl font-semibold leading-snug">
            필요한 AI 서비스만 골라
            <br />
            회사 안에서 쓰는 업무 시스템
          </p>
          <dl className="grid max-w-md grid-cols-2 gap-3 text-sm">
            <div className="rounded-2xl bg-white/10 p-4">
              <dt className="text-white/60">설치된 회사</dt>
              <dd className="mt-1 font-semibold">{company?.name ?? '없음'}</dd>
            </div>
            <div className="rounded-2xl bg-white/10 p-4">
              <dt className="text-white/60">접속 범위</dt>
              <dd className="mt-1 font-semibold">{company ? `사내망 ${company.network}` : '—'}</dd>
            </div>
            <div className="rounded-2xl bg-white/10 p-4">
              <dt className="text-white/60">외부 접근</dt>
              <dd className="mt-1 font-semibold">차단</dd>
            </div>
            <div className="rounded-2xl bg-white/10 p-4">
              <dt className="text-white/60">데이터 보관</dt>
              <dd className="mt-1 font-semibold">회사 서버</dd>
            </div>
          </dl>
        </div>
        <p className="text-xs text-white/50">DejaFix 인프라 위에서 동작 · 데모 프로토타입</p>
      </section>

      <section className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm space-y-8">
          <div>
            <Link to="/" className="text-sm text-ink-500 hover:text-ink-900">← 서비스 둘러보기</Link>
            <p className="mt-6 text-sm text-ink-500">{company ? `${company.name} 워크스페이스` : 'AXcellent'}</p>
            <h1 className="mt-1 text-2xl font-semibold">로그인</h1>
          </div>
          <form onSubmit={submit} className="space-y-3">
            <label className="block space-y-1 text-sm">
              <span className="text-ink-700">회사 이메일</span>
              <input className="field" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
            </label>
            <label className="block space-y-1 text-sm">
              <span className="text-ink-700">비밀번호</span>
              <input className="field" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button className="btn-primary w-full py-2.5">로그인</button>
          </form>
          <div className="space-y-2">
            <p className="text-xs font-medium text-ink-500">데모 계정</p>
            <div className="grid grid-cols-2 gap-2">
              {ACCOUNTS.map((a) => (
                <button key={a.id} type="button" onClick={() => enter(a.id)}
                  className="rounded-xl border border-ink-100 bg-surface px-3 py-2 text-left text-sm hover:border-brand-500">
                  <span className="block font-medium">{a.role}</span>
                  <span className="block truncate text-xs text-ink-500">{a.email}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
