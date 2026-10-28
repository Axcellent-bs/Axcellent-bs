import { useEffect, useState, type FormEvent } from 'react'
import { Link } from '@/lib/nav'
import { DEV_API } from '@/planning/api'

// 개발 도구 연결: 고객사 개발팀이 쓰는 형상관리·CI·배포 도구를 개발·배포 AI(dev)에 잇는 창구.
// 형상관리 설정은 dev 서비스(`/api/planning/vcs`)에 저장된다. 토큰은 서버 메모리에만 있고 화면에는 끝 4자리만 온다.
type VcsConfig = {
  kind: string
  repo: string
  base_url: string
  base_branch: string
  token_set: boolean
  token_hint: string
  source: 'screen' | 'env' | 'default'
}
type VcsTest = { ok: boolean; kind: string; code: string | null; message: string; checked_at: string }

const KINDS = [
  { id: 'github', label: 'GitHub', ready: true },
  { id: 'gitlab', label: 'GitLab (사내 설치형 포함)', ready: false },
  { id: 'bitbucket', label: 'Bitbucket', ready: false },
  { id: 'svn', label: 'SVN · 읽기 전용', ready: false },
  { id: 'mock', label: '연결 안 함 (시연 데이터)', ready: true },
]
const KIND_LABEL: Record<string, string> = Object.fromEntries(KINDS.map((k) => [k.id, k.label]))
const SOURCE: Record<VcsConfig['source'], string> = { screen: '이 화면', env: '서버 설정 파일', default: '기본값' }
const PERMISSIONS = ['Contents 읽기·쓰기', 'Pull requests 읽기·쓰기', 'Commit statuses 읽기']

