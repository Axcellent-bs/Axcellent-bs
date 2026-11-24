import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { checkKey, connectWithKey, signupWithKey, useCompany, type CompanyInfo, type IssuedKey } from './company'
import { sessionFromLogin, setSession } from '@/lib/humanSession'
import { emailTaken, homeOf, registerAccount, useSession } from './session'

// 관리자(회사 계정) 가입: 운영자에게 받은 키 확인 → 관리자 계정 → 회사 정보 → 연결. 끝나면 그 관리자로 바로 들어간다.
// 이 설치에 이미 연결된 회사의 키면 회사 정보·연결은 건너뛰고 그 회사의 관리자 계정만 만든다.
// 데모 로그인(서버 로그인이 꺼져 있을 때)에서는 계정을 이 브라우저에만 저장하고 비밀번호는 저장하지 않는다(session.tsx).
// 서버 로그인이 켜져 있으면: 회사 전용 키(관제 센터에서 회사 이름을 넣어 발급한 키)는 로그인 전에 확인하고,
// 그 키로 그 회사의 첫 관리자 계정을 서버에 만든 뒤(세션 받음) 그 회사로 연결한다(로드맵 7단계, 캣북스는 그대로).
// 예전 키(회사 정보 없음)는 지금처럼 운영자가 만든 관리자 계정으로 먼저 로그인해야 한다.
// 회사는 계정으로 정한다. 사내망·PC IP는 쓰지 않아 화면에 묻지 않는다(노예진 11/24).
const WORDMARK = `${import.meta.env.BASE_URL}axcellent-wordmark.webp`
const STAGES = ['발급 키 확인', '관리자 계정', '회사 정보', '연결'] as const

