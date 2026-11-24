import { useState, type FormEvent, type ReactNode } from 'react'
import { DangerRow, Facts, PageHead, SectionHead, StatusChip, Tabs, TONE_DOT, type Tone } from '@/components/connect/parts'
import CompanyAiTools from '@/components/connect/CompanyAiTools'
import BrandPromoSettings from '@/components/connect/BrandPromoSettings'
import type { ConnectionState } from '@/lib/companyConnection'
import { useHosts } from './store'
import { SERVICES } from './services'
import { checkKey, connectWithKey, disconnect, moveCompany, updateCompanyInfo, useCompany, type CompanyInfo, type IssuedKey } from './company'
import { sessionFromLogin, setSession } from '@/lib/humanSession'
import SiteConnect from './SiteConnect'
import { useSession } from './session'

// 회사 연결 (고객사 시스템 관리자). 맨 위: 연결 지도(이 서버 → 회사 → 판매 서버 → 사내 PC 상태 한 줄),
// 아래: AXcellent 연결, 사이트 연결(플랫폼·직접 만든 사이트, 회사가 연결된 뒤에만).
// AXcellent 연결은 키를 넣어 확인하고 → 회사 정보를 직접 입력하면 → 이 워크스페이스가 그 회사로 연결된다.
// 키 발급은 AXcellent 운영팀이 하고, 연결·끊기는 여기서 고객사가 한다. 회사 전용 키(발급 때 회사를 등록한 키)는
// 그 회사에서만 연결된다. 지금 회사 연결을 끊은 관리자가 다른 회사 전용 키를 넣으면 "회사 옮기기"로 간다:
// 나와 내가 연결한 일반회원이 그 회사로 옮기고, 지금 회사 기록은 그대로 남는다(로드맵 7단계 B안).
// 회사는 계정으로 정한다. 사내망·PC IP는 쓰지 않아 화면에 묻지 않는다(노예진 11/24).
/** 사내망은 쓰지 않는다(회사는 계정으로 정한다). 서버 칸은 빈 값으로 둔다 */
const NO_NETWORK = ''

// 탭: 사이트·API 연결(예전 이 화면 전체) / 공통 AI 도구 연결(회사 AI 모델·키·예산, 회사 AI 연결 설계)
// 11/24 브랜드: 홍보 영상 방향(재미 먼저·제품 소개)·브랜드 노출 (BrandPromoSettings)
const TABS = [{ id: 'site', label: '사이트·API 연결' }, { id: 'ai', label: '공통 AI 도구 연결' }, { id: 'brand', label: '브랜드' }] as const
type TabId = (typeof TABS)[number]['id']
const readTab = (): TabId => {
  const t = typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('tab') : null
  return t === 'ai' || t === 'brand' ? t : 'site'
}

type Phase = { at: 'key' } | { at: 'move'; key: IssuedKey } | { at: 'info'; key: IssuedKey }

