import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from '@/lib/nav'
import { DEV_API, codingSettingsApi, useMe, vcsApi, type CodingSetting, type CodingSettings, type VcsAuth, type VcsConnection, type VcsInventory, type VcsTestResult } from '@/lib/planning'
import { UsageBar } from '@/components/dev/coding'
import { NotionConnect } from '@/components/planning/notion'
import { humanizePlanIds } from '@/lib/format'
import { Notice, PageHead, SectionHead, StatusChip } from '@/components/connect/parts'
import { GithubAppCard } from '@/components/connect/GithubAppCard'
import { DeployTools } from '@/components/connect/DeployTools'
import { useToolList } from '@/components/connect/ToolForm'
import { useDejafixStatus } from '@/components/connect/DejafixConnect'
import { appChoices, deployRows, dejafixCard, dejafixSummary } from '@/lib/toolForms'
import { EMPTY_REPO_FORM, repoAppLabel, repoBody, repoFormError, repoFormOf, type RepoForm } from '@/lib/repoForm'
import { useSession } from './session'
import AiConnectionView from '@/components/connect/AiConnectionView'
import PersonalAiConnect from '@/components/connect/PersonalAiConnect'

// 도구 연결 (/system/tools, 모두 연다): 고객사 개발팀이 쓰는 형상관리·배포·문서 도구와 AI 연결을 잇는 창구.
// 관리자·일반회원 모두 같은 화면을 쓰고 바꿀 수 있다(관리자만 다른 건 회사 연결·회원·서버 관리 두 화면, 11/19 결정).
// 배포와 맨 위 GitHub 앱은 공용 도구 연결 보관함(dashboard-api tool-connections), 레포 연결은 개발 AI 저장소다.
// 레포를 GitHub App으로 붙일 때는 맨 위 GitHub 앱 칸의 앱을 고른다(`app_ref`). 앱은 한 번만 넣고 배포와 같이 쓴다.
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
// 인증 방식. 기본은 이 레포만 여는 세분화 토큰, 조직이 GitHub App을 쓰면 더 보기에서
const AUTHS: { id: VcsAuth; label: string }[] = [{ id: 'token', label: '접근 토큰' }, { id: 'app', label: 'GitHub App' }]

/** 레포 주소 붙여 넣기 → 서버·레포. https://github.com/o/n · git@github.com:o/n.git · 사내 설치형 주소 · o/n */
type RepoAddr = { repo: string; base_url: string; web: string; owner: string; name: string }
export function parseRepo(raw: string): RepoAddr | null {
  const s = raw.trim().replace(/\/+$/, '')
  const m = s.match(/^git@([^:\s]+):([\w.-]+)\/([\w.-]+?)(?:\.git)?$/)
    ?? s.match(/^(?:https?:\/\/)?([^/\s]+\.[^/\s]+)\/([\w.-]+)\/([\w.-]+?)(?:\.git)?(?:\/.*)?$/)
  const short = m ? null : s.match(/^([\w.-]+)\/([\w.-]+?)(?:\.git)?$/)
  const [host, owner, name] = m ? [m[1].toLowerCase(), m[2], m[3]] : short ? ['github.com', short[1], short[2]] : ['', '', '']
  if (!owner || !name) return null
  const h = host.replace(/^www\./, '')
  const base_url = h === 'github.com' ? '' : h.endsWith('.ghe.com') ? `https://api.${h}` : `https://${h}/api/v3`
  return { repo: `${owner}/${name}`, base_url, web: `https://${h}`, owner, name }
}

/** 이 레포용 세분화 토큰 만들기 화면 (이름·만료·권한을 미리 채움. 레포 고르기만 사람이 한다) */
export function tokenUrl(a: RepoAddr): string {
  const q = new URLSearchParams({
    name: `AXcellent ${a.name}`.slice(0, 40), description: `AXcellent 개발 AI · ${a.repo}`, target_name: a.owner,
    expires_in: '90', contents: 'write', pull_requests: 'write', issues: 'write', statuses: 'read',
  })
  return `${a.web}/settings/personal-access-tokens/new?${q}`
}

