import { useEffect, useState, type FormEvent } from 'react'
import { Link } from '@/lib/nav'
import { DEV_API, codingSettingsApi, useMe, vcsApi, type CodingStep, type CodingSetting, type CodingSettings, type VcsAuth, type VcsConnection, type VcsInventory, type VcsTestResult } from '@/lib/planning'
import { UsageBar } from '@/components/dev/coding'

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
const PERMISSIONS = ['Contents 읽기·쓰기', 'Pull requests 읽기·쓰기', 'Issues 읽기·쓰기', 'Commit statuses 읽기']
// 인증 방식. 회사 레포는 조직이 설치하는 GitHub App, 사람 토큰은 개인 레포·시험용
const AUTHS: { id: VcsAuth; label: string }[] = [{ id: 'app', label: 'GitHub App (조직 설치)' }, { id: 'token', label: '개인 접근 토큰' }]

// 형상관리 외의 연결 지점. 형상관리 연결로 함께 되는 것과 아직 없는 것을 나눠 보여 준다
const OTHERS: { name: string; tools: string; state: 'with-vcs' | 'linked' | 'planned'; to?: string }[] = [
  { name: 'CI 결과', tools: 'GitHub Actions · GitLab CI · Jenkins', state: 'with-vcs' },
  { name: '배포', tools: 'GitHub Actions · Jenkins · Argo CD', state: 'planned' },
  { name: '배포 후 확인', tools: 'DejaFix (Zabbix)', state: 'linked', to: '/system/hosts' },
  { name: '요청 가져오기', tools: 'Jira · Linear · 노션 DB', state: 'planned' },
  { name: '알림', tools: '슬랙 · Teams · 잔디', state: 'planned' },
]

const api = vcsApi(DEV_API)
type Form = { id?: string; kind: string; base_url: string; repo: string; base_branch: string; token: string; description: string; paths: string; mount: string; auth: VcsAuth; app_id: string; installation_id: string; private_key: string; key_name: string }
const EMPTY: Form = { kind: 'github', base_url: '', repo: '', base_branch: 'main', token: '', description: '', paths: '', mount: '', auth: 'app', app_id: '', installation_id: '', private_key: '', key_name: '' }
const toForm = (c: VcsConnection): Form => ({ id: c.id, kind: c.kind, base_url: c.base_url ?? '', repo: c.repo, base_branch: c.base_branch || 'main', token: '', description: c.description ?? '', paths: c.paths.join(', '), mount: c.mount ?? '', auth: c.auth ?? 'token', app_id: c.app_id ?? '', installation_id: c.installation_id ?? '', private_key: '', key_name: '' })
// 담당 경로: 쉼표·줄바꿈으로 나눈다
const splitPaths = (x: string) => x.split(/[,\n]/).map((p) => p.trim()).filter(Boolean)
const failed = (id: string, message: string): VcsTestResult => ({ ok: false, code: 'unavailable', message, checked_at: new Date().toISOString(), connection_id: id })

const TONE = { ok: 'bg-emerald-50 text-emerald-700', bad: 'bg-red-50 text-red-700', wait: 'bg-amber-50 text-amber-700', none: 'bg-ink-100 text-ink-700' }

