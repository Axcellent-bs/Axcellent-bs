// 연결 페이지 › 문서 › 노션 (관리자). 기본은 "노션 계정 연결"(OAuth: 노션 로그인 → 페이지 고르기),
// 회사가 노션 공개 통합을 아직 등록하지 않았으면 통합 토큰 직접 입력. 기획의 "노션에서 가져오기"가 이 연결을 쓴다.
// 토큰은 기획 서버에만 저장되고 화면에는 끝 4자리와 확인한 워크스페이스 이름만 보인다.
import { useEffect, useState, type FormEvent } from 'react'
import { notionApi, type NotionConnection } from '@/lib/planning'

const TONE = { ok: 'bg-emerald-50 text-emerald-700', none: 'bg-ink-100 text-ink-700', wait: 'bg-amber-50 text-amber-700' }
const BACK: Record<string, string> = {
  ok: '노션 계정을 연결했어요.',
  denied: '노션에서 연결을 취소했어요.',
  failed: '노션 계정 연결에 실패했어요. 다시 시도해 주세요.',
  bad_state: '연결 요청이 만료됐거나 올바르지 않아요. 다시 시도해 주세요.',
}
const REASON: Record<string, string> = {
  invalid_client: '서버 .env의 Client ID·Secret을 확인해 주세요 (따옴표·공백 없이).',
  invalid_grant: '노션에 등록한 Redirect URI가 서버 주소와 글자까지 같은지 확인하고 다시 눌러 주세요.',
  unauthorized_client: '노션 개발자 포털에서 OAuth 연결 설정을 확인해 주세요.',
  invalid_request: '연결 요청이 올바르지 않아요. 다시 눌러 주세요.',
}
const kst = (iso: string | null) => (iso ? new Date(iso).toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '')

