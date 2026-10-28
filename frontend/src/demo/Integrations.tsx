import { useState, type FormEvent } from 'react'
import { ago } from '@/lib/format'
import { SCOPES, newId, now, useIntegrations, type Integration } from './store'

// 회사가 운영하는 서비스(예: 캣북스 온라인 서점)를 연결하는 창구. URL·API·키를 넣으면 부서 AI가 그 서비스의 데이터를 받아 온다.
// 솔루션은 서비스 안에 들어가지 않고, 여기서 연결만 한다.
const STATUS: Record<Integration['status'], { label: string; tone: string }> = {
  connected: { label: '연결됨', tone: 'bg-emerald-50 text-emerald-700' },
  untested: { label: '연결 확인 전', tone: 'bg-amber-50 text-amber-700' },
  failed: { label: '연결 실패', tone: 'bg-red-50 text-red-700' },
}
const EMPTY = { name: '', kind: '쇼핑몰', url: '', apiBase: '', apiKey: '', scopes: ['주문', '결제'] as string[] }
const mask = (key: string) => (key.length <= 6 ? '••••' : `${key.slice(0, 3)}••••••${key.slice(-4)}`)
const validUrl = (u: string) => /^https?:\/\/[^\s/]+/.test(u.trim())

export default function Integrations() {
  const [items, setItems] = useIntegrations()
  const [form, setForm] = useState(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [testing, setTesting] = useState<string | null>(null)

  const update = (id: string, patch: Partial<Integration>) => setItems((list) => list.map((x) => (x.id === id ? { ...x, ...patch } : x)))
  const test = (id: string) => {
    setTesting(id)
    setTimeout(() => {
      update(id, { status: 'connected', lastSync: now() })
      setTesting(null)
    }, 1000)
  }
  const toggleScope = (s: string) =>
    setForm((f) => ({ ...f, scopes: f.scopes.includes(s) ? f.scopes.filter((x) => x !== s) : [...f.scopes, s] }))

  const add = (e: FormEvent) => {
    e.preventDefault()
    if (!form.name.trim()) return setError('서비스 이름을 입력하세요.')
    if (!validUrl(form.url) || !validUrl(form.apiBase)) return setError('서비스 주소와 API 주소는 http(s)://로 시작해야 합니다.')
    if (!form.apiKey.trim()) return setError('API 키를 입력하세요.')
    const item: Integration = { id: newId(), ...form, name: form.name.trim(), status: 'untested', lastSync: null }
    setItems((list) => [...list, item])
    setForm(EMPTY)
    setError(null)
    test(item.id)
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-8 py-8">
      <header>
        <p className="text-sm text-ink-500">시스템 관리</p>
        <h1 className="text-2xl font-semibold">회사 서비스 연결</h1>
      </header>

      <ul className="grid gap-4 lg:grid-cols-2">
        {items.map((x) => (
          <li key={x.id} className="card space-y-4 p-6">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs text-ink-500">{x.kind}</p>
                <p className="card-title text-lg">{x.name}</p>
              </div>
              <span className={`chip ${STATUS[x.status].tone}`}>{testing === x.id ? '확인 중…' : STATUS[x.status].label}</span>
            </div>
            <dl className="grid grid-cols-[88px_1fr] gap-x-3 gap-y-2 text-sm">
              <dt className="text-ink-500">서비스 주소</dt>
              <dd className="truncate font-mono text-xs leading-5">{x.url}</dd>
              <dt className="text-ink-500">API 주소</dt>
              <dd className="truncate font-mono text-xs leading-5">{x.apiBase}</dd>
              <dt className="text-ink-500">API 키</dt>
              <dd className="font-mono text-xs leading-5">{mask(x.apiKey)}</dd>
              <dt className="text-ink-500">받는 데이터</dt>
              <dd className="flex flex-wrap gap-1">{x.scopes.map((s) => <span key={s} className="chip bg-ink-100 text-ink-700">{s}</span>)}</dd>
              <dt className="text-ink-500">마지막 수신</dt>
              <dd className="text-ink-700">{x.lastSync ? ago(x.lastSync) : '—'}</dd>
            </dl>
            <div className="flex gap-2">
              <button type="button" className="btn-ghost" onClick={() => test(x.id)} disabled={testing === x.id}>연결 확인</button>
              <button type="button" className="btn-quiet" onClick={() => setItems((list) => list.filter((y) => y.id !== x.id))}>연결 해제</button>
            </div>
          </li>
        ))}
      </ul>

      <section className="card p-6">
        <h2 className="card-title">새 서비스 연결</h2>
        <form onSubmit={add} className="mt-4 space-y-3">
          <div className="grid gap-3 md:grid-cols-[2fr_1fr]">
            <input className="field" placeholder="서비스 이름 (예: 캣북스 모바일 앱)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} aria-label="서비스 이름" />
            <select className="field" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} aria-label="종류">
              {['쇼핑몰', '모바일 앱', '결제사', '배송사', '사내 시스템'].map((k) => <option key={k}>{k}</option>)}
            </select>
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            <input className="field font-mono" placeholder="서비스 주소 https://" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} aria-label="서비스 주소" />
            <input className="field font-mono" placeholder="API 주소 https://" value={form.apiBase} onChange={(e) => setForm({ ...form, apiBase: e.target.value })} aria-label="API 주소" />
            <input className="field font-mono" type="password" placeholder="API 키" value={form.apiKey} onChange={(e) => setForm({ ...form, apiKey: e.target.value })} aria-label="API 키" />
          </div>
          <fieldset className="flex flex-wrap items-center gap-2">
            <legend className="mb-2 text-sm text-ink-500">받을 데이터</legend>
            {SCOPES.map((s) => (
              <label key={s} className={`chip cursor-pointer px-3 py-1 text-xs ${form.scopes.includes(s) ? 'bg-brand-600 text-white' : 'bg-ink-100 text-ink-700'}`}>
                <input type="checkbox" className="sr-only" checked={form.scopes.includes(s)} onChange={() => toggleScope(s)} />
                {s}
              </label>
            ))}
          </fieldset>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button className="btn-primary">연결</button>
        </form>
      </section>
    </div>
  )
}
