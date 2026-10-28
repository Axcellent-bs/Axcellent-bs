import { useEffect, useState, type FormEvent } from 'react'
import { Link } from '@/lib/nav'
import { DEV_API, vcsApi, type VcsConnection, type VcsTestResult } from '@/planning/api'

// 개발 도구 연결: 고객사 개발팀이 쓰는 형상관리·CI·배포 도구를 개발·배포 AI(dev)에 잇는 창구.
// 형상관리 연결은 레포마다 1개, 여러 개. dev 서비스(`/api/planning/vcs/connections`)에 저장되고 토큰은 끝 4자리만 온다.
// 연결이 2개 이상이면 AI가 PR마다 대상 레포를 제안하고 사람이 승인한다.
const KINDS = [
  { id: 'github', label: 'GitHub', ready: true },
  { id: 'gitlab', label: 'GitLab (사내 설치형 포함)', ready: false },
  { id: 'bitbucket', label: 'Bitbucket', ready: false },
  { id: 'svn', label: 'SVN · 읽기 전용', ready: false },
]
const KIND_LABEL: Record<string, string> = Object.fromEntries(KINDS.map((k) => [k.id, k.label]))
const SOURCE: Record<string, string> = { screen: '이 화면', env: '서버 설정 파일' }
const PERMISSIONS = ['Contents 읽기·쓰기', 'Pull requests 읽기·쓰기', 'Commit statuses 읽기']

// 형상관리 외의 연결 지점. 형상관리 연결로 함께 되는 것과 아직 없는 것을 나눠 보여 준다
const OTHERS: { name: string; tools: string; state: 'with-vcs' | 'linked' | 'planned'; to?: string }[] = [
  { name: 'CI 결과', tools: 'GitHub Actions · GitLab CI · Jenkins', state: 'with-vcs' },
  { name: '배포', tools: 'GitHub Actions · Jenkins · Argo CD', state: 'planned' },
  { name: '배포 후 확인', tools: 'DejaFix (Zabbix)', state: 'linked', to: '/system/hosts' },
  { name: '요청 가져오기', tools: 'Jira · Linear · 노션 DB', state: 'planned' },
  { name: '알림', tools: '슬랙 · Teams · 잔디', state: 'planned' },
]

const api = vcsApi(DEV_API)
type Form = { id?: string; kind: string; base_url: string; repo: string; base_branch: string; token: string; description: string; paths: string }
const EMPTY: Form = { kind: 'github', base_url: '', repo: '', base_branch: 'main', token: '', description: '', paths: '' }
const toForm = (c: VcsConnection): Form => ({ id: c.id, kind: c.kind, base_url: c.base_url ?? '', repo: c.repo, base_branch: c.base_branch || 'main', token: '', description: c.description ?? '', paths: c.paths.join(', ') })
// 담당 경로: 쉼표·줄바꿈으로 나눈다
const splitPaths = (x: string) => x.split(/[,\n]/).map((p) => p.trim()).filter(Boolean)
const failed = (id: string, message: string): VcsTestResult => ({ ok: false, code: 'unavailable', message, checked_at: new Date().toISOString(), connection_id: id })

const TONE = { ok: 'bg-emerald-50 text-emerald-700', bad: 'bg-red-50 text-red-700', wait: 'bg-amber-50 text-amber-700', none: 'bg-ink-100 text-ink-700' }

