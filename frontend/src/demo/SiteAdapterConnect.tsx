import { useEffect, useState, type FormEvent } from 'react'
import { More, Notice, SectionHead, StatusChip } from '@/components/connect/parts'
import { ApiError } from '@/lib/apiError'
import { companyConnection, type ConnectionState } from '@/lib/companyConnection'
import { HUMAN_TOKEN_SET } from '@/lib/env'
import { ago } from '@/lib/format'
import { siteAdapter, type SiteAdapterState, type SiteAutoResult, type SiteMapping, type SiteResource } from '@/lib/siteAdapter'
import SiteWrites from './SiteWrites'

// 다른 형식 API·플랫폼으로 만든 사이트 연결: 고객사 자기 API를 사이트 변환기로 잇는다(사이트 코드는 건드리지 않음).
// 주소·키만 넣고 [알아서 연결]을 누르면 변환기가 목록 경로와 칸 짝을 규칙 + 회사 AI로 고르고,
// 첫 쪽을 실제로 바꿔 본 뒤 저장한다. 사람이 짝을 적는 칸은 없다. 고른 짝은 아래에 읽기 전용으로 보인다.
// 연결하면 AI들의 판매 서버 주소가 변환기로 바뀐다. 사이트에 쓰는 일은 연결 뒤 "사이트 쓰기"(SiteWrites)에서
// 동작마다 켜야 사이트로 가고, 그 전에는 사람 할 일로 넘어간다.
const LABEL: Record<SiteResource, Record<string, string>> = {
  orders: {
    order_id: '주문 번호', status: '주문 상태', created_at: '주문 시각', updated_at: '바뀐 시각',
    buyer_id: '구매자', product_id: '상품', qty: '수량', total_amount: '주문 금액', paid_amount: '받은 금액',
    refunded_amount: '환불 금액',
  },
  products: { product_id: '상품 번호', name: '상품 이름', price: '가격', category_code: '분류', active: '판매 중' },
}
const RES_NAME: Record<SiteResource, string> = { orders: '주문', products: '상품' }
const STATUS_NAME: Record<string, string> = { pending_payment: '결제 전', paid: '결제됨', cancelled: '취소' }
const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : '요청에 실패했습니다')

