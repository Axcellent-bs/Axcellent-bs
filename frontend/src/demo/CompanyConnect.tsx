import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { DangerRow, Facts, PageHead, SectionHead, StatusChip, TONE_DOT, type Tone } from '@/components/connect/parts'
import type { ConnectionState } from '@/lib/companyConnection'
import { privateIp, useHosts } from './store'
import { useEnabledServices } from './services'
import { checkKey, connectWithKey, disconnect, updateCompanyInfo, useCompany, useKeys, type CompanyInfo, type IssuedKey } from './company'
import Integrations from './Integrations'

// 회사 연결 (고객사 시스템 관리자). 맨 위: 연결 지도(이 서버 → 회사 → 판매 서버 → 사내 PC 상태 한 줄),
// 아래: AXcellent 연결, 회사 판매 서버 연결(회사가 연결된 뒤에만).
// AXcellent 연결은 키를 넣어 확인하고 → 회사 정보를 직접 입력하면 → 이 워크스페이스가 그 회사로 연결된다.
// 키에는 회사 정보가 없다(연결 권한만). 키 발급은 AXcellent 운영팀이 하고, 연결·끊기는 여기서 고객사가 한다.
const STEPS = ['회사 정보 저장', '사내망 확인', '첫 상태 신호 보내기', '연결 완료'] as const
const STEP_MS = 1100
const INDUSTRIES = ['온라인 쇼핑몰', '오프라인 유통', '여행사', '카페 체인', '병원', '제조', '기타']
/** 이 서버가 있는 사내망 (설치할 때 찾은 값이라 가정) */
const DETECTED_NETWORK = '192.168.30.0/24'
const DEMO_INFO: CompanyInfo = { name: '새벽커피', industry: '카페 체인', network: DETECTED_NETWORK }

type Phase = { at: 'key' } | { at: 'info'; key: IssuedKey } | { at: 'run'; key: IssuedKey; info: CompanyInfo }

