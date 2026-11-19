import { useEffect, useState, type FormEvent } from 'react'
import { More, Notice, SectionHead, StatusChip } from '@/components/connect/parts'
import { ApiError } from '@/lib/apiError'
import { companyConnection, type ConnectionState } from '@/lib/companyConnection'
import { HUMAN_TOKEN_SET } from '@/lib/env'
import { ago } from '@/lib/format'
import { preview, siteAdapter, unfilled, type SiteAdapterState, type SiteMapping, type SiteProposal, type SiteResource } from '@/lib/siteAdapter'

// 다른 형식 API·플랫폼으로 만든 사이트 연결: 고객사 자기 API를 사이트 변환기로 잇는다(사이트 코드는 건드리지 않음).
// 순서: 주소·키 → 1. 둘러보기(목록 경로 찾기, 경로를 고르면 짝 제안) → 짝 확인(칸·주문 상태) → 2. 연결(첫 쪽을 실제로 바꿔 본 뒤 저장).
// 연결하면 AI들의 판매 서버 주소가 변환기로 바뀐다. 읽기만 바꾸고, 사이트에 쓰는 일은 사람 할 일로 넘어간다.
const LABEL: Record<SiteResource, Record<string, string>> = {
  orders: {
    order_id: '주문 번호', status: '주문 상태', created_at: '주문 시각', updated_at: '바뀐 시각',
    buyer_id: '구매자', product_id: '상품', qty: '수량', total_amount: '주문 금액', paid_amount: '받은 금액',
  },
  products: { product_id: '상품 번호', name: '상품 이름', price: '가격', category_code: '분류', active: '판매 중' },
}
const RES_NAME: Record<SiteResource, string> = { orders: '주문', products: '상품' }
// 주문 한 건에 상품이 여러 개인 사이트의 주문 줄 칸
const LINE_LABEL: Record<string, string> = { product_id: '상품', qty: '수량', unit_price: '단가', paid_amount: '결제액' }
const STATUS: { value: string; label: string }[] = [
  { value: '', label: '정하지 않음' },
  { value: 'pending_payment', label: '결제 전' },
  { value: 'paid', label: '결제됨' },
  { value: 'cancelled', label: '취소' },
]
const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : '요청에 실패했습니다')

