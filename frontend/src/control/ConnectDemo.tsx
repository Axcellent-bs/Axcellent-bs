// 고객사 연결 시연: 새 고객사를 AXcellent에 연결하는 과정을 단계별로 자동 재생한다. 입력은 받지 않고 보기만 한다.
// 등록 → 설치 키 발급 → 고객사 서버에 설치 → 첫 상태 신호 확인 → 서비스 고르기 → 완료
import { useEffect, useState } from 'react'
import { COMPANIES, LATEST_VERSION } from './data'
import { DEMO_NEW_COMPANY, connectCompany } from '@/demo/company'

const NEW = { name: DEMO_NEW_COMPANY.name, industry: '카페 체인', network: DEMO_NEW_COMPANY.network, contact: '운영 담당 (본사 IT)' }
const KEY = 'AXC-7Q2M-91KD-4F8A'

const STEPS = [
  { title: '고객사 등록', ms: 4200 },
  { title: '설치 키 발급', ms: 2800 },
  { title: '고객사 서버에 설치', ms: 6200 },
  { title: '첫 상태 신호 확인', ms: 4000 },
  { title: '서비스 고르기', ms: 4200 },
  { title: '연결 완료', ms: 0 },
] as const

const INSTALL_LOG = [
  '고객사 담당자가 설치 파일 실행',
  `사내망 ${NEW.network} 확인`,
  '외부 접근 차단 규칙 적용',
  'DejaFix 설치 (PC·서버 연결, 장애 대응)',
  'AXcellent 서비스 준비',
  '설치 키 확인 완료',
]
const PICKED = ['고객 문의', '리뷰·지식', '공지·쿠폰', '재무 관리']

/** 글자를 하나씩 보여 준다 (타이핑 효과) */
const typed = (text: string, t: number, start: number, speed = 70) => text.slice(0, Math.max(0, Math.floor((t - start) / speed)))

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-ink-500">{label}</span>
      <span className={`flex h-10 items-center border border-ink-100 bg-surface px-3 ${mono ? 'font-mono text-xs' : ''}`}>
        {value}<span className="ml-px inline-block h-4 w-px animate-pulse bg-ink-900" aria-hidden="true" />
      </span>
    </label>
  )
}

