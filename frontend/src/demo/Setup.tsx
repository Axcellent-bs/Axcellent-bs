import { useEffect, useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router'
import { privateIp } from './store'
import { checkKey, connectWithKey, useCompany, useKeys, type CompanyInfo, type IssuedKey } from './company'
import { useSession } from './session'

// 처음 설정: 회사에 처음 설치했을 때 한 번 하는 화면. 아직 시스템 관리자 계정이 없으므로 로그인 대신 여기서 시작한다.
// 연결 키 확인 → 시스템 관리자 계정 만들기 → 회사 정보 입력 → 연결. 끝나면 시스템 관리자로 바로 들어간다.
// 데모라 계정은 실제로 만들지 않고, 끝나면 데모의 시스템 관리자 계정으로 로그인한다.
const WORDMARK = `${import.meta.env.BASE_URL}axcellent-wordmark.png`
const STAGES = ['연결 키 확인', '시스템 관리자 계정', '회사 정보', '연결'] as const
const RUN_STEPS = ['관리자 계정 만들기', '회사 정보 저장', '사내망 확인', '첫 상태 신호 보내기', '설정 완료'] as const
const INDUSTRIES = ['온라인 쇼핑몰', '오프라인 유통', '여행사', '카페 체인', '병원', '제조', '기타']
const DETECTED_NETWORK = '192.168.30.0/24'
const validNetwork = (v: string) => /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/.test(v.trim()) && privateIp(v.trim().split('/')[0])

export default function Setup() {
  const company = useCompany()
  const keys = useKeys()
  const { signIn } = useSession()
  const navigate = useNavigate()
  const [stage, setStage] = useState(0)
  const [keyInput, setKeyInput] = useState('')
  const [key, setKey] = useState<IssuedKey | null>(null)
  const [admin, setAdmin] = useState({ name: '', email: '', password: '' })
  const [info, setInfo] = useState<CompanyInfo>({ name: '', industry: INDUSTRIES[0], network: DETECTED_NETWORK })
  const [error, setError] = useState<string | null>(null)
  const [run, setRun] = useState(-1)

  // 연결 단계 진행. 끝나면 회사를 연결하고 시스템 관리자로 로그인한다
  useEffect(() => {
    if (run < 0 || !key) return
    if (run >= RUN_STEPS.length - 1) {
      connectWithKey(key, info)
      const t = setTimeout(() => {
        const a = signIn('admin')
        if (a) navigate('/system/services', { replace: true })
      }, 1200)
      return () => clearTimeout(t)
    }
    const t = setTimeout(() => setRun((r) => r + 1), 1000)
    return () => clearTimeout(t)
  }, [run]) // eslint-disable-line react-hooks/exhaustive-deps

  // 이미 설정이 끝난 회사는 로그인으로
  if (company && run < 0) return <Navigate to="/login" replace />

  const next = (e: FormEvent) => {
    e.preventDefault()
    if (stage === 0) {
      const r = checkKey(keyInput)
      if (!r.ok) return setError(r.reason)
      setKey(r.issued)
    }
    if (stage === 1) {
      if (!admin.name.trim()) return setError('관리자 이름을 입력하세요.')
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(admin.email.trim())) return setError('관리자 이메일을 확인하세요.')
      if (admin.password.length < 8) return setError('비밀번호는 8자 이상이에요.')
    }
    if (stage === 2) {
      if (!info.name.trim()) return setError('회사 이름을 입력하세요.')
      if (!validNetwork(info.network)) return setError('사내망 주소를 192.168.30.0/24 같은 형태로 입력하세요. 사내 IP 대역만 돼요.')
      setInfo({ ...info, name: info.name.trim(), network: info.network.trim() })
      setRun(0)
    }
    setError(null)
    setStage((s) => s + 1)
  }

  // 데모 편의: 칸 채우기
  // 아직 어느 회사도 쓴 적 없는 키를 먼저 고른다
  const demoKey = keys.find((k) => k.status === 'issued' && !k.company) ?? keys.find((k) => k.status === 'issued')
  const fillDemo = () => {
    setError(null)
    if (stage === 0 && demoKey) setKeyInput(demoKey.key)
    if (stage === 1) setAdmin({ name: '시스템 관리자', email: 'admin@saebyeok.example', password: 'demo-password' })
    if (stage === 2) setInfo({ name: '새벽커피', industry: '카페 체인', network: DETECTED_NETWORK })
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#1d1d3a] px-6 py-12">
      <div className="w-full max-w-xl space-y-6">
        <div className="space-y-2 text-center">
          <img src={WORDMARK} alt="AXcellent" className="mx-auto h-9 w-auto" />
          <p className="text-sm text-white/60">처음 설정 · 이 서버에 처음 설치했을 때 한 번만 해요</p>
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
              <h1 className="text-lg font-semibold">연결 키 넣기</h1>
              <p className="text-sm text-ink-500">AXcellent에서 받은 키를 넣어 주세요. 키가 확인되면 시스템 관리자 계정을 만들 수 있어요.</p>
              <input className="field font-mono tracking-wider" placeholder="AXC-XXXX-XXXX-XXXX" value={keyInput} onChange={(e) => setKeyInput(e.target.value)} aria-label="연결 키" />
              {error && <p className="text-sm text-red-700">{error}</p>}
              <div className="flex items-center gap-2">
                <button className="btn-primary px-5">키 확인</button>
                {demoKey && <button type="button" className="btn-ghost ml-auto px-2.5 py-1 text-xs" onClick={fillDemo}>데모: 발급된 키 넣기</button>}
              </div>
            </form>
          )}

          {stage === 1 && (
            <form onSubmit={next} className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-lg font-semibold">시스템 관리자 계정 만들기</h1>
                <span className="chip bg-emerald-50 text-emerald-700">키 확인됨</span>
              </div>
              <p className="text-sm text-ink-500">이 계정으로 서비스 고르기, 사내 PC 연결, 직원 계정 관리를 해요.</p>
              <input className="field" placeholder="이름" value={admin.name} onChange={(e) => setAdmin({ ...admin, name: e.target.value })} aria-label="관리자 이름" />
              <input className="field" placeholder="회사 이메일" value={admin.email} onChange={(e) => setAdmin({ ...admin, email: e.target.value })} aria-label="관리자 이메일" />
              <input className="field" type="password" placeholder="비밀번호 (8자 이상)" value={admin.password} onChange={(e) => setAdmin({ ...admin, password: e.target.value })} aria-label="비밀번호" />
              {error && <p className="text-sm text-red-700">{error}</p>}
              <div className="flex items-center gap-2">
                <button className="btn-primary px-5">다음</button>
                <button type="button" className="btn-ghost ml-auto px-2.5 py-1 text-xs" onClick={fillDemo}>데모: 채우기</button>
              </div>
            </form>
          )}

          {stage === 2 && (
            <form onSubmit={next} className="space-y-3">
              <h1 className="text-lg font-semibold">회사 정보 입력</h1>
              <div className="grid gap-3 sm:grid-cols-2">
                <input className="field" placeholder="회사 이름" value={info.name} onChange={(e) => setInfo({ ...info, name: e.target.value })} aria-label="회사 이름" />
                <select className="field" value={info.industry} onChange={(e) => setInfo({ ...info, industry: e.target.value })} aria-label="업종">
                  {INDUSTRIES.map((x) => <option key={x}>{x}</option>)}
                </select>
              </div>
              <label className="block space-y-1 text-sm">
                <span className="text-ink-700">사내망 주소 <span className="text-xs text-ink-500">(이 서버에서 찾은 값, 다르면 고치세요)</span></span>
                <input className="field font-mono" value={info.network} onChange={(e) => setInfo({ ...info, network: e.target.value })} />
              </label>
              <p className="text-xs text-ink-500">회사 이름은 직원 화면 상단 "AXcellent with ○○"에 나와요. AXcellent에는 회사 이름과 상태 신호만 전달돼요.</p>
              {error && <p className="text-sm text-red-700">{error}</p>}
              <div className="flex items-center gap-2">
                <button className="btn-primary px-5">연결</button>
                <button type="button" className="btn-quiet" onClick={() => { setStage(1); setError(null) }}>이전</button>
                <button type="button" className="btn-ghost ml-auto px-2.5 py-1 text-xs" onClick={fillDemo}>데모: 채우기</button>
              </div>
            </form>
          )}

          {stage === 3 && (
            <div className="space-y-3" aria-live="polite">
              <h1 className="text-lg font-semibold">{info.name} 설정하는 중</h1>
              <ol className="space-y-2">
                {RUN_STEPS.map((s, i) => {
                  const done = i < run || (i === run && run === RUN_STEPS.length - 1)
                  const nowStep = i === run && !done
                  return (
                    <li key={s} className={`flex items-center gap-3 rounded-xl px-4 py-2.5 text-sm ${nowStep ? 'bg-brand-50 font-semibold text-brand-700' : done ? 'text-ink-900' : 'text-ink-500'}`}>
                      <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs ${done ? 'bg-emerald-700 text-white' : nowStep ? 'animate-pulse bg-brand-600 text-white' : 'bg-ink-100 text-ink-500'}`}>{done ? '✓' : i + 1}</span>
                      {s}
                      {done && i === 0 && <span className="ml-auto text-xs font-normal text-ink-500">{admin.email}</span>}
                      {done && i === 2 && <span className="ml-auto font-mono text-xs font-normal text-ink-500">{info.network}</span>}
                    </li>
                  )
                })}
              </ol>
              {run >= RUN_STEPS.length - 1 && <p className="text-sm text-emerald-700">설정이 끝났어요. 시스템 관리자로 들어가요.</p>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