export default function SiteAdapterConnect({ onState, tenant }: { onState?: (s: ConnectionState | 'error') => void; tenant?: string }) {
  const [state, setState] = useState<SiteAdapterState | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [base, setBase] = useState('')
  const [token, setToken] = useState('')
  const [header, setHeader] = useState('Authorization')
  const [scheme, setScheme] = useState('Bearer')
  const [result, setResult] = useState<SiteAutoResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    siteAdapter.get()
      .then((s) => {
        if (!live) return
        setState(s)
        setBase(s.connection?.adapter?.base_url ?? '')
        if (s.mapping) { setHeader(s.mapping.auth_header); setScheme(s.mapping.auth_scheme) }
      })
      .catch((e) => { if (live) setLoadError(message(e)) })
    return () => { live = false }
  }, [])

  const connect = (e: FormEvent) => {
    e.preventDefault()
    setBusy(true); setError(null); setResult(null)
    void (async () => {
      try {
        const got = await siteAdapter.auto({ base_url: base, token: token || undefined, auth_header: header, auth_scheme: scheme, tenant })
        setResult(got)
        if (got.saved) {
          setState(await siteAdapter.get()); setToken('')
          const conn = await companyConnection.get()
          onState?.(conn.mode === 'internal' ? await companyConnection.mode('connected') : conn)
        }
      } catch (err) { setError(message(err)) } finally { setBusy(false) }
    })()
  }

  const canWrite = HUMAN_TOKEN_SET && state !== null
  const linked = state?.connection?.kind === 'adapter'
  const status = !state ? null : linked && state.connection?.enabled ? <StatusChip tone="ok">연결됨</StatusChip> : state.configured ? <StatusChip tone="off">꺼짐</StatusChip> : <StatusChip tone="wait">연결 전</StatusChip>
  const shown: SiteMapping | null = result?.mapping ?? state?.mapping ?? null
  const unplaced = result ? (Object.keys(RES_NAME) as SiteResource[]).flatMap((r) => (result.ai[r]?.unplaced ?? []).map((u) => ({ ...u, r }))) : []

  return (
    <section className="space-y-4">
      <SectionHead as="h3" title="다른 형식 API 연결" right={status}
        desc="사이트가 자기 API(예약·주문·상품 목록)를 갖고 있으면 주소와 키만 넣어 주세요. 변환기가 목록과 칸을 알아서 찾아 AXcellent 규격으로 바꿔 읽어요. 규칙으로 못 찾은 칸은 회사 AI가 고르고, 고객 값은 AI에 보내지 않아요." />

      {!HUMAN_TOKEN_SET && <Notice>로그인이 필요해요. 로그인하면 연결할 수 있어요.</Notice>}
      {loadError && <Notice tone="bad">변환기 상태를 읽지 못했어요: {loadError}</Notice>}
      {linked && state?.connection?.verified_at && (
        <p className="text-sm text-ink-700">마지막 확인 {ago(state.connection.verified_at)} · {state.connection.updated_by ?? '—'}</p>
      )}

      <form onSubmit={connect} className="space-y-3">
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            <span className="text-ink-700">사이트 API 주소</span>
            <input className="field w-full font-mono" placeholder="http://192.168.0.10:3100" value={base}
              onChange={(e) => { setBase(e.target.value); setResult(null) }} />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-ink-700">API 키 (읽기 권한)</span>
            <input className="field w-full font-mono" type="password" autoComplete="off" value={token}
              placeholder={state?.token_saved ? '저장된 키를 써요. 바꿀 때만 넣어요' : '키가 없는 API면 비워 둬요'}
              onChange={(e) => setToken(e.target.value)} />
          </label>
        </div>
        <More summary="키를 보내는 방식 (기본: Authorization: Bearer)">
          <div className="flex flex-wrap gap-2">
            <input aria-label="키 헤더 이름" className="field font-mono" value={header} onChange={(e) => setHeader(e.target.value)} />
            <input aria-label="키 앞에 붙는 말" className="field font-mono" value={scheme} placeholder="(없음)" onChange={(e) => setScheme(e.target.value)} />
          </div>
        </More>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary" disabled={busy || !base.trim() || !canWrite}>{busy ? '찾아서 연결하는 중…' : linked ? '알아서 다시 연결' : '알아서 연결'}</button>
          <span className="text-xs text-ink-500">첫 쪽을 실제로 바꿔 보고 통과해야 저장해요. 키는 서버에만 저장돼요.</span>
        </div>
      </form>

      {result?.saved && (
        <p role="status" className="text-sm text-emerald-800">
          연결했어요.{result.counts ? ` 첫 쪽에서 주문 ${result.counts.orders}건·상품 ${result.counts.products}건을 바꿔 읽었어요.` : ''}
        </p>
      )}
      {result && !result.saved && (
        <Notice tone="bad">연결하지 못했어요: {result.reason}. 사이트 API가 그 값을 주는지 확인한 뒤 다시 눌러 주세요.</Notice>
      )}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}

      {shown && (
        <div className="space-y-3 border-t border-ink-100 pt-4">
          <h4 className="text-sm font-semibold text-ink-900">찾은 짝</h4>
          {(Object.keys(RES_NAME) as SiteResource[]).map((r) => {
            const res = shown.resources[r]
            if (!res) return null
            const filled = result?.ai[r]?.filled ?? {}
            return (
              <table key={r} className="w-full text-sm" aria-label={`${RES_NAME[r]} 칸 짝`}>
                <caption className="pb-1 text-left text-xs text-ink-500">{RES_NAME[r]} · <span className="font-mono">{res.path}</span></caption>
                <tbody>
                  {Object.entries(res.fields).map(([name, expr]) => (
                    <tr key={name} className="border-t border-ink-100">
                      <td className="py-1.5 pr-2">{LABEL[r][name] ?? name}</td>
                      <td className="py-1.5 pr-2 font-mono text-xs">{expr}</td>
                      <td className="py-1.5 text-xs text-ink-500">{filled[name] ? <span title={filled[name].why}>AI가 찾음</span> : '규칙'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          })}
          {Object.keys(shown.resources.orders?.values?.status ?? {}).length > 0 && (
            <p className="text-xs leading-5 text-ink-500">
              주문 상태: {Object.entries(shown.resources.orders.values?.status ?? {}).map(([raw, to]) => `${raw} → ${STATUS_NAME[to] ?? '모름'}`).join(', ')}
            </p>
          )}
          {unplaced.length > 0 && (
            <div className="text-xs leading-5 text-ink-500">
              <p>AXcellent 형식에 자리가 없어 읽지 않는 사이트 칸:</p>
              <ul className="list-disc pl-5">{unplaced.map((u) => <li key={`${u.r}.${u.path}`}><span className="font-mono">{u.path}</span> — {u.meaning}</li>)}</ul>
            </div>
          )}
        </div>
      )}
      {linked && state?.configured && state.mapping && (
        <SiteWrites baseUrl={state.connection?.adapter?.base_url ?? ''} authHeader={state.mapping.auth_header}
          authScheme={state.mapping.auth_scheme} canWrite={canWrite} />
      )}
    </section>
  )
}
