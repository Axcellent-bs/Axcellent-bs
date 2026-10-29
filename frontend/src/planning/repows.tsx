// 작업·PR 탭의 레포 작업 공간: 연결된 레포의 PR 게시판·PR 상세(대화·커밋·변경 파일)·브랜치.
// 사람이 GitHub에 직접 올린 PR도 보이고, AI가 만든 PR은 내부 PR 화면으로 잇는다.
// 화면 모양은 GitHub·GitLab의 PR 화면 순서를 따른다 (상태 탭 · 검색 · 줄 / 머리 · 버튼 · 탭 · 곁 칸).
import { useEffect, useMemo, useRef, useState, type Dispatch, type ReactNode } from 'react'
import { Button, Modal, useDismiss, useToast } from '@dejafix/common-frontend'
import type { Action, State as S } from './data'
import { DEV_API, PlanningApiError, kst, repoApi, vcsApi, type MergeMethod, type RepoBranch, type RepoCheckState, type RepoFile, type RepoPr, type RepoPrDetail, type RepoPrState, type VcsConnection } from './api'
import { prSlug } from './links'
import { Work } from './pages'

// ---------------------------------------------------------------- 공용

const STATE_LABEL: Record<RepoPrState, string> = { open: '열림', merged: '머지됨', closed: '닫힘' }
const CI_LABEL: Record<RepoCheckState, string> = { passed: 'CI 통과', failed: 'CI 실패', pending: 'CI 진행 중' }
const METHOD_LABEL: Record<MergeMethod, string> = { merge: 'Merge commit', squash: 'Squash', rebase: 'Rebase' }
const FILE_STATUS: Record<string, string> = { added: '추가', removed: '삭제', modified: '수정', renamed: '이름 바꿈', copied: '복사', changed: '수정' }

