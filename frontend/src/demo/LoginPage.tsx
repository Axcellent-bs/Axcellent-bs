import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { ACCOUNTS, customerAccounts, homeOf, isLinked, useSession, type LoginMode } from './session'
import { useCompany } from './company'
import { serverLogin } from '@/lib/authApi'

/** 서버 로그인이 꺼져 있어 데모 로그인을 쓰는 동안 로그인·가입 화면에 다는 안내 */
export function DemoLoginNotice({ mode }: { mode: LoginMode }) {
  if (mode !== 'demo') return null
  return (
    <p role="status" className="rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-800">
      서버 로그인이 아직 꺼져 있어 데모 로그인으로 들어갑니다. 비밀번호는 확인하지 않고, 계정은 이 브라우저에만 기억해요.
    </p>
  )
}

/** 서버 세션이 끝나 로그인 화면으로 왔을 때 안내 */
export function ExpiredNotice({ expired }: { expired: boolean }) {
  if (!expired) return null
  return <p role="status" className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">로그인이 만료됐어요. 다시 로그인해 주세요.</p>
}

/** 로그인·회원가입 왼쪽 안내 칸 */
export function AuthAside() {
  const company = useCompany()
  return (
    <section className="hidden flex-col justify-between bg-[#1d1d3a] p-12 text-white lg:flex">
      <div>
        <h1><img src={`${import.meta.env.BASE_URL}axcellent-wordmark.webp`} alt="AXcellent" className="h-10 w-auto" /></h1>
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
            <dd className="mt-1 font-semibold">{company ? '허용 IP' : '—'}</dd>
          </div>
          <div className="rounded-2xl bg-white/10 p-4">
            <dt className="text-white/60">외부 접근</dt>
            <dd className="mt-1 font-semibold">허용 IP만</dd>
          </div>
          <div className="rounded-2xl bg-white/10 p-4">
            <dt className="text-white/60">데이터 보관</dt>
            <dd className="mt-1 font-semibold">회사 서버</dd>
          </div>
        </dl>
      </div>
      <p className="text-xs text-white/50">DejaFix 인프라 위에서 동작 · 데모 프로토타입</p>
    </section>
  )
}

// 허용된 IP에서만 열리는 로그인 화면.
// - 서버 로그인이 켜져 있으면 이메일·비밀번호를 서버가 확인한다(dashboard-api `/auth/login`). 데모 계정 버튼은 없다
// - 꺼져 있으면(503 LOGIN_DISABLED) 데모 로그인: 데모 계정 + 회원가입한 계정, 비밀번호는 확인하지 않는다(session.tsx)
export default function LoginPage() {
  const { signIn, signInServer, fallBackToDemo, mode, expired } = useSession()
  const company = useCompany()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const enter = (id: string) => {
    const a = signIn(id)
    if (a) navigate(homeOf(a), { replace: true })
  }
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (mode !== 'demo') {
      if (!email.trim() || !password) return setError('이메일과 비밀번호를 입력하세요.')
      setBusy(true)
      const r = await serverLogin(email, password)
      setBusy(false)
      if (r.kind === 'ok') {
        const role = r.session.account.role
        if (role === 'operator') return setError('운영자 계정은 관제 센터 로그인(/control/login)에서 들어가요.')
        if (!company && role !== 'admin') return setError('회사 연결 전에는 관리자만 로그인할 수 있어요.')
        setPassword('')
        const a = signInServer(r.session)
        return navigate(homeOf(a), { replace: true })
      }
      if (r.kind === 'error') return setError(r.message)
      fallBackToDemo() // 서버 로그인이 꺼져 있다 → 아래 데모 로그인으로
    }
    const a = customerAccounts().find((x) => x.email === email.trim().toLowerCase())
    if (!a) return setError('등록되지 않은 계정입니다. 아래 데모 계정으로 들어가세요.')
    if (!company && !a.admin) return setError('회사 연결 전에는 관리자만 로그인할 수 있어요.')
    if (!isLinked(a)) return setError('회사 관리자가 계정이나 이 PC를 회사에 연결하면 로그인할 수 있어요.')
    if (!password) return setError('비밀번호를 입력하세요.')
    enter(a.id)
  }

  return (
    <div className="grid min-h-screen bg-app text-ink-900 lg:grid-cols-[1.1fr_1fr]">
      <AuthAside />

      <section className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm space-y-8">
          <div>
            <Link to="/" className="text-sm text-ink-500 hover:text-ink-900">← 서비스 둘러보기</Link>
            <p className="mt-6 text-sm text-ink-500">{company ? `${company.name} 워크스페이스` : 'AXcellent'}</p>
            <h1 className="mt-1 text-2xl font-semibold">로그인</h1>
          </div>
          <ExpiredNotice expired={expired} />
          <DemoLoginNotice mode={mode} />
          {!company && (
            <div className="space-y-3 rounded-2xl bg-amber-50 px-4 py-4 text-sm text-amber-800">
              <p className="font-semibold">아직 회사가 연결되지 않았어요</p>
              <p>관리자는 운영자에게 받은 키로 관리자 가입을 해 주세요. 회사 연결도 거기서 해요.</p>
              <Link to="/setup" className="btn-primary w-full py-2.5">관리자 가입</Link>
              <p className="text-xs">연결을 끊은 경우에는 관리자로 로그인해서 다시 연결해요.</p>
            </div>
          )}
          <form onSubmit={(e) => void submit(e)} className="space-y-3">
            <label className="block space-y-1 text-sm">
              <span className="text-ink-700">회사 이메일</span>
              <input className="field" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" />
            </label>
            <label className="block space-y-1 text-sm">
              <span className="text-ink-700">비밀번호</span>
              <input className="field" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button className="btn-primary w-full py-2.5" disabled={busy}>{busy ? '확인하는 중…' : '로그인'}</button>
          </form>
          <p className="text-center text-sm text-ink-500">
            계정이 없나요? <Link to="/signup" className="font-semibold text-brand-600 hover:underline">회원가입</Link>
          </p>
          {/* 데모 계정 버튼은 서버 로그인이 꺼져 있을 때만 (켜져 있으면 비밀번호 없이 들어가는 길을 막는다) */}
          {mode === 'demo' && <div className="space-y-2">
            <p className="text-xs font-medium text-ink-500">데모 계정</p>
            <div className="grid gap-2">
              {ACCOUNTS.map((a) => {
                // 회사 연결 전에는 시스템 관리자만 들어갈 수 있다
                const locked = !company && !a.admin
                return (
                  <button key={a.id} type="button" onClick={() => enter(a.id)} disabled={locked} title={locked ? '회사 연결 뒤에 쓸 수 있어요' : undefined}
                    className="rounded-xl border border-ink-100 bg-surface px-3 py-2 text-left text-sm hover:border-brand-500 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-ink-100">
                    <span className="block font-medium">{a.role}</span>
                    <span className="block truncate text-xs text-ink-500">{locked ? '연결 뒤에 사용' : a.email}</span>
                  </button>
                )
              })}
            </div>
          </div>}
        </div>
      </section>
    </div>
  )
}
