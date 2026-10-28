import { useEffect, useState, type FormEvent } from 'react'
import { privateIp, useHosts } from './store'
import { useEnabledServices } from './services'
import { checkKey, connectWithKey, disconnect, updateCompanyInfo, useCompany, useKeys, type CompanyInfo, type IssuedKey } from './company'

// AXcellent 연결 (고객사 시스템 관리자). 키를 넣어 확인하고 → 회사 정보를 직접 입력하면 → 이 워크스페이스가 그 회사로 연결된다.
// 키에는 회사 정보가 없다(연결 권한만). 키 발급은 AXcellent 운영팀이 하고, 연결·끊기는 여기서 고객사가 한다.
const STEPS = ['회사 정보 저장', '사내망 확인', '첫 상태 신호 보내기', '연결 완료'] as const
const STEP_MS = 1100
const INDUSTRIES = ['온라인 쇼핑몰', '오프라인 유통', '여행사', '카페 체인', '병원', '제조', '기타']
/** 이 서버가 있는 사내망 (설치할 때 찾은 값이라 가정) */
const DETECTED_NETWORK = '192.168.30.0/24'
const DEMO_INFO: CompanyInfo = { name: '새벽커피', industry: '카페 체인', network: DETECTED_NETWORK }

type Phase = { at: 'key' } | { at: 'info'; key: IssuedKey } | { at: 'run'; key: IssuedKey; info: CompanyInfo }

const validNetwork = (v: string) => /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/.test(v.trim()) && privateIp(v.trim().split('/')[0])

