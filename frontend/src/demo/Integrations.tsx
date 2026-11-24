import { useEffect, useState, type FormEvent } from 'react'
import { DangerRow, Facts, More, Notice, SectionHead, StatusChip } from '@/components/connect/parts'
import { ApiError } from '@/lib/apiError'
import { companyConnection, type ConnectionHistory, type ConnectionState, type ConnectionTest } from '@/lib/companyConnection'
import { HUMAN_TOKEN_SET } from '@/lib/env'
import { ago } from '@/lib/format'

// 회사 판매 서버(고객사 판매 서버, shop-api 규격) 연결 창구. 저장하면 부서 AI가 모두 재시작 없이 이 주소를 읽는다.
// 연결 확인은 서버(dashboard-api)가 그 주소를 실제로 불러 상태·상품·구매자를 읽어 본 결과다. 회사 연결 화면(CompanyConnect) 아래쪽에 들어간다.
// 계층: 보조 사실 › 주소 바꾸기(확인 → 저장) › 기록·규칙(펼침) › 연결 해제(맨 아래). 앞으로는 회사 판매 서버만 쓴다(내부 데이터로 바꾸는 창구 없음).
const ACTION: Record<ConnectionHistory['action'], string> = { save: '주소 저장', mode: '모드 전환', recheck: '다시 확인', clear: '연결 해제' }
const modeOf = (enabled: boolean | null) => (enabled === null ? '' : enabled ? ' (실제 연결)' : ' (내부 데이터)')
const normalize = (u: string) => u.trim().replace(/\/+$/, '')
const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : '요청에 실패했습니다')

export default function Integrations({ onState, embedded = false, tenant }: { onState?: (s: ConnectionState | 'error') => void; embedded?: boolean; tenant?: string } = {}) {
  const [state, setStateRaw] = useState<ConnectionState | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [url, setUrl] = useState('')
  const [result, setResult] = useState<ConnectionTest | null>(null)
  const [busy, setBusy] = useState<'test' | 'save' | 'clear' | null>(null)
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

  const run = async (kind: 'test' | 'save' | 'clear', work: () => Promise<void>) => {
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
    const saved = await companyConnection.save(url, tenant)
    // 저장만으로 내부 데이터 모드가 풀리지 않을 수 있어, 실제로 회사 판매 서버를 읽게 한다
    setState(saved.mode === 'internal' ? await companyConnection.mode('connected') : saved)
    setResult(null)
  })
  const clear = () => void run('clear', async () => {
    setState(await companyConnection.clear())
    setResult(null)
    setUrl('')
    setConfirmClear(false)
  })

  const tested = result !== null && result.ok && result.url === normalize(url)
  // 저장한 주소가 있는데 아직 내부 데이터로 읽는 상태: 그 주소 그대로 저장하면 바로 전환한다
  const pendingSwitch = state?.mode === 'internal' && !!state.connection && normalize(url) === normalize(state.connection.shop_api_url)
  const canWrite = HUMAN_TOKEN_SET && (state?.editable ?? false)
  const status = !state ? null : state.mode === 'connected' ? <StatusChip tone="ok">회사 서버 연결됨</StatusChip> : state.connected === false ? <StatusChip tone="off">연결 안 됨</StatusChip> : <StatusChip tone="wait">내부 데이터 사용 중</StatusChip>

  return (
    <section className="space-y-4">
      <SectionHead as={embedded ? 'h3' : 'h2'} title={embedded ? 'AXcellent 규격 API 연결' : '회사 사이트 서버'}
        desc="부서 AI가 주문·상품·구매자를 읽는 곳이에요. 바꾸면 다시 켜지 않아도 모든 부서 AI가 바로 따라가요."
        right={status} />

      {!HUMAN_TOKEN_SET && <Notice>로그인이 필요해요. 로그인하면 확인·저장할 수 있어요.</Notice>}
      {state && !state.editable && <Notice>서버에 회사 연결 파일 설정(AX_COMPANY_CONNECTION_FILE)이 없어 저장할 수 없어요. run_all.sh로 서버를 켜면 생겨요.</Notice>}
      {loadError && <Notice tone="bad">연결 상태를 읽지 못했어요: {loadError}</Notice>}

      <div className="card">
        <div className="space-y-6 p-6">
          {state && (
            <div className="border-t border-ink-100 pt-5">
              <Facts cols={3} items={[
                ['마지막 확인', state.connection?.verified_at ? ago(state.connection.verified_at) : '—'],
                ['바꾼 사람', state.connection?.updated_by ?? '—'],
                ['받는 데이터', '주문·상품·구매자 조회'],
              ]} />
            </div>
          )}

          <form onSubmit={test} className="space-y-3 border-t border-ink-100 pt-5">
            <SectionHead as="h3" title="회사 사이트 서버 주소" desc="연결 확인을 먼저 하고, 세 항목을 모두 통과하면 저장할 수 있어요. 저장하면 회사 사이트 서버로 바뀌어요." />
            <div className="flex flex-wrap gap-2">
              <input id="shop-api-url" aria-label="사이트 서버 주소" className="field min-w-[260px] flex-1 font-mono" placeholder="http://10.20.30.40:8080/shop-api"
                value={url} onChange={(e) => { setUrl(e.target.value); setResult(null) }} />
              <button className="btn-ghost" disabled={busy !== null || !url.trim() || !canWrite}>{busy === 'test' ? '확인 중…' : '1. 연결 확인'}</button>
              <button type="button" className="btn-primary" onClick={save} disabled={busy !== null || !(tested || pendingSwitch) || !canWrite}>{busy === 'save' ? '저장 중…' : '2. 저장'}</button>
            </div>
            {pendingSwitch && <p role="status" className="text-sm text-amber-800">아직 내부 데이터로 읽고 있어요. 저장하면 회사 사이트 서버로 바꿔요</p>}
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
            <p className="text-xs leading-5 text-amber-800">사이트 서버를 바꾸면 확정하지 않은 결산 숫자가 바뀔 수 있어요. 확정한 결산은 그대로이고, 차이는 재무 화면에 결산 차이로 보여요.</p>
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
              <p>연결 해제는 저장한 주소를 지우고 기본 사이트 서버로 돌아가요.</p>
            </More>
          </div>
        </div>

        {state?.connection && (
          <DangerRow note={confirmClear ? '저장한 회사 사이트 서버 주소를 지우고 기본 사이트 서버로 돌아가요.' : '저장한 주소를 지울 때만 써요.'}>
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