// 연결 대장 상태 (FR-AG-012 연결 시 보안 조건)
const INV: Record<VcsInventory['status'], { label: string; tone: string }> = {
  hosted: { label: '클라우드 · 제공사 패치', tone: TONE.none },
  ok: { label: '패치 기준 충족', tone: TONE.ok },
  outdated: { label: '패치 필요', tone: TONE.bad },
  no_policy: { label: '패치 기준 없음', tone: TONE.wait },
  unknown: { label: '점검 전', tone: TONE.none },
  error: { label: '점검 실패', tone: TONE.bad },
}
// 취약점 소식 판정 (KEV·NVD)
const VULN: Record<string, { label: string; tone: string }> = {
  emergency: { label: '긴급 · 실제 악용 중', tone: TONE.bad },
  warning: { label: '알려진 취약점', tone: TONE.wait },
  clear: { label: '취약점 해당 없음', tone: TONE.ok },
  feed_failed: { label: '취약점 점검 실패', tone: TONE.wait },
}
const PRODUCT: Record<string, string> = { 'github.com': 'github.com', ghes: 'GitHub Enterprise Server', gitlab: 'GitLab', mock: '시연용' }
const when = (iso: string) => (iso ? new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—')

/** 연결 하나의 보안 점검 줄: 서버·버전·토큰 권한·만료·마지막 점검, 경고 */
function InventoryRow({ inv }: { inv?: VcsInventory }) {
  if (!inv) return null
  const st = INV[inv.status] ?? INV.unknown
  return (
    <div className="sm:col-span-2 lg:col-span-4 rounded-lg border border-ink-100 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-ink-500">보안 점검</span>
        <span className={`chip ${st.tone}`}>{st.label}</span>
        {inv.product && <span className="chip bg-ink-100 text-ink-700">{PRODUCT[inv.product] ?? inv.product}{inv.version ? ` ${inv.version}` : ''}</span>}
        {inv.min_version && <span className="chip bg-ink-100 text-ink-500">기준 {inv.min_version}</span>}
        {inv.vuln && VULN[inv.vuln] && <span className={`chip ${VULN[inv.vuln].tone}`}>{VULN[inv.vuln].label}{inv.vuln === 'warning' ? ` ${inv.vuln_total ?? 0}건` : ''}</span>}
        {inv.safety_hold && <span className={`chip ${inv.safety_hold.hold ? TONE.bad : TONE.none}`}>{inv.safety_hold.hold ? '쓰기 보류' : '보류 해제'} · 안전 서비스 {inv.safety_hold.sent ? '알림 보냄' : '알림 대기'}</span>}
        <span className="ml-auto text-xs text-ink-500">마지막 점검 {when(inv.checked_at)}</span>
      </div>
      <div className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        <div><span className="text-xs text-ink-500">토큰 권한 </span>{inv.scopes.length ? inv.scopes.join(', ') : inv.scope_note || '—'}</div>
        <div><span className="text-xs text-ink-500">토큰 만료 </span>{inv.token_expires || '—'}</div>
        {!!inv.kev_cves?.length && <div className="sm:col-span-2"><span className="text-xs text-ink-500">근거 CVE (KEV) </span><span className="mono">{inv.kev_cves.join(', ')}</span></div>}
        {inv.vuln === 'warning' && !!inv.vuln_cves?.length && <div className="sm:col-span-2"><span className="text-xs text-ink-500">걸린 CVE </span><span className="mono">{inv.vuln_cves.join(', ')}{(inv.vuln_total ?? 0) > inv.vuln_cves.length ? ' …' : ''}</span></div>}
      </div>
      {(inv.warnings.length > 0 || inv.error || inv.vuln_error || (inv.safety_hold && !inv.safety_hold.sent)) && (
        <ul className="mt-2 space-y-0.5 text-sm text-red-700">
          {inv.error && <li>{inv.error}{inv.error_code ? ` (${inv.error_code})` : ''}</li>}
          {inv.vuln_error && <li>{inv.vuln_error}</li>}
          {inv.safety_hold && !inv.safety_hold.sent && inv.safety_hold.error && <li>{inv.safety_hold.error} → 다음 점검 때 다시 보냄</li>}
          {inv.warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      )}
    </div>
  )
}

// 요청 작업 코딩 방식: 기획에서 넘어온 작업을 누가 코딩하나 (회사 설정)
const CODING_MODES: { id: CodingSetting; label: string }[] = [
  { id: 'auto', label: '자동' },
  { id: 'agent', label: '서버 AI' },
  { id: 'subscription', label: '내 Claude' },
]
const codingApi = codingSettingsApi(DEV_API)
const MCP_URL = `${DEV_API.replace(/\/$/, '')}/api/planning/mcp`
const MCP_ADD = `claude mcp add --transport http axcellent-dev ${MCP_URL} --header "Authorization: Bearer <내 토큰>"`

function CopyLine({ text }: { text: string }) {
  const [done, setDone] = useState(false)
  const copy = () => {
    navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500) }).catch(() => {})
  }
  return (
    <div className="flex items-start gap-2">
      <code className="mono min-w-0 flex-1 break-all rounded-md bg-ink-50 px-3 py-2 text-xs">{text}</code>
      <button type="button" className="btn-ghost shrink-0 px-2 py-1 text-xs" onClick={copy}>{done ? '복사됨' : '복사'}</button>
    </div>
  )
}