// 형상관리 외의 연결 지점. 형상관리 연결로 함께 되는 것과 아직 없는 것을 나눠 보여 준다
const OTHERS: { name: string; tools: string; state: 'with-vcs' | 'deploy' | 'dejafix' | 'planned' }[] = [
  { name: 'CI 결과', tools: 'GitHub Actions · GitLab CI · Jenkins', state: 'with-vcs' },
  { name: '배포', tools: 'GitHub Actions · Jenkins', state: 'deploy' },
  { name: '배포 후 확인', tools: 'DejaFix (Zabbix)', state: 'dejafix' },
  { name: '요청 가져오기', tools: 'Jira · Linear · 노션 DB', state: 'planned' },
  { name: '알림', tools: '슬랙 · Teams · 잔디', state: 'planned' },
]

const api = vcsApi(DEV_API)
type Form = RepoForm
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
// 개발 서버 프록시 주소(/dev-api)처럼 앞이 빠진 주소면 지금 화면 주소를 앞에 붙인다 (명령에 그대로 붙여 넣을 수 있게)
const MCP_URL = new URL(`${DEV_API.replace(/\/$/, '')}/api/planning/mcp`, window.location.href).href
// 로컬: run_all.sh가 만든 작업 대기열 전용 토큰(backend/.data/tokens.env WORK_TOKEN)을 읽어 넣는다 (Git Bash, 레포 폴더).
// 이 값은 내 PC의 Claude Code 설정(~/.claude.json)에 남으므로 승인·머지 권한이 있는 사람 토큰은 쓰지 않는다
const MCP_ADD = `claude mcp add --transport http axcellent-dev ${MCP_URL} --header "Authorization: Bearer $(sed -n 's/^WORK_TOKEN=//p' backend/.data/tokens.env)"`
// Codex·Cursor는 토큰을 설정 파일이 아니라 환경변수(AXCELLENT_WORK_TOKEN)에서 읽는다
const TOKEN_ENV = 'AXCELLENT_WORK_TOKEN'
const SET_TOKEN_ENV = `setx ${TOKEN_ENV} "$(sed -n 's/^WORK_TOKEN=//p' backend/.data/tokens.env)"`
const CODEX_ADD = `codex mcp add axcellent-dev --url ${MCP_URL} --bearer-token-env-var ${TOKEN_ENV}`
const CURSOR_JSON = JSON.stringify({ mcpServers: { 'axcellent-dev': { url: MCP_URL, headers: { Authorization: `Bearer \${env:${TOKEN_ENV}}` } } } }, null, 2)
const ASK = 'axcellent-dev에서 작업 목록 보여 줘. 하나 가져가서 명세대로 구현하고 PR 올려 줘.'
type AiClient = 'claude' | 'codex' | 'cursor'
const AI_CLIENTS: { id: AiClient; label: string }[] = [
  { id: 'claude', label: 'Claude Code' },
  { id: 'codex', label: 'Codex' },
  { id: 'cursor', label: 'Cursor' },
]

/** 여러 개 중 하나 고르기 (연결 페이지는 기획 화면 CSS를 안 읽어서 이 화면 안에서 그린다). 고른 것은 파란 바탕·흰 글씨 */
function Seg<T extends string>({ label, items, value, onPick, disabled }: {
  label: string; items: { id: T; label: string }[]; value?: T; onPick: (id: T) => void; disabled?: boolean
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-lg border border-ink-100" role="group" aria-label={label}>
      {items.map((it) => (
        <button key={it.id} type="button" aria-pressed={value === it.id} disabled={disabled} onClick={() => onPick(it.id)}
          className={`px-3 py-1 text-xs ${value === it.id ? 'bg-brand-600 !text-white' : 'text-ink-700 hover:bg-ink-50'}`}>
          {it.label}
        </button>
      ))}
    </div>
  )
}

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

