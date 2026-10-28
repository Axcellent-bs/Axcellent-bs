import { useEffect, useState, type FormEvent } from 'react'
import { checkKey, connectWithKey, disconnect, useCompany, useKeys, type IssuedKey } from './company'

// AXcellent 연결 (고객사 시스템 관리자). AXcellent에서 받은 연결 키를 넣으면 이 워크스페이스가 그 회사로 연결된다.
// 키 발급은 AXcellent 운영팀이 하고(관제 센터), 연결·끊기는 여기서 고객사가 한다.
const STEPS = ['키 확인', '회사 정보 받기', '사내망 확인', '첫 상태 신호 보내기', '연결 완료'] as const
const STEP_MS = 1100

export default function CompanyConnect() {
  const company = useCompany()
  const keys = useKeys()
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState<IssuedKey | null>(null)
  const [step, setStep] = useState(0)
  const [confirmOff, setConfirmOff] = useState(false)

  // 연결 단계를 하나씩 진행하고, 끝나면 회사를 연결한다
  useEffect(() => {
    if (!running) return
    if (step >= STEPS.length - 1) {
      connectWithKey(running)
      const t = setTimeout(() => setRunning(null), 900)
      return () => clearTimeout(t)
    }
    const t = setTimeout(() => setStep((s) => s + 1), STEP_MS)
    return () => clearTimeout(t)
  }, [running, step])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const r = checkKey(input)
    if (!r.ok) return setError(r.reason)
    setError(null)
    setStep(0)
    setRunning(r.issued)
  }

  // 데모 편의: 아직 안 쓴 발급 키를 한 글자씩 넣는다
  const demoKey = keys.find((k) => k.status === 'issued')
  const fillDemo = () => {
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
        <p className="mt-1 text-sm text-ink-500">AXcellent에서 받은 연결 키를 넣으면 이 워크스페이스가 우리 회사로 연결돼요.</p>
      </header>

      <section className={`card flex flex-wrap items-center gap-4 p-5 ${company ? '' : 'border-dashed'}`}>
        <span className={`flex h-10 w-10 items-center justify-center text-sm font-semibold ${company ? 'bg-emerald-50 text-emerald-700' : 'bg-ink-100 text-ink-500'}`}>
          {company ? '✓' : '—'}
        </span>
        <div className="min-w-0">
          <p className="text-xs text-ink-500">지금 연결</p>
          <p className="text-lg font-semibold">{company ? company.name : '연결된 회사 없음'}</p>
          {company && <p className="text-xs text-ink-500">{company.industry} · 사내망 <span className="font-mono">{company.network}</span></p>}
        </div>
        {company && !running && (
          <div className="ml-auto flex items-center gap-2">
            {confirmOff ? (
              <>
                <span className="text-sm text-ink-700">연결을 끊으면 상단의 회사 이름이 빠져요.</span>
                <button type="button" className="btn-quiet" onClick={() => setConfirmOff(false)}>취소</button>
                <button type="button" className="btn-primary bg-red-700 hover:bg-red-800" onClick={() => { disconnect(); setConfirmOff(false) }}>연결 끊기</button>
              </>
            ) : (
              <button type="button" className="btn-ghost" onClick={() => setConfirmOff(true)}>연결 끊기</button>
            )}
          </div>
        )}
      </section>

      {running ? (
        <section className="card space-y-4 p-6" aria-live="polite">
          <h2 className="card-title">{running.company.name}에 연결하는 중</h2>
          <ol className="space-y-2">
            {STEPS.map((s, i) => {
              const done = i < step || (i === step && step === STEPS.length - 1)
              const now = i === step && !done
              return (
                <li key={s} className={`flex items-center gap-3 px-4 py-3 text-sm ${now ? 'bg-brand-50 font-semibold text-brand-700' : done ? 'text-ink-900' : 'text-ink-500'}`}>
                  <span className={`flex h-6 w-6 items-center justify-center text-xs ${done ? 'bg-emerald-700 text-white' : now ? 'animate-pulse bg-brand-600 text-white' : 'bg-ink-100 text-ink-500'}`}>{done ? '✓' : i + 1}</span>
                  {s}
                  {done && i === 1 && <span className="ml-auto text-xs font-normal text-ink-500">{running.company.name} · {running.company.industry}</span>}
                  {done && i === 2 && <span className="ml-auto font-mono text-xs font-normal text-ink-500">{running.company.network}</span>}
                  {done && i === 3 && <span className="ml-auto text-xs font-normal text-ink-500">상태 신호만 보내요 (업무 데이터 X)</span>}
                </li>
              )
            })}
          </ol>
        </section>
      ) : (
        <section className="card space-y-4 p-6">
          <h2 className="card-title">{company ? '다른 키로 다시 연결' : '연결 키 넣기'}</h2>
          <form onSubmit={submit} className="flex flex-wrap gap-2">
            <input className="field min-w-[260px] flex-1 font-mono tracking-wider" placeholder="AXC-XXXX-XXXX-XXXX" value={input}
              onChange={(e) => setInput(e.target.value)} aria-label="연결 키" />
            <button className="btn-primary px-5">연결</button>
          </form>
          {error && <p className="text-sm text-red-700">{error}</p>}
          <div className="flex flex-wrap items-center gap-3 border-t border-ink-100 pt-3 text-xs text-ink-500">
            <span>키는 AXcellent 운영팀이 회사마다 하나씩 발급해요.</span>
            {demoKey && <button type="button" className="btn-ghost px-2.5 py-1 text-xs" onClick={fillDemo}>데모: 발급된 키 넣기 ({demoKey.company.name})</button>}
          </div>
        </section>
      )}
    </div>
  )
}
