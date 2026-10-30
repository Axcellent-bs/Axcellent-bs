// 작업·PR 탭의 레포 작업 공간: 연결된 레포의 PR 게시판·PR 상세(대화·커밋·변경 파일)·브랜치.
// 사람이 GitHub에 직접 올린 PR도 보이고, AI가 만든 PR은 내부 PR 화면으로 잇는다.
// 화면 모양은 GitHub·GitLab의 PR 화면 순서를 따른다 (상태 탭 · 검색 · 줄 / 머리 · 버튼 · 탭 · 곁 칸).
import { useEffect, useMemo, useRef, useState, type Dispatch, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import { Button, Modal, useDismiss, useToast } from '@dejafix/common-frontend'
import type { Action, State as S } from '@/lib/planningData'
import { DiffFiles } from '@/components/dev/diff'
import { DEV_API, PlanningApiError, kst, repoApi, vcsApi, type MergeMethod, type RepoBranch, type RepoCheckState, type RepoGraph, type RepoGraphCommit, type RepoPr, type RepoIssue, type RepoIssueState, type RepoPrDetail, type RepoPrState, type VcsConnection } from '@/lib/planning'
import { layoutGraph, laneColor, type GraphRow } from '@/components/dev/graphlayout'
import { prSlug, toPath, go } from '@/components/planning-common/links'
import { Work } from '@/components/dev/work'

// ---------------------------------------------------------------- 공용

const STATE_LABEL: Record<RepoPrState, string> = { open: '열림', merged: '머지됨', closed: '닫힘' }
const CI_LABEL: Record<RepoCheckState, string> = { passed: 'CI 통과', failed: 'CI 실패', pending: 'CI 진행 중' }
const METHOD_LABEL: Record<MergeMethod, string> = { merge: 'Merge commit', squash: 'Squash', rebase: 'Rebase' }

// 오류 코드 → 해요체 알림 (백엔드 문장은 그대로 쓰지 않는다)
const ERR: Record<string, string> = {
  NO_VCS: '연결된 레포가 없어요',
  NO_ISSUES: '이 형상관리 연결에는 이슈 칸이 없어요',
  NOT_FOUND: '찾지 못했어요',
  READ_ONLY: '읽기 전용 연결이라 바꿀 수 없어요',
  GATE_REJECTED: '실행 관문이 보류했어요. AI 교차검증 탭에서 확인해 주세요',
  OPEN_PR: '열린 PR이 있는 브랜치라 지우지 않았어요',
  PROTECTED_BRANCH: '기준·기본·보호 브랜치는 지우지 않아요',
  INTERNAL_FLOW: '중단한 AI 작업이라 작업 화면에서 다시 시도해 주세요',
  REPO_NOT_APPROVED: '올릴 레포를 먼저 승인해 주세요',
  VCS_CONFLICT: '이미 바뀐 상태라 처리하지 않았어요',
  VCS_MERGE_FAILED: '머지하지 못했어요',
  VCS_UNAUTHORIZED: '레포 토큰이 맞지 않아요',
  VCS_FORBIDDEN: '레포 토큰 권한이 부족해요',
  VCS_RATE_LIMITED: '요청 한도에 걸렸어요. 잠시 뒤 다시 해 주세요',
  VCS_UNAVAILABLE: '레포 서버에 연결하지 못했어요',
  UNAUTHORIZED: '사람 토큰이 필요해요',
  FORBIDDEN: '사람 토큰이 필요해요',
}
const errText = (e: unknown) => (e instanceof PlanningApiError ? ERR[e.code] ?? `처리하지 못했어요 (${e.code})` : '개발·배포 서버에 연결하지 못했어요')

/** ISO 시각 → "방금 · n분 전 · n시간 전 · n일 전", 30일 넘으면 날짜 */
export function ago(iso?: string | null): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const m = Math.floor((Date.now() - t) / 60_000)
  if (m < 1) return '방금'
  if (m < 60) return `${m}분 전`
  if (m < 60 * 24) return `${Math.floor(m / 60)}시간 전`
  if (m < 60 * 24 * 30) return `${Math.floor(m / 1440)}일 전`
  return kst(iso).slice(0, 5)
}

const issueHref = (n?: number) => toPath(`work/prs/issues${n ? `/${n}` : ''}`)
const repoHref = (n?: number, sub?: string) => toPath(`work/prs/repo${n ? `/${n}` : ''}${sub ? `/${sub}` : ''}`)

function PrState({ v }: { v: RepoPrState }) {
  return <span className={`rp-state rp-${v}`}>{STATE_LABEL[v]}</span>
}
function Ci({ v }: { v?: RepoCheckState | null }) {
  if (!v) return <span className="rp-ci rp-ci-none" aria-hidden />
  return <span className={`rp-ci rp-ci-${v}`} title={CI_LABEL[v]} aria-label={CI_LABEL[v]} />
}
function Branches({ head, base }: { head: string; base: string }) {
  return <span className="rp-br"><code>{head}</code><span aria-hidden>→</span><code>{base}</code></span>
}
function AiTag({ id }: { id: string }) {
  return <a href={toPath(`work/prs/${prSlug(id)}`)} className="grade rp-ai" title={`내부 ${id}`}>AI · {id}</a>
}