/** AI 도구 연결: 내 PC의 AI 코딩 도구(Claude Code·Codex·Cursor)에 작업 대기열 MCP 서버를 붙이는 순서 */
function ClaudeConnect({ connected }: { connected: string[] }) {
  const me = useMe()
  const mine = me?.name ? me.claude_connected : null
  const [client, setClient] = useState<AiClient>('claude')
  return (
    <div className="space-y-3 rounded-lg border border-ink-100 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">AI 도구 연결</h3>
        {me?.name && !connected.includes(me.name) && <span className={`chip ${mine ? TONE.ok : TONE.none}`} title="최근 7일 작업 목록을 열었는지">{me.name} {mine ? '연결됨' : '연결 없음'}</span>}
        <span className={`chip ${connected.length ? TONE.ok : TONE.none}`} title="최근 7일 작업 목록을 연 사람">{connected.length ? `연결됨 ${connected.join(', ')}` : '연결 없음'}</span>
      </div>
      <Seg label="AI 도구" items={AI_CLIENTS} value={client} onPick={setClient} />
      <ol className="list-decimal space-y-3 pl-5 text-sm">
        {client === 'claude' ? (
          <>
            <li className="space-y-1">
              <span className="flex flex-wrap items-center gap-2">작업 대기열 서버 붙이기 <span className="chip bg-ink-100 text-ink-500">Git Bash · 레포 폴더</span></span>
              <CopyLine text={MCP_ADD} />
            </li>
            <li className="space-y-1"><span>확인</span><CopyLine text="claude mcp list" /></li>
          </>
        ) : (
          <>
            <li className="space-y-1">
              <span className="flex flex-wrap items-center gap-2">토큰을 환경변수로 <span className="chip bg-ink-100 text-ink-500">Git Bash · 레포 폴더 · 다시 켠 뒤 적용</span></span>
              <CopyLine text={SET_TOKEN_ENV} />
            </li>
            {client === 'codex' ? (
              <>
                <li className="space-y-1"><span>작업 대기열 서버 붙이기</span><CopyLine text={CODEX_ADD} /></li>
                <li className="space-y-1"><span>확인</span><CopyLine text="codex mcp list" /></li>
              </>
            ) : (
              <li className="space-y-1">
                <span className="flex flex-wrap items-center gap-2">작업 대기열 서버 붙이기 <span className="chip bg-ink-100 text-ink-500">~/.cursor/mcp.json</span></span>
                <CopyLine text={CURSOR_JSON} />
              </li>
            )}
          </>
        )}
        <li className="space-y-1">
          <span>레포 폴더에서 작업 가져가기</span>
          <CopyLine text={ASK} />
        </li>
      </ol>
    </div>
  )
}

/** 요청 작업을 누가 코딩하는지 판단 근거: 자동이면 규칙 두 줄 중 맞은 줄을 진하게, 고정이면 설정, 예외(예산·LLM 없음)는 따로 */
function CodingWhy({ cfg }: { cfg: CodingSettings }) {
  const who = cfg.agents_connected
  const mine = cfg.resolved === 'subscription'
  const ruled = who.length ? 'subscription' : 'agent'
  const row = (on: boolean) => `flex flex-wrap items-center gap-1.5 ${on ? 'font-medium text-ink-900' : 'text-ink-500'}`
  const target = mine ? '내 Claude · 연결한 개발자 Claude가 작업 목록에서 가져감' : '서버 AI · 서버가 바로 코딩'
  if (cfg.mode !== 'auto') {
    return <div className="space-y-1"><div className={row(true)}>회사 설정 고정 → {target}</div></div>
  }
  return (
    <div className="space-y-1">
      <div className={row(!!who.length && ruled === cfg.resolved)}>
        <span>최근 7일 본인 Claude 연결</span>
        {who.length ? who.map((n) => <span key={n} className="chip bg-ink-100 text-ink-700">{n}</span>) : <span className="chip bg-ink-100 text-ink-500">없음</span>}
        <span>→ 내 Claude</span>
      </div>
      <div className={row(!who.length && ruled === cfg.resolved)}>연결 없음 → 서버 AI</div>
      {ruled !== cfg.resolved && <div className={row(true)}><span className="chip bg-amber-50 text-amber-800">예외</span>{cfg.why}</div>}
      <div className="pt-1 text-xs text-ink-500">{target}</div>
    </div>
  )
}

/** 내 연결 › 고급 · 개발자 PC 연결: 요청 작업 코딩 방식(회사 설정, 개발 AI) + 개발자 PC AI 도구(MCP) 안내 */
function DevPcSection() {
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
    <div className="space-y-6">
    <section className="space-y-4 border border-brand-100 bg-brand-50/30 p-5">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-[15px] font-semibold">누가 코딩하나</h3>
        {cfg && <span className={`chip ${cfg.resolved === 'agent' ? TONE.none : TONE.ok}`}>지금: {cfg.resolved === 'agent' ? '서버 AI' : '내 Claude'}</span>}
        {error && <span className="chip bg-amber-50 text-amber-700">{error}</span>}
      </div>
      <Seg label="요청 작업 코딩 방식" items={CODING_MODES} value={cfg?.mode} disabled={!cfg || busy} onPick={(id) => void choose(id)} />
      {cfg && (
        <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-2 text-sm">
          <dt className="text-ink-500">판단</dt>
          <dd><CodingWhy cfg={cfg} /></dd>
          {!cfg.company_key?.key_set && <><dt className="text-ink-500">서버 AI 체험</dt>
          <dd><UsageBar used={cfg.trial_used} limit={cfg.trial_limit} wide title="서버 AI(기본 키)가 코딩에 쓴 토큰 / 체험 한도" /></dd></>}
        </dl>
      )}
    </section>
    <ClaudeConnect connected={cfg?.agents_connected ?? []} />
    </div>
  )
}