// 형상관리 외의 연결 지점. 형상관리 연결로 함께 되는 것과 아직 없는 것을 나눠 보여 준다
const OTHERS: { name: string; tools: string; state: 'with-vcs' | 'linked' | 'planned'; to?: string }[] = [
  { name: 'CI 결과', tools: 'GitHub Actions · GitLab CI · Jenkins', state: 'with-vcs' },
  { name: '배포', tools: 'GitHub Actions · Jenkins · Argo CD', state: 'planned' },
  { name: '배포 후 확인', tools: 'DejaFix (Zabbix)', state: 'linked', to: '/system/hosts' },
  { name: '요청 가져오기', tools: 'Jira · Linear · 노션 DB', state: 'planned' },
  { name: '알림', tools: '슬랙 · Teams · 잔디', state: 'planned' },
]

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${DEV_API}/api/planning${path}`, { cache: 'no-store', ...init })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((body as { error?: { message?: string } }).error?.message ?? `응답 ${res.status}`)
  return (body as { data: T }).data
}
const isConfig = (x: unknown): x is VcsConfig => !!x && typeof x === 'object' && 'kind' in x

/** 형상관리 설정 + 다른 개발 도구. 개발·배포 화면의 톱니바퀴 창과 시스템 관리 화면이 같이 쓴다 */
export function DevToolsPanel() {
  const [cfg, setCfg] = useState<VcsConfig | null>(null)
  const [offline, setOffline] = useState(false)
  const [form, setForm] = useState({ kind: 'github', base_url: '', repo: '', base_branch: 'dev', token: '' })
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [test, setTest] = useState<VcsTest | null>(null)
  const [testing, setTesting] = useState(false)

  const load = (c: VcsConfig) => {
    setCfg(c)
    setForm({ kind: c.kind === 'mock' ? 'github' : c.kind, base_url: c.base_url, repo: c.repo, base_branch: c.base_branch || 'dev', token: '' })
  }
  useEffect(() => {
    call<unknown>('/vcs')
      .then((d) => (isConfig(d) ? load(d) : setOffline(true)))
      .catch(() => setOffline(true))
  }, [])

  const runTest = async () => {
    setTesting(true)
    try {
      setTest(await call<VcsTest>('/vcs/test', { method: 'POST' }))
    } catch (e) {
      setTest({ ok: false, kind: cfg?.kind ?? '', code: 'unavailable', message: (e as Error).message, checked_at: new Date().toISOString() })
    } finally {
      setTesting(false)
    }
  }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (form.kind !== 'mock' && !/^[\w.-]+\/[\w.-]+$/.test(form.repo.trim())) return setError('레포는 owner/name 형태로 넣어 주세요.')
    if (form.kind !== 'mock' && !form.token && !cfg?.token_set) return setError('접근 토큰을 넣어 주세요.')
    setSaving(true)
    setError(null)
    try {
      load(await call<VcsConfig>('/vcs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) }))
      setTest(null)
      await runTest()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const connected = cfg && cfg.kind !== 'mock'
  const status = !cfg ? null : !connected ? { label: '연결 안 함', tone: 'bg-ink-100 text-ink-700' }
    : test?.ok ? { label: '연결됨', tone: 'bg-emerald-50 text-emerald-700' }
    : test ? { label: '연결 실패', tone: 'bg-red-50 text-red-700' }
    : { label: '확인 전', tone: 'bg-amber-50 text-amber-700' }
  const kindReady = KINDS.find((k) => k.id === form.kind)?.ready

  return (
    <div className="space-y-6">
      <section className="card space-y-5 p-6">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="card-title text-lg">형상관리</h2>
          {status && <span className={`chip ${status.tone}`}>{testing ? '확인 중…' : status.label}</span>}
          {offline && <span className="chip bg-amber-50 text-amber-700">개발·배포 AI에 연결할 수 없음</span>}
          <span className="ml-auto text-xs text-ink-500">개발·배포 AI가 PR 만들기·검수·머지·역추적에 씁니다</span>
        </div>

        {cfg && (
          <dl className="grid gap-x-6 gap-y-3 border-t border-ink-100 pt-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
            <div><dt className="text-xs text-ink-500">종류</dt><dd className="mt-0.5 font-medium">{KIND_LABEL[cfg.kind] ?? cfg.kind}</dd></div>
            <div><dt className="text-xs text-ink-500">레포</dt><dd className="mt-0.5 font-mono">{cfg.repo || '—'}</dd></div>
            <div><dt className="text-xs text-ink-500">병합 대상 브랜치</dt><dd className="mt-0.5 font-mono">{cfg.base_branch || '—'}</dd></div>
            <div><dt className="text-xs text-ink-500">API 주소</dt><dd className="mt-0.5 truncate font-mono text-xs leading-5">{cfg.base_url || '기본 주소'}</dd></div>
            <div><dt className="text-xs text-ink-500">접근 토큰</dt><dd className="mt-0.5 font-mono">{cfg.token_set ? cfg.token_hint : '없음'}</dd></div>
            <div><dt className="text-xs text-ink-500">설정 출처</dt><dd className="mt-0.5">{SOURCE[cfg.source]}</dd></div>
            {test && (
              <div className="sm:col-span-2 lg:col-span-3">
                <dt className="text-xs text-ink-500">마지막 확인</dt>
                <dd className={`mt-0.5 ${test.ok ? 'text-emerald-700' : 'text-red-700'}`}>
                  {test.message}{test.code ? ` (${test.code})` : ''} · {new Date(test.checked_at).toLocaleTimeString('ko-KR')}
                </dd>
              </div>
            )}
          </dl>
        )}

        <form onSubmit={save} className="space-y-3 border-t border-ink-100 pt-4">
          <div className="grid gap-3 md:grid-cols-[1fr_2fr]">
            <select className="field" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} aria-label="형상관리 종류">
              {KINDS.map((k) => <option key={k.id} value={k.id} disabled={!k.ready}>{k.label}{k.ready ? '' : ' — 준비 중'}</option>)}
            </select>
            <input className="field font-mono" placeholder="API 주소 (비우면 https://api.github.com, 사내 설치형은 https://<주소>/api/v3)" value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })} aria-label="API 주소" disabled={form.kind === 'mock'} />
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            <input className="field font-mono" placeholder="레포 owner/name" value={form.repo} onChange={(e) => setForm({ ...form, repo: e.target.value })} aria-label="레포" disabled={form.kind === 'mock'} />
            <input className="field font-mono" placeholder="병합 대상 브랜치" value={form.base_branch} onChange={(e) => setForm({ ...form, base_branch: e.target.value })} aria-label="병합 대상 브랜치" disabled={form.kind === 'mock'} />
            <input className="field font-mono" type="password" autoComplete="off" placeholder={cfg?.token_set ? '접근 토큰 (비우면 기존 토큰 유지)' : '접근 토큰'} value={form.token}
              onChange={(e) => setForm({ ...form, token: e.target.value })} aria-label="접근 토큰" disabled={form.kind === 'mock'} />
          </div>
          {form.kind !== 'mock' && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className="text-ink-500">토큰 권한 (레포 1개만)</span>
              {PERMISSIONS.map((p) => <span key={p} className="chip bg-ink-100 text-ink-700">{p}</span>)}
            </div>
          )}
          {error && <p className="text-sm text-red-700">{error}</p>}
          <div className="flex gap-2">
            <button className="btn-primary px-5" disabled={saving || offline || !kindReady}>{saving ? '저장 중…' : '저장'}</button>
            <button type="button" className="btn-ghost" onClick={runTest} disabled={testing || offline || !cfg}>연결 확인</button>
          </div>
        </form>
      </section>

      <section className="card p-6">
        <h2 className="card-title">다른 개발 도구</h2>
        <ul className="mt-3 divide-y divide-ink-100">
          {OTHERS.map((o) => (
            <li key={o.name} className="grid grid-cols-[120px_1fr_auto] items-center gap-3 py-3 text-sm">
              <span className="font-medium">{o.name}</span>
              <span className="text-ink-500">{o.tools}</span>
              {o.state === 'with-vcs' ? (
                <span className={`chip ${connected ? 'bg-emerald-50 text-emerald-700' : 'bg-ink-100 text-ink-700'}`}>{connected ? '형상관리로 함께 읽음' : '형상관리 연결 뒤'}</span>
              ) : o.state === 'linked' ? (
                <Link href={o.to ?? '#'} className="chip bg-emerald-50 text-emerald-700">연결됨 · 사내 PC 연결</Link>
              ) : (
                <span className="chip bg-ink-100 text-ink-500">준비 중</span>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

export default function DevTools() {
  return (
    <div className="space-y-6 px-8 py-8">
      <header>
        <p className="text-sm text-ink-500">시스템 관리</p>
        <h1 className="text-2xl font-semibold">개발 도구 연결</h1>
      </header>
      <DevToolsPanel />
    </div>
  )
}

/** 개발·배포 화면 오른쪽 위 톱니바퀴로 여는 창 */
export function DevToolsDialog({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 pt-16" onClick={onClose} role="presentation">
      <div role="dialog" aria-modal="true" aria-label="개발 도구 연결" className="w-full max-w-4xl space-y-4 rounded-2xl p-5 shadow-xl"
        style={{ background: 'rgb(var(--canvas))' }} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">개발 도구 연결</h2>
          <button className="btn-quiet px-2 py-1" onClick={onClose} aria-label="닫기">✕</button>
        </div>
        <DevToolsPanel />
      </div>
    </div>
  )
}