export function NotionConnect() {
  const [conn, setConn] = useState<NotionConnection | null>(null)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [offline, setOffline] = useState(false)

  const [back, setBack] = useState<string | null>(null) // 노션 계정 연결에서 돌아온 결과
  useEffect(() => {
    notionApi.get().then(setConn).catch(() => setOffline(true))
    const q = new URLSearchParams(window.location.search)
    const r = q.get('notion')
    if (r) {
      const why = (q.get('reason') ?? '').replace(/[^a-z0-9_]/g, '').slice(0, 40)
      setBack(why ? `${r}:${why}` : r)
      window.history.replaceState(null, '', window.location.pathname) // 주소에서 결과를 지운다
    }
  }, [])
  const startOauth = async () => {
    setBusy(true)
    setError(null)
    try { window.location.href = (await notionApi.oauthStart()).url } catch (x) { setError((x as Error).message); setBusy(false) }
  }

  const act = async (f: () => Promise<NotionConnection>) => {
    setBusy(true)
    setError(null)
    try { setConn(await f()); setToken('') } catch (x) { setError((x as Error).message) } finally { setBusy(false) }
  }
  const save = (e: FormEvent) => {
    e.preventDefault()
    if (!token.trim()) return setError('노션 토큰을 넣어 주세요.')
    void act(() => notionApi.save(token.trim()))
  }

  const status = !conn ? null
    : conn.source === 'saved' ? { label: `연결됨 ${conn.hint}`, tone: TONE.ok }
    : conn.source === 'env' ? { label: `서버 설정 ${conn.hint}`, tone: TONE.ok }
    : { label: '연결 안 됨', tone: TONE.none }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="card-title text-lg">노션</h2>
        {status && <span className={`chip ${status.tone}`}>{status.label}</span>}
        {conn?.workspace && <span className="chip bg-ink-100 text-ink-700">{conn.workspace}</span>}
        {offline && <span className={`chip ${TONE.wait}`}>기획 AI에 연결할 수 없음</span>}
        {conn?.connected && (
          <div className="ml-auto flex gap-1.5">
            <button type="button" className="btn-ghost" disabled={busy} onClick={() => void act(() => notionApi.check())}>연결 확인</button>
            {conn.source === 'saved' && (
              <button type="button" className="btn-quiet" disabled={busy}
                onClick={() => { if (window.confirm('노션 토큰을 지울까요? 기획의 "노션에서 가져오기"가 멈춰요.')) void act(() => notionApi.remove()) }}>지우기</button>
            )}
          </div>
        )}
      </div>

      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
        <div><dt className="text-xs text-ink-500">쓰는 곳</dt><dd className="mt-0.5">요청·기획서 › 문서·기획서 › 노션에서 가져오기</dd></div>
        <div><dt className="text-xs text-ink-500">권한</dt><dd className="mt-0.5">읽기만 (공유한 페이지)</dd></div>
        <div><dt className="text-xs text-ink-500">마지막 확인</dt><dd className="mt-0.5">{kst(conn?.checked_at ?? null) || '—'}</dd></div>
      </dl>

      {back && <p className={`text-sm ${back === 'ok' ? 'text-emerald-700' : 'text-red-700'}`}>{BACK[back.split(':')[0]] ?? '노션 계정 연결 결과를 알 수 없어요.'}{back.includes(':') && <> <span className="mono">({back.split(':')[1]})</span> {REASON[back.split(':')[1]] ?? ''}</>}</p>}

      <div className="flex flex-wrap items-center gap-3 border-t border-ink-100 pt-4">
        <button type="button" className="btn-primary px-4" disabled={busy || offline || !conn?.oauth_ready} onClick={() => void startOauth()}>
          {conn?.source === 'saved' ? '다른 노션 계정으로 다시 연결' : '노션 계정 연결'}
        </button>
        <span className="text-xs text-ink-500">{conn?.oauth_ready ? '노션 로그인 → 가져올 페이지 고르기 → 돌아오면 끝' : '노션 공개 통합이 아직 등록되지 않았어요 (아래 직접 입력은 지금도 돼요)'}</span>
      </div>
      {conn && !conn.oauth_ready && (
        <ol className="list-decimal space-y-0.5 pl-5 text-xs text-ink-500">
          <li><a className="underline" href="https://app.notion.com/developers/connections" target="_blank" rel="noreferrer">노션 개발자 포털</a> › <b>New integration</b> › OAuth 연결 만들기 (워크스페이스 소유자)</li>
          <li>설치 범위 <b>Any workspace</b> (만든 뒤 못 바꿈), 기능은 <b>콘텐츠 읽기</b>만</li>
          <li>OAuth 설정의 Redirect URI: <span className="mono">http://localhost:8101/api/planning/notion/oauth/callback</span> (실서버는 https 주소)</li>
          <li><b>Configuration</b> 탭의 Client ID·Secret을 서버 .env <span className="mono">PLANNING_NOTION_CLIENT_ID</span>·<span className="mono">PLANNING_NOTION_CLIENT_SECRET</span>에 넣고 서버 다시 켜기</li>
        </ol>
      )}

      <details className="text-sm">
        <summary className="cursor-pointer text-xs text-ink-500">직접 입력 (내부 통합 토큰)</summary>
      <form className="mt-2 flex flex-wrap items-end gap-2" onSubmit={save}>
        <label className="min-w-[280px] flex-1 space-y-1 text-sm">
          <span className="text-xs text-ink-500">통합 토큰 (notion.so/profile/integrations › 내부 통합 › 시크릿)</span>
          <input className="field mono w-full text-xs" type="password" autoComplete="off" value={token}
            placeholder={conn?.connected ? '바꾸려면 새 토큰' : 'ntn_…'} onChange={(e) => setToken(e.target.value)} />
        </label>
        <button type="submit" className="btn-ghost px-4" disabled={busy || offline}>{busy ? '확인 중…' : '토큰으로 연결'}</button>
      </form>
      <p className="mt-1 text-xs text-ink-500">내부 통합은 가져올 노션 페이지마다 ··· › 연결 › 이 통합을 추가해야 읽혀요.</p>
      </details>
      {error && <p className="text-sm text-red-700">{error}</p>}
    </section>
  )
}
