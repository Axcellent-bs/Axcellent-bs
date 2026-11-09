import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { issueKey } from './company'

// 조직 플랜 시작: 플랜·회사 정보 → 담당자 이메일 인증 → 결제 → 발급 키(화면 + 이메일).
// 받은 키로 관리자(회사 계정) 가입(/setup?key=)을 한다. 발급한 키는 운영자 관제 센터 키 목록(/control/keys)에도 보인다.
// 데모라 결제·메일은 실제로 하지 않고, 인증 코드는 화면에 보여 준다.
const WORDMARK = `${import.meta.env.BASE_URL}axcellent-wordmark.png`
const STAGES = ['플랜·회사 정보', '이메일 인증', '결제', '키 발급'] as const
const PLAN = { name: '조직 플랜', price: 990_000, seats: 50, features: ['AI 서비스 13개 중 필요한 것만 켜기', '사내 설치 · 외부 접근 차단', '관리자 계정 + 일반회원 50명', 'DejaFix 인프라 감시 포함'] }
const PAYMENTS = ['법인카드', '계좌이체', '세금계산서 발행 후 납부'] as const
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const won = (n: number) => `₩${n.toLocaleString('ko-KR')}`

export default function PlanPurchase() {
  const [stage, setStage] = useState(0)
  const [form, setForm] = useState({ company: '', name: '', email: '' })
  const [code, setCode] = useState('')
  const [sent, setSent] = useState('')
  const [payment, setPayment] = useState<(typeof PAYMENTS)[number]>(PAYMENTS[0])
  const [key, setKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const sendCode = () => setSent(String(Math.floor(100000 + Math.random() * 900000)))

  const next = (e: FormEvent) => {
    e.preventDefault()
    if (stage === 0) {
      if (!form.company.trim()) return setError('회사 이름을 입력하세요.')
      if (!form.name.trim()) return setError('담당자 이름을 입력하세요.')
      if (!EMAIL.test(form.email.trim())) return setError('업무 이메일 형식이 아닙니다.')
      sendCode()
    }
    if (stage === 1 && code.trim() !== sent) return setError('인증 코드가 맞지 않습니다.')
    if (stage === 2) setKey(issueKey(`${form.company.trim()} · ${PLAN.name} · ${form.email.trim()}`).key)
    setError(null)
    setStage((s) => s + 1)
  }
  const copy = async () => {
    if (!key) return
    try {
      await navigator.clipboard.writeText(key)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#1d1d3a] px-6 py-12">
      <div className="w-full max-w-xl space-y-6">
        <div className="space-y-2 text-center">
          <Link to="/"><img src={WORDMARK} alt="AXcellent" className="mx-auto h-9 w-auto" /></Link>
          <p className="text-sm text-white/60">{PLAN.name} 시작 · 결제하면 관리자 가입 키를 바로 받아요</p>
        </div>

        <ol className="flex flex-wrap justify-center gap-2" aria-label="진행 순서">
          {STAGES.map((s, i) => (
            <li key={s} className={`chip px-3 py-1 text-xs ${i === stage ? 'bg-white text-[#1d1d3a]' : i < stage ? 'bg-emerald-50 text-emerald-700' : 'bg-white/10 text-white/60'}`}>
              {i < stage ? '✓ ' : `${i + 1}. `}{s}
            </li>
          ))}
        </ol>

        <div className="card space-y-4 p-6">
          {stage < 3 && (
            <div className="flex items-baseline justify-between gap-3 rounded-xl bg-app px-4 py-3">
              <div>
                <p className="font-semibold">{PLAN.name}</p>
                <p className="text-xs text-ink-500">관리자 1 + 일반회원 {PLAN.seats}명</p>
              </div>
              <p className="text-lg font-semibold">{won(PLAN.price)}<span className="text-xs font-normal text-ink-500"> / 월</span></p>
            </div>
          )}

          {stage === 0 && (
            <form onSubmit={next} className="space-y-3">
              <ul className="grid gap-1 text-sm text-ink-700 sm:grid-cols-2">
                {PLAN.features.map((f) => <li key={f}>✓ {f}</li>)}
              </ul>
              <input className="field" placeholder="회사 이름" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} aria-label="회사 이름" />
              <div className="grid gap-3 sm:grid-cols-2">
                <input className="field" placeholder="담당자 이름" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} aria-label="담당자 이름" autoComplete="name" />
                <input className="field" type="email" placeholder="업무 이메일" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} aria-label="업무 이메일" autoComplete="email" />
              </div>
              {error && <p className="text-sm text-red-700">{error}</p>}
              <button className="btn-primary px-5">인증 코드 받기</button>
            </form>
          )}

          {stage === 1 && (
            <form onSubmit={next} className="space-y-3">
              <h1 className="text-lg font-semibold">이메일 인증</h1>
              <p className="text-sm text-ink-500"><span className="font-mono">{form.email}</span>로 보낸 6자리 코드를 넣어 주세요.</p>
              <input className="field font-mono tracking-[0.4em]" inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} aria-label="인증 코드" />
              {error && <p className="text-sm text-red-700">{error}</p>}
              <div className="flex items-center gap-2">
                <button className="btn-primary px-5">확인</button>
                <button type="button" className="btn-quiet" onClick={() => { sendCode(); setCode(''); setError(null) }}>다시 보내기</button>
                <span className="chip ml-auto bg-ink-100 font-mono text-ink-700" title="데모: 메일 대신 화면에 보여 줘요">데모 코드 {sent}</span>
              </div>
            </form>
          )}

          {stage === 2 && (
            <form onSubmit={next} className="space-y-3">
              <h1 className="text-lg font-semibold">결제</h1>
              <dl className="grid grid-cols-2 gap-2 text-sm">
                <div className="rounded-xl border border-ink-100 px-3 py-2"><dt className="text-xs text-ink-500">회사</dt><dd className="font-medium">{form.company}</dd></div>
                <div className="rounded-xl border border-ink-100 px-3 py-2"><dt className="text-xs text-ink-500">담당자</dt><dd className="font-medium">{form.name}</dd></div>
              </dl>
              <fieldset className="space-y-2">
                <legend className="text-sm text-ink-700">결제 방법</legend>
                {PAYMENTS.map((p) => (
                  <label key={p} className="flex items-center gap-2 rounded-xl border border-ink-100 px-3 py-2 text-sm">
                    <input type="radio" name="payment" checked={payment === p} onChange={() => setPayment(p)} />
                    {p}
                  </label>
                ))}
              </fieldset>
              <div className="flex items-center gap-2">
                <button className="btn-primary px-5">{won(PLAN.price)} 결제</button>
                <button type="button" className="btn-quiet" onClick={() => { setStage(0); setError(null) }}>이전</button>
                <span className="chip ml-auto bg-ink-100 text-ink-700">데모 결제</span>
              </div>
            </form>
          )}

          {stage === 3 && key && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-lg font-semibold">관리자 가입 키</h1>
                <span className="chip bg-emerald-50 text-emerald-700">결제 완료 · {payment}</span>
              </div>
              <div className="flex items-center gap-2 rounded-xl bg-app px-4 py-3">
                <span className="font-mono text-lg tracking-wider">{key}</span>
                <button type="button" className="btn-ghost ml-auto px-3 py-1.5 text-xs" onClick={copy}>{copied ? '복사됨' : '복사'}</button>
              </div>
              <p className="text-sm text-ink-500"><span className="font-mono">{form.email}</span>로도 보냈어요.</p>
              <Link to={`/setup?key=${encodeURIComponent(key)}`} className="btn-primary w-full py-2.5">이 키로 관리자 가입</Link>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