export default function SiteAdapterConnect({ onState, tenant }: { onState?: (s: ConnectionState | 'error') => void; tenant?: string }) {
  const [state, setState] = useState<SiteAdapterState | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [base, setBase] = useState('')
  const [token, setToken] = useState('')
  const [header, setHeader] = useState('Authorization')
  const [scheme, setScheme] = useState('Bearer')
  const [ordersPath, setOrdersPath] = useState('')
  const [productsPath, setProductsPath] = useState('')
  const [found, setFound] = useState<SiteProposal | null>(null)
  const [mapping, setMapping] = useState<SiteMapping | null>(null)
  const [busy, setBusy] = useState<'look' | 'save' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    siteAdapter.get()
      .then((s) => {
        if (!live) return
        setState(s)
        const m = s.mapping
        setBase(s.connection?.adapter?.base_url ?? '')
        if (m) {
          setHeader(m.auth_header); setScheme(m.auth_scheme)
          setOrdersPath(m.resources.orders.path); setProductsPath(m.resources.products.path)
          setMapping(m)
        }
      })
      .catch((e) => { if (live) setLoadError(message(e)) })
    return () => { live = false }
  }, [])

  // 주소·경로·키 보내는 방식을 바꾸면 전에 받은 짝은 그 입력과 맞지 않는다. 다시 둘러보게 한다
  const stale = () => { setFound(null); setMapping(null) }
  const run = async (kind: 'look' | 'save', work: () => Promise<void>) => {
    setBusy(kind); setError(null); setDone(null)
    try { await work() } catch (e) { setError(message(e)) } finally { setBusy(null) }
  }
  const look = (e: FormEvent) => {
    e.preventDefault()
    void run('look', async () => {
      const both = ordersPath.trim() && productsPath.trim()
      const got = await siteAdapter.propose({
        base_url: base, token: token || undefined, auth_header: header, auth_scheme: scheme,
        ...(both ? { orders_path: ordersPath.trim(), products_path: productsPath.trim() } : {}),
      })
      setFound(got)
      if (got.mapping) setMapping(got.mapping)
    })
  }
  const save = () => void run('save', async () => {
    if (!mapping) return
    const saved = await siteAdapter.save(base, mapping, token, tenant)
    setState(saved); setToken('')
    const conn = await companyConnection.get()
    onState?.(conn.mode === 'internal' ? await companyConnection.mode('connected') : conn)
    const c = saved.counts
    setDone(c ? `연결했어요. 첫 쪽에서 주문 ${c.orders}건·상품 ${c.products}건을 바꿔 읽었어요.` : '연결했어요.')
  })

  const setField = (res: SiteResource, name: string, expr: string) =>
    setMapping((m) => m && { ...m, resources: { ...m.resources, [res]: { ...m.resources[res], fields: { ...m.resources[res].fields, [name]: expr } } } })
  const setLine = (name: string, expr: string) =>
    setMapping((m) => {
      const lines = m?.resources.orders.items
      if (!m || !lines) return m
      return { ...m, resources: { ...m.resources, orders: { ...m.resources.orders, items: { ...lines, fields: { ...lines.fields, [name]: expr } } } } }
    })
  const setStatus = (raw: string, to: string) =>
    setMapping((m) => {
      if (!m) return m
      const orders = m.resources.orders
      return { ...m, resources: { ...m.resources, orders: { ...orders, values: { ...orders.values, status: { ...orders.values?.status, [raw]: to } } } } }
    })

  const canWrite = HUMAN_TOKEN_SET && state !== null
  // 빈 칸 이름: orders.qty → "주문 수량", orders.status=x → "주문 상태 x"
  const blanks = (mapping ? unfilled(mapping) : []).map((key) => {
    const [res, rest, sub] = key.split('.') as [SiteResource, string, string | undefined]
    if (rest.startsWith('status=')) return `주문 상태 ${rest.slice('status='.length)}`
    if (rest === 'items' && sub) return `주문 줄 ${LINE_LABEL[sub] ?? sub}`
    return `${RES_NAME[res]} ${LABEL[res][rest] ?? rest}`
  })
  const linked = state?.connection?.kind === 'adapter'
  const status = !state ? null : linked && state.connection?.enabled ? <StatusChip tone="ok">연결됨</StatusChip> : state.configured ? <StatusChip tone="off">꺼짐</StatusChip> : <StatusChip tone="wait">연결 전</StatusChip>

  return (
    <section className="space-y-4">
      <SectionHead as="h3" title="다른 형식 API 연결" right={status}
        desc="사이트가 자기 API(예약·주문·상품 목록)를 갖고 있으면, 칸 짝을 정해 AXcellent 규격으로 바꿔 읽어요. 플랫폼으로 만든 사이트도 API 주소와 키가 있으면 같은 방법으로 이어요." />

      {!HUMAN_TOKEN_SET && <Notice>사람 토큰이 없어 둘러보기·연결이 꺼져 있어요. run_web.sh로 화면을 켜면 들어가요.</Notice>}
      {loadError && <Notice tone="bad">변환기 상태를 읽지 못했어요: {loadError}</Notice>}
      {linked && state?.connection?.verified_at && (
        <p className="text-sm text-ink-700">마지막 확인 {ago(state.connection.verified_at)} · {state.connection.updated_by ?? '—'}</p>
      )}

      <form onSubmit={look} className="space-y-3">
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            <span className="text-ink-700">사이트 API 주소</span>
            <input className="field w-full font-mono" placeholder="http://192.168.0.10:3100" value={base}
              onChange={(e) => { setBase(e.target.value); stale() }} />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-ink-700">API 키 (읽기 권한)</span>
            <input className="field w-full font-mono" type="password" autoComplete="off" value={token}
              placeholder={state?.token_saved ? '저장된 키를 써요. 바꿀 때만 넣어요' : '키가 없는 API면 비워 둬요'}
              onChange={(e) => setToken(e.target.value)} />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-ink-700">주문(예약) 목록 경로</span>
            <input className="field w-full font-mono" list="site-paths" placeholder="/api/v1/bookings" value={ordersPath} onChange={(e) => { setOrdersPath(e.target.value); stale() }} />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-ink-700">상품 목록 경로</span>
            <input className="field w-full font-mono" list="site-paths" placeholder="/api/v1/tours" value={productsPath} onChange={(e) => { setProductsPath(e.target.value); stale() }} />
          </label>
        </div>
        <datalist id="site-paths">{found?.paths.map((p) => <option key={p} value={p} />)}</datalist>
        <More summary="키를 보내는 방식 (기본: Authorization: Bearer)">
          <div className="flex flex-wrap gap-2">
            <input aria-label="키 헤더 이름" className="field font-mono" value={header} onChange={(e) => { setHeader(e.target.value); stale() }} />
            <input aria-label="키 앞에 붙는 말" className="field font-mono" value={scheme} placeholder="(없음)" onChange={(e) => { setScheme(e.target.value); stale() }} />
          </div>
        </More>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-ghost" disabled={busy !== null || !base.trim() || !canWrite}>{busy === 'look' ? '둘러보는 중…' : '1. 둘러보기'}</button>
          <span className="text-xs text-ink-500">경로를 비워 두고 둘러보면 사이트가 알려 주는 목록 경로를 찾아요.</span>
        </div>
        {found && !found.mapping && (
          <p role="status" className="text-sm text-ink-700">
            {found.paths.length ? `목록 경로 ${found.paths.length}개를 찾았어요. 경로 칸에서 고른 뒤 다시 둘러보세요.` : '사이트가 경로 목록(openapi.json)을 알려 주지 않아요. 경로를 직접 넣어 주세요.'}
          </p>
        )}
      </form>

      {mapping && (
        <div className="space-y-4 border-t border-ink-100 pt-4">
          <div>
            <h4 className="text-sm font-semibold text-ink-900">칸 짝 확인</h4>
            <p className="mt-0.5 text-sm leading-6 text-ink-500">왼쪽이 AXcellent가 쓰는 칸, 가운데가 사이트 응답의 칸이에요. 첫 줄 값을 보고 맞는지 확인해 주세요. 빈 칸은 직접 채워야 연결돼요.</p>
          </div>
          {(Object.keys(LABEL) as SiteResource[]).map((r) => (
            <datalist key={r} id={`site-keys-${r}`}>{found?.samples?.[r].keys.map((k) => <option key={k} value={k} />)}</datalist>
          ))}
          {(Object.keys(LABEL) as SiteResource[]).map((r) => (
            <table key={r} className="w-full text-sm" aria-label={`${RES_NAME[r]} 칸 짝`}>
              <caption className="pb-1 text-left text-xs text-ink-500">{RES_NAME[r]} · <span className="font-mono">{mapping.resources[r].path}</span>{found?.samples ? ` · 첫 쪽 ${found.samples[r].count}줄` : ''}</caption>
              <thead><tr className="text-left text-xs text-ink-500"><th className="py-1 font-normal">AXcellent 칸</th><th className="py-1 font-normal">사이트 칸</th><th className="py-1 font-normal">첫 줄 값</th></tr></thead>
              <tbody>
                {Object.entries(mapping.resources[r].fields).map(([name, expr]) => (
                  <tr key={name} className="border-t border-ink-100">
                    <td className="py-1.5 pr-2">{LABEL[r][name] ?? name}</td>
                    <td className="py-1.5 pr-2">
                      <input aria-label={`${RES_NAME[r]} ${LABEL[r][name] ?? name} 사이트 칸`} list={`site-keys-${r}`}
                        className={`field w-full font-mono ${expr.trim() ? '' : 'border-amber-700'}`} value={expr} placeholder="정해 주세요"
                        onChange={(e) => setField(r, name, e.target.value)} />
                    </td>
                    <td className="py-1.5 font-mono text-xs text-ink-500">{preview(found?.samples?.[r].first, expr)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
          {mapping.resources.orders.items && (
            <table className="w-full text-sm" aria-label="주문 줄 짝">
              <caption className="pb-1 text-left text-xs text-ink-500">주문 줄 · 주문 한 건에 상품이 여러 개 · <span className="font-mono">{mapping.resources.orders.items.path}</span></caption>
              <tbody>
                {Object.keys(LINE_LABEL).map((name) => {
                  const expr = mapping.resources.orders.items?.fields[name] ?? ''
                  return (
                    <tr key={name} className="border-t border-ink-100">
                      <td className="py-1.5 pr-2">{LINE_LABEL[name]}</td>
                      <td className="py-1.5">
                        <input aria-label={`주문 줄 ${LINE_LABEL[name]} 사이트 칸`} className={`field w-full font-mono ${expr.trim() || name === 'qty' || name === 'unit_price' ? '' : 'border-amber-700'}`}
                          value={expr} placeholder={name === 'qty' ? '없으면 1' : name === 'unit_price' ? '없으면 결제액 ÷ 수량' : '정해 주세요'} onChange={(e) => setLine(name, e.target.value)} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
          {Object.keys(mapping.resources.orders.values?.status ?? {}).length > 0 && (
            <table className="w-full text-sm" aria-label="주문 상태 짝">
              <caption className="pb-1 text-left text-xs text-ink-500">주문 상태 · 첫 쪽에 나온 값</caption>
              <tbody>
                {Object.entries(mapping.resources.orders.values?.status ?? {}).map(([raw, to]) => (
                  <tr key={raw} className="border-t border-ink-100">
                    <td className="py-1.5 pr-2 font-mono">{raw}</td>
                    <td className="py-1.5">
                      <select aria-label={`${raw} 상태`} className={`field ${to ? '' : 'border-amber-700'}`} value={to} onChange={(e) => setStatus(raw, e.target.value)}>
                        {STATUS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn-primary" onClick={save} disabled={busy !== null || blanks.length > 0 || !base.trim() || !canWrite}>{busy === 'save' ? '확인하고 연결하는 중…' : '2. 연결'}</button>
            {blanks.length > 0 && <span className="text-xs text-amber-800">아직 정하지 않은 칸: {blanks.join(', ')}</span>}
          </div>
          <p className="text-xs leading-5 text-ink-500">연결하면 사이트에서 첫 쪽을 다시 읽어 바꿔 보고, 통과해야 저장해요. 키는 서버에만 저장되고 화면에 다시 보이지 않아요. 환불·쿠폰처럼 사이트에 쓰는 일은 사람 할 일로 넘어가요.</p>
        </div>
      )}
      {done && <p role="status" className="text-sm text-emerald-800">{done}</p>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </section>
  )
}
