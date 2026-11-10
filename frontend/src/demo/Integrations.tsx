import { useEffect, useState, type FormEvent } from 'react'
import { DangerRow, Facts, More, Notice, SectionHead, StatusChip } from '@/components/connect/parts'
import { PreparingChip } from '@/components/Preparing'
import { ApiError } from '@/lib/apiError'
import { companyConnection, type ConnectionHistory, type ConnectionMode, type ConnectionState, type ConnectionTest } from '@/lib/companyConnection'
import { HUMAN_TOKEN_SET } from '@/lib/env'
import { ago } from '@/lib/format'

// 회사 판매 서버(고객사 판매 서버, shop-api 규격) 연결 창구. 저장하면 부서 AI가 모두 재시작 없이 이 주소를 읽는다.
// 연결 확인은 서버(dashboard-api)가 그 주소를 실제로 불러 상태·상품·구매자를 읽어 본 결과다. 회사 연결 화면(CompanyConnect) 아래쪽에 들어간다.
// 계층: 지금 읽는 곳(두 모드 중 하나를 고르는 카드) › 보조 사실 › 주소 바꾸기(확인 → 저장) › 기록·규칙(펼침) › 연결 해제(맨 아래)
const ACTION: Record<ConnectionHistory['action'], string> = { save: '주소 저장', mode: '모드 전환', recheck: '다시 확인', clear: '연결 해제' }
const modeOf = (enabled: boolean | null) => (enabled === null ? '' : enabled ? ' (실제 연결)' : ' (내부 데이터)')
const normalize = (u: string) => u.trim().replace(/\/+$/, '')
const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : '요청에 실패했습니다')