/** 물음표 아이콘: 누르면 옆에 설명 칸이 열린다 (밖을 누르거나 Esc로 닫힘) */
function Help({ label, children, right }: { label: string; children: ReactNode; right?: boolean }) {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
  return (
    <span className="relative inline-flex">
      <button type="button" aria-label={`${label} 도움말`} aria-expanded={open} onClick={() => setOpen((v) => !v)}
        className="grid h-5 w-5 place-items-center rounded-full border border-ink-300 text-[11px] font-semibold text-ink-500 hover:border-brand-600 hover:text-brand-600">?</button>
      {open && (
        <>
          <span className="fixed inset-0 z-30" onClick={() => setOpen(false)} aria-hidden="true" />
          <span role="dialog" aria-label={`${label} 도움말`}
            className={`absolute top-7 z-40 block w-72 space-y-2 rounded-lg border border-ink-100 bg-surface p-3 text-left text-xs font-normal leading-5 text-ink-700 shadow-lg ${right ? 'right-0' : 'left-0'}`}>
            {children}
          </span>
        </>
      )}
    </span>
  )
}

/** 도구 연결 보관함(dashboard-api)을 받는 중이거나 못 받았을 때 */
function ToolsLoading({ error }: { error: string | null }) {
  return error ? <Notice tone="bad">도구 연결을 불러오지 못했어요: {error}</Notice> : <p className="text-sm text-ink-500">불러오는 중…</p>
}

/** 일반 회원: 회사 공용 연결은 상태만 본다 */
function ReadOnlyNotice() {
  return <Notice tone="info">회사 공용 연결이라 관리자만 바꿀 수 있어요. 지금 상태만 보여요.</Notice>
}

// 연결 창 왼쪽 메뉴 (설정 창처럼 묶음 › 항목). 고른 항목만 오른쪽에 보인다
type PanelItem = 'repos' | 'others' | 'deploy' | 'notion' | 'ai' | 'mine'
const PANEL_GROUPS: { label: string; items: { id: PanelItem; label: string }[] }[] = [
  { label: '형상관리', items: [
    { id: 'repos', label: '레포 연결' },
    { id: 'others', label: '다른 개발 도구' },
  ] },
  { label: '배포', items: [{ id: 'deploy', label: '배포 도구' }] }, // 공용 도구 연결 보관함 (components/connect/DeployTools.tsx)
  { label: '문서', items: [{ id: 'notion', label: '노션' }] }, // 기획 영역 (components/planning/notion.tsx)
  // AI 연결: 회사 AI 상태·사용량 + 개발 AI 코딩 설정 보기(components/connect/AiConnectionView.tsx·DevCodingSummary.tsx).
  // 내 연결: 개인 AI 연결(components/connect/PersonalAiConnect.tsx) + 고급 · 개발자 PC 연결(코딩 방식·MCP 안내)
  { label: 'AI', items: [{ id: 'ai', label: 'AI 연결' }, { id: 'mine', label: '내 연결' }] },
]
const PANEL_IDS = PANEL_GROUPS.flatMap((g) => g.items.map((i) => i.id))
const TAB_KEY = 'ax.connections.item'
const readTab = (): PanelItem => {
  // 노션 계정 연결에서 돌아오면(?notion=…) 노션 항목을 연다
  if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('notion')) return 'notion'
  try {
    const v = localStorage.getItem(TAB_KEY) as PanelItem | null
    return v && PANEL_IDS.includes(v) ? v : 'repos'
  } catch { return 'repos' }
}