const when = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString('ko-KR', { year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'

export default function CompanyConnect() {
  const company = useCompany()
  const { account } = useSession()
  const [tab, setTab] = useState<TabId>(readTab)
  const [phase, setPhase] = useState<Phase>({ at: 'key' })
  const [input, setInput] = useState('')
  const [info, setInfo] = useState<CompanyInfo>({ name: '', industry: '', network: NO_NETWORK })
  const [error, setError] = useState<string | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [confirmOff, setConfirmOff] = useState(false)
  const [editing, setEditing] = useState(false)
  const [showKey, setShowKey] = useState(false)
  const [hosts] = useHosts()
  /** 판매 서버 연결 상태 (아래 판매 서버 구역이 받아 온 값을 연결 지도도 같이 쓴다) */
  const [shop, setShop] = useState<ConnectionState | 'error' | null>(null)

  const submitKey = async (e: FormEvent) => {
    e.preventDefault()
    let r: Awaited<ReturnType<typeof checkKey>>
    try { r = await checkKey(input) } catch (x) { return setError((x as Error).message) }
    if (!r.ok) {
      // 다른 회사 전용 키: 지금 회사 연결이 없으면 회사 옮기기로, 있으면 먼저 끊어야 한다고 알린다
      const other = await checkKey(input, true).catch(() => null)
      if (other?.ok && other.issued.forCompany && other.issued.signup) {
        if (company) return setError(`${other.issued.company?.name ?? '다른 회사'} 전용 키예요. 그 회사로 옮기려면 지금 회사 연결을 먼저 끊어 주세요. 지금 회사 기록은 그대로 남아요.`)
        setError(null)
        return setPhase({ at: 'move', key: other.issued })
      }
      return setError(r.reason)
    }
    setError(null)
    // 같은 키로 다시 연결하면 지난번에 입력한 회사 정보를 미리 채운다
    const prev = r.issued.company
    setInfo(prev ? { name: prev.name, industry: prev.industry, network: prev.network, siteUrl: prev.siteUrl } : { name: '', industry: '', network: NO_NETWORK, siteUrl: '' })
    setPhase({ at: 'info', key: r.issued })
  }
  const [moving, setMoving] = useState(false)
  const confirmMove = async () => {
    if (phase.at !== 'move') return
    setMoving(true)
    try {
      const r = await moveCompany(phase.key.key)
      const session = sessionFromLogin(r.login)
      if (!session) throw new Error('옮겼는데 로그인 정보를 받지 못했어요. 다시 로그인해 주세요.')
      setSession(session) // 새 회사 세션으로 바꾸면 회사·키 목록을 다시 읽는다
      const c = phase.key.company
      setInfo({ name: c?.name ?? '', industry: c?.industry ?? '', network: NO_NETWORK, siteUrl: c?.siteUrl ?? '' })
      setError(null)
      setPhase({ at: 'info', key: phase.key })
    } catch (x) {
      setError((x as Error).message)
    } finally {
      setMoving(false)
    }
  }

  const submitInfo = (e: FormEvent) => {
    e.preventDefault()
    if (phase.at !== 'info') return
    if (!info.name.trim()) return setError('회사 이름을 입력하세요.')
    if (info.siteUrl?.trim() && !/^https?:\/\/\S+$/.test(info.siteUrl.trim())) return setError('사이트 주소는 http:// 또는 https://로 시작해야 해요.')
    setError(null)
    // 서버에 바로 연결한다(꾸민 진행 단계 없음). 실패하면 이 단계에 머물러 오류를 보여 준다
    const key = phase.key
    setConnecting(true)
    void connectWithKey(key, { ...info, name: info.name.trim(), network: NO_NETWORK, siteUrl: info.siteUrl?.trim() })
      .then(() => { setPhase({ at: 'key' }); setInput(''); setShowKey(false) })
      .catch((x: Error) => setError(x.message))
      .finally(() => setConnecting(false))
  }

  const saveInfo = (e: FormEvent) => {
    e.preventDefault()
    if (!info.name.trim()) return setError('회사 이름을 입력하세요.')
    if (info.siteUrl?.trim() && !/^https?:\/\/\S+$/.test(info.siteUrl.trim())) return setError('사이트 주소는 http:// 또는 https://로 시작해야 해요.')
    setError(null)
    void updateCompanyInfo({ ...info, name: info.name.trim(), network: info.network.trim() }).catch((x: Error) => setError(x.message))
    setEditing(false)
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
        이 워크스페이스를 회사와 잇고, 부서 AI가 읽을 회사 사이트 서버를 정해요. 업무 데이터는 회사 밖으로 나가지 않아요.
      </PageHead>

      <Tabs label="회사 연결" items={TABS} value={tab} onPick={setTab} />

      {tab === 'brand' ? <BrandPromoSettings /> : tab === 'ai' ? <CompanyAiTools me={account?.name ?? ''} /> : <>
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
                  <p className="mt-1 text-sm text-ink-500">{when(company.connectedAt)}에 연결</p>
                </div>
                {!editing && (
                  <button type="button" className="btn-ghost"
                    onClick={() => { setInfo({ name: company.name, industry: company.industry, network: company.network, siteUrl: company.siteUrl ?? '' }); setEditing(true); setError(null) }}>
                    회사 정보 고치기
                  </button>
                )}
              </div>

              {editing ? (
                <form onSubmit={saveInfo} className="space-y-3 border-t border-ink-100 pt-5">
                  <div className="grid gap-3 md:grid-cols-2">
                    <label className="block space-y-1 text-sm">
                      <span className="text-ink-700">회사 이름</span>
                      <input className="field" value={info.name} onChange={(e) => setInfo({ ...info, name: e.target.value })} />
                    </label>
                  </div>
                  <label className="block space-y-1 text-sm">
                    <span className="text-ink-700">사이트 주소 <span className="text-xs text-ink-500">(선택 · 머리글에 사이트 아이콘과 링크로 보여요)</span></span>
                    <input className="field font-mono" value={info.siteUrl ?? ''} placeholder="https://www.example.com" onChange={(e) => setInfo({ ...info, siteUrl: e.target.value })} />
                  </label>
                  {error && <p className="text-sm text-red-700">{error}</p>}
                  <div className="flex gap-2">
                    <button className="btn-primary px-5">저장</button>
                    <button type="button" className="btn-quiet" onClick={() => { setEditing(false); setError(null) }}>취소</button>
                  </div>
                </form>
              ) : (
                <div className="grid gap-6 border-t border-ink-100 pt-5 lg:grid-cols-[3fr_2fr]">
                  <Facts cols={2} items={[
                    ['연결 키', <span className="font-mono">{company.key ? `${company.key.slice(0, 8)}••••-••••` : '—'}</span>],
                    ['사용 중인 서비스', `${SERVICES.length}개`],
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
                const idx = phase.at === 'key' || phase.at === 'move' ? 0 : connecting ? 2 : 1
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
                <SectionHead as="h3" title="연결 키 넣기" desc="AXcellent에서 발급받은 회사 전용 키를 넣어요. 다른 회사 전용 키를 넣으면 그 회사로 옮기는 단계로 가요." />
                {keyForm}
                {error && <p className="text-sm text-red-700">{error}</p>}
              </div>
            )}

            {phase.at === 'move' && (
              <div className="space-y-4 p-6">
                <SectionHead as="h3" title={`${phase.key.company?.name ?? '새 회사'}로 옮기기`}
                  desc="이 키는 다른 회사 전용이에요. 옮기면 나와 내가 연결한 일반회원이 그 회사로 가요. 지금 회사의 기록은 그대로 남고, 다시 연결하면 돌아와요." />
                <p className="text-sm text-ink-700">다른 관리자와 그 관리자가 연결한 회원은 지금 회사에 남아요. 옮긴 회원은 다시 로그인하면 새 회사로 들어가요.</p>
                {error && <p className="text-sm text-red-700">{error}</p>}
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn-primary px-5" disabled={moving} onClick={() => void confirmMove()}>{moving ? '옮기는 중…' : '이 회사로 옮기기'}</button>
                  <button type="button" className="btn-quiet" onClick={() => { setPhase({ at: 'key' }); setError(null) }}>취소</button>
                </div>
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
                </div>
                <label className="block space-y-1 text-sm">
                  <span className="text-ink-700">사이트 주소 <span className="text-xs text-ink-500">(선택 · 머리글에 사이트 아이콘과 링크로 보여요)</span></span>
                  <input className="field font-mono" value={info.siteUrl ?? ''} placeholder="https://www.example.com" onChange={(e) => setInfo({ ...info, siteUrl: e.target.value })} />
                </label>
                {error && <p className="text-sm text-red-700">{error}</p>}
                <div className="flex flex-wrap items-center gap-2">
                  <button className="btn-primary px-5" disabled={connecting}>{connecting ? '연결하는 중…' : '연결'}</button>
                  <button type="button" className="btn-quiet" onClick={() => { setPhase({ at: 'key' }); setError(null) }}>키 다시 넣기</button>
                </div>
              </form>
            )}

          </div>
        )}
      </section>

      {connected && <SiteConnect onShop={setShop} />}
      </>}
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
    ? { name: '사이트 서버', value: '회사 연결 뒤', tone: 'off', note: '회사를 먼저 연결해요' }
    : shop === 'error' ? { name: '사이트 서버', value: '읽지 못함', tone: 'bad', note: '연결 상태를 받지 못했어요' }
    : !shop ? { name: '사이트 서버', value: '확인 중…', tone: 'off', note: '' }
    : shop.mode === 'connected'
      ? { name: '사이트 서버', value: '회사 서버', tone: 'ok', note: hostOf(shop.shop_api_url) }
      : { name: '사이트 서버', value: '내부 데이터', tone: 'wait', note: '회사 서버 대신 기본 사이트 서버를 읽어요' }
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