const validNetwork = (v: string) => /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/.test(v.trim()) && privateIp(v.trim().split('/')[0])
const when = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString('ko-KR', { year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'

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
  /** 판매 서버 연결 상태 (아래 판매 서버 구역이 받아 온 값을 연결 지도도 같이 쓴다) */
  const [shop, setShop] = useState<ConnectionState | 'error' | null>(null)

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

  const online = hosts.filter((h) => h.status === 'online').length
  const connected = !!company && phase.at === 'key'
  const keyForm = (
    <form onSubmit={submitKey} className="flex flex-wrap gap-2">
      <input className="field min-w-[260px] flex-1 font-mono tracking-wider" placeholder="AXC-XXXX-XXXX-XXXX" value={input}
        onChange={(e) => setInput(e.target.value)} aria-label="연결 키" />
      <button className="btn-primary px-5">키 확인</button>
    </form>
  )

  return (
    <div className="space-y-10 px-4 py-8 sm:px-8">
      <PageHead where="시스템 관리" title="회사 연결">
        이 워크스페이스를 회사와 잇고, 부서 AI가 읽을 회사 판매 서버를 정해요. 업무 데이터는 회사 밖으로 나가지 않아요.
      </PageHead>

      <ConnectionMap company={company?.name ?? null} shop={company ? shop : null} pcs={{ online, total: hosts.length }} />

      <section className="space-y-4">
        <SectionHead title="AXcellent 연결" desc="운영팀이 발급한 연결 키로 이 워크스페이스를 회사와 이어요."
          right={connected ? <StatusChip tone="ok">연결됨</StatusChip> : <StatusChip tone="off">연결 전</StatusChip>} />

        {connected && company ? (
          <div className="card">
            <div className="space-y-6 p-6">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-xs text-ink-500">연결된 회사</p>
                  <p className="mt-0.5 text-3xl font-semibold tracking-tight">{company.name}</p>
                  <p className="mt-1 text-sm text-ink-500">{company.industry} · {when(company.connectedAt)}에 연결</p>
                </div>
                {!editing && (
                  <button type="button" className="btn-ghost"
                    onClick={() => { setInfo({ name: company.name, industry: company.industry, network: company.network }); setEditing(true); setError(null) }}>
                    회사 정보 고치기
                  </button>
                )}
              </div>

              {editing ? (
                <form onSubmit={saveInfo} className="space-y-3 border-t border-ink-100 pt-5">
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
                <div className="grid gap-6 border-t border-ink-100 pt-5 lg:grid-cols-[3fr_2fr]">
                  <Facts cols={2} items={[
                    ['사내망 주소', <span className="font-mono">{company.network}</span>],
                    ['연결 키', <span className="font-mono">{company.key ? `${company.key.slice(0, 8)}••••-••••` : '—'}</span>],
                    ['사용 중인 서비스', `${enabled.length}개`],
                    ['연결된 사내 PC', `${online} / ${hosts.length}대`],
                  ]} />
                  <div className="border-l-2 border-ink-100 pl-4 text-sm leading-6 text-ink-700">
                    <p className="text-xs text-ink-500">AXcellent로 보내는 것</p>
                    <p className="mt-1">회사 이름과 상태 신호(살아 있는지·버전·오류 수·사용량)만 보내요. 업무 데이터는 보내지 않아요.</p>
                  </div>
                </div>
              )}

              {showKey && (
                <div className="space-y-3 border-t border-ink-100 pt-5">
                  <SectionHead as="h3" title="다른 키로 다시 연결" desc="새 키를 확인하면 회사 정보를 다시 입력하는 단계로 가요." />
                  {keyForm}
                  {error && !editing && <p className="text-sm text-red-700">{error}</p>}
                </div>
              )}
            </div>
            <DangerRow note={confirmOff ? '연결을 끊으면 상단의 회사 이름이 빠지고, 직원들은 로그인할 수 없어요.' : '키를 바꾸거나 연결을 끊는 일은 여기서 해요.'}>
              {confirmOff ? (
                <>
                  <button type="button" className="btn-quiet" onClick={() => setConfirmOff(false)}>취소</button>
                  <button type="button" className="btn-primary bg-red-700 hover:bg-red-800" onClick={() => { disconnect(); setConfirmOff(false); setEditing(false) }}>연결 끊기</button>
                </>
              ) : (
                <>
                  <button type="button" className="btn-quiet" onClick={() => { setShowKey((v) => !v); setError(null) }}>{showKey ? '키 입력 닫기' : '다른 키로 다시 연결'}</button>
                  <button type="button" className="btn-ghost text-red-700" onClick={() => setConfirmOff(true)}>연결 끊기</button>
                </>
              )}
            </DangerRow>
          </div>
        ) : (
          <div className="card">
            <ol className="flex border-b border-ink-100 text-sm" aria-label="연결 순서">
              {['키 넣기', '회사 정보 입력', '연결'].map((label, i) => {
                const idx = phase.at === 'key' ? 0 : phase.at === 'info' ? 1 : 2
                const state = i === idx ? 'now' : i < idx ? 'done' : 'next'
                return (
                  <li key={label} aria-current={state === 'now' ? 'step' : undefined}
                    className={`flex flex-1 items-center gap-2 border-b-2 px-4 py-3 ${state === 'now' ? 'border-brand-600 font-semibold text-ink-900' : state === 'done' ? 'border-emerald-700 text-ink-700' : 'border-transparent text-ink-500'}`}>
                    <span className={`grid h-5 w-5 place-items-center text-[11px] ${state === 'done' ? 'bg-emerald-700 text-white' : state === 'now' ? 'bg-brand-600 text-white' : 'bg-ink-100 text-ink-500'}`}>
                      {state === 'done' ? '✓' : i + 1}
                    </span>
                    {label}
                  </li>
                )
              })}
            </ol>

            {phase.at === 'key' && (
              <div className="space-y-4 p-6">
                <SectionHead as="h3" title="연결 키 넣기" desc="키는 AXcellent 운영자가 발급해요. 키에는 회사 정보가 없고, 다음 단계에서 직접 입력해요." />
                {keyForm}
                {error && <p className="text-sm text-red-700">{error}</p>}
                {demoKey && <button type="button" className="btn-quiet px-0 text-xs" onClick={fillDemoKey}>데모: 발급된 키 넣기</button>}
              </div>
            )}

            {phase.at === 'info' && (
              <form onSubmit={submitInfo} className="space-y-4 p-6">
                <SectionHead as="h3" title="회사 정보 입력" desc={'회사 이름은 직원 화면 상단 "AXcellent with ○○"에 나와요.'}
                  right={<StatusChip tone="ok">키 확인됨 <span className="font-mono">{phase.key.key.slice(0, 8)}…</span></StatusChip>} />
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
                  <button type="button" className="btn-quiet ml-auto px-0 text-xs" onClick={() => setInfo(DEMO_INFO)}>데모: 회사 정보 채우기</button>
                </div>
              </form>
            )}

            {phase.at === 'run' && (
              <div className="space-y-4 p-6" aria-live="polite">
                <SectionHead as="h3" title={`${phase.info.name} 연결하는 중`} />
                <ol className="space-y-1">
                  {STEPS.map((s, i) => {
                    const done = i < step || (i === step && step === STEPS.length - 1)
                    const now = i === step && !done
                    return (
                      <li key={s} className={`flex items-center gap-3 px-4 py-2.5 text-sm ${now ? 'bg-brand-50 font-semibold text-brand-700' : done ? 'text-ink-900' : 'text-ink-500'}`}>
                        <span className={`grid h-6 w-6 place-items-center text-xs ${done ? 'bg-emerald-700 text-white' : now ? 'animate-pulse bg-brand-600 text-white motion-reduce:animate-none' : 'bg-ink-100 text-ink-500'}`}>{done ? '✓' : i + 1}</span>
                        {s}
                        {done && i === 0 && <span className="ml-auto text-xs font-normal text-ink-500">{phase.info.name} · {phase.info.industry}</span>}
                        {done && i === 1 && <span className="ml-auto font-mono text-xs font-normal text-ink-500">{phase.info.network}</span>}
                        {done && i === 2 && <span className="ml-auto text-xs font-normal text-ink-500">상태 신호만 보내요</span>}
                      </li>
                    )
                  })}
                </ol>
              </div>
            )}
          </div>
        )}
      </section>

      {connected && <Integrations onState={setShop} />}
    </div>
  )
}

type MapNode = { name: string; value: ReactNode; tone: Tone; note: string }

const hostOf = (url: string) => {
  try { return new URL(url).host } catch { return url }
}

/** 연결 지도: 이 서버에서 회사·판매 서버·사내 PC까지 어디까지 이어졌는지 한 줄로. 끊긴 곳이 바로 보이게 한다 */
function ConnectionMap({ company, shop, pcs }: { company: string | null; shop: ConnectionState | 'error' | null; pcs: { online: number; total: number } }) {
  const shopNode: MapNode = !company
    ? { name: '판매 서버', value: '회사 연결 뒤', tone: 'off', note: '회사를 먼저 연결해요' }
    : shop === 'error' ? { name: '판매 서버', value: '읽지 못함', tone: 'bad', note: '연결 상태를 받지 못했어요' }
    : !shop ? { name: '판매 서버', value: '확인 중…', tone: 'off', note: '' }
    : shop.mode === 'connected'
      ? { name: '판매 서버', value: '회사 서버', tone: 'ok', note: hostOf(shop.shop_api_url) }
      : { name: '판매 서버', value: '내부 데이터', tone: 'wait', note: '회사 서버 대신 기본 판매 서버를 읽어요' }
  const nodes: MapNode[] = [
    { name: 'AXcellent', value: '상태 신호만', tone: company ? 'ok' : 'off', note: '업무 데이터는 보내지 않아요' },
    { name: '우리 회사', value: company ?? '연결 전', tone: company ? 'ok' : 'off', note: company ? '연결 키로 연결됨' : '아래에서 키를 넣어요' },
    shopNode,
    {
      name: '사내 PC', value: `${pcs.online} / ${pcs.total}대`,
      tone: pcs.total === 0 ? 'off' : pcs.online === pcs.total ? 'ok' : 'wait',
      note: pcs.total === 0 ? '등록한 PC가 없어요' : pcs.online === pcs.total ? '모두 연결됨' : `${pcs.total - pcs.online}대 끊김`,
    },
  ]
  return (
    <ol className="grid gap-px border border-ink-100 bg-ink-100 sm:grid-cols-2 lg:grid-cols-4" aria-label="연결 지도">
      {nodes.map((n, i) => {
        const linked = i < nodes.length - 1 && n.tone === 'ok' && nodes[i + 1].tone === 'ok'
        return (
          <li key={n.name} className="relative bg-surface px-5 py-4">
            <p className="flex items-center gap-2 text-xs text-ink-500">
              <span aria-hidden="true" className={`h-2 w-2 ${TONE_DOT[n.tone]}`} />
              {n.name}
            </p>
            <p className="mt-1.5 truncate text-lg font-semibold">{n.value}</p>
            <p className="mt-0.5 truncate text-xs text-ink-500" title={n.note}>{n.note || ' '}</p>
            {i < nodes.length - 1 && (
              <span aria-hidden="true" className={`absolute -right-2 top-1/2 z-10 hidden h-0.5 w-4 lg:block ${linked ? 'bg-emerald-700' : 'bg-ink-300'}`} />
            )}
          </li>
        )
      })}
    </ol>
  )
}