/** 지우기·머지 같은 되돌리기 어려운 일 확인 창. 내용은 라벨·값 줄로만 */
function Confirm({ title, rows, ok, danger, busy, onOk, onClose }: { title: string; rows: [string, ReactNode][]; ok: string; danger?: boolean; busy?: boolean; onOk: () => void; onClose: () => void }) {
  return (
    <Modal open onClose={onClose} title={title} footer={<>
      <Button onClick={onClose}>취소</Button>
      <Button variant={danger ? 'dark' : 'primary'} disabled={busy} onClick={onOk} autoFocus>{ok}</Button>
    </>}>
      <dl className="pairs">{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
    </Modal>
  )
}

// 연결(레포) 고르기. 마지막에 고른 것은 이 브라우저에만 기억한다
const CONN_KEY = 'ax.repo.connection'
function useConnection(): [VcsConnection[], string, (id: string) => void] {
  const [list, setList] = useState<VcsConnection[]>([])
  const [pick, setPick] = useState(() => { try { return localStorage.getItem(CONN_KEY) ?? '' } catch { return '' } })
  useEffect(() => {
    let alive = true
    vcsApi(DEV_API).list().then((x) => alive && setList(x)).catch(() => {})
    return () => { alive = false }
  }, [])
  const id = list.find((c) => c.id === pick)?.id ?? list[0]?.id ?? ''
  const set = (x: string) => { setPick(x); try { localStorage.setItem(CONN_KEY, x) } catch { /* 저장 못 해도 화면은 돈다 */ } }
  return [list, id, set]
}

function useLoad<T>(load: () => Promise<T>, deps: unknown[]): { data?: T; error?: unknown; loading: boolean; reload: () => void } {
  const [tick, setTick] = useState(0)
  const [st, setSt] = useState<{ data?: T; error?: unknown; loading: boolean }>({ loading: true })
  useEffect(() => {
    let alive = true
    setSt((x) => ({ ...x, loading: true }))
    load().then((data) => alive && setSt({ data, loading: false })).catch((error) => alive && setSt({ error, loading: false }))
    return () => { alive = false }
  }, [...deps, tick]) // eslint-disable-line react-hooks/exhaustive-deps
  return { ...st, reload: () => setTick((n) => n + 1) }
}

function LoadError({ error }: { error: unknown }) {
  const code = error instanceof PlanningApiError ? error.code : ''
  return <div className="empty"><b>{code === 'NO_VCS' ? '연결된 레포 없음' : errText(error)}</b>{code === 'NO_VCS' && <span>개발 도구 연결</span>}</div>
}

// ---------------------------------------------------------------- 탭 틀: 작업 · 레포 PR · 이슈 · 브랜치 · 커밋 그래프

export function PrsTab({ s, d, parts }: { s: S; d: Dispatch<Action>; parts: string[] }) {
  const [view, a, b] = parts
  const mode = view === 'repo' ? 'repo' : view === 'issues' ? 'issues' : view === 'branches' ? 'branches' : view === 'graph' ? 'graph' : 'work'
  const [conns, conn, setConn] = useConnection()
  const repo = conns.find((c) => c.id === conn)
  return (
    <>
      <div className="rp-bar">
        <nav className="seg rp-views" aria-label="작업·PR 보기">
          <a href={toPath('work/prs')} className={mode === 'work' ? 'on' : ''} aria-current={mode === 'work' ? 'page' : undefined}>작업</a>
          <a href={repoHref()} className={mode === 'repo' ? 'on' : ''} aria-current={mode === 'repo' ? 'page' : undefined}>레포 PR</a>
          <a href={issueHref()} className={mode === 'issues' ? 'on' : ''} aria-current={mode === 'issues' ? 'page' : undefined}>이슈</a>
          <a href={toPath('work/prs/branches')} className={mode === 'branches' ? 'on' : ''} aria-current={mode === 'branches' ? 'page' : undefined}>브랜치</a>
          <a href={graphHref()} className={mode === 'graph' ? 'on' : ''} aria-current={mode === 'graph' ? 'page' : undefined}>커밋 그래프</a>
        </nav>
        {mode !== 'work' && conns.length > 1 && (
          <select className="select mono" value={conn} onChange={(e) => setConn(e.target.value)} aria-label="레포">
            {conns.map((c) => <option key={c.id} value={c.id}>{c.repo || c.id}</option>)}
          </select>
        )}
        {mode !== 'work' && conns.length === 1 && repo && <code className="rp-repo">{repo.repo}</code>}
      </div>
      {mode === 'work' && <Work s={s} d={d} item={view} />}
      {mode === 'repo' && !a && <Board key={conn} conn={conn} />}
      {mode === 'repo' && a && /^\d+$/.test(a) && <PrView key={`${conn}:${a}`} conn={conn} number={Number(a)} tab={b} />}
      {mode === 'issues' && !a && <IssueBoard key={conn} conn={conn} />}
      {mode === 'issues' && a && /^\d+$/.test(a) && <IssueView key={`${conn}:${a}`} conn={conn} number={Number(a)} />}
      {mode === 'branches' && <BranchTable key={conn} conn={conn} />}
      {mode === 'graph' && <GraphView key={conn} conn={conn} sel={a && /^[0-9A-Za-z]{4,64}$/.test(a) ? a : undefined} />}
    </>
  )
}

// ---------------------------------------------------------------- PR 게시판

function Board({ conn }: { conn: string }) {
  const [tab, setTab] = useState<RepoPrState | 'all'>('open')
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  useEffect(() => { const t = setTimeout(() => setQuery(q.trim()), 300); return () => clearTimeout(t) }, [q])
  const list = useLoad(() => repoApi(DEV_API, conn).prs('all', query || undefined), [conn, query])
  const items = list.data?.items ?? []
  const count = (x: RepoPrState) => items.filter((p) => p.state === x).length
  const rows = tab === 'all' ? items : items.filter((p) => p.state === tab)
  const tabs: [RepoPrState | 'all', string, number][] = [['open', '열림', count('open')], ['merged', '머지', count('merged')], ['closed', '닫힘', count('closed')], ['all', '전체', items.length]]
  return (
    <section className="rp-board">
      <div className="rp-board-head">
        <div className="rp-tabs" role="tablist" aria-label="PR 상태">
          {tabs.map(([id, label, n]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>
              {id !== 'all' && <span className={`rp-dot rp-${id}`} aria-hidden />}{label} <b>{n}</b>
            </button>
          ))}
        </div>
        <label className="search rp-search">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="제목 · 브랜치 · #번호 · 작성자" aria-label="PR 찾기" />
        </label>
        <Button size="small" onClick={list.reload} disabled={list.loading} aria-label="새로 읽기">새로 읽기</Button>
      </div>
      {list.error ? <LoadError error={list.error} /> : list.loading && !list.data ? <div className="empty"><b>읽는 중</b></div> : !rows.length ? <div className="empty"><b>PR 없음</b></div> : (
        <ul className="rp-list">
          {rows.map((p) => <PrRow key={p.number} p={p} />)}
        </ul>
      )}
    </section>
  )
}

function PrRow({ p }: { p: RepoPr }) {
  const when = p.state === 'merged' ? p.merged_at : p.state === 'closed' ? p.closed_at : p.created_at
  const verb = p.state === 'merged' ? '머지' : p.state === 'closed' ? '닫힘' : '열림'
  return (
    <li>
      <a href={repoHref(p.number)} className="rp-row">
        <span className={`rp-icon rp-${p.state}`} title={STATE_LABEL[p.state]} aria-label={STATE_LABEL[p.state]} />
        <span className="rp-main">
          <span className="rp-title">
            {p.title}
            {p.draft && <span className="tag">초안</span>}
            {p.internal && <span className="grade rp-ai">AI · {p.internal.id}</span>}
          </span>
          <span className="rp-meta">
            <span className="mono">#{p.number}</span>
            <Branches head={p.branch} base={p.base} />
            {p.author && <span>{p.author}</span>}
            <span>{verb} {ago(when)}</span>
          </span>
        </span>
        <Ci v={p.checks} />
        <span className="rp-cmt" aria-label={`코멘트 ${p.comments ?? 0}`}>
          {!!p.comments && <><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden><path d="M4 5h16v11H9l-5 4z" /></svg>{p.comments}</>}
        </span>
      </a>
    </li>
  )
}

// ---------------------------------------------------------------- 이슈 (GitHub Issues 기준: 열림·닫힘, 라벨, 담당자, 코멘트)
// 이슈 칸이 없는 형상관리(SVN·Perforce 등)는 409 NO_ISSUES. 그런 연결은 나중에 게시판·요청란으로 대신한다

const ISSUE_LABEL: Record<RepoIssueState, string> = { open: '열림', closed: '닫힘' }
const issueTone = (i: RepoIssue) => (i.state === 'open' ? 'rp-open' : i.reason === 'not_planned' ? 'rp-iss-skip' : 'rp-iss-done')

function IssueBoard({ conn }: { conn: string }) {
  const [tab, setTab] = useState<RepoIssueState | 'all'>('open')
  const [label, setLabel] = useState('')
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  useEffect(() => { const t = setTimeout(() => setQuery(q.trim()), 300); return () => clearTimeout(t) }, [q])
  const list = useLoad(() => repoApi(DEV_API, conn).issues('all', query || undefined), [conn, query])
  const items = list.data?.items ?? []
  const labels = [...new Set(items.flatMap((i) => i.labels))].sort()
  const byLabel = label ? items.filter((i) => i.labels.includes(label)) : items
  const rows = tab === 'all' ? byLabel : byLabel.filter((i) => i.state === tab)
  const count = (x: RepoIssueState) => byLabel.filter((i) => i.state === x).length
  const tabs: [RepoIssueState | 'all', string, number][] = [['open', '열림', count('open')], ['closed', '닫힘', count('closed')], ['all', '전체', byLabel.length]]
  return (
    <section className="rp-board">
      <div className="rp-board-head">
        <div className="rp-tabs" role="tablist" aria-label="이슈 상태">
          {tabs.map(([id, text, n]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>
              {id !== 'all' && <span className={`rp-dot ${id === 'open' ? 'rp-open' : 'rp-iss-done'}`} aria-hidden />}{text} <b>{n}</b>
            </button>
          ))}
        </div>
        {labels.length > 0 && (
          <select className="select" value={label} onChange={(e) => setLabel(e.target.value)} aria-label="라벨">
            <option value="">라벨 전체</option>
            {labels.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        )}
        <label className="search rp-search">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="제목 · #번호 · 작성자 · 라벨" aria-label="이슈 찾기" />
        </label>
        <Button size="small" onClick={list.reload} disabled={list.loading} aria-label="새로 읽기">새로 읽기</Button>
      </div>
      {list.error ? <LoadError error={list.error} /> : list.loading && !list.data ? <div className="empty"><b>읽는 중</b></div> : !rows.length ? <div className="empty"><b>이슈 없음</b></div> : (
        <ul className="rp-list">
          {rows.map((i) => <IssueRow key={i.number} i={i} onLabel={setLabel} />)}
        </ul>
      )}
    </section>
  )
}

function IssueRow({ i, onLabel }: { i: RepoIssue; onLabel: (l: string) => void }) {
  return (
    <li>
      <a href={issueHref(i.number)} className="rp-row">
        <span className={`rp-icon ${issueTone(i)}`} title={ISSUE_LABEL[i.state]} aria-label={ISSUE_LABEL[i.state]} />
        <span className="rp-main">
          <span className="rp-title">
            {i.title}
            {i.labels.map((l) => (
              <button key={l} type="button" className="rp-label" onClick={(e) => { e.preventDefault(); onLabel(l) }} title={`${l} 라벨만 보기`}>{l}</button>
            ))}
          </span>
          <span className="rp-meta">
            <span className="mono">#{i.number}</span>
            {i.author && <span>{i.author}</span>}
            <span>{i.state === 'closed' ? `닫힘 ${ago(i.closed_at)}` : `열림 ${ago(i.created_at)}`}</span>
            {i.assignees.length > 0 && <span>담당 {i.assignees.join(', ')}</span>}
          </span>
        </span>
        <span />
        <span className="rp-cmt" aria-label={`코멘트 ${i.comments ?? 0}`}>
          {!!i.comments && <><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden><path d="M4 5h16v11H9l-5 4z" /></svg>{i.comments}</>}
        </span>
      </a>
    </li>
  )
}

function IssueView({ conn, number }: { conn: string; number: number }) {
  const toast = useToast()
  const api = useMemo(() => repoApi(DEV_API, conn), [conn])
  const det = useLoad(() => api.issue(number), [api, number])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const back = <a href={issueHref()} className="rp-back">‹ 이슈 목록</a>
  if (det.error) return <>{back}<LoadError error={det.error} /></>
  if (!det.data) return <>{back}<div className="empty"><b>읽는 중</b></div></>
  const x = det.data
  const i = x.issue
  const send = async () => {
    if (!text.trim()) return
    setBusy(true)
    try {
      await api.commentIssue(number, text)
      setText('')
      toast.show('코멘트를 남겼어요')
      det.reload()
    } catch (e) {
      toast.show(errText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="rp-detail">
      {back}
      <header className="rp-head">
        <div className="rp-head-main">
          <h1 className="rp-h1">{i.title} <span className="rp-num">#{i.number}</span></h1>
          <div className="rp-head-meta">
            <span className={`rp-state ${issueTone(i)}`}>{i.state === 'closed' ? (i.reason === 'not_planned' ? '안 함' : '완료') : '열림'}</span>
            {i.author && <span>{i.author}</span>}
            <span>{ago(i.created_at)}</span>
            {i.url && <a href={i.url} target="_blank" rel="noopener noreferrer" className="plain-link">레포에서 보기</a>}
          </div>
        </div>
      </header>
      <div className="rp-body">
        <div className="rp-talk">
          <article className="rp-cmt-box">
            <div className="rp-cmt-h"><b>{i.author || '-'}</b><span>{ago(i.created_at)}</span></div>
            <div className="rp-cmt-body">{i.body?.trim() ? i.body : <span className="muted">설명 없음</span>}</div>
          </article>
          {x.comments.map((c) => (
            <article key={c.id} className="rp-cmt-box">
              <div className="rp-cmt-h"><b>{c.author || '-'}</b><span>{ago(c.at)}</span></div>
              <div className="rp-cmt-body">{c.body}</div>
            </article>
          ))}
          {!x.read_only && (
            <div className="rp-compose">
              <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="코멘트" aria-label="코멘트"
                onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) void send() }} />
              <div><Button variant="primary" size="small" disabled={busy || !text.trim()} onClick={() => void send()}>코멘트</Button></div>
            </div>
          )}
        </div>
        <aside className="rp-side">
          <section><h3>담당자</h3><p>{i.assignees.length ? i.assignees.join(', ') : <span className="muted">없음</span>}</p></section>
          <section><h3>라벨</h3><p>{i.labels.length ? i.labels.map((l) => <span key={l} className="rp-label">{l}</span>) : <span className="muted">없음</span>}</p></section>
          <section><h3>코멘트</h3><p>{x.comments.length}</p></section>
        </aside>
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- PR 상세

type Pending = { kind: 'merge' | 'close' | 'reopen' | 'revert' } | null

function PrView({ conn, number, tab }: { conn: string; number: number; tab?: string }) {
  const toast = useToast()
  const api = useMemo(() => repoApi(DEV_API, conn), [conn])
  const det = useLoad(() => api.pr(number), [api, number])
  const [method, setMethod] = useState<MergeMethod>('merge')
  const [pending, setPending] = useState<Pending>(null)
  const [busy, setBusy] = useState(false)
  if (det.error) return <><Back /><LoadError error={det.error} /></>
  if (!det.data) return <><Back /><div className="empty"><b>읽는 중</b></div></>
  const x = det.data
  const p = x.pr
  const view = tab === 'commits' ? 'commits' : tab === 'files' ? 'files' : 'talk'
  const ro = x.read_only

  const act = async (kind: NonNullable<Pending>['kind']) => {
    setBusy(true)
    try {
      if (kind === 'merge') { const r = await api.merge(number, method); toast.show(r.via === 'internal' ? 'AI 작업 흐름으로 머지했어요' : '머지했어요') }
      if (kind === 'close') { await api.close(number); toast.show('PR을 닫았어요') }
      if (kind === 'reopen') { await api.reopen(number); toast.show('PR을 다시 열었어요') }
      if (kind === 'revert') {
        const r = await api.revert(number)
        toast.show(`되돌리기 PR #${r.revert.number}을 열었어요`)
        go(repoHref(r.revert.number))
      }
      det.reload()
    } catch (e) {
      toast.show(errText(e))
    } finally {
      setBusy(false)
      setPending(null)
    }
  }

  const confirmRows: [string, ReactNode][] = [['PR', <><span className="mono">#{p.number}</span> {p.title}</>], ['브랜치', <Branches head={p.branch} base={p.base} />]]
  const lines = x.diff.files.reduce((n, f) => n + f.additions + f.deletions, 0)
  return (
    <section className="rp-detail">
      <Back />
      <header className="rp-head">
        <div className="rp-head-main">
          <h1 className="rp-h1">{p.title} <span className="rp-num">#{p.number}</span></h1>
          <div className="rp-head-meta">
            <PrState v={p.state} />
            {x.internal && <AiTag id={x.internal.id} />}
            <Branches head={p.branch} base={p.base} />
            {p.author && <span>{p.author}</span>}
            <span>{ago(p.created_at)}</span>
          </div>
        </div>
        <div className="rp-actions">
          {p.state === 'open' && <MergeButton method={method} setMethod={setMethod} disabled={ro || busy} onMerge={() => setPending({ kind: 'merge' })} />}
          {p.state === 'open' && <Button disabled={ro || busy} onClick={() => setPending({ kind: 'close' })}>닫기</Button>}
          {p.state === 'closed' && <Button disabled={ro || busy} onClick={() => setPending({ kind: 'reopen' })}>다시 열기</Button>}
          {p.state === 'merged' && <Button disabled={ro || busy} onClick={() => setPending({ kind: 'revert' })}>되돌리기</Button>}
        </div>
      </header>

      <nav className="subtabs rp-subtabs" aria-label="PR 보기">
        <a href={repoHref(number)} className={view === 'talk' ? 'on' : ''} aria-current={view === 'talk' ? 'page' : undefined}>대화 <span className="subtab-count">{x.comments.length}</span></a>
        <a href={repoHref(number, 'commits')} className={view === 'commits' ? 'on' : ''} aria-current={view === 'commits' ? 'page' : undefined}>커밋 <span className="subtab-count">{x.commits.length}</span></a>
        <a href={repoHref(number, 'files')} className={view === 'files' ? 'on' : ''} aria-current={view === 'files' ? 'page' : undefined}>변경 파일 <span className="subtab-count">{x.diff.total_files}</span></a>
        <span className="rp-diffstat"><span className="rp-add">+{x.diff.files.reduce((n, f) => n + f.additions, 0)}</span> <span className="rp-del">−{x.diff.files.reduce((n, f) => n + f.deletions, 0)}</span></span>
      </nav>

      <div className={view === 'files' ? 'rp-files-wrap' : 'rp-body'}>
        {view === 'talk' && <Talk x={x} api={api} readOnly={ro} onDone={det.reload} />}
        {view === 'commits' && <Commits x={x} />}
        {view === 'files' && <DiffFiles files={x.diff.files} truncated={x.diff.truncated} total={x.diff.total_files} draftKey={`${conn}#${number}`} editable={!ro && p.state === 'open'} />}
        {view !== 'files' && <Sidebar x={x} lines={lines} />}
      </div>

      {pending?.kind === 'merge' && <Confirm title="머지" ok={`${METHOD_LABEL[method]} 머지`} busy={busy} onClose={() => setPending(null)} onOk={() => act('merge')} rows={[...confirmRows, ['방식', METHOD_LABEL[method]], ['CI', x.checks ? CI_LABEL[x.checks.state] : '-'], ...(x.internal ? [['흐름', `AI 작업 ${x.internal.id}`] as [string, ReactNode]] : [])]} />}
      {pending?.kind === 'close' && <Confirm title="PR 닫기" ok="닫기" danger busy={busy} onClose={() => setPending(null)} onOk={() => act('close')} rows={[...confirmRows, ...(x.internal ? [['흐름', `AI 작업 ${x.internal.task} 중단`] as [string, ReactNode]] : [])]} />}
      {pending?.kind === 'reopen' && <Confirm title="PR 다시 열기" ok="다시 열기" busy={busy} onClose={() => setPending(null)} onOk={() => act('reopen')} rows={confirmRows} />}
      {pending?.kind === 'revert' && <Confirm title="되돌리기 PR 열기" ok="되돌리기 PR 열기" danger busy={busy} onClose={() => setPending(null)} onOk={() => act('revert')} rows={[...confirmRows, ['새 PR', `Revert "${p.title}"`], ['바꾼 줄', `${lines}줄 · ${x.diff.total_files}파일`]]} />}
    </section>
  )
}

function Back() {
  return <a href={repoHref()} className="rp-back">‹ PR 목록</a>
}

function MergeButton({ method, setMethod, disabled, onMerge }: { method: MergeMethod; setMethod: (m: MergeMethod) => void; disabled?: boolean; onMerge: () => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useDismiss(ref, open, () => setOpen(false))
  return (
    <div className="rp-split" ref={ref}>
      <Button variant="primary" disabled={disabled} onClick={onMerge}>머지 · {METHOD_LABEL[method]}</Button>
      <Button variant="primary" disabled={disabled} aria-label="머지 방식" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}>▾</Button>
      {open && (
        <div className="rp-menu" role="menu">
          {(Object.keys(METHOD_LABEL) as MergeMethod[]).map((m) => (
            <button key={m} type="button" role="menuitemradio" aria-checked={m === method} className={m === method ? 'on' : ''} onClick={() => { setMethod(m); setOpen(false) }}>
              {METHOD_LABEL[m]}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Talk({ x, api, readOnly, onDone }: { x: RepoPrDetail; api: ReturnType<typeof repoApi>; readOnly: boolean; onDone: () => void }) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const send = async () => {
    if (!text.trim()) return
    setBusy(true)
    try {
      await api.comment(x.pr.number, text)
      setText('')
      toast.show('코멘트를 남겼어요')
      onDone()
    } catch (e) {
      toast.show(errText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="rp-talk">
      <article className="rp-cmt-box">
        <div className="rp-cmt-h"><b>{x.pr.author || '-'}</b><span>{ago(x.pr.created_at)}</span></div>
        <div className="rp-cmt-body">{x.pr.body?.trim() ? x.pr.body : <span className="muted">설명 없음</span>}</div>
      </article>
      {x.checks && (
        <div className={`rp-ci-sum rp-ci-sum-${x.checks.state}`}>
          <Ci v={x.checks.state} /><b>{CI_LABEL[x.checks.state]}</b>
          <span className="muted">{x.checks.runs.filter((r) => r.state === 'passed').length}/{x.checks.runs.length}</span>
        </div>
      )}
      {x.comments.map((c) => (
        <article key={c.id} className="rp-cmt-box">
          <div className="rp-cmt-h"><b>{c.author || '-'}</b><span>{ago(c.at)}</span></div>
          <div className="rp-cmt-body">{c.body}</div>
        </article>
      ))}
      {!readOnly && (
        <div className="rp-compose">
          <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="코멘트" aria-label="코멘트"
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) void send() }} />
          <div><Button variant="primary" size="small" disabled={busy || !text.trim()} onClick={() => void send()}>코멘트</Button></div>
        </div>
      )}
    </div>
  )
}

function Commits({ x }: { x: RepoPrDetail }) {
  if (!x.commits.length) return <div className="empty"><b>커밋 없음</b></div>
  return (
    <ol className="rp-commits">
      {x.commits.map((c) => {
        const [first, ...rest] = c.message.split('\n')
        return (
          <li key={c.sha}>
            <div className="rp-commit-main">
              <b>{first}</b>
              {rest.join('\n').trim() && <span className="muted small">…</span>}
              <span className="rp-meta">{c.author && <span>{c.author}</span>}<span>{ago(c.at)}</span></span>
            </div>
            <code className="rp-sha">{c.sha.slice(0, 7)}</code>
          </li>
        )
      })}
    </ol>
  )
}

function Sidebar({ x, lines }: { x: RepoPrDetail; lines: number }) {
  return (
    <aside className="rp-side">
      <section>
        <h3>CI</h3>
        {x.checks?.runs.length ? (
          <ul className="rp-runs">{x.checks.runs.map((r, i) => <li key={`${r.name}-${i}`}><Ci v={r.state} /><span>{r.name || '-'}</span></li>)}</ul>
        ) : <span className="muted small">검사 없음</span>}
      </section>
      <section>
        <h3>연결된 작업</h3>
        {x.internal ? (
          <dl className="pairs">
            <div><dt>내부 PR</dt><dd><a href={toPath(`work/prs/${prSlug(x.internal.id)}`)}>{x.internal.id}</a></dd></div>
            <div><dt>작업</dt><dd><a href={toPath(`work/prs/${encodeURIComponent(x.internal.task)}`)}>{x.internal.task}</a></dd></div>
            <div><dt>근거</dt><dd><a href={toPath(`work/prs/${prSlug(x.internal.id)}`)}>근거 체인</a></dd></div>
          </dl>
        ) : <span className="muted small">사람이 올린 PR</span>}
      </section>
      <section>
        <h3>브랜치</h3>
        <dl className="pairs">
          <div><dt>head</dt><dd><code>{x.pr.branch}</code></dd></div>
          <div><dt>base</dt><dd><code>{x.pr.base}</code></dd></div>
          <div><dt>크기</dt><dd>{lines}줄 · {x.diff.total_files}파일</dd></div>
        </dl>
        <a href={toPath('work/prs/branches')} className="small">브랜치 목록</a>
      </section>
      <section>
        <h3>레포</h3>
        <code className="small">{x.repo || x.connection}</code>
        {x.pr.url && /^https?:\/\//.test(x.pr.url) && <div><a href={x.pr.url} target="_blank" rel="noopener noreferrer" className="small">원본 열기 ↗</a></div>}
      </section>
    </aside>
  )
}

// ---------------------------------------------------------------- 브랜치

function BranchTable({ conn }: { conn: string }) {
  const toast = useToast()
  const list = useLoad(() => repoApi(DEV_API, conn).branches(), [conn])
  const [del, setDel] = useState<RepoBranch | null>(null)
  const [busy, setBusy] = useState(false)
  if (list.error) return <LoadError error={list.error} />
  if (!list.data) return <div className="empty"><b>읽는 중</b></div>
  const ro = list.data.read_only
  const remove = async (b: RepoBranch) => {
    setBusy(true)
    try {
      await repoApi(DEV_API, conn).deleteBranch(b.name)
      toast.show(`${b.name} 브랜치를 지웠어요`)
      list.reload()
    } catch (e) {
      toast.show(errText(e))
    } finally {
      setBusy(false)
      setDel(null)
    }
  }
  const keep = (b: RepoBranch) => b.default || b.base || b.protected || b.pr?.state === 'open'
  return (
    <section>
      <div className="table-wrap">
        <table className="data rp-branches">
          <thead><tr><th>브랜치</th><th>마지막 커밋</th><th className="num">앞섬</th><th className="num">뒤처짐</th><th>PR</th><th aria-label="지우기" /></tr></thead>
          <tbody>
            {list.data.items.map((b) => (
              <tr key={b.name} className={b.default || b.base ? 'rp-pin' : undefined}>
                <td>
                  <code>{b.name}</code>
                  <span className="tags rp-br-tags">
                    {b.default && <span className="tag tag-green">기본</span>}
                    {b.base && <span className="tag">기준</span>}
                    {b.protected && <span className="tag">보호</span>}
                    {b.pr?.state === 'merged' && <span className="tag">머지됨</span>}
                  </span>
                </td>
                <td className="muted">{ago(b.last_commit_at) || '-'}</td>
                <td className="num">{b.ahead ?? '-'}</td>
                <td className="num">{b.behind ?? '-'}</td>
                <td>{b.pr ? <a href={repoHref(b.pr.number)} className="rp-br-pr"><span className={`rp-dot rp-${b.pr.state}`} aria-hidden /><span className="mono">#{b.pr.number}</span> {STATE_LABEL[b.pr.state]}</a> : <span className="muted">-</span>}</td>
                <td className="num">{!keep(b) && !ro && <Button size="small" disabled={busy} onClick={() => setDel(b)} aria-label={`${b.name} 지우기`}>지우기</Button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {del && <Confirm title="브랜치 지우기" ok="지우기" danger busy={busy} onClose={() => setDel(null)} onOk={() => void remove(del)} rows={[['브랜치', <code>{del.name}</code>], ['PR', del.pr ? `#${del.pr.number} ${STATE_LABEL[del.pr.state]}` : '-'], ['앞섬', del.ahead ?? '-']]} />}
    </section>
  )
}

// ---------------------------------------------------------------- 커밋 그래프 (SourceTree처럼: 줄 · 점 · 머지 선 · 브랜치·PR 꼬리표)

const graphHref = (sha?: string) => toPath(sha ? `work/prs/graph/${sha}` : 'work/prs/graph')
const LANE = 14
const ROW = 30
const GRAPH_LIMITS = [100, 200, 500] as const
const GRAPH_KEY = 'ax.repo.graph.limit'

function readLimit(): number {
  try {
    const n = Number(localStorage.getItem(GRAPH_KEY))
    return (GRAPH_LIMITS as readonly number[]).includes(n) ? n : 200
  } catch { return 200 }
}

function GraphView({ conn, sel }: { conn: string; sel?: string }) {
  const api = useMemo(() => repoApi(DEV_API, conn), [conn])
  const [limit, setLimitState] = useState(readLimit)
  const [picked, setPicked] = useState<string[] | null>(null) // null = 브랜치 전체
  const g = useLoad(() => api.graph(picked, limit), [api, limit, picked?.join('\n') ?? '*'])
  const setLimit = (n: number) => { setLimitState(n); try { localStorage.setItem(GRAPH_KEY, String(n)) } catch { /* 기억 못 해도 된다 */ } }
  const data = g.data
  const commits = useMemo(() => (Array.isArray(data?.commits) ? data.commits : []), [data])
  const base = data?.base_branch ?? ''
  const layout = useMemo(() => {
    const heads = data?.heads ?? {}
    const def = data?.branches?.find((b) => b.default)?.name
    const reserve: [string, string][] = []
    if (base && heads[base]) reserve.push([base, heads[base]])
    if (def && def !== base && heads[def]) reserve.push([def, heads[def]])
    return layoutGraph(commits, base, reserve)
  }, [commits, data, base])
  const bySha = useMemo(() => new Map(commits.map((c) => [c.sha, c])), [commits])

  const open = (sha?: string) => { go(graphHref(sha)) }
  useEffect(() => {
    if (!sel) return
    // 브랜치 고르기 창이 열려 있으면 Esc는 그 창만 닫는다
    const on = (e: KeyboardEvent) => { if (e.key === 'Escape' && !(e.target instanceof HTMLInputElement) && !document.querySelector('.rg-pick-menu')) open() }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [sel])

  const onRowKey = (e: ReactKeyboardEvent<HTMLTableRowElement>, sha: string) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(sha) }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const next = (e.key === 'ArrowDown' ? e.currentTarget.nextElementSibling : e.currentTarget.previousElementSibling) as HTMLElement | null
      next?.focus()
    }
  }

  const branchCount = data?.branches?.length ?? 0
  return (
    <section className="rp-board rg">
      <div className="rp-board-head">
        <BranchPick branches={data?.branches ?? []} base={base} value={picked} onChange={setPicked} />
        <div className="seg rg-limit" role="group" aria-label="커밋 수">
          {GRAPH_LIMITS.map((n) => (
            <button key={n} type="button" className={limit === n ? 'on' : ''} aria-pressed={limit === n} onClick={() => setLimit(n)}>{n}</button>
          ))}
        </div>
        {data && (
          <span className="rg-count">
            <span>커밋 <b>{commits.length}</b></span>
            <span>브랜치 <b>{picked ? `${data.selected.length}/${branchCount}` : branchCount}</b></span>
            {data.truncated && <span className="tag tag-amber">한도까지</span>}
          </span>
        )}
        <Button size="small" onClick={g.reload} disabled={g.loading} aria-label="새로 읽기">{g.loading && data ? '읽는 중' : '새로 읽기'}</Button>
      </div>
      {g.error ? <LoadError error={g.error} /> : !data ? <div className="empty"><b>읽는 중</b></div> : !commits.length ? <div className="empty"><b>커밋 없음</b></div> : (
        <div className={sel ? 'rg-body rg-with-side' : 'rg-body'}>
          <div className="rg-scroll">
            <table className="rg-table" aria-label="커밋 그래프" aria-rowcount={commits.length}>
              <thead>
                <tr>
                  <th className="rg-g" style={{ width: layout.width * LANE + 8 }}>그래프</th>
                  <th>메시지</th>
                  <th className="rg-author">작성자</th>
                  <th className="rg-when">시각</th>
                  <th className="rg-shacol">커밋</th>
                </tr>
              </thead>
              <tbody>
                {commits.map((c, i) => (
                  <tr key={c.sha} tabIndex={0} className={sel === c.sha ? 'on' : undefined} aria-selected={sel === c.sha}
                    onClick={() => open(sel === c.sha ? undefined : c.sha)} onKeyDown={(e) => onRowKey(e, c.sha)}>
                    <td className="rg-g"><GraphCell row={layout.rows[i]} width={layout.width} c={c} head={c.refs.length > 0} /></td>
                    <td className="rg-msg">
                      <span className="rg-msg-in">
                        <Labels c={c} base={base} />
                        <span className="rg-text" title={c.message}>{c.message.split('\n')[0]}</span>
                      </span>
                    </td>
                    <td className="rg-author">{c.author || '-'}</td>
                    <td className="rg-when" title={kst(c.at)}>{ago(c.at)}</td>
                    <td className="rg-shacol"><code className="rg-sha">{c.sha.slice(0, 7)}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {sel && <CommitPanel key={sel} api={api} sha={sel} row={bySha.get(sel)} base={base} known={bySha} onPick={open} onClose={() => open()} />}
        </div>
      )}
    </section>
  )
}

function GraphCell({ row, width, c, head }: { row?: GraphRow; width: number; c: RepoGraphCommit; head: boolean }) {
  if (!row) return null
  const w = width * LANE + 8
  const x = (k: number) => 4 + k * LANE + LANE / 2
  const ys = [0, ROW / 2, ROW]
  const label = `${c.sha.slice(0, 7)} · ${row.merge ? '머지 커밋' : '커밋'} · 부모 ${c.parents.length}${c.refs.length ? ` · ${c.refs.join(', ')}` : ''}`
  return (
    <svg className="rg-svg" width={w} height={ROW} viewBox={`0 0 ${w} ${ROW}`} role="img" aria-label={label}>
      {row.segs.map((s, i) => {
        const x1 = x(s.a)
        const x2 = x(s.b)
        const y0 = ys[s.y0]
        const y1 = ys[s.y1]
        const m = (y0 + y1) / 2
        const d = x1 === x2 ? `M${x1} ${y0}V${y1}` : `M${x1} ${y0}C${x1} ${m} ${x2} ${m} ${x2} ${y1}`
        return <path key={i} d={d} className={`rg-c${s.c}`} />
      })}
      <circle cx={x(row.col)} cy={ROW / 2} r={head ? 5 : 4} className={`rg-node rg-c${row.c}${row.merge ? ' rg-merge' : ''}`}>
        <title>{label}</title>
      </circle>
    </svg>
  )
}

/** 브랜치 끝·PR 꼬리표. 기준 브랜치가 먼저 */
function Labels({ c, base }: { c: RepoGraphCommit; base: string }) {
  if (!c.refs.length && !c.prs.length) return null
  const refs = [...c.refs].sort((a, b) => Number(b === base) - Number(a === base))
  return (
    <span className="rg-labels">
      {refs.map((r) => (
        <span key={r} className={`rg-ref rg-c${laneColor(r, base, 0)}${r === base ? ' rg-ref-base' : ''}`} title={`브랜치 ${r}`}>
          <span className="rg-sw" aria-hidden />{r}
        </span>
      ))}
      {c.prs.map((p) => (
        <a key={p.number} href={repoHref(p.number)} className="rg-pr" title={`${STATE_LABEL[p.state]} · ${p.title}`} onClick={(e) => e.stopPropagation()}>
          <span className={`rp-dot rp-${p.state}`} aria-hidden />PR #{p.number}
        </a>
      ))}
    </span>
  )
}

function BranchPick({ branches, base, value, onChange }: { branches: RepoGraph['branches']; base: string; value: string[] | null; onChange: (v: string[] | null) => void }) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<Set<string>>(new Set())
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const all = branches.map((b) => b.name)
  const start = () => { setDraft(new Set(value ?? all)); setQ(''); setOpen(true) }
  const finish = () => {
    setOpen(false)
    const next = all.filter((n) => draft.has(n))
    if (!next.length) return
    const same = value ? next.length === value.length && next.every((n) => value.includes(n)) : next.length === all.length
    if (!same) onChange(next.length === all.length ? null : next)
  }
  useDismiss(ref, open, finish)
  const toggle = (n: string) => setDraft((s) => { const t = new Set(s); if (t.has(n)) t.delete(n); else t.add(n); return t })
  const shown = q.trim() ? branches.filter((b) => b.name.toLowerCase().includes(q.trim().toLowerCase())) : branches
  const label = !value ? '브랜치 전체' : value.length === 1 ? value[0] : `브랜치 ${value.length}개`
  return (
    <div className="rg-pick" ref={ref}>
      <Button size="small" aria-haspopup="dialog" aria-expanded={open} onClick={() => (open ? finish() : start())} disabled={!branches.length}>
        <span className="rg-pick-label">{label}</span> ▾
      </Button>
      {open && (
        <div className="rp-menu rg-pick-menu" role="dialog" aria-label="브랜치 고르기">
          <div className="rg-pick-quick">
            <button type="button" onClick={() => setDraft(new Set(all))}>전체</button>
            {base && all.includes(base) && <button type="button" onClick={() => setDraft(new Set([base]))}>기준만</button>}
            <button type="button" onClick={() => setDraft(new Set())}>비우기</button>
          </div>
          {branches.length > 10 && (
            <input className="rg-pick-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="브랜치 찾기" aria-label="브랜치 찾기" autoFocus />
          )}
          <div className="rg-pick-list">
            {shown.map((b) => (
              <label key={b.name} className="rg-pick-row">
                <input type="checkbox" checked={draft.has(b.name)} onChange={() => toggle(b.name)} />
                <span className={`rg-sw rg-c${laneColor(b.name, base, 0)}`} aria-hidden />
                <code>{b.name}</code>
                {b.base && <span className="tag">기준</span>}
                {b.default && <span className="tag tag-green">기본</span>}
              </label>
            ))}
          </div>
          <div className="rg-pick-foot">
            <span className="muted small">{draft.size}/{all.length}</span>
            <Button size="small" variant="primary" disabled={!draft.size} onClick={finish}>적용</Button>
          </div>
        </div>
      )}
    </div>
  )
}

function CommitPanel({ api, sha, row, base, known, onPick, onClose }: {
  api: ReturnType<typeof repoApi>; sha: string; row?: RepoGraphCommit; base: string
  known: Map<string, RepoGraphCommit>; onPick: (sha: string) => void; onClose: () => void
}) {
  const det = useLoad(() => api.commit(sha), [api, sha])
  const c = det.data?.commit
  const message = c?.message ?? row?.message ?? ''
  const [first, ...rest] = message.split('\n')
  const body = rest.join('\n').trim()
  const parents = c?.parents ?? row?.parents ?? []
  const files = c?.files ?? []
  return (
    <aside className="rg-side" aria-label="커밋 상세">
      <div className="rg-side-h">
        <code className="rp-sha">{sha.slice(0, 7)}</code>
        {parents.length > 1 && <span className="tag">머지</span>}
        <button type="button" className="rg-x" onClick={onClose} aria-label="닫기">×</button>
      </div>
      <h3 className="rg-side-title">{first || (det.loading ? '읽는 중' : '-')}</h3>
      {body && <pre className="rg-side-body">{body}</pre>}
      {det.error ? <LoadError error={det.error} /> : null}
      <dl className="pairs">
        <div><dt>작성자</dt><dd>{c?.author || row?.author || '-'}</dd></div>
        <div><dt>시각</dt><dd>{kst(c?.at ?? row?.at) || '-'}</dd></div>
        <div><dt>커밋</dt><dd><code className="rg-full">{sha}</code></dd></div>
        <div><dt>부모</dt><dd className="rg-parents">
          {parents.length ? parents.map((p) => (
            known.has(p)
              ? <button key={p} type="button" className="rg-sha rg-link" onClick={() => onPick(p)} title={known.get(p)?.message.split('\n')[0]}>{p.slice(0, 7)}</button>
              : <code key={p} className="rg-sha">{p.slice(0, 7)}</code>
          )) : <span className="muted">없음 (첫 커밋)</span>}
        </dd></div>
        {!!row?.refs.length && <div><dt>브랜치</dt><dd><Labels c={{ ...row, prs: [] }} base={base} /></dd></div>}
        {!!row?.prs.length && <div><dt>PR</dt><dd className="rg-prs">
          {row.prs.map((p) => (
            <span key={p.number}>
              <a href={repoHref(p.number)} className="rp-br-pr"><span className={`rp-dot rp-${p.state}`} aria-hidden /><span className="mono">#{p.number}</span> {p.title}</a>
              {p.internal && <AiTag id={p.internal} />}
            </span>
          ))}
        </dd></div>}
      </dl>
      <section className="rg-side-files">
        <h4>바뀐 파일 <span className="muted">{c ? c.total_files : ''}</span>
          {c && <span className="rg-side-n"><span className="rp-add">+{files.reduce((n, f) => n + f.additions, 0)}</span> <span className="rp-del">−{files.reduce((n, f) => n + f.deletions, 0)}</span></span>}
        </h4>
        {!c ? (det.loading ? <span className="muted small">읽는 중</span> : null) : !files.length ? <span className="muted small">없음</span> : (
          <ul className="rg-files">
            {files.map((f) => (
              <li key={f.path} title={f.path}>
                <span className="rg-file-name">{f.path.split('/').pop()}<span className="muted small"> {f.path.split('/').slice(0, -1).join('/')}</span></span>
                <span className="rp-tree-n"><span className="rp-add">+{f.additions}</span> <span className="rp-del">−{f.deletions}</span></span>
              </li>
            ))}
            {c.total_files > files.length && <li className="muted small">… {c.total_files - files.length}개 더</li>}
          </ul>
        )}
      </section>
      {c?.url && /^https?:\/\//.test(c.url) && <a href={c.url} target="_blank" rel="noopener noreferrer" className="small rg-ext">GitHub에서 열기 ↗</a>}
    </aside>
  )
}
