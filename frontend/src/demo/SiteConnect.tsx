import { useState, type FormEvent, type ReactNode } from 'react'
import { Notice, SectionHead, StatusChip } from '@/components/connect/parts'
import { PreparingChip } from '@/components/Preparing'
import type { ConnectionState } from '@/lib/companyConnection'
import Integrations from './Integrations'

// 사이트 연결: 고객사 사이트가 어떻게 만들어졌는지 고르면(① 직접 만든 사이트 ② 사이트 제작 플랫폼) 그 경우에 맞는 연결 창구를 보여 준다.
// 고객사는 고르고·누르고·붙여 넣기만 한다. 사이트 코드나 서버는 건드리지 않는다.
// 'AXcellent 규격 API'만 실제 연결(Integrations, shop-api)이고, 나머지는 이 화면 안 데모 상태라 "준비 중" 표시를 붙인다.
// 붙여 넣은 키·토큰·비밀번호는 다시 보여 주지 않는다(키·토큰은 앞 몇 글자만).

const OPS = [
  { id: 'gha', name: 'GitHub Actions', group: '배포', use: '문제 생기면 이전 버전으로 되돌리기' },
  { id: 'jenkins', name: 'Jenkins', group: '배포', use: '문제 생기면 이전 버전으로 되돌리기' },
  { id: 'argo', name: 'Argo CD', group: '배포', use: '문제 생기면 이전 버전으로 되돌리기' },
  { id: 'zabbix', name: 'Zabbix', group: '감시', use: '장애 신호 받기' },
  { id: 'grafana', name: 'Grafana', group: '감시', use: '장애 신호 받기' },
  { id: 'cw', name: 'CloudWatch', group: '감시', use: '장애 신호 받기' },
] as const
type Read = 'axcellent' | 'api' | 'db'