/** 내 Claude(MCP 작업 대기열 서버) 연결하기: 내 PC의 Claude Code에 작업 대기열 서버를 붙이는 순서 */
function ClaudeConnect({ connected }: { connected: string[] }) {
  const me = useMe()
  const mine = me?.name ? me.claude_connected : null
  return (
    <div className="space-y-3 rounded-lg border border-ink-100 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">내 Claude 연결</h3>
        {me?.name && <span className={`chip ${mine ? TONE.ok : TONE.none}`}>{me.name} · {mine ? '연결됨' : '연결 없음'}</span>}
        <span className={`chip ${connected.length ? TONE.ok : TONE.none}`}>{connected.length ? `팀 연결 ${connected.join(', ')}` : '팀 연결 없음'}</span>
        <span className="text-xs text-ink-500">최근 7일 안에 작업을 본 개발자 · 내 Claude 작업은 맡긴 사람의 Claude만 가져감</span>
      </div>
      <ol className="list-decimal space-y-3 pl-5 text-sm">
        <li className="space-y-1">
          <span>내 사람 토큰 받기</span>
          <span className="block text-xs text-ink-500">운영자에게 받기 · 저장소에 커밋 금지</span>
        </li>
        <li className="space-y-1">
          <span>내 PC 터미널에서 작업 대기열 서버 붙이기</span>
          <CopyLine text={MCP_ADD} />
        </li>
        <li className="space-y-1">
          <span>확인</span>
          <CopyLine text="claude mcp list" />
        </li>
        <li className="space-y-1">
          <span>레포 폴더에서 Claude Code로 작업 가져가기</span>
          <CopyLine text="axcellent-dev에서 작업 목록 보여 줘. 하나 가져가서 명세대로 구현하고 PR 올려 줘." />
        </li>
      </ol>
    </div>
  )
}

