import { useEffect, useState, type FormEvent } from 'react'
import { PreparingChip } from '@/components/Preparing'
import { ApiError } from '@/lib/apiError'
import { companyConnection, type ConnectionHistory, type ConnectionMode, type ConnectionState, type ConnectionTest } from '@/lib/companyConnection'
import { HUMAN_TOKEN_SET } from '@/lib/env'
import { ago } from '@/lib/format'

// 회사 서비스(고객사 판매 서버, shop-api 규격) 연결 창구. 저장하면 부서 AI가 모두 재시작 없이 이 주소를 읽는다.
// 연결 확인은 서버(dashboard-api)가 그 주소를 실제로 불러 상태·상품·구매자를 읽어 본 결과다. 회사 연결 화면(CompanyConnect) 아래쪽에 들어간다.
const SOURCE: Record<ConnectionState['source'], { label: string; tone: string }> = {
  connection: { label: '연결됨', tone: 'bg-emerald-50 text-emerald-700' },
  env: { label: '서버 설정값', tone: 'bg-ink-100 text-ink-700' },
  default: { label: '기본 (로컬 판매 서버)', tone: 'bg-ink-100 text-ink-700' },
}
const MODES: { id: ConnectionMode; label: string }[] = [
  { id: 'internal', label: '내부 데이터' },
  { id: 'connected', label: '실제 연결' },
]
const ACTION: Record<ConnectionHistory['action'], string> = { save: '주소 저장', mode: '모드 전환', recheck: '다시 확인', clear: '연결 해제' }
const modeOf = (enabled: boolean | null) => (enabled === null ? '' : enabled ? ' (실제 연결)' : ' (내부 데이터)')
const normalize = (u: string) => u.trim().replace(/\/+$/, '')
const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : '요청에 실패했습니다')