export default function Integrations({ onState }: { onState?: (s: ConnectionState | 'error') => void } = {}) {
  const [state, setStateRaw] = useState<ConnectionState | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [url, setUrl] = useState('')
  const [result, setResult] = useState<ConnectionTest | null>(null)
  const [busy, setBusy] = useState<'test' | 'save' | 'clear' | 'mode' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const setState = (s: ConnectionState) => { setStateRaw(s); onState?.(s) }

  useEffect(() => {
    let live = true
    companyConnection.get()
      .then((s) => { if (!live) return; setState(s); setUrl(s.connection?.shop_api_url ?? '') })
      .catch((e) => { if (!live) return; setLoadError(message(e)); onState?.('error') })
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
    setConfirmClear(false)
  })

  const tested = result !== null && result.ok && result.url === normalize(url)
  const canWrite = HUMAN_TOKEN_SET && (state?.editable ?? false)
  const status = !state ? null : state.mode === 'connected' ? <StatusChip tone="ok">회사 서버 연결됨</StatusChip> : <StatusChip tone="wait">내부 데이터 사용 중</StatusChip>

  const modes: { id: ConnectionMode; title: string; url: string | null; desc: string }[] = state ? [
    { id: 'internal', title: '내부 데이터', url: state.internal_url, desc: '설치할 때 정한 기본 판매 서버예요. 시험·시연용으로 써요.' },
    { id: 'connected', title: '회사 판매 서버', url: state.connection?.shop_api_url ?? null, desc: '회사가 운영하는 실제 판매 서버예요.' },
  ] : []

  return (
    <section className="space-y-4">
      <SectionHead title="회사 판매 서버"
        desc="부서 AI가 주문·상품·구매자를 읽는 곳이에요. 바꾸면 다시 켜지 않아도 모든 부서 AI가 바로 따라가요."
        right={status} />

      {!HUMAN_TOKEN_SET && <Notice>사람 토큰이 없어 확인·저장이 꺼져 있어요. run_web.sh로 화면을 켜면 들어가요.</Notice>}
      {state && !state.editable && <Notice>서버에 회사 연결 파일 설정(AX_COMPANY_CONNECTION_FILE)이 없어 저장할 수 없어요. run_all.sh로 서버를 켜면 생겨요.</Notice>}
      {loadError && <Notice tone="bad">연결 상태를 읽지 못했어요: {loadError}</Notice>}

      <div className="card">
        <div className="space-y-6 p-6">
          {state && (
            <div className="space-y-3">
              <p className="text-xs text-ink-500">지금 읽는 곳</p>
              <div role="radiogroup" aria-label="판매 서버" className="grid gap-3 md:grid-cols-2">
                {modes.map((m) => {
                  const active = state.mode === m.id
                  const blocked = m.id === 'connected' && !state.connection
                  return (
                    <div key={m.id} role="radio" aria-checked={active}
                      className={`flex flex-col gap-2 border p-4 ${active ? 'border-brand-600 bg-brand-50/50' : 'border-ink-100'}`}>
                      <div className="flex items-center justify-between gap-2">
                        <p className="font-semibold">{m.title}</p>
                        {active ? <StatusChip tone="ok">지금 읽는 중</StatusChip> : (
                          <button type="button" className="btn-ghost px-2.5 py-1 text-xs" onClick={() => switchMode(m.id)}
                            disabled={busy !== null || !canWrite || blocked}>
                            {busy === 'mode' ? '바꾸는 중…' : '이걸로 바꾸기'}
                          </button>
                        )}
                      </div>
                      <p className={`break-all font-mono text-xs ${m.url ? 'text-ink-900' : 'text-ink-500'}`}>{m.url ?? '아래에서 주소를 확인·저장하면 켤 수 있어요'}</p>
                      <p className="text-xs text-ink-500">{m.desc}</p>
                    </div>
                  )
                })}
              </div>
              <p className="text-xs leading-5 text-amber-800">판매 서버를 바꾸면 확정하지 않은 결산 숫자가 바뀔 수 있어요. 확정한 결산은 그대로이고, 차이는 재무 화면에 결산 차이로 보여요.</p>
            </div>
          )}

          {state && (
            <div className="border-t border-ink-100 pt-5">
              <Facts cols={4} items={[
                ['마지막 확인', state.connection?.verified_at ? ago(state.connection.verified_at) : '—'],
                ['바꾼 사람', state.connection?.updated_by ?? '—'],
                ['받는 데이터', '주문·상품·구매자 조회'],
                ['API 키', <PreparingChip />],
              ]} />
            </div>
          )}

          <form onSubmit={test} className="space-y-3 border-t border-ink-100 pt-5">
            <SectionHead as="h3" title="회사 판매 서버 주소" desc="연결 확인을 먼저 하고, 세 항목을 모두 통과하면 저장할 수 있어요. 저장하면 회사 판매 서버로 바뀌어요." />
            <div className="flex flex-wrap gap-2">
              <input id="shop-api-url" aria-label="판매 서버 주소" className="field min-w-[260px] flex-1 font-mono" placeholder="http://10.20.30.40:8080/shop-api"
                value={url} onChange={(e) => { setUrl(e.target.value); setResult(null) }} />
              <button className="btn-ghost" disabled={busy !== null || !url.trim() || !canWrite}>{busy === 'test' ? '확인 중…' : '1. 연결 확인'}</button>
              <button type="button" className="btn-primary" onClick={save} disabled={busy !== null || !tested || !canWrite}>{busy === 'save' ? '저장 중…' : '2. 저장'}</button>
            </div>
            {result && (
              <ul className="grid gap-2 sm:grid-cols-3" aria-label="연결 확인 결과">
                {result.checks.map((c) => (
                  <li key={c.key} className={`border-l-2 px-3 py-2 text-sm ${c.ok ? 'border-emerald-700 bg-emerald-50/60' : 'border-red-700 bg-red-50'}`}>
                    <p className={`font-medium ${c.ok ? 'text-emerald-800' : 'text-red-800'}`}>{c.ok ? '통과' : '실패'} · {c.label}</p>
                    <p className="mt-0.5 text-xs text-ink-500">{c.detail}</p>
                  </li>
                ))}
              </ul>
            )}
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
          </form>

          <div className="space-y-2 border-t border-ink-100 pt-4">
            {state && state.history.length > 0 && (
              <More summary={`바꾼 기록 ${state.history.length}건`}>
                <ul className="space-y-1" aria-label="회사 연결 바꾼 기록">
                  {state.history.map((h) => (
                    <li key={`${h.at}-${h.action}`}>
                      <span className="text-ink-700">{ago(h.at)} · {h.by} · {ACTION[h.action]}</span>{' '}
                      <span className="font-mono">{h.from ?? '—'}</span>{modeOf(h.from_enabled)} → <span className="font-mono">{h.to ?? '—'}</span>{modeOf(h.to_enabled)}
                    </li>
                  ))}
                </ul>
              </More>
            )}
            <More summary="어떤 주소를 쓸 수 있나요">
              <p>사내 주소(192.168.x.x 등)는 바로 돼요. 사내 밖 주소는 설치 운영자가 허용 목록(AX_COMPANY_SHOP_ALLOWED_HOSTS)에 넣은 https 주소만 돼요.</p>
              <p>내부 데이터로 바꾸면 저장한 주소는 남긴 채 기본 판매 서버를 읽어요. 연결 해제는 저장한 주소까지 지워요.</p>
              <p>토큰이 필요한 쿠폰·환불 읽기는 <PreparingChip /></p>
            </More>
          </div>
        </div>

        {state?.connection && (
          <DangerRow note={confirmClear ? '저장한 회사 판매 서버 주소를 지우고 기본 판매 서버로 돌아가요.' : '저장한 주소를 지울 때만 써요.'}>
            {confirmClear ? (
              <>
                <button type="button" className="btn-quiet" onClick={() => setConfirmClear(false)}>취소</button>
                <button type="button" className="btn-primary bg-red-700 hover:bg-red-800" onClick={clear} disabled={busy !== null || !canWrite}>{busy === 'clear' ? '해제 중…' : '연결 해제'}</button>
              </>
            ) : (
              <button type="button" className="btn-ghost text-red-700" onClick={() => setConfirmClear(true)} disabled={busy !== null || !canWrite}>연결 해제</button>
            )}
          </DangerRow>
        )}
      </div>
    </section>
  )
}