/** 요청 작업 코딩 방식 + 서버 AI 체험 한도 */
/** 고객사 API 키(BYOK)와 서버 AI 월 예산. 키는 보내기만 하고 화면에는 끝 4자리만 */
function CompanyKeyBudget({ cfg, onSaved }: { cfg: CodingSettings; onSaved: (c: CodingSettings) => void }) {
  const key = cfg.company_key
  const [form, setForm] = useState({ api_key: '', base_url: key?.base_url ?? '', model: key?.model ?? '' })
  const [budget, setBudget] = useState(cfg.budget?.limit != null ? String(cfg.budget.limit) : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const act = async (f: () => Promise<CodingSettings>) => {
    setBusy(true)
    setError(null)
    try { onSaved(await f()); setForm((x) => ({ ...x, api_key: '' })) } catch (x) { setError((x as Error).message) } finally { setBusy(false) }
  }
  const saveKey = (e: FormEvent) => {
    e.preventDefault()
    if (!form.api_key && !key?.key_set) return setError('고객사 API 키를 넣어 주세요.')
    void act(() => codingApi.setCompanyKey({ ...(form.api_key ? { api_key: form.api_key } : {}), base_url: form.base_url.trim(), model: form.model.trim() }))
  }
  const saveBudget = (e: FormEvent) => {
    e.preventDefault()
    const n = budget.trim() === '' ? null : Number(budget.replace(/,/g, ''))
    if (n !== null && (!Number.isInteger(n) || n < 0)) return setError('월 예산은 0 이상 정수(토큰)로 넣어 주세요.')
    void act(() => codingApi.setBudget(n))
  }
  const b = cfg.budget
  return (
    <div className="space-y-3 rounded-lg border border-ink-100 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">고객사 API 키 · 월 예산</h3>
        <span className={`chip ${key?.key_set ? TONE.ok : TONE.none}`}>{key?.key_set ? `고객사 키 ${key.key_hint}` : '서버 기본 키 (체험)'}</span>
        {key?.key_set && <button type="button" className="btn-quiet ml-auto px-2 py-1 text-xs" disabled={busy} onClick={() => { if (window.confirm('고객사 API 키를 지울까요? 그 뒤로는 서버 기본 키와 체험 한도로 돌아요.')) void act(() => codingApi.deleteCompanyKey()) }}>키 지우기</button>}
      </div>
      <form className="grid gap-2 sm:grid-cols-[2fr_2fr_1.5fr_auto] sm:items-end" onSubmit={saveKey}>
        <label className="space-y-1 text-sm"><span className="text-xs text-ink-500">API 키</span>
          <input className="field mono w-full text-xs" type="password" autoComplete="off" value={form.api_key} placeholder={key?.key_set ? '비우면 지금 키 그대로' : 'sk-…'} onChange={(e) => setForm({ ...form, api_key: e.target.value })} /></label>
        <label className="space-y-1 text-sm"><span className="text-xs text-ink-500">주소 (비우면 서버 설정)</span>
          <input className="field mono w-full text-xs" value={form.base_url} placeholder="https://…" onChange={(e) => setForm({ ...form, base_url: e.target.value })} /></label>
        <label className="space-y-1 text-sm"><span className="text-xs text-ink-500">모델 (비우면 서버 설정)</span>
          <input className="field mono w-full text-xs" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} /></label>
        <button type="submit" className="btn-ghost px-3 py-1.5 text-xs" disabled={busy}>{key?.key_set ? '키 바꾸기' : '키 넣기'}</button>
      </form>
      <form className="flex flex-wrap items-end gap-3" onSubmit={saveBudget}>
        <label className="space-y-1 text-sm"><span className="text-xs text-ink-500">월 예산 (토큰, 비우면 없음)</span>
          <input className="field mono w-44 text-xs" inputMode="numeric" value={budget} placeholder="예: 5000000" onChange={(e) => setBudget(e.target.value)} /></label>
        <button type="submit" className="btn-ghost px-3 py-1.5 text-xs" disabled={busy}>예산 저장</button>
        {b && (b.limit != null
          ? <UsageBar used={b.used} limit={b.limit} label={`${b.month}`} title="이번 달 서버 AI 코딩이 쓴 토큰 / 월 예산" />
          : <span className="text-xs text-ink-500">{b.month} 사용 <span className="mono">{b.used.toLocaleString()}</span> 토큰 · 예산 없음</span>)}
      </form>
      {error && <p className="text-sm text-red-700">{error}</p>}
    </div>
  )
}

// 서버 AI 코딩 단계별 모델: 비우면 기본 모델
const STEP_LABEL: { id: CodingStep; label: string }[] = [
  { id: 'plan', label: '파일 고르기' },
  { id: 'write', label: '코드 쓰기' },
  { id: 'review', label: '리뷰' },
]