/** 붙여 넣은 값은 앞 몇 글자만 */
const head = (v: string) => `${v.trim().slice(0, 4)}••••`
const hostOnly = (v: string) => v.trim().replace(/^https?:\/\//, '').replace(/\/.*$/, '')
const Radio = ({ on, onPick, title, desc }: { on: boolean; onPick: () => void; title: string; desc: ReactNode }) => (
  <button type="button" role="radio" aria-checked={on} onClick={onPick}
    className={`flex flex-col gap-1 border p-4 text-left ${on ? 'border-brand-600 bg-brand-50/50' : 'border-ink-100 hover:border-ink-300'}`}>
    <span className="font-semibold">{title}</span>
    <span className="text-xs leading-5 text-ink-500">{desc}</span>
  </button>
)
const Field = ({ label, children }: { label: string; children: ReactNode }) => (
  <label className="block space-y-1 text-sm"><span className="text-ink-700">{label}</span>{children}</label>
)

export default function SiteConnect({ onShop }: { onShop: (s: ConnectionState | 'error') => void }) {
  const [kind, setKind] = useState<'platform' | 'custom'>('custom')
  return (
    <div className="space-y-6">
      <section className="space-y-4">
        <SectionHead title="사이트 연결" desc="사이트가 어떻게 만들어졌는지 골라 주세요. 사이트 코드나 서버는 건드리지 않고, 여기서 고르고·누르고·붙여 넣기만 해요." />
        <div role="radiogroup" aria-label="사이트 종류" className="grid gap-3 md:grid-cols-2">
          <Radio on={kind === 'custom'} onPick={() => setKind('custom')} title="직접 만든 사이트예요"
            desc="자체 개발한 서비스·예약 사이트 등. 이미 쓰는 API·DB·운영 도구를 연결해요." />
          <Radio on={kind === 'platform'} onPick={() => setKind('platform')} title="사이트 제작 플랫폼으로 만들었어요"
            desc={<>플랫폼 위에 만든 사이트예요. 플랫폼 계정으로 허락하면 한 번에 연결돼요. <PreparingChip /></>} />
        </div>
      </section>
      {kind === 'platform' && <PlatformPanel />}
      <div className={kind === 'custom' ? '' : 'hidden'}><CustomPanel onShop={onShop} /></div>
    </div>
  )
}

// 플랫폼(카페24 등) 연결은 보류: 플랫폼 앱 등록에 사업자 등록이 필요하다 (2026-11-16 결정). 지어낸 연결 상태는 보여 주지 않는다.
function PlatformPanel() {
  return (
    <section className="space-y-4" aria-label="플랫폼 연결">
      <SectionHead as="h3" title="플랫폼 연결" desc="플랫폼 계정으로 허락하면 한 번에 연결되는 방식이에요." right={<PreparingChip />} />
      <div className="card space-y-2 p-6">
        <p className="text-sm text-ink-700">플랫폼 연결은 아직 준비 중이에요. 열리면 이 자리에서 플랫폼을 고르고 허락하기만 하면 돼요.</p>
        <p className="text-sm text-ink-500">그전에는 사이트가 쓰는 API로 연결할 수 있어요. 위에서 "직접 만든 사이트예요"를 골라 주세요.</p>
      </div>
    </section>
  )
}

type Mapped = { from: string; to: string }
const MAPS: Mapped[] = [{ from: '예약', to: '주문' }, { from: '예약 금액', to: '결제 금액' }, { from: '상품(투어)', to: '상품' }, { from: '이용자', to: '구매자' }]

function CustomPanel({ onShop }: { onShop: (s: ConnectionState | 'error') => void }) {
  const [read, setRead] = useState<Read>('axcellent')
  // 다른 형식 API
  const [api, setApi] = useState({ url: '', auth: '키(헤더)', token: '' })
  const [apiOk, setApiOk] = useState<{ host: string; hint: string } | null>(null)
  // 읽기 전용 DB
  const [db, setDb] = useState({ host: '', name: '', user: '', pw: '' })
  const [dbOk, setDbOk] = useState<{ host: string; user: string } | null>(null)
  const [mapOk, setMapOk] = useState(false)
  // 운영 도구
  const [ops, setOps] = useState<Record<string, string>>({})
  const [opIn, setOpIn] = useState<Record<string, string>>({})
  // 업무 처리
  const [biz, setBiz] = useState(false)
  const [admin, setAdmin] = useState('')
  const [adminOk, setAdminOk] = useState(false)

  const readDone = (read === 'api' && apiOk) || (read === 'db' && dbOk)
  const opCount = Object.keys(ops).length
  const connectApi = (e: FormEvent) => {
    e.preventDefault()
    if (!api.url.trim() || !api.token.trim()) return
    setApiOk({ host: hostOnly(api.url), hint: head(api.token) })
    setApi({ ...api, token: '' }); setMapOk(false)
  }
  const connectDb = (e: FormEvent) => {
    e.preventDefault()
    if (!db.host.trim() || !db.user.trim() || !db.pw) return
    setDbOk({ host: hostOnly(db.host), user: db.user.trim() })
    setDb({ ...db, pw: '' }); setMapOk(false)
  }
  const pickRead = (r: Read) => { setRead(r); setMapOk(false) }

  return (
    <section className="space-y-4" aria-label="직접 만든 사이트 연결">
      <SectionHead as="h3" title="직접 만든 사이트 연결" desc="이미 가진 것만 골라 연결해요. 사이트 데이터 읽기만 필수예요." />

      <div className="card">
        <div className="space-y-4 p-6">
          <SectionHead as="h3" title="1. 사이트 데이터 읽기" desc="아래 셋 중 하나를 골라요."
            right={<StatusChip tone="wait">필수</StatusChip>} />
          <div role="radiogroup" aria-label="사이트 데이터 읽는 방법" className="grid gap-3 md:grid-cols-3">
            <Radio on={read === 'axcellent'} onPick={() => pickRead('axcellent')} title="AXcellent 규격 API" desc="AXcellent 규격(shop-api)을 그대로 쓰는 사이트예요. 주소만 확인·저장해요." />
            <Radio on={read === 'api'} onPick={() => pickRead('api')} title="다른 형식의 API" desc="이미 있는 API 주소와 지금 쓰는 인증을 붙여 넣어요." />
            <Radio on={read === 'db'} onPick={() => pickRead('db')} title="읽기 전용 DB 계정" desc="접속 주소와 읽기만 되는 계정을 넣어요." />
          </div>

          {read === 'api' && (
            <form onSubmit={connectApi} className="space-y-3 border-t border-ink-100 pt-4">
              <div className="flex items-center gap-2"><p className="text-sm font-semibold">다른 형식의 API</p><PreparingChip /></div>
              <div className="grid gap-3 md:grid-cols-[2fr_1fr_2fr]">
                <Field label="API 주소"><input className="field font-mono" placeholder="https://api.example.com" value={api.url} onChange={(e) => setApi({ ...api, url: e.target.value })} /></Field>
                <Field label="인증 방식">
                  <select className="field" value={api.auth} onChange={(e) => setApi({ ...api, auth: e.target.value })}>
                    {['키(헤더)', '토큰(Bearer)', '아이디·비밀번호'].map((x) => <option key={x}>{x}</option>)}
                  </select>
                </Field>
                <Field label="키·토큰"><input type="password" autoComplete="off" className="field font-mono" placeholder="지금 쓰는 값을 붙여 넣어요" value={api.token} onChange={(e) => setApi({ ...api, token: e.target.value })} /></Field>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <button className="btn-primary px-5" disabled={!api.url.trim() || !api.token.trim()}>연결 확인</button>
                {apiOk && <StatusChip tone="ok">읽기 확인됨 · <span className="font-mono">{apiOk.host}</span> · 키 <span className="font-mono">{apiOk.hint}</span></StatusChip>}
              </div>
            </form>
          )}

          {read === 'db' && (
            <form onSubmit={connectDb} className="space-y-3 border-t border-ink-100 pt-4">
              <div className="flex items-center gap-2"><p className="text-sm font-semibold">읽기 전용 DB 계정</p><PreparingChip /></div>
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="접속 주소"><input className="field font-mono" placeholder="db.example.com:5432" value={db.host} onChange={(e) => setDb({ ...db, host: e.target.value })} /></Field>
                <Field label="데이터베이스 이름"><input className="field font-mono" value={db.name} onChange={(e) => setDb({ ...db, name: e.target.value })} /></Field>
                <Field label="계정"><input className="field font-mono" autoComplete="off" value={db.user} onChange={(e) => setDb({ ...db, user: e.target.value })} /></Field>
                <Field label="비밀번호"><input type="password" autoComplete="new-password" className="field font-mono" value={db.pw} onChange={(e) => setDb({ ...db, pw: e.target.value })} /></Field>
              </div>
              <p className="text-xs text-ink-500">읽기만 되는 계정이면 돼요. 비밀번호는 저장 뒤 다시 보여 드리지 않아요.</p>
              <div className="flex flex-wrap items-center gap-3">
                <button className="btn-primary px-5" disabled={!db.host.trim() || !db.user.trim() || !db.pw}>연결 확인</button>
                {dbOk && <StatusChip tone="ok">읽기 확인됨 · <span className="font-mono">{dbOk.user}@{dbOk.host}</span></StatusChip>}
              </div>
            </form>
          )}

          {readDone && (
            <div className="space-y-3 border-t border-ink-100 pt-4">
              <p className="text-sm font-semibold">항목 맞추기 <span className="text-xs font-normal text-ink-500">AXcellent가 사이트 항목을 살펴보고 이렇게 짝을 지어 봤어요.</span></p>
              <ul className="grid gap-2 sm:grid-cols-2" aria-label="제안한 항목 짝">
                {MAPS.map((m) => (
                  <li key={m.from} className="flex items-center gap-2 border border-ink-100 px-3 py-2 text-sm">
                    <span>{m.from}</span><span aria-hidden="true" className="text-ink-300">→</span><span className="font-semibold">{m.to}</span>
                  </li>
                ))}
              </ul>
              {mapOk ? <StatusChip tone="ok">짝 확인함</StatusChip> : <button type="button" className="btn-primary px-5" onClick={() => setMapOk(true)}>맞아요, 확인</button>}
            </div>
          )}
        </div>
        {read === 'axcellent' && (
          <div className="border-t border-ink-100 p-4"><Integrations onState={onShop} embedded /></div>
        )}
      </div>

      <div className="card space-y-4 p-6">
        <SectionHead as="h3" title="2. 운영 도구 연결" desc="장애가 났을 때 되돌리기·신호 받기에 필요해요. 각 도구에서 발급한 토큰을 붙여 넣거나, 로그인해서 허락해요."
          right={<><StatusChip tone="wait">장애 대응에 필요</StatusChip><PreparingChip /></>} />
        <ul className="divide-y divide-ink-100 border border-ink-100">
          {OPS.map((o) => (
            <li key={o.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
              <div className="min-w-[150px]"><p className="font-semibold">{o.name}</p><p className="text-xs text-ink-500">{o.group} · {o.use}</p></div>
              {ops[o.id] ? (
                <>
                  <StatusChip tone="ok">연결됨 · <span className="font-mono">{ops[o.id]}</span></StatusChip>
                  <button type="button" className="btn-quiet ml-auto text-red-700" onClick={() => setOps(Object.fromEntries(Object.entries(ops).filter(([k]) => k !== o.id)))}>끊기</button>
                </>
              ) : (
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  <input type="password" autoComplete="off" aria-label={`${o.name} 토큰`} className="field w-52 font-mono" placeholder="토큰 붙여 넣기"
                    value={opIn[o.id] ?? ''} onChange={(e) => setOpIn({ ...opIn, [o.id]: e.target.value })} />
                  <button type="button" className="btn-ghost" disabled={!opIn[o.id]?.trim()}
                    onClick={() => { setOps({ ...ops, [o.id]: head(opIn[o.id]) }); setOpIn({ ...opIn, [o.id]: '' }) }}>연결</button>
                  <button type="button" className="btn-quiet" onClick={() => setOps({ ...ops, [o.id]: '로그인 허락' })}>로그인해서 허락</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div className="card space-y-4 p-6">
        <SectionHead as="h3" title="3. 업무 처리 (쿠폰·환불·판매 중지)" desc="관리자 API가 있으면 켜 주세요. 없으면 AXcellent가 처리 순서를 만들어 담당자에게 보내요."
          right={<><StatusChip tone="off">선택</StatusChip><PreparingChip /></>} />
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={biz} onChange={(e) => { setBiz(e.target.checked); setAdminOk(false) }} />
          관리자 API가 있어요
        </label>
        {biz ? (
          <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (admin.trim()) setAdminOk(true) }}>
            <input aria-label="관리자 API 주소" className="field min-w-[260px] flex-1 font-mono" placeholder="https://admin.example.com/api" value={admin} onChange={(e) => { setAdmin(e.target.value); setAdminOk(false) }} />
            <button className="btn-ghost" disabled={!admin.trim()}>연결 확인</button>
            {adminOk && <StatusChip tone="ok">연결됨 · <span className="font-mono">{hostOnly(admin)}</span></StatusChip>}
          </form>
        ) : <Notice tone="info">관리자 API가 없어도 괜찮아요. 쿠폰·환불·판매 중지는 담당자에게 처리 순서를 보내 사람이 처리해요.</Notice>}
      </div>

      <div className="flex flex-wrap items-center gap-3 border border-ink-100 bg-ink-50/60 px-5 py-3 text-sm">
        <span className="font-semibold">연결 상태</span>
        <span>읽기 {read === 'axcellent' ? '(AXcellent 규격 API 아래 상태 참고)' : readDone && mapOk ? '✓' : readDone ? '짝 확인 전' : '—'}</span>
        <span className="text-ink-300">·</span>
        <span>운영 도구 {opCount > 0 ? `${opCount}개 ✓` : '—'}</span>
        <span className="text-ink-300">·</span>
        <span>업무 처리 {biz && adminOk ? '✓' : '(사람이 처리)'}</span>
      </div>
    </section>
  )
}