export default function Integrations() {
  const [state, setState] = useState<ConnectionState | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [url, setUrl] = useState('')
  const [result, setResult] = useState<ConnectionTest | null>(null)
  const [busy, setBusy] = useState<'test' | 'save' | 'clear' | 'mode' | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    companyConnection.get()
      .then((s) => { if (!live) return; setState(s); setUrl(s.connection?.shop_api_url ?? '') })
      .catch((e) => live && setLoadError(message(e)))
    return () => { live = false }
  }, [])

  const run = async (kind: 'test' | 'save' | 'clear' | 'mode', work: () => Promise<void>) => {
    setBusy(kind)
    setError(null)
    try {
      await work()
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(null)
    }
  }
  const test = (e: FormEvent) => {
    e.preventDefault()
    void run('test', async () => setResult(await companyConnection.test(url)))
  }
  const save = () => void run('save', async () => {
    const saved = await companyConnection.save(url)
    setState(saved)
    setResult(null)
  })
  const switchMode = (mode: ConnectionMode) => void run('mode', async () => setState(await companyConnection.mode(mode)))
  const clear = () => void run('clear', async () => {
    setState(await companyConnection.clear())
    setResult(null)
    setUrl('')
  })

  const tested = result !== null && result.ok && result.url === normalize(url)
  const canWrite = HUMAN_TOKEN_SET && (state?.editable ?? false)

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">회사 서비스</h2>

      <section className="card space-y-5 p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs text-ink-500">판매 서버 (shop-api 규격)</p>
            <p className="card-title text-lg">고객사 쇼핑몰 연결</p>
          </div>
          {state && <span className={`chip ${SOURCE[state.source].tone}`}>{SOURCE[state.source].label}</span>}
        </div>

        {state && (
          <div className="space-y-2">
            <div role="radiogroup" aria-label="데이터 모드" className="inline-flex rounded-xl bg-ink-100 p-1 text-sm">
              {MODES.map((m) => {
                const active = state.mode === m.id
                const disabled = busy !== null || !canWrite || active || (m.id === 'connected' && !state.connection)
                return (
                  <button
                    key={m.id}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    disabled={disabled}
                    onClick={() => switchMode(m.id)}
                    className={`rounded-lg px-3 py-1.5 ${active ? 'bg-white font-semibold text-ink-900 shadow-sm' : 'text-ink-500'}`}
                  >
                    {busy === 'mode' && !active ? '바꾸는 중…' : m.label}
                  </button>
                )
              })}
            </div>
            <p className="text-xs text-ink-500">
              내부 데이터: <span className="font-mono">{state.internal_url}</span>
              {state.connection && <> · 실제 연결: <span className="font-mono">{state.connection.shop_api_url}</span></>}
              {!state.connection && ' · 실제 연결은 아래에서 주소를 확인·저장하면 켤 수 있어요.'}
            </p>
            <p className="text-xs text-amber-700">판매 서버를 바꾸면 확정하지 않은 결산 숫자가 바뀔 수 있어요. 확정한 결산은 그대로이고, 차이는 재무 화면에 결산 차이로 보여요.</p>
          </div>
        )}

        {loadError && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">연결 상태를 읽지 못했어요: {loadError}</p>}
        {state && (
          <dl className="grid grid-cols-[88px_1fr] gap-x-3 gap-y-2 text-sm">
            <dt className="text-ink-500">지금 주소</dt>
            <dd className="truncate font-mono text-xs leading-5">{state.shop_api_url}</dd>
            <dt className="text-ink-500">확인 시각</dt>
            <dd className="text-ink-700">{state.connection?.verified_at ? ago(state.connection.verified_at) : '—'}</dd>
            <dt className="text-ink-500">바꾼 사람</dt>
            <dd className="text-ink-700">{state.connection?.updated_by ?? '—'}</dd>
            <dt className="text-ink-500">API 키</dt>
            <dd><PreparingChip /></dd>
            <dt className="text-ink-500">받는 데이터</dt>
            <dd className="text-ink-700">주문·상품·구매자 조회 (토큰이 필요한 쿠폰·환불은 <PreparingChip />)</dd>
          </dl>
        )}

        <form onSubmit={test} className="space-y-3">
          <label className="block text-sm text-ink-500" htmlFor="shop-api-url">판매 서버 주소</label>
          <input
            id="shop-api-url"
            className="field w-full font-mono"
            placeholder="http://192.168.0.251:8080/shop-api"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <button className="btn-ghost" disabled={busy !== null || !url.trim() || !canWrite}>{busy === 'test' ? '확인 중…' : '연결 확인'}</button>
            <button type="button" className="btn-primary" onClick={save} disabled={busy !== null || !tested || !canWrite}>{busy === 'save' ? '저장 중…' : '저장'}</button>
            {state?.connection && (
              <button type="button" className="btn-quiet" onClick={clear} disabled={busy !== null || !canWrite}>{busy === 'clear' ? '해제 중…' : '연결 해제'}</button>
            )}
          </div>
        </form>

        {result && (
          <ul className="space-y-1 text-sm" aria-label="연결 확인 결과">
            {result.checks.map((c) => (
              <li key={c.key} className="flex gap-2">
                <span className={`chip ${c.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'}`}>{c.ok ? '통과' : '실패'}</span>
                <span className="text-ink-700">{c.label}</span>
                <span className="text-ink-500">{c.detail}</span>
              </li>
            ))}
          </ul>
        )}
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}

        {state && state.history.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-ink-500">바꾼 기록</p>
            <ul className="space-y-0.5 text-xs text-ink-500" aria-label="회사 연결 바꾼 기록">
              {state.history.map((h) => (
                <li key={`${h.at}-${h.action}`}>
                  {ago(h.at)} · {h.by} · {ACTION[h.action]} · <span className="font-mono">{h.from ?? '—'}</span>{modeOf(h.from_enabled)} → <span className="font-mono">{h.to ?? '—'}</span>{modeOf(h.to_enabled)}
                </li>
              ))}
            </ul>
          </div>
        )}

        <p className="text-xs text-ink-500">
          사내 주소(192.168.x.x 등)는 바로 되고, 사내 밖 주소는 설치 운영자가 허용 목록(AX_COMPANY_SHOP_ALLOWED_HOSTS)에 넣은 https 주소만 돼요. 세 항목을 모두 통과해야 저장할 수 있어요. 저장하면 실제 연결 모드가 되고, 모든 부서 AI가 다시 켜지 않아도 바로 이 주소를 읽어요. 내부 데이터로 바꾸면 주소는 남긴 채 원래 판매 서버를 읽고, 연결 해제는 주소까지 지워요.
          {!HUMAN_TOKEN_SET && ' 사람 토큰이 없어 확인·저장이 꺼져 있어요 (run_web.sh로 켜면 들어가요).'}
          {state && !state.editable && ' 서버에 회사 연결 파일 설정(AX_COMPANY_CONNECTION_FILE)이 없어 저장할 수 없어요 (run_all.sh로 켜면 생겨요).'}
        </p>
      </section>
    </div>
  )
}