function StepModels({ cfg, onSaved }: { cfg: CodingSettings; onSaved: (c: CodingSettings) => void }) {
  const [draft, setDraft] = useState<Partial<Record<CodingStep, string>>>(cfg.models ?? {})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const changed = STEP_LABEL.some((s) => (draft[s.id] ?? '') !== (cfg.models?.[s.id] ?? ''))
  const save = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try { onSaved(await codingApi.setModels(draft)) } catch (x) { setError((x as Error).message) } finally { setBusy(false) }
  }
  return (
    <form className="space-y-2 rounded-lg border border-ink-100 p-4" onSubmit={save}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">단계별 모델</h3>
        <span className="text-xs text-ink-500">서버 AI만 · 비우면 기본 {cfg.model_default ? <span className="mono">{cfg.model_default}</span> : '모델'}</span>
        <button type="submit" className="btn-ghost ml-auto px-3 py-1 text-xs" disabled={busy || !changed}>{busy ? '저장 중…' : '저장'}</button>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        {STEP_LABEL.map((s) => (
          <label key={s.id} className="space-y-1 text-sm">
            <span className="text-xs text-ink-500">{s.label}</span>
            <input className="field mono w-full text-xs" value={draft[s.id] ?? ''} placeholder={cfg.model_default ?? '기본 모델'}
              onChange={(e) => setDraft((d) => ({ ...d, [s.id]: e.target.value.trim() }))} maxLength={100} />
          </label>
        ))}
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
    </form>
  )
}

function CodingModeSection() {
  const [cfg, setCfg] = useState<CodingSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { codingApi.get().then(setCfg).catch(() => setError('개발 AI에 연결할 수 없음')) }, [])
  const choose = async (mode: CodingSetting) => {
    setBusy(true)
    setError(null)
    try { setCfg(await codingApi.set(mode)) } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <section className="card space-y-4 p-6">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="card-title text-lg">요청 작업 코딩</h2>
        {cfg && <span className={`chip ${cfg.resolved === 'agent' ? TONE.none : TONE.ok}`}>지금: {cfg.resolved === 'agent' ? '서버 AI' : '내 Claude'}</span>}
        {error && <span className="chip bg-amber-50 text-amber-700">{error}</span>}
      </div>
      <div className="seg-toggle" role="group" aria-label="요청 작업 코딩 방식">
        {CODING_MODES.map((m) => (
          <button key={m.id} type="button" aria-pressed={cfg?.mode === m.id} disabled={!cfg || busy} onClick={() => void choose(m.id)}>{m.label}</button>
        ))}
      </div>
      {cfg && (
        <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-2 text-sm">
          <dt className="text-ink-500">이유</dt><dd>{cfg.why}</dd>
          {!cfg.company_key?.key_set && <><dt className="text-ink-500">서버 AI 체험</dt>
          <dd><UsageBar used={cfg.trial_used} limit={cfg.trial_limit} wide title="서버 AI(기본 키)가 코딩에 쓴 토큰 / 체험 한도" /></dd></>}
        </dl>
      )}
      {cfg && <CompanyKeyBudget cfg={cfg} onSaved={setCfg} />}
      {cfg && <StepModels cfg={cfg} onSaved={setCfg} />}
      <ClaudeConnect connected={cfg?.agents_connected ?? []} />
    </section>
  )
}