export default function CompanyConnect() {
  const company = useCompany()
  const keys = useKeys()
  const [phase, setPhase] = useState<Phase>({ at: 'key' })
  const [input, setInput] = useState('')
  const [info, setInfo] = useState<CompanyInfo>({ name: '', industry: INDUSTRIES[0], network: DETECTED_NETWORK })
  const [error, setError] = useState<string | null>(null)
  const [step, setStep] = useState(0)
  const [confirmOff, setConfirmOff] = useState(false)
  const [editing, setEditing] = useState(false)
  const [showKey, setShowKey] = useState(false)
  const enabled = useEnabledServices()
  const [hosts] = useHosts()

  // 연결 단계를 하나씩 진행하고, 끝나면 회사를 연결한다
  useEffect(() => {
    if (phase.at !== 'run') return
    if (step >= STEPS.length - 1) {
      connectWithKey(phase.key, phase.info)
      const t = setTimeout(() => { setPhase({ at: 'key' }); setInput(''); setShowKey(false) }, 900)
      return () => clearTimeout(t)
    }
    const t = setTimeout(() => setStep((s) => s + 1), STEP_MS)
    return () => clearTimeout(t)
  }, [phase, step])

  const submitKey = (e: FormEvent) => {
    e.preventDefault()
    const r = checkKey(input)
    if (!r.ok) return setError(r.reason)
    setError(null)
    // 같은 키로 다시 연결하면 지난번에 입력한 회사 정보를 미리 채운다
    const prev = r.issued.company
    setInfo(prev ? { name: prev.name, industry: prev.industry, network: prev.network } : { name: '', industry: INDUSTRIES[0], network: DETECTED_NETWORK })
    setPhase({ at: 'info', key: r.issued })
  }
  const submitInfo = (e: FormEvent) => {
    e.preventDefault()
    if (phase.at !== 'info') return
    if (!info.name.trim()) return setError('회사 이름을 입력하세요.')
    if (!validNetwork(info.network)) return setError('사내망 주소를 192.168.30.0/24 같은 형태로 입력하세요. 사내 IP 대역만 돼요.')
    setError(null)
    setStep(0)
    setPhase({ at: 'run', key: phase.key, info: { ...info, name: info.name.trim(), network: info.network.trim() } })
  }

  const saveInfo = (e: FormEvent) => {
    e.preventDefault()
    if (!info.name.trim()) return setError('회사 이름을 입력하세요.')
    if (!validNetwork(info.network)) return setError('사내망 주소를 192.168.30.0/24 같은 형태로 입력하세요. 사내 IP 대역만 돼요.')
    setError(null)
    updateCompanyInfo({ ...info, name: info.name.trim(), network: info.network.trim() })
    setEditing(false)
  }

  // 데모 편의: 아직 안 쓴 키를 한 글자씩 넣는다
  // 아직 어느 회사도 쓴 적 없는 키를 먼저 고른다
  const demoKey = keys.find((k) => k.status === 'issued' && !k.company) ?? keys.find((k) => k.status === 'issued')
  const fillDemoKey = () => {
    if (!demoKey) return
    setError(null)
    let i = 0
    const t = setInterval(() => {
      i += 1
      setInput(demoKey.key.slice(0, i))
      if (i >= demoKey.key.length) clearInterval(t)
    }, 45)
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-8 py-8">
      <header>
        <p className="text-sm text-ink-500">시스템 관리</p>
        <h1 className="text-2xl font-semibold">AXcellent 연결</h1>
        <p className="mt-1 text-sm text-ink-500">AXcellent에서 받은 연결 키를 넣고, 우리 회사 정보를 입력하면 연결돼요.</p>
      </header>

      {company && phase.at === 'key' ? (
        <section className="card space-y-5 p-6">
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-50 text-lg font-semibold text-emerald-700">✓</span>
            <div className="min-w-0">
              <p className="text-xs text-ink-500">연결된 회사</p>
              <p className="text-xl font-semibold">{company.name}</p>
            </div>
            <span className="chip bg-emerald-50 text-emerald-700">연결됨</span>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              {confirmOff ? (
                <>
                  <span className="text-sm text-ink-700">연결을 끊으면 상단의 회사 이름이 빠지고, 직원들은 로그인할 수 없어요.</span>
                  <button type="button" className="btn-quiet" onClick={() => setConfirmOff(false)}>취소</button>
                  <button type="button" className="btn-primary bg-red-700 hover:bg-red-800" onClick={() => { disconnect(); setConfirmOff(false); setEditing(false) }}>연결 끊기</button>
                </>
              ) : (
                <>
                  {!editing && <button type="button" className="btn-ghost" onClick={() => { setInfo({ name: company.name, industry: company.industry, network: company.network }); setEditing(true); setError(null) }}>회사 정보 고치기</button>}
                  <button type="button" className="btn-ghost" onClick={() => setConfirmOff(true)}>연결 끊기</button>
                </>
              )}
            </div>
          </div>

          {editing ? (
            <form onSubmit={saveInfo} className="space-y-3 border-t border-ink-100 pt-4">
              <div className="grid gap-3 md:grid-cols-3">
                <label className="block space-y-1 text-sm">
                  <span className="text-ink-700">회사 이름</span>
                  <input className="field" value={info.name} onChange={(e) => setInfo({ ...info, name: e.target.value })} />
                </label>
                <label className="block space-y-1 text-sm">
                  <span className="text-ink-700">업종</span>
                  <select className="field" value={info.industry} onChange={(e) => setInfo({ ...info, industry: e.target.value })}>
                    {[...new Set([info.industry, ...INDUSTRIES])].map((x) => <option key={x}>{x}</option>)}
                  </select>
                </label>
                <label className="block space-y-1 text-sm">
                  <span className="text-ink-700">사내망 주소</span>
                  <input className="field font-mono" value={info.network} onChange={(e) => setInfo({ ...info, network: e.target.value })} />
                </label>
              </div>
              {error && <p className="text-sm text-red-700">{error}</p>}
              <div className="flex gap-2">
                <button className="btn-primary px-5">저장</button>
                <button type="button" className="btn-quiet" onClick={() => { setEditing(false); setError(null) }}>취소</button>
              </div>
            </form>
          ) : (
            <dl className="grid gap-x-6 gap-y-4 border-t border-ink-100 pt-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
              <div><dt className="text-xs text-ink-500">업종</dt><dd className="mt-0.5 font-medium">{company.industry}</dd></div>
              <div><dt className="text-xs text-ink-500">사내망 주소</dt><dd className="mt-0.5 font-mono">{company.network}</dd></div>
              <div><dt className="text-xs text-ink-500">연결 키</dt><dd className="mt-0.5 font-mono">{company.key ? `${company.key.slice(0, 8)}••••-••••` : '—'}</dd></div>
              <div><dt className="text-xs text-ink-500">연결한 날</dt><dd className="mt-0.5">{company.connectedAt ? new Date(company.connectedAt).toLocaleString('ko-KR', { year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}</dd></div>
              <div><dt className="text-xs text-ink-500">사용 중인 서비스</dt><dd className="mt-0.5">{enabled.length}개</dd></div>
              <div><dt className="text-xs text-ink-500">연결된 사내 PC</dt><dd className="mt-0.5">{hosts.filter((h) => h.status === 'online').length} / {hosts.length}대</dd></div>
              <div className="sm:col-span-2 lg:col-span-3"><dt className="text-xs text-ink-500">AXcellent로 보내는 것</dt><dd className="mt-0.5 text-ink-700">회사 이름과 상태 신호(살아 있는지·버전·오류 수·사용량)만 보내요. 업무 데이터는 회사 밖으로 나가지 않아요.</dd></div>
            </dl>
          )}

          <div className="border-t border-ink-100 pt-4">
            {showKey ? (
              <div className="space-y-3">
                <h2 className="card-title">다른 키로 다시 연결</h2>
                <form onSubmit={submitKey} className="flex flex-wrap gap-2">
                  <input className="field min-w-[260px] flex-1 font-mono tracking-wider" placeholder="AXC-XXXX-XXXX-XXXX" value={input}
                    onChange={(e) => setInput(e.target.value)} aria-label="연결 키" />
                  <button className="btn-primary px-5">키 확인</button>
                  <button type="button" className="btn-quiet" onClick={() => { setShowKey(false); setError(null) }}>닫기</button>
                </form>
                {error && !editing && <p className="text-sm text-red-700">{error}</p>}
              </div>
            ) : (
              <button type="button" className="btn-quiet px-0 text-xs" onClick={() => setShowKey(true)}>다른 키로 다시 연결하기</button>
            )}
          </div>
        </section>
      ) : (
        <>
          {!company && phase.at === 'key' && (
            <section className="card flex items-center gap-4 border-dashed p-5">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-ink-100 text-sm font-semibold text-ink-500">—</span>
              <div>
                <p className="text-xs text-ink-500">지금 연결</p>
                <p className="text-lg font-semibold">연결된 회사 없음</p>
              </div>
            </section>
          )}

          {/* 진행 표시: 키 → 회사 정보 → 연결 */}
          <ol className="flex flex-wrap gap-2 text-sm" aria-label="연결 순서">
            {['1. 키 넣기', '2. 회사 정보 입력', '3. 연결'].map((label, i) => {
              const idx = phase.at === 'key' ? 0 : phase.at === 'info' ? 1 : 2
              return (
                <li key={label} className={`chip px-3 py-1 text-xs ${i === idx ? 'bg-brand-600 text-white' : i < idx ? 'bg-emerald-50 text-emerald-700' : 'bg-ink-100 text-ink-500'}`}>
                  {i < idx ? '✓ ' : ''}{label}
                </li>
              )
            })}
          </ol>

          {phase.at === 'key' && (
            <section className="card space-y-4 p-6">
              <h2 className="card-title">연결 키 넣기</h2>
              <form onSubmit={submitKey} className="flex flex-wrap gap-2">
                <input className="field min-w-[260px] flex-1 font-mono tracking-wider" placeholder="AXC-XXXX-XXXX-XXXX" value={input}
                  onChange={(e) => setInput(e.target.value)} aria-label="연결 키" />
                <button className="btn-primary px-5">키 확인</button>
              </form>
              {error && <p className="text-sm text-red-700">{error}</p>}
              <div className="flex flex-wrap items-center gap-3 border-t border-ink-100 pt-3 text-xs text-ink-500">
                <span>키는 AXcellent 운영팀이 발급해요. 키에는 회사 정보가 없고, 다음 단계에서 직접 입력해요.</span>
                {demoKey && <button type="button" className="btn-ghost px-2.5 py-1 text-xs" onClick={fillDemoKey}>데모: 발급된 키 넣기</button>}
              </div>
            </section>
          )}
        </>
      )}

      {phase.at === 'info' && (
        <section className="card space-y-4 p-6">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="card-title">회사 정보 입력</h2>
            <span className="chip bg-emerald-50 text-emerald-700">키 확인됨 · <span className="font-mono">{phase.key.key.slice(0, 8)}…</span></span>
          </div>
          <form onSubmit={submitInfo} className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
              <label className="block space-y-1 text-sm">
                <span className="text-ink-700">회사 이름</span>
                <input className="field" value={info.name} onChange={(e) => setInfo({ ...info, name: e.target.value })} placeholder="예: 새벽커피" />
              </label>
              <label className="block space-y-1 text-sm">
                <span className="text-ink-700">업종</span>
                <select className="field" value={info.industry} onChange={(e) => setInfo({ ...info, industry: e.target.value })}>
                  {INDUSTRIES.map((x) => <option key={x}>{x}</option>)}
                </select>
              </label>
            </div>
            <label className="block space-y-1 text-sm">
              <span className="text-ink-700">사내망 주소 <span className="text-xs text-ink-500">(이 서버에서 찾은 값, 다르면 고치세요)</span></span>
              <input className="field font-mono" value={info.network} onChange={(e) => setInfo({ ...info, network: e.target.value })} />
            </label>
            {error && <p className="text-sm text-red-700">{error}</p>}
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn-primary px-5">연결</button>
              <button type="button" className="btn-quiet" onClick={() => { setPhase({ at: 'key' }); setError(null) }}>키 다시 넣기</button>
              <button type="button" className="btn-ghost ml-auto px-2.5 py-1 text-xs" onClick={() => setInfo(DEMO_INFO)}>데모: 회사 정보 채우기</button>
            </div>
            <p className="text-xs text-ink-500">회사 이름은 직원 화면 상단 "AXcellent with ○○"에 나와요. AXcellent에는 상태 신호와 함께 회사 이름만 전달돼요.</p>
          </form>
        </section>
      )}

      {phase.at === 'run' && (
        <section className="card space-y-4 p-6" aria-live="polite">
          <h2 className="card-title">{phase.info.name} 연결하는 중</h2>
          <ol className="space-y-2">
            {STEPS.map((s, i) => {
              const done = i < step || (i === step && step === STEPS.length - 1)
              const now = i === step && !done
              return (
                <li key={s} className={`flex items-center gap-3 rounded-xl px-4 py-3 text-sm ${now ? 'bg-brand-50 font-semibold text-brand-700' : done ? 'text-ink-900' : 'text-ink-500'}`}>
                  <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs ${done ? 'bg-emerald-700 text-white' : now ? 'animate-pulse bg-brand-600 text-white' : 'bg-ink-100 text-ink-500'}`}>{done ? '✓' : i + 1}</span>
                  {s}
                  {done && i === 0 && <span className="ml-auto text-xs font-normal text-ink-500">{phase.info.name} · {phase.info.industry}</span>}
                  {done && i === 1 && <span className="ml-auto font-mono text-xs font-normal text-ink-500">{phase.info.network}</span>}
                  {done && i === 2 && <span className="ml-auto text-xs font-normal text-ink-500">상태 신호만 보내요 (업무 데이터 X)</span>}
                </li>
              )
            })}
          </ol>
        </section>
      )}
    </div>
  )
}