export default function Setup() {
  const company = useCompany()
  const { signIn, mode, account } = useSession()
  const serverMode = mode === 'server'
  // 서버 로그인으로 들어온 고객사 관리자 (서버 로그인일 때 연결은 이 계정으로 한다)
  const serverAdmin = serverMode && !!account?.server && !!account.admin
  const navigate = useNavigate()
  const [stage, setStage] = useState(0)
  // 조직 플랜 결제 뒤 "이 키로 관리자 가입"으로 오면 키가 채워져 있다 (/setup?key=)
  const [keyInput, setKeyInput] = useState(() => new URLSearchParams(window.location.search).get('key') ?? '')
  const [key, setKey] = useState<IssuedKey | null>(null)
  const [admin, setAdmin] = useState({ name: '', email: '', password: '' })
  const [info, setInfo] = useState<CompanyInfo>({ name: '', industry: '', network: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // 관리자 계정을 만들고 그 계정으로 들어간다
  function finish() {
    if (serverMode) {
      if (serverAdmin && account) navigate(homeOf(account), { replace: true })
      else setError('관리자 계정으로 로그인한 뒤 연결해 주세요.')
      return
    }
    try {
      const a = signIn(registerAccount({ name: admin.name, email: admin.email }, true).id)
      if (a) navigate(homeOf(a), { replace: true })
    } catch (x) {
      setError(x instanceof Error ? x.message : '관리자 계정을 만들지 못했어요.')
    }
  }
  // 회사 전용 키로 첫 관리자 가입을 하는가 (서버 로그인, 로그인 전, 키 확인에서 가입 가능)
  const keySignup = serverMode && !serverAdmin && !!key?.forCompany && !!key.signup
  // 이 설치에 이미 연결된 회사의 키인가 (그러면 회사 정보·연결은 건너뛴다)
  const sameCompany = !!company && !!key && key.company?.id === company.id

  const next = async (e: FormEvent) => {
    e.preventDefault()
    if (stage === 0) {
      let r: Awaited<ReturnType<typeof checkKey>>
      // 서버 로그인인데 아직 로그인 전이면 로그인 없이 확인한다(회사 전용 키의 첫 관리자 가입)
      try { r = await checkKey(keyInput, serverMode && !account) } catch (x) { return setError((x as Error).message) }
      if (!r.ok) return setError(r.reason)
      setKey(r.issued)
      if (r.issued.company) setInfo((prev) => ({ ...prev, name: r.issued.company?.name || prev.name, siteUrl: r.issued.company?.siteUrl }))
    }
    if (stage === 1 && serverMode && keySignup) {
      // 회사 전용 키로 그 회사의 첫 관리자 가입 → 받은 세션으로 이어서 연결
      if (!admin.name.trim()) return setError('관리자 이름을 입력하세요.')
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(admin.email.trim())) return setError('관리자 이메일을 확인하세요.')
      if (admin.password.length < 8) return setError('비밀번호는 8자 이상이에요.')
      try {
        const data = await signupWithKey(keyInput, { name: admin.name.trim(), email: admin.email.trim(), password: admin.password })
        const session = sessionFromLogin(data)
        if (!session) return setError('가입은 됐는데 로그인 정보를 받지 못했어요. 로그인 화면에서 들어가 주세요.')
        setSession(session)
      } catch (x) { return setError((x as Error).message) }
    } else if (stage === 1 && serverMode) {
      if (!serverAdmin) return setError('운영자에게 받은 관리자 계정으로 먼저 로그인해 주세요.')
      if (sameCompany) return finish()
    }
    if (stage === 1 && !serverMode) {
      if (!admin.name.trim()) return setError('관리자 이름을 입력하세요.')
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(admin.email.trim())) return setError('관리자 이메일을 확인하세요.')
      if (admin.password.length < 8) return setError('비밀번호는 8자 이상이에요.')
      if (emailTaken(admin.email)) return setError('이미 가입된 이메일이에요.')
      if (sameCompany) return finish()
    }
    if (stage === 2) {
      if (!info.name.trim()) return setError('회사 이름을 입력하세요.')
      if (info.siteUrl?.trim() && !/^https?:\/\/\S+$/.test(info.siteUrl.trim())) return setError('사이트 주소는 http:// 또는 https://로 시작해야 해요.')
      if (!key) return setError('키를 다시 확인해 주세요.')
      // 서버에 바로 연결한다(꾸민 진행 단계 없음). 실패하면 이 단계에 머문다
      setBusy(true)
      try {
        if (serverMode) await connectWithKey(key, { ...info, name: info.name.trim(), siteUrl: info.siteUrl?.trim() })
      } catch (x) {
        setBusy(false)
        return setError((x as Error).message)
      }
      setBusy(false)
      setError(null)
      setStage(3)
      return finish()
    }
    setError(null)
    setStage((s) => s + 1)
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#1d1d3a] px-6 py-12">
      <div className="w-full max-w-xl space-y-6">
        <div className="space-y-2 text-center">
          <img src={WORDMARK} alt="AXcellent" className="mx-auto h-9 w-auto" />
          <p className="text-sm text-white/60">관리자 가입 · 운영자에게 받은 키로 회사 계정을 만들어요</p>
          <Link to="/signup" className="text-xs text-white/60 underline hover:text-white">일반회원 가입은 여기</Link>
        </div>

        <ol className="flex flex-wrap justify-center gap-2" aria-label="설정 순서">
          {STAGES.map((s, i) => (
            <li key={s} className={`chip px-3 py-1 text-xs ${i === stage ? 'bg-white text-[#1d1d3a]' : i < stage ? 'bg-emerald-50 text-emerald-700' : 'bg-white/10 text-white/60'}`}>
              {i < stage ? '✓ ' : `${i + 1}. `}{s}
            </li>
          ))}
        </ol>

        <div className="card space-y-4 p-6">
          {stage === 0 && (
            <form onSubmit={next} className="space-y-3">
              <h1 className="text-lg font-semibold">발급 키 넣기</h1>
              <p className="text-sm text-ink-500">운영자에게 받은 키를 넣어 주세요. 키가 확인되면 관리자 계정을 만들 수 있어요.</p>
              <input className="field font-mono tracking-wider" placeholder="AXC-XXXX-XXXX-XXXX" value={keyInput} onChange={(e) => setKeyInput(e.target.value)} aria-label="발급 키" />
              {error && <p className="text-sm text-red-700">{error}</p>}
              <div className="flex items-center gap-2">
                <button className="btn-primary px-5">키 확인</button>
                <Link to="/plans" className="btn-quiet">키가 없나요? 조직 플랜 시작</Link>
              </div>
            </form>
          )}

          {stage === 1 && (
            <form onSubmit={next} className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-lg font-semibold">관리자 계정 만들기</h1>
                {sameCompany && <span className="chip bg-ink-100 text-ink-700">{company?.name}</span>}
                {key?.forCompany && <span className="chip bg-ink-100 text-ink-700">{key.company?.name ?? key.forCompany} 전용 키</span>}
                {company && !sameCompany && !key?.forCompany && <span className="chip bg-amber-50 text-amber-700">새 회사로 연결 · 지금 연결된 {company.name}은 끊겨요</span>}
                <span className="chip bg-emerald-50 text-emerald-700">키 확인됨</span>
              </div>
              <p className="text-sm text-ink-500">이 계정으로 회사 연결과 회원·서버 관리(회원 연결, 사내 PC 연결)를 해요.</p>
              {serverMode && !keySignup ? (
                serverAdmin && account ? (
                  <p className="rounded-xl bg-ink-50 px-4 py-3 text-sm text-ink-700">로그인한 관리자 계정 <span className="font-medium text-ink-900">{account.name}</span> <span className="font-mono text-xs text-ink-500">{account.email}</span>으로 연결해요.</p>
                ) : (
                  <div className="space-y-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">
                    <p>서버 로그인이 켜져 있어 관리자 계정은 AXcellent 운영자가 만들어 드려요. 받은 관리자 계정으로 먼저 로그인하면 회사 연결 화면에서 이 키로 이어서 연결할 수 있어요.</p>
                    <Link to="/login" className="btn-ghost px-3 py-1.5 text-xs">로그인하기</Link>
                  </div>
                )
              ) : <>
              <input className="field" placeholder="이름" value={admin.name} onChange={(e) => setAdmin({ ...admin, name: e.target.value })} aria-label="관리자 이름" />
              <input className="field" placeholder="회사 이메일" value={admin.email} onChange={(e) => setAdmin({ ...admin, email: e.target.value })} aria-label="관리자 이메일" />
              <input className="field" type="password" placeholder="비밀번호 (8자 이상)" value={admin.password} onChange={(e) => setAdmin({ ...admin, password: e.target.value })} aria-label="비밀번호" />
              </>}
              {error && <p className="text-sm text-red-700">{error}</p>}
              <div className="flex items-center gap-2">
                <button className="btn-primary px-5" disabled={serverMode && !serverAdmin && !keySignup}>{sameCompany ? '가입하기' : '다음'}</button>
              </div>
            </form>
          )}

          {stage === 2 && (
            <form onSubmit={next} className="space-y-3">
              <h1 className="text-lg font-semibold">회사 정보 입력</h1>
              <div className="grid gap-3">
                <input className="field" placeholder="회사 이름" value={info.name} onChange={(e) => setInfo({ ...info, name: e.target.value })} aria-label="회사 이름" />
              </div>
              <p className="text-xs text-ink-500">회사 이름은 직원 화면 상단 "AXcellent with ○○"에 나와요. AXcellent에는 회사 이름과 상태 신호만 전달돼요.</p>
              {error && <p className="text-sm text-red-700">{error}</p>}
              <div className="flex items-center gap-2">
                <button className="btn-primary px-5" disabled={busy}>{busy ? '연결하는 중…' : '연결'}</button>
                <button type="button" className="btn-quiet" onClick={() => { setStage(1); setError(null) }}>이전</button>
              </div>
            </form>
          )}

          {stage === 3 && (
            <div className="space-y-2" aria-live="polite">
              <h1 className="text-lg font-semibold">{info.name} 연결했어요</h1>
              <p className="text-sm text-ink-700">관리자로 들어가요. 회사 연결 화면에서 사이트 데이터 연결(사이트 주소·API 키)을 이어서 해 주세요.</p>
              {error && <p className="text-sm text-red-700">{error}</p>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