/** 연결 창: 왼쪽 메뉴(형상관리·배포·문서·AI) + 오른쪽 내용 */
export function ToolConnectionsPanel({ title, onClose }: { title?: string; onClose?: () => void } = {}) {
  const { account } = useSession()
  const admin = !!account?.admin
  // 도구 연결은 관리자·일반회원이 똑같이 쓴다(관리자만 다른 건 회사 연결·회원·서버 관리 두 화면). admin은 관리자 화면으로 가는 링크에만 쓴다
  const canEdit = !!account
  const tools = useToolList()
  const dejafixLive = useDejafixStatus()
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
  const [tab, setTab] = useState<PanelItem>(readTab)
  // 레포를 GitHub App으로 붙일 때 고르는 앱: 보관함 github_app (맨 위 GitHub 앱 칸)
  const apps = tools.data ? appChoices(tools.data.connections) : []
  const pickTab = (t: PanelItem) => {
    setTab(t)
    try { localStorage.setItem(TAB_KEY, t) } catch { /* 저장이 막혀도 탭은 바뀐다 */ }
  }

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
    const why = repoFormError(form, editing, apps.map((a) => a.id))
    if (why) return setError(why)
    setSaving(true)
    setError(null)
    try {
      const next = await api.save(repoBody(form))
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
  // 다른 개발 도구 목록의 배포·DejaFix 상태: 도구 연결 보관함에서 (못 받으면 상태를 쓰지 않는다)
  const deployCount = tools.data ? deployRows(tools.data.connections).length : null
  // DejaFix는 같이 설치된다: 연결 출처·응답 여부는 프록시 /status에서 (못 읽으면 "확인할 수 없음")
  const dejafixSum = tools.data && dejafixLive.status !== undefined ? dejafixSummary(dejafixCard(dejafixLive.status, tools.data.connections)) : null
  const dejafixOk = dejafixSum?.tone === 'ok'
  const dejafixLabel = dejafixSum ? dejafixSum.label : 'DejaFix 연결'
  const kindReady = KINDS.find((k) => k.id === form?.kind)?.ready
  const editing = form?.id ? list?.find((c) => c.id === form.id) : undefined

  // 연결 추가는 목록 맨 위, 수정은 그 연결 카드 바로 아래에 연다
  const addr = form ? parseRepo(form.addr) : null
  const setAddr = (raw: string) => setForm((f) => {
    if (!f) return f
    const p = parseRepo(raw)
    return p ? { ...f, addr: raw, kind: 'github', repo: p.repo, base_url: p.base_url } : { ...f, addr: raw, repo: raw.trim() }
  })
  const appAuth = form?.auth === 'app'
  const formEl = form && (
          <form onSubmit={save} className="space-y-3 rounded-xl border border-brand-200 bg-brand-50/40 p-4">
            <h3 className="font-medium">{editing ? `${editing.repo} 수정` : '새 레포'}</h3>
            <div className="space-y-1">
              <label htmlFor="repo-addr" className="text-xs text-ink-500">레포 주소</label>
              <div className="flex flex-wrap items-center gap-2">
                <input id="repo-addr" className="field min-w-0 flex-1" placeholder="https://github.com/owner/name" value={form.addr} onChange={(e) => setAddr(e.target.value)} autoFocus={!editing} />
                {addr && <span className="chip bg-ink-100 text-ink-700">{addr.base_url ? new URL(addr.web).host : 'github.com'} · {addr.repo}</span>}
              </div>
            </div>
            {!appAuth && (
              <div className="space-y-1">
                <span className="flex items-center gap-1.5 text-xs text-ink-500">접근 토큰
                  <Help label="접근 토큰">
                    <b className="block text-ink-900">이 레포만 여는 토큰</b>
                    <ol className="list-decimal space-y-1 pl-4">
                      <li><i>토큰 만들기</i> → GitHub 화면 (이름·만료 90일·권한 채워짐)</li>
                      <li>Repository access › <i>Only select repositories</i> › {addr?.repo ?? '이 레포'}</li>
                      <li><i>Generate token</i> → 복사 → 여기 붙여 넣기</li>
                    </ol>
                    <span className="flex flex-wrap gap-1">{PERMISSIONS.map((p) => <span key={p} className="chip bg-ink-100 text-ink-700">{p}</span>)}</span>
                    <span className="block text-amber-800">조직 레포 · 조직 관리자 승인 필요할 수 있음</span>
                  </Help>
                </span>
                <div className="flex flex-wrap gap-2">
                  <input className="field min-w-0 flex-1" type="password" autoComplete="off" placeholder={editing?.token_set ? `${editing.token_hint ?? ''} · 비우면 그대로` : 'github_pat_…'} value={form.token}
                    onChange={(e) => setForm({ ...form, token: e.target.value })} aria-label="접근 토큰" />
                  <a className={`btn-ghost ${addr ? '' : 'pointer-events-none opacity-50'}`} href={addr ? tokenUrl(addr) : undefined} target="_blank" rel="noreferrer" aria-disabled={!addr}>토큰 만들기</a>
                </div>
              </div>
            )}
            <details className="group" open={appAuth || !!editing?.paths.length || !!editing?.mount}>
              <summary className="cursor-pointer select-none text-xs text-ink-500">더 보기 · 브랜치 · 인증 · 담당 경로</summary>
              <div className="mt-3 space-y-3">
                <div className="grid gap-3 md:grid-cols-3">
                  <select className="field" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} aria-label="형상관리 종류">
                    {KINDS.map((k) => <option key={k.id} value={k.id} disabled={!k.ready}>{k.label}{k.ready ? '' : ' — 준비 중'}</option>)}
                  </select>
                  <input className="field" placeholder="병합 대상 브랜치 (비우면 레포 기본 브랜치)" value={form.base_branch} onChange={(e) => setForm({ ...form, base_branch: e.target.value })} aria-label="병합 대상 브랜치" />
                  <input className="field" placeholder="API 주소 (주소에서 자동)" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} aria-label="API 주소" />
                </div>
                {form.kind === 'github' && (
                  <div className="space-y-2">
                    <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="인증 방식">
                      {AUTHS.map((a) => (
                        <button key={a.id} type="button" role="radio" aria-checked={form.auth === a.id} onClick={() => setForm({ ...form, auth: a.id })}
                          className={`chip py-1 ${form.auth === a.id ? 'bg-brand-600 !text-white' : 'bg-ink-100 text-ink-700'}`}>{a.label}</button>
                      ))}
                    </div>
                    {appAuth && (apps.length ? (
                      <div className="space-y-1">
                        <select className="field w-full md:w-1/2" value={form.app_ref} onChange={(e) => setForm({ ...form, app_ref: e.target.value })} aria-label="GitHub 앱">
                          <option value="">GitHub 앱 고르기</option>
                          {apps.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
                        </select>
                        <p className="text-xs text-ink-500">앱 ID·설치 ID·개인 키는 위 <a className="underline" href="#github-app">GitHub 앱</a> 칸에서 한 번만 넣어요. 배포와 같은 앱을 써요.</p>
                      </div>
                    ) : (
                      <p className="text-sm text-ink-500">{tools.data ? '연결한 GitHub 앱이 없어요. ' : 'GitHub 앱 목록을 불러오는 중이에요. '}<a className="underline" href="#github-app">위 GitHub 앱 칸</a>에서 먼저 앱을 연결해 주세요.</p>
                    ))}
                  </div>
                )}
                <div className="grid gap-3 md:grid-cols-2">
                  <input className="field" placeholder="설명 (예: 쇼핑몰 웹)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} aria-label="설명" />
                  <textarea className="field" rows={2} placeholder="담당 경로 (예: shop-web/, src/)" value={form.paths} onChange={(e) => setForm({ ...form, paths: e.target.value })} aria-label="담당 경로" />
                  <input className="field md:col-span-2" placeholder="회사 코드 위치 (예: shop-web/ · 레포가 회사 코드의 한 폴더일 때. 비우면 레포 루트)" value={form.mount} onChange={(e) => setForm({ ...form, mount: e.target.value })} aria-label="회사 코드 위치" />
                </div>
              </div>
            </details>
            {error && <p className="text-sm text-red-700">{error}</p>}
            <div className="flex gap-2">
              <button className="btn-primary px-5" disabled={saving || offline || !kindReady}>{saving ? '연결 중…' : editing ? '저장' : '연결'}</button>
              <button type="button" className="btn-ghost" onClick={() => { setForm(null); setError(null) }}>취소</button>
            </div>
          </form>
  )

  return (
    <div className={`flex flex-col gap-0 p-0 md:flex-row ${onClose ? 'h-full overflow-hidden' : 'card'}`}>
      <nav className="shrink-0 overflow-y-auto border-b border-ink-100 p-3 md:w-56 md:border-b-0 md:border-r" aria-label="연결 메뉴">
        {title && <h2 className="mb-3 px-2 text-lg font-semibold">{title}</h2>}
        {PANEL_GROUPS.map((g) => (
          <div key={g.label} className="mb-3 last:mb-0">
            <div className="px-2 pb-1 text-xs text-ink-500">{g.label}</div>
            {g.items.map((it) => {
              const hint = it.id === 'repos' && list ? (list.length ? `${list.length}개` : '없음')
                : it.id === 'deploy' && deployCount !== null ? (deployCount ? `${deployCount}개` : '없음') : ''
              return (
                <button key={it.id} type="button" aria-current={tab === it.id ? 'page' : undefined} onClick={() => pickTab(it.id)}
                  className={`flex w-full items-center justify-between gap-2 border-l-2 px-2 py-1.5 text-left text-sm ${tab === it.id ? 'border-brand-600 bg-ink-100 font-medium text-ink-900' : 'border-transparent text-ink-700 hover:bg-ink-50'}`}>
                  {it.label}
                  {hint && <span className="text-xs font-normal text-ink-500">{hint}</span>}
                </button>
              )
            })}
          </div>
        ))}
      </nav>
      <div className={`relative min-w-0 flex-1 p-6 ${onClose ? 'overflow-y-auto pt-12' : ''}`}>
      {onClose && <button className="btn-quiet absolute right-3 top-3 px-2 py-1" onClick={onClose} aria-label="닫기">✕</button>}
      {tab === 'notion' ? (
        <div className="space-y-4">
          {!canEdit && <ReadOnlyNotice />}
          <NotionConnect readOnly={!canEdit} />
        </div>
      ) : tab === 'ai' ? <AiConnectionView /> : tab === 'mine' ? (
        <div className="space-y-8">
          <PersonalAiConnect />
          <details className="card group p-6">
            <summary className="cursor-pointer select-none text-[15px] font-semibold">고급 · 개발자 PC 연결</summary>
            <div className="mt-4 space-y-6">
              <p className="text-sm text-ink-700">개발자가 자기 PC의 Claude Code·Codex·Cursor로 작업 목록에서 요청 작업을 가져가 코딩하게 해요. 여기서 쓰는 토큰은 작업 대기열 전용이에요.</p>
              <DevPcSection />
            </div>
          </details>
        </div>
      ) : tab === 'deploy' ? (
        tools.data ? <DeployTools data={tools.data} admin={canEdit} onChanged={tools.reload} /> : <ToolsLoading error={tools.error} />
      ) : tab === 'repos' ? (
      <section className="space-y-4">
        {!canEdit && <ReadOnlyNotice />}
        {tools.data ? <GithubAppCard data={tools.data} admin={canEdit} onChanged={tools.reload} /> : <ToolsLoading error={tools.error} />}
        <SectionHead title="레포 연결"
          desc={list && list.length > 1
            ? '개발 AI가 코드를 읽고 PR을 올리는 레포예요. 레포가 여럿이면 PR마다 AI가 대상 레포를 제안하고 사람이 승인해요.'
            : '개발 AI가 코드를 읽고 PR을 올리는 레포예요. 레포마다 접근 토큰이나 GitHub App으로 연결해요.'}
          right={<>
            {offline && <StatusChip tone="wait">개발·배포 AI에 연결할 수 없음</StatusChip>}
            {canEdit && !!list?.length && <button type="button" className="btn-ghost" onClick={checkAll} disabled={checking || offline}>{checking ? '점검 중…' : '보안 점검'}</button>}
            {canEdit && <button type="button" className="btn-primary px-4" disabled={offline || (!!form && !form.id)}
              onClick={() => { setForm({ ...EMPTY_REPO_FORM }); setError(null) }}>레포 연결</button>}
          </>} />

        {canEdit && form && !form.id && formEl}

        {list && !list.length && !form && (
          <div className="border border-dashed border-ink-300 px-6 py-8 text-center">
            <p className="font-medium">연결한 레포가 없어요</p>
            <p className="mt-1 text-sm text-ink-500">{canEdit ? '개발 AI가 코드를 읽고 PR을 올릴 레포가 아직 없어요. 위의 레포 연결로 첫 레포를 이어 주세요.' : '관리자가 연결하면 여기에 보여요.'}</p>
          </div>
        )}

        {!!list?.length && (
          <ul className="space-y-3">
            {list.map((c) => {
              const st = status(c)
              const t = tests[c.id]
              return (
                <li key={c.id} className={`rounded-xl border p-4 ${form?.id === c.id ? 'border-ink-500' : 'border-ink-100'}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-base font-semibold">{c.repo}</span>
                    <span className={`chip ${st.tone}`}>{st.label}</span>
                    <span className="text-xs text-ink-500">{KIND_LABEL[c.kind] ?? c.kind} · {SOURCE[c.source] ?? c.source}</span>
                    {canEdit && (
                      <div className="ml-auto flex gap-1.5">
                        <button type="button" className="btn-ghost" onClick={() => runTest(c.id)} disabled={!!testing || offline}>연결 확인</button>
                        <button type="button" className="btn-ghost" onClick={() => { setForm(repoFormOf(c)); setError(null) }}>수정</button>
                        <button type="button" className="btn-quiet" onClick={() => remove(c)}>삭제</button>
                      </div>
                    )}
                  </div>
                  <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
                    <div><dt className="text-xs text-ink-500">병합 대상 브랜치</dt><dd className="mt-0.5">{c.base_branch || '—'}</dd></div>
                    <div><dt className="text-xs text-ink-500">API 주소</dt><dd className="mt-0.5 truncate text-xs leading-5">{c.base_url || '기본 주소'}</dd></div>
                    {c.auth === 'app' ? (
                      <div><dt className="text-xs text-ink-500">GitHub App</dt><dd className="mt-0.5">{repoAppLabel(c)}</dd></div>
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
                          {humanizePlanIds(t.message)}{t.code ? ` (${t.code})` : ''} · {new Date(t.checked_at).toLocaleTimeString('ko-KR')}
                        </dd>
                      </div>
                    )}
                  </dl>
                  {canEdit && form?.id === c.id && <div className="mt-4">{formEl}</div>}
                </li>
              )
            })}
          </ul>
        )}

        {!form && error && <p className="text-sm text-red-700">{error}</p>}
      </section>
      ) : (
      <section>
        <SectionHead title="다른 개발 도구" desc="CI·배포·이슈 도구예요. 형상관리와 함께 읽거나, 사내 PC 연결로 이어요." />
        <ul className="mt-3 divide-y divide-ink-100">
          {OTHERS.map((o) => (
            <li key={o.name} className="grid grid-cols-[120px_1fr_auto] items-center gap-3 py-3 text-sm">
              <span className="font-medium">{o.name}</span>
              <span className="text-ink-500">{o.tools}</span>
              {o.state === 'with-vcs' ? (
                <span className={`chip ${connected ? 'bg-emerald-50 text-emerald-700' : 'bg-ink-100 text-ink-700'}`}>{connected ? '형상관리로 함께 읽음' : '형상관리 연결 뒤'}</span>
              ) : o.state === 'deploy' ? (
                <button type="button" className={`chip ${deployCount ? 'bg-emerald-50 text-emerald-700' : 'bg-ink-100 text-ink-700'}`} onClick={() => pickTab('deploy')}>
                  {deployCount === null ? '배포 도구 보기' : deployCount ? `연결됨 ${deployCount}개 · 배포 도구` : '연결 전 · 배포 도구'}
                </button>
              ) : o.state === 'dejafix' ? (
                admin
                  ? <Link href="/system/members?tab=pcs" className={`chip ${dejafixOk ? 'bg-emerald-50 text-emerald-700' : 'bg-ink-100 text-ink-700'}`}>{dejafixLabel} · 회원·서버 관리</Link>
                  : <span className={`chip ${dejafixOk ?'bg-emerald-50 text-emerald-700' : 'bg-ink-100 text-ink-700'}`}>{dejafixLabel}</span>
              ) : (
                <span className="chip bg-ink-100 text-ink-500">준비 중</span>
              )}
            </li>
          ))}
        </ul>
      </section>
      )}
      </div>
    </div>
  )
}

/** 도구 연결(/system/tools): 시스템 관리 아래, 모두 연다. 형상관리 · 배포 · 문서 · AI(AI 연결 보기 · 내 연결). 예전 /connections */
export default function ToolConnections() {
  return (
    <div className="space-y-6 px-4 py-8 sm:px-8">
      <PageHead where="시스템 관리" title="도구 연결">AI들이 쓰는 회사 바깥 도구를 이어요. 키·토큰은 서버에만 저장되고 화면에는 가린 값만 보여요.</PageHead>
      <ToolConnectionsPanel />
    </div>
  )
}