function StepBody({ step, t }: { step: number; t: number }) {
  if (step === 0) {
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="회사 이름" value={typed(NEW.name, t, 0, 140)} />
        <Field label="업종" value={typed(NEW.industry, t, 700)} />
        <Field label="사내망 주소" value={typed(NEW.network, t, 1500, 55)} mono />
        <Field label="연락 담당" value={typed(NEW.contact, t, 2500, 55)} />
      </div>
    )
  }
  if (step === 1) {
    const shown = t > 900
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3 border border-dashed border-ink-300 bg-app px-5 py-4">
          <span className="text-sm text-ink-500">설치 키</span>
          <span className="font-mono text-lg tracking-wider">{shown ? KEY : '발급 중…'}</span>
          {shown && <span className="chip bg-emerald-50 text-emerald-700">발급됨</span>}
        </div>
        <ul className="space-y-1 text-sm text-ink-700">
          <li>· 한 번만 쓰는 키예요. 24시간 뒤에 만료돼요.</li>
          <li>· 고객사 담당자에게 설치 파일과 함께 전달해요.</li>
          <li>· 키는 연결 확인에만 쓰이고, 고객사 데이터를 여는 데는 쓰이지 않아요.</li>
        </ul>
      </div>
    )
  }
  if (step === 2) {
    const per = STEPS[2].ms / (INSTALL_LOG.length + 0.5)
    const done = Math.min(INSTALL_LOG.length, Math.floor(t / per))
    return (
      <div className="space-y-4">
        <div className="h-2 bg-ink-100" aria-hidden="true">
          <div className="h-2 bg-brand-600 transition-[width] duration-300" style={{ width: `${(done / INSTALL_LOG.length) * 100}%` }} />
        </div>
        <ol className="space-y-2 bg-[#1d1d3a] p-4 font-mono text-xs text-[#e4e4f4]">
          {INSTALL_LOG.map((line, i) => (
            <li key={line} className={i < done ? '' : i === done ? 'text-white/50' : 'invisible'}>
              {i < done ? '✓' : '…'} {line}
            </li>
          ))}
        </ol>
        <p className="text-xs text-ink-500">설치는 고객사 서버에서 고객사 담당자가 해요. 우리는 원격으로 들어가지 않아요.</p>
      </div>
    )
  }
  if (step === 3) {
    const got = t > 2200
    return (
      <div className="space-y-4">
        <div className={`flex items-center gap-3 px-5 py-4 ${got ? 'bg-emerald-50 text-emerald-800' : 'bg-app text-ink-700'}`}>
          <span className={`h-3 w-3 ${got ? 'bg-emerald-700' : 'animate-pulse bg-ink-500'}`} aria-hidden="true" />
          {got ? <span className="font-medium">첫 상태 신호를 받았어요 · 버전 {LATEST_VERSION} · PC 1대 · 정상</span> : <span>첫 상태 신호를 기다리는 중…</span>}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="border border-ink-100 p-4">
            <p className="text-sm font-semibold text-emerald-700">받는 것</p>
            <p className="mt-1 text-sm text-ink-700">살아 있는지, 버전, 오류 수, 사용량, 비용</p>
          </div>
          <div className="border border-ink-100 p-4">
            <p className="text-sm font-semibold text-red-700">받지 않는 것</p>
            <p className="mt-1 text-sm text-ink-700">주문, 고객 문의, 인사 기록 같은 업무 데이터</p>
          </div>
        </div>
      </div>
    )
  }
  if (step === 4) {
    const on = Math.min(PICKED.length, Math.floor(t / 800))
    return (
      <div className="space-y-3">
        <p className="text-sm text-ink-500">고객사가 쓸 서비스만 켜요. 나중에 고객사 시스템 관리자가 서비스 고르기에서 바꿀 수 있어요.</p>
        <ul className="grid gap-2 sm:grid-cols-2">
          {PICKED.map((name, i) => (
            <li key={name} className={`flex items-center justify-between border px-4 py-3 text-sm transition ${i < on ? 'border-brand-600 bg-brand-50' : 'border-ink-100 bg-surface text-ink-500'}`}>
              <span className="font-medium">{name}</span>
              <span className={`chip ${i < on ? 'bg-brand-600 text-white' : 'bg-ink-100 text-ink-500'}`}>{i < on ? '사용 중' : '꺼짐'}</span>
            </li>
          ))}
        </ul>
      </div>
    )
  }
  return (
    <div className="space-y-4">
      <div className="bg-emerald-50 px-5 py-4 text-emerald-800">
        <p className="font-semibold">{NEW.name}를 연결했어요</p>
        <p className="mt-1 text-sm">버전 {LATEST_VERSION} · 서비스 {PICKED.length}개 · 사내망 {NEW.network}</p>
        <p className="mt-1 text-sm">이제 고객사 화면 상단에 "AXcellent with {NEW.name}"가 나와요.</p>
      </div>
      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr><th className="px-4 py-2 font-medium">고객사</th><th className="px-3 py-2 font-medium">상태</th><th className="px-3 py-2 font-medium">마지막 신호</th></tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            <tr className="bg-brand-50">
              <td className="px-4 py-2 font-medium">{NEW.name} <span className="chip ml-1 bg-brand-600 text-white">새로 연결</span></td>
              <td className="px-3 py-2 text-emerald-700">정상</td>
              <td className="px-3 py-2 text-ink-700">방금</td>
            </tr>
            {COMPANIES.slice(0, 3).map((c) => (
              <tr key={c.id}><td className="px-4 py-2">{c.name}</td><td className="px-3 py-2 text-ink-700">{c.health === 'ok' ? '정상' : c.health === 'warn' ? '경고' : '연결 끊김'}</td><td className="px-3 py-2 text-ink-500">{c.lastSeenMin}분 전</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function ConnectDemo() {
  const [step, setStep] = useState(0)
  const [t, setT] = useState(0)
  const [playing, setPlaying] = useState(true)

  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => {
      setT((prev) => {
        const next = prev + 100
        const dur = STEPS[step].ms
        if (dur && next >= dur) {
          if (step < STEPS.length - 1) setStep(step + 1)
          return 0
        }
        return next
      })
    }, 100)
    return () => clearInterval(id)
  }, [playing, step])

  const last = step === STEPS.length - 1
  // 연결이 끝나면 고객사 화면 상단이 "AXcellent with 새벽커피"로 바뀐다
  useEffect(() => {
    if (last) connectCompany(DEMO_NEW_COMPANY.id)
  }, [last])
  const go = (i: number) => { setStep(i); setT(0) }
  const restart = () => { go(0); setPlaying(true) }

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-8 pb-12">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-ink-500">관제 센터 · 시연</p>
          <h1 className="text-2xl font-semibold">고객사 연결</h1>
          <p className="mt-1 text-sm text-ink-500">새 고객사를 AXcellent에 연결하는 과정이에요. 자동으로 넘어가요.</p>
        </div>
        <div className="flex gap-2">
          {!last && (
            <button type="button" className="btn-ghost" onClick={() => setPlaying((p) => !p)}>{playing ? '일시정지' : '이어서 재생'}</button>
          )}
          <button type="button" className="btn-primary" onClick={restart}>처음부터</button>
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
        <ol className="card divide-y divide-ink-100 self-start" aria-label="연결 단계">
          {STEPS.map((s, i) => {
            const state = i < step ? 'done' : i === step ? 'now' : 'todo'
            return (
              <li key={s.title}>
                <button type="button" onClick={() => go(i)} aria-current={state === 'now' ? 'step' : undefined}
                  className={`flex w-full items-center gap-3 px-4 py-3 text-left text-sm ${state === 'now' ? 'bg-brand-50 font-semibold text-brand-700' : state === 'done' ? 'text-ink-700' : 'text-ink-500'}`}>
                  <span className={`flex h-6 w-6 shrink-0 items-center justify-center text-xs ${state === 'done' ? 'bg-emerald-700 text-white' : state === 'now' ? 'bg-brand-600 text-white' : 'bg-ink-100 text-ink-500'}`}>
                    {state === 'done' ? '✓' : i + 1}
                  </span>
                  {s.title}
                </button>
              </li>
            )
          })}
        </ol>

        <section className="card space-y-5 p-6" aria-live="polite">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">{step + 1}. {STEPS[step].title}</h2>
            {!last && (
              <div className="h-1 w-32 bg-ink-100" aria-hidden="true">
                <div className="h-1 bg-brand-600" style={{ width: `${Math.min(100, (t / STEPS[step].ms) * 100)}%` }} />
              </div>
            )}
          </div>
          <StepBody step={step} t={t} />
        </section>
      </div>
    </div>
  )
}