// 오류 코드 → 해요체 알림 (백엔드 문장은 그대로 쓰지 않는다)
const ERR: Record<string, string> = {
  NO_VCS: '연결된 레포가 없어요',
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

const repoHref = (n?: number, sub?: string) => `#/work/prs/repo${n ? `/${n}` : ''}${sub ? `/${sub}` : ''}`

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
  return <a href={`#/work/prs/${prSlug(id)}`} className="grade rp-ai" title={`내부 ${id}`}>AI · {id}</a>
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

// ---------------------------------------------------------------- 탭 틀: 작업 · 레포 PR · 브랜치

export function PrsTab({ s, d, parts }: { s: S; d: Dispatch<Action>; parts: string[] }) {
  const [view, a, b] = parts
  const mode = view === 'repo' ? 'repo' : view === 'branches' ? 'branches' : 'work'
  const [conns, conn, setConn] = useConnection()
  const repo = conns.find((c) => c.id === conn)
  return (
    <>
      <div className="rp-bar">
        <nav className="seg rp-views" aria-label="작업·PR 보기">
          <a href="#/work/prs" className={mode === 'work' ? 'on' : ''} aria-current={mode === 'work' ? 'page' : undefined}>작업</a>
          <a href={repoHref()} className={mode === 'repo' ? 'on' : ''} aria-current={mode === 'repo' ? 'page' : undefined}>레포 PR</a>
          <a href="#/work/prs/branches" className={mode === 'branches' ? 'on' : ''} aria-current={mode === 'branches' ? 'page' : undefined}>브랜치</a>
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
      {mode === 'branches' && <BranchTable key={conn} conn={conn} />}
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
        location.hash = repoHref(r.revert.number)
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
        {view === 'files' && <Files x={x} />}
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
            <div><dt>내부 PR</dt><dd><a href={`#/work/prs/${prSlug(x.internal.id)}`}>{x.internal.id}</a></dd></div>
            <div><dt>작업</dt><dd><a href={`#/work/prs/${encodeURIComponent(x.internal.task)}`}>{x.internal.task}</a></dd></div>
            <div><dt>근거</dt><dd><a href={`#/work/prs/${prSlug(x.internal.id)}`}>근거 체인</a></dd></div>
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
        <a href="#/work/prs/branches" className="small">브랜치 목록</a>
      </section>
      <section>
        <h3>레포</h3>
        <code className="small">{x.repo || x.connection}</code>
        {x.pr.url && /^https?:\/\//.test(x.pr.url) && <div><a href={x.pr.url} target="_blank" rel="noopener noreferrer" className="small">원본 열기 ↗</a></div>}
      </section>
    </aside>
  )
}

// ---- 변경 파일: 왼쪽 파일 목록, 오른쪽 파일별 diff (머리 고정, 접기)

interface DiffRow { kind: 'hunk' | 'add' | 'del' | 'ctx' | 'note'; old?: number; new?: number; text: string }
const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/
function parsePatch(patch: string): DiffRow[] {
  const out: DiffRow[] = []
  let o = 0
  let n = 0
  for (const line of patch.split('\n')) {
    const m = HUNK.exec(line)
    if (m) { o = Number(m[1]); n = Number(m[2]); out.push({ kind: 'hunk', text: line }); continue }
    if (line.startsWith('+')) out.push({ kind: 'add', new: n++, text: line.slice(1) })
    else if (line.startsWith('-')) out.push({ kind: 'del', old: o++, text: line.slice(1) })
    else if (line.startsWith('\\')) out.push({ kind: 'note', text: line })
    else if (line !== '' || out.length) out.push({ kind: 'ctx', old: o++, new: n++, text: line.slice(1) })
  }
  // 끝의 빈 줄(마지막 줄바꿈)은 버린다
  while (out.length && out[out.length - 1].kind === 'ctx' && out[out.length - 1].text === '') out.pop()
  return out
}

function Files({ x }: { x: RepoPrDetail }) {
  const [shut, setShut] = useState<Set<string>>(new Set())
  const toggle = (p: string) => setShut((s) => { const t = new Set(s); if (t.has(p)) t.delete(p); else t.add(p); return t })
  const files = x.diff.files
  if (!files.length) return <div className="empty"><b>바뀐 파일 없음</b></div>
  const jump = (i: number) => document.getElementById(`rp-f-${i}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  return (
    <div className="rp-files">
      <nav className="rp-tree" aria-label="바뀐 파일">
        {files.map((f, i) => (
          <button key={f.path} type="button" onClick={() => jump(i)} title={f.path}>
            <span className="rp-tree-name">{f.path.split('/').pop()}<span className="muted small"> {f.path.split('/').slice(0, -1).join('/')}</span></span>
            <span className="rp-tree-n"><span className="rp-add">+{f.additions}</span> <span className="rp-del">−{f.deletions}</span></span>
          </button>
        ))}
        {x.diff.truncated && <span className="tag tag-amber">잘림 · {files.length}/{x.diff.total_files}</span>}
      </nav>
      <div className="rp-diffs">
        {files.map((f, i) => <FileBlock key={f.path} f={f} i={i} open={!shut.has(f.path)} onToggle={() => toggle(f.path)} />)}
      </div>
    </div>
  )
}

function FileBlock({ f, i, open, onToggle }: { f: RepoFile; i: number; open: boolean; onToggle: () => void }) {
  const rows = useMemo(() => (f.patch ? parsePatch(f.patch) : []), [f.patch])
  return (
    <section className="rp-file" id={`rp-f-${i}`}>
      <button type="button" className="rp-file-h" onClick={onToggle} aria-expanded={open}>
        <span aria-hidden className="rp-caret">{open ? '▾' : '▸'}</span>
        <code className="rp-file-path">{f.previous_path && f.previous_path !== f.path ? `${f.previous_path} → ` : ''}{f.path}</code>
        <span className="tag">{FILE_STATUS[f.status] ?? f.status}</span>
        {f.truncated && <span className="tag tag-amber">잘림</span>}
        <span className="rp-tree-n"><span className="rp-add">+{f.additions}</span> <span className="rp-del">−{f.deletions}</span></span>
      </button>
      {open && (rows.length ? (
        <div className="rp-code" role="table" aria-label={`${f.path} diff`}>
          {rows.map((r, k) => (
            <div key={k} className={`rp-ln rp-ln-${r.kind}`} role="row">
              <span className="rp-no" role="cell">{r.old ?? ''}</span>
              <span className="rp-no" role="cell">{r.new ?? ''}</span>
              <span className="rp-sign" role="cell" aria-hidden>{r.kind === 'add' ? '+' : r.kind === 'del' ? '−' : ''}</span>
              <span className="rp-txt" role="cell">{r.text}</span>
            </div>
          ))}
          {f.truncated && <div className="rp-ln rp-ln-note"><span className="rp-no" /><span className="rp-no" /><span className="rp-sign" /><span className="rp-txt">… 잘림</span></div>}
        </div>
      ) : <div className="rp-nodiff muted small">{f.truncated ? '잘림' : 'diff 없음 (바이너리·큰 파일)'}</div>)}
    </section>
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