/** 형상관리 연결 목록 + 다른 개발 도구. 개발·배포 화면의 톱니바퀴 창과 시스템 관리 화면이 같이 쓴다 */
export function DevToolsPanel() {
  const [list, setList] = useState<VcsConnection[] | null>(null)
  const [offline, setOffline] = useState(false)
  const [tests, setTests] = useState<Record<string, VcsTestResult>>({})
  const [testing, setTesting] = useState<string | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [inv, setInv] = useState<Record<string, VcsInventory>>({})
  const [checking, setChecking] = useState(false)
  const putInv = (rows: VcsInventory[]) => setInv(Object.fromEntries(rows.map((r) => [r.connection_id, r])))

  useEffect(() => {
    api.list().then(setList).catch(() => { setList([]); setOffline(true) })
    api.inventory().then(putInv).catch(() => {})
  }, [])

  const checkAll = async () => {
    setChecking(true)
    try { putInv(await api.checkAll()) } catch (e) { setError((e as Error).message) } finally { setChecking(false) }
  }

  const runTest = async (id: string) => {
    setTesting(id)
    try {
      const r = await api.test(id)
      setTests((t) => ({ ...t, [id]: r && typeof r.message === 'string' ? r : failed(id, '확인 결과 없음') }))
      const got = r?.inventory
      if (got) setInv((m) => ({ ...m, [id]: got }))
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
    const app = form.auth === 'app'
    if (app) {
      if (!/^\d+$/.test(form.app_id.trim()) || !/^\d+$/.test(form.installation_id.trim())) return setError('앱 ID와 설치 ID는 숫자로 넣어 주세요.')
      if (!form.private_key && !editing?.key_set) return setError('GitHub App 개인 키(.pem) 파일을 골라 주세요.')
    } else if (!form.token && !editing?.token_set) return setError('접근 토큰을 넣어 주세요.')
    setSaving(true)
    setError(null)
    try {
      const { paths, token, private_key, key_name: _name, app_id, installation_id, ...rest } = form
      const next = await api.save({
        ...rest, repo, base_url: form.base_url.trim(), base_branch: form.base_branch.trim() || 'main',
        description: form.description.trim(), paths: splitPaths(paths), mount: form.mount.trim(),
        ...(app ? { app_id: app_id.trim(), installation_id: installation_id.trim(), ...(private_key ? { private_key } : {}) } : token ? { token } : {}),
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

  // 개인 키는 파일에서 바로 읽어 보낸다. 화면에는 파일 이름만 남기고 내용은 보여 주지 않는다
  const pickKey = (file?: File) => {
    if (!file || !form) return
    if (file.size > 16_000) return setError('개인 키 파일이 너무 커요. GitHub App에서 받은 .pem 파일을 골라 주세요.')
    void file.text().then((text) => {
      if (!text.includes('PRIVATE KEY')) return setError('GitHub App 개인 키(.pem) 파일이 아니에요.')
      setError(null)
      setForm((f) => (f ? { ...f, private_key: text, key_name: file.name } : f))
    })
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
              <input className="field" placeholder="API 주소 (비우면 github.com, 사내 설치형은 https://<주소>/api/v3)" value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })} aria-label="API 주소" />
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              <input className="field" placeholder="레포 owner/name" value={form.repo} onChange={(e) => setForm({ ...form, repo: e.target.value })} aria-label="레포" />
              <input className="field" placeholder="병합 대상 브랜치" value={form.base_branch} onChange={(e) => setForm({ ...form, base_branch: e.target.value })} aria-label="병합 대상 브랜치" />
              {form.auth === 'token' && (
                <input className="field" type="password" autoComplete="off" placeholder={editing?.token_set ? '접근 토큰 (비우면 기존 토큰 유지)' : '접근 토큰'} value={form.token}
                  onChange={(e) => setForm({ ...form, token: e.target.value })} aria-label="접근 토큰" />
              )}
            </div>
            {form.kind === 'github' && (
              <div className="space-y-2">
                <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="인증 방식">
                  {AUTHS.map((a) => (
                    <button key={a.id} type="button" role="radio" aria-checked={form.auth === a.id} onClick={() => setForm({ ...form, auth: a.id })}
                      className={`chip py-1 ${form.auth === a.id ? 'bg-brand-600 !text-white' : 'bg-ink-100 text-ink-700'}`}>{a.label}</button>
                  ))}
                </div>
                {form.auth === 'app' && (
                  <div className="grid gap-3 md:grid-cols-3">
                    <input className="field" inputMode="numeric" placeholder="앱 ID (App ID)" value={form.app_id} onChange={(e) => setForm({ ...form, app_id: e.target.value })} aria-label="앱 ID" />
                    <input className="field" inputMode="numeric" placeholder="설치 ID (Installation ID)" value={form.installation_id} onChange={(e) => setForm({ ...form, installation_id: e.target.value })} aria-label="설치 ID" />
                    <label className="field flex cursor-pointer items-center gap-2 text-sm">
                      <input type="file" accept=".pem,application/x-pem-file" className="sr-only" onChange={(e) => pickKey(e.target.files?.[0])} aria-label="개인 키 파일" />
                      <span className="chip bg-ink-100 text-ink-700">개인 키 .pem</span>
                      <span className="truncate text-ink-500">{form.key_name || (editing?.key_set ? '넣음 · 바꾸려면 다시 고르기' : '파일 고르기')}</span>
                    </label>
                  </div>
                )}
              </div>
            )}
            <div className="grid gap-3 md:grid-cols-2">
              <input className="field" placeholder="설명 (예: 쇼핑몰 웹)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} aria-label="설명" />
              <textarea className="field" rows={2} placeholder="담당 경로 (예: shop-web/, src/)" value={form.paths} onChange={(e) => setForm({ ...form, paths: e.target.value })} aria-label="담당 경로" />
              <input className="field md:col-span-2" placeholder="회사 코드 위치 (예: shop-web/ · 레포가 회사 코드의 한 폴더일 때. 비우면 레포 루트)" value={form.mount} onChange={(e) => setForm({ ...form, mount: e.target.value })} aria-label="회사 코드 위치" />
            </div>
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className="text-ink-500">{form.auth === 'app' ? '앱 권한 (설치할 때 이 레포만 고르기)' : '토큰 권한 (이 레포만)'}</span>
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
      <CodingModeSection />
      <section className="card space-y-4 p-6">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="card-title text-lg">형상관리</h2>
          {list && <span className={`chip ${connected ? TONE.none : TONE.wait}`}>레포 {list.length}</span>}
          {list && list.length > 1 && <span className="chip bg-ink-100 text-ink-700">PR마다 AI 제안 · 사람 승인</span>}
          {offline && <span className="chip bg-amber-50 text-amber-700">개발·배포 AI에 연결할 수 없음</span>}
          {!!list?.length && <button type="button" className="btn-ghost ml-auto" onClick={checkAll} disabled={checking || offline}>{checking ? '점검 중…' : '보안 점검'}</button>}
          <button type="button" className={`btn-primary px-4 ${list?.length ? '' : 'ml-auto'}`} disabled={offline || (!!form && !form.id)}
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
                    <span className="font-medium">{c.repo}</span>
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
                    <div><dt className="text-xs text-ink-500">병합 대상 브랜치</dt><dd className="mt-0.5">{c.base_branch || '—'}</dd></div>
                    <div><dt className="text-xs text-ink-500">API 주소</dt><dd className="mt-0.5 truncate text-xs leading-5">{c.base_url || '기본 주소'}</dd></div>
                    {c.auth === 'app' ? (
                      <div><dt className="text-xs text-ink-500">GitHub App</dt><dd className="mt-0.5">앱 {c.app_id} · 설치 {c.installation_id}{c.key_set ? '' : ' · 키 없음'}</dd></div>
                    ) : (
                      <div><dt className="text-xs text-ink-500">접근 토큰</dt><dd className="mt-0.5">{c.token_set ? c.token_hint : '없음'}</dd></div>
                    )}
                    <div><dt className="text-xs text-ink-500">설명</dt><dd className="mt-0.5">{c.description || '—'}</dd></div>
                    <div><dt className="text-xs text-ink-500">회사 코드 위치</dt><dd className="mt-0.5">{c.mount || '레포 루트'}</dd></div>
                    <div className="sm:col-span-2 lg:col-span-4">
                      <dt className="text-xs text-ink-500">담당 경로</dt>
                      <dd className="mt-1 flex flex-wrap gap-1.5">
                        {c.paths.length ? c.paths.map((p) => <span key={p} className="chip bg-ink-100 text-ink-700">{p}</span>) : <span className="text-ink-500">—</span>}
                      </dd>
                    </div>
                    <InventoryRow inv={inv[c.id]} />
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