/** 형상관리 연결 목록 + 다른 개발 도구. 개발·배포 화면의 톱니바퀴 창과 시스템 관리 화면이 같이 쓴다 */
export function DevToolsPanel() {
  const [list, setList] = useState<VcsConnection[] | null>(null)
  const [offline, setOffline] = useState(false)
  const [tests, setTests] = useState<Record<string, VcsTestResult>>({})
  const [testing, setTesting] = useState<string | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.list().then(setList).catch(() => { setList([]); setOffline(true) })
  }, [])

  const runTest = async (id: string) => {
    setTesting(id)
    try {
      const r = await api.test(id)
      setTests((t) => ({ ...t, [id]: r && typeof r.message === 'string' ? r : failed(id, '확인 결과 없음') }))
    } catch (e) {
      setTests((t) => ({ ...t, [id]: failed(id, (e as Error).message) }))
    } finally {
      setTesting(null)
    }
  }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!form) return
    const editing = list?.find((c) => c.id === form.id)
    const repo = form.repo.trim()
    if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) return setError('레포는 owner/name 형태로 넣어 주세요.')
    if (!form.token && !editing?.token_set) return setError('접근 토큰을 넣어 주세요.')
    setSaving(true)
    setError(null)
    try {
      const { paths, token, ...rest } = form
      const next = await api.save({
        ...rest, repo, base_url: form.base_url.trim(), base_branch: form.base_branch.trim() || 'main',
        description: form.description.trim(), paths: splitPaths(paths), ...(token ? { token } : {}),
      })
      setList(next)
      setForm(null)
      // 저장한 연결을 바로 확인한다 (새 연결은 레포 이름으로 찾는다)
      const saved = next.find((c) => (form.id ? c.id === form.id : c.repo === repo))
      if (saved) void runTest(saved.id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const remove = async (c: VcsConnection) => {
    if (!window.confirm(`${c.repo} 연결을 지울까요?`)) return
    setError(null)
    try {
      setList(await api.remove(c.id))
      if (form?.id === c.id) setForm(null)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const status = (c: VcsConnection) => {
    const t = tests[c.id]
    if (testing === c.id) return { label: '확인 중…', tone: TONE.none }
    return t?.ok ? { label: '연결됨', tone: TONE.ok } : t ? { label: '연결 실패', tone: TONE.bad } : { label: '확인 전', tone: TONE.wait }
  }
  const connected = !!list?.length
  const kindReady = KINDS.find((k) => k.id === form?.kind)?.ready
  const editing = form?.id ? list?.find((c) => c.id === form.id) : undefined

  // 연결 추가는 목록 맨 위, 수정은 그 연결 카드 바로 아래에 연다
  const formEl = form && (
          <form onSubmit={save} className="space-y-3 rounded-xl border border-brand-200 bg-brand-50/40 p-4">
            <h3 className="font-medium">{editing ? `${editing.repo} 수정` : '연결 추가'}</h3>
            <div className="grid gap-3 md:grid-cols-[1fr_2fr]">
              <select className="field" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} aria-label="형상관리 종류">
                {KINDS.map((k) => <option key={k.id} value={k.id} disabled={!k.ready}>{k.label}{k.ready ? '' : ' — 준비 중'}</option>)}
              </select>
              <input className="field font-mono" placeholder="API 주소 (비우면 github.com, 사내 설치형은 https://<주소>/api/v3)" value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })} aria-label="API 주소" />
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              <input className="field font-mono" placeholder="레포 owner/name" value={form.repo} onChange={(e) => setForm({ ...form, repo: e.target.value })} aria-label="레포" />
              <input className="field font-mono" placeholder="병합 대상 브랜치" value={form.base_branch} onChange={(e) => setForm({ ...form, base_branch: e.target.value })} aria-label="병합 대상 브랜치" />
              <input className="field font-mono" type="password" autoComplete="off" placeholder={editing?.token_set ? '접근 토큰 (비우면 기존 토큰 유지)' : '접근 토큰'} value={form.token}
                onChange={(e) => setForm({ ...form, token: e.target.value })} aria-label="접근 토큰" />
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <input className="field" placeholder="설명 (예: 쇼핑몰 웹)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} aria-label="설명" />
              <textarea className="field font-mono" rows={2} placeholder="담당 경로 (예: shop-web/, src/)" value={form.paths} onChange={(e) => setForm({ ...form, paths: e.target.value })} aria-label="담당 경로" />
            </div>
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className="text-ink-500">토큰 권한 (이 레포만)</span>
              {PERMISSIONS.map((p) => <span key={p} className="chip bg-ink-100 text-ink-700">{p}</span>)}
            </div>
            {error && <p className="text-sm text-red-700">{error}</p>}
            <div className="flex gap-2">
              <button className="btn-primary px-5" disabled={saving || offline || !kindReady}>{saving ? '저장 중…' : '저장'}</button>
              <button type="button" className="btn-ghost" onClick={() => { setForm(null); setError(null) }}>취소</button>
            </div>
          </form>
  )

  return (
    <div className="space-y-6">
      <section className="card space-y-4 p-6">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="card-title text-lg">형상관리</h2>
          {list && <span className={`chip ${connected ? TONE.none : TONE.wait}`}>레포 {list.length}</span>}
          {list && list.length > 1 && <span className="chip bg-ink-100 text-ink-700">PR마다 AI 제안 · 사람 승인</span>}
          {offline && <span className="chip bg-amber-50 text-amber-700">개발·배포 AI에 연결할 수 없음</span>}
          <button type="button" className="btn-primary ml-auto px-4" disabled={offline || (!!form && !form.id)}
            onClick={() => { setForm({ ...EMPTY }); setError(null) }}>연결 추가</button>
        </div>

        {form && !form.id && formEl}

        {list && !list.length && !form && <p className="border-t border-ink-100 pt-4 text-sm text-ink-500">연결 없음 · 시연 데이터</p>}

        {!!list?.length && (
          <ul className="space-y-3">
            {list.map((c) => {
              const st = status(c)
              const t = tests[c.id]
              return (
                <li key={c.id} className={`rounded-xl border p-4 ${form?.id === c.id ? 'border-ink-400' : 'border-ink-100'}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono font-medium">{c.repo}</span>
                    <span className="chip bg-ink-100 text-ink-700">{KIND_LABEL[c.kind] ?? c.kind}</span>
                    <span className={`chip ${st.tone}`}>{st.label}</span>
                    <span className="chip bg-ink-100 text-ink-500">{SOURCE[c.source] ?? c.source}</span>
                    <div className="ml-auto flex gap-1.5">
                      <button type="button" className="btn-ghost" onClick={() => runTest(c.id)} disabled={!!testing || offline}>연결 확인</button>
                      <button type="button" className="btn-ghost" onClick={() => { setForm(toForm(c)); setError(null) }}>수정</button>
                      <button type="button" className="btn-quiet" onClick={() => remove(c)}>삭제</button>
                    </div>
                  </div>
                  <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
                    <div><dt className="text-xs text-ink-500">병합 대상 브랜치</dt><dd className="mt-0.5 font-mono">{c.base_branch || '—'}</dd></div>
                    <div><dt className="text-xs text-ink-500">API 주소</dt><dd className="mt-0.5 truncate font-mono text-xs leading-5">{c.base_url || '기본 주소'}</dd></div>
                    <div><dt className="text-xs text-ink-500">접근 토큰</dt><dd className="mt-0.5 font-mono">{c.token_set ? c.token_hint : '없음'}</dd></div>
                    <div><dt className="text-xs text-ink-500">설명</dt><dd className="mt-0.5">{c.description || '—'}</dd></div>
                    <div className="sm:col-span-2 lg:col-span-4">
                      <dt className="text-xs text-ink-500">담당 경로</dt>
                      <dd className="mt-1 flex flex-wrap gap-1.5">
                        {c.paths.length ? c.paths.map((p) => <span key={p} className="chip bg-ink-100 font-mono text-ink-700">{p}</span>) : <span className="text-ink-500">—</span>}
                      </dd>
                    </div>
                    {t && (
                      <div className="sm:col-span-2 lg:col-span-4">
                        <dt className="text-xs text-ink-500">마지막 확인</dt>
                        <dd className={`mt-0.5 ${t.ok ? 'text-emerald-700' : 'text-red-700'}`}>
                          {t.message}{t.code ? ` (${t.code})` : ''} · {new Date(t.checked_at).toLocaleTimeString('ko-KR')}
                        </dd>
                      </div>
                    )}
                  </dl>
                  {form?.id === c.id && <div className="mt-4">{formEl}</div>}
                </li>
              )
            })}
          </ul>
        )}

        {!form && error && <p className="text-sm text-red-700">{error}</p>}
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
