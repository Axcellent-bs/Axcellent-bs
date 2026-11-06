// 요청 탭 = 모든 요청이 지금 어느 단계에 있나를 한눈에 (프로젝트 탭은 요청 1건을 깊게).
// 위: 같은 목록을 거르는 필터 한 줄 (주소 #/requests/<필터>). 아래: 요청 한 건 = 작은 제목 한 줄 + 가로 흐름바(접수 → 기획 → 작업 → 배포 → 처리) + 다음.
// 멈춘 요청(사람 확인·질문·교차검증·채택 대기)이 먼저. 평소엔 제목과 흐름바만, 줄 클릭 = 흐름바 아래 글씨 펼치기, 제목 클릭 = 프로젝트, "다음 ›" = 다음 할 일.
import { Fragment, useEffect, useState, type Dispatch, type KeyboardEvent, type MouseEvent } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, RELATION_LABEL, TODAY, byRelation, type Action, type Request, type State as S } from '@/lib/planningData'
import { useAllReleaseChecks, type ReleaseCheck } from '@/lib/planning'
import { Tag } from '@/components/planning-common/parts'
import { Pager, usePage } from '@/components/planning-common/pager'
import { Ref, convoHref, isFollowUp, nextOf, prName, prSlug, reviewHref, reviewOf, toPath } from '@/components/planning-common/links'
import { gather } from '@/components/planning/projects'
import { releaseState } from '@/components/planning/specs'
import { devDiscarded, pendingReturns } from '@/lib/returned'

// ---------------------------------------------------------------- 필터
// 들어온 경로를 짧은 말로 (시스템 이름은 화면에 내지 않는다)
function viaLabel(via: string): string {
  if (via.startsWith('create_ticket')) return '변경 요청'
  if (via.startsWith('ticket_draft_propose')) return '개선 초안'
  if (via.startsWith('개선 초안')) return '개선 초안'
  if (via.startsWith('link_incident')) return '장애 후속'
  if (via === 'dev_team') return '개발팀 기획'
  return via.replace(/\s*\(.*\)$/, '')
}

export type RequestFilter = 'all' | 'incoming' | 'progress' | 'done' | 'followup'
export const FILTERS: { id: RequestFilter; label: string; match: (r: Request) => boolean }[] = [
  { id: 'all', label: '전체', match: (r) => r.state !== '중복 연결' },
  { id: 'incoming', label: '들어온 요청', match: (r) => !isFollowUp(r) && r.state === '접수' },
  { id: 'progress', label: '진행 중', match: (r) => !isFollowUp(r) && (r.state === '기획 중' || r.state === '작업 중') },
  { id: 'done', label: '해결', match: (r) => !isFollowUp(r) && r.state === '완료' },
  { id: 'followup', label: '후속 요청', match: isFollowUp },
]
export const parseFilter = (x?: string): RequestFilter => (FILTERS.some((f) => f.id === x) ? (x as RequestFilter) : 'all')
export const incomingCount = (s: S) => s.requests.filter(FILTERS[1].match).length

// ---------------------------------------------------------------- 단계 계산 (순수 함수)
export const STAGES = ['접수', '기획', '개발', '배포', '회신'] as const
export interface Cell { kind: 'past' | 'now' | 'next'; value?: string; meta?: string; href?: string; warn?: boolean }
export interface Wait { label: string; rank: number } // rank 작을수록 먼저 (사람 확인 0 · 질문 1 · 교차검증 2 · 채택·초안 3)
export interface PipeRow { r: Request; stage: number; finished: boolean; cells: Cell[]; wait?: Wait; born?: string; last?: string; minutes: number; dups: Request[] }

export const toMin = (t: string) => {
  const m = /^(\d+)-(\d+) (\d+):(\d+)$/.exec(t)
  return m ? ((+m[1] * 31 + +m[2]) * 24 + +m[3]) * 60 + +m[4] : 0
}
export const shortTime = (t?: string) => (!t ? '' : t.startsWith(TODAY) ? t.slice(6) : t)
export function duration(min: number): string {
  if (min < 60) return `${Math.max(min, 0)}분`
  if (min < 1440) return `${Math.floor(min / 60)}시간${min % 60 ? ` ${min % 60}분` : ''}`
  return `${Math.floor(min / 1440)}일 ${Math.floor((min % 1440) / 60)}시간`
}
const stageOf = (r: Request) => (r.state === '접수' ? 0 : r.state === '기획 중' ? 1 : r.state === '작업 중' ? 2 : 4)
const waitOf = (lane: 'ai' | 'human') => (lane === 'human' ? { label: '사람 확인 대기', rank: 0 } : { label: 'AI 검토 대기', rank: 2 })

export function pipeline(s: S, r: Request, rc?: ReleaseCheck): PipeRow {
  const g = gather(s, r)
  const stage = stageOf(r)
  const finished = r.state === '완료'
  const acts = s.activity.filter((a) => a.ctx === r.ctx).slice().sort((a, b) => toMin(a.t) - toMin(b.t))
  const born = (acts.find((a) => a.text.includes(r.id)) ?? acts[0])?.t
  const last = acts[acts.length - 1]?.t
  const now = s.activity.reduce((m, a) => Math.max(m, toMin(a.t)), 0)
  const kind = (i: number): Cell['kind'] => (finished || i < stage ? 'past' : i === stage ? 'now' : 'next')
  let wait: Wait | undefined

  // 접수
  const c0: Cell = { kind: kind(0), value: viaLabel(r.via), meta: shortTime(born), href: convoHref(r.ctx) }
  if (c0.kind === 'now') {
    wait = { label: r.from === 'hr' || r.from === 'data' ? '채택 판단 대기' : '기획서 초안 전', rank: 3 }
    c0.meta = wait.label
    c0.warn = true
  }

  // 기획
  const c1: Cell = { kind: kind(1) }
  if (c1.kind !== 'next') {
    const sp = g.spec
    if (!sp) c1.value = '기획서 없음'
    else {
      const xr = reviewOf(s, sp.id)
      c1.value = sp.id
      c1.href = xr ? reviewHref(xr) : toPath(`work/specs/${sp.id}`)
      if (xr) { const w = waitOf(xr.lane); c1.meta = w.label; if (c1.kind === 'now') wait = w }
      else if (sp.status === '확정') c1.meta = sp.confirmedBy === 'auto' ? '자동 확정' : sp.confirmedBy === 'human' ? '사람 확인 후 확정' : '교차검증 후 확정'
      else if (sp.question) {
        c1.meta = '질문 대기'
        const th = s.threads.find((t) => t.on === sp.id)
        if (th) c1.href = toPath(`talk/${th.id}`)
        if (c1.kind === 'now') wait = { label: '질문 대기', rank: 1 }
      } else c1.meta = '초안'
    }
    if (c1.kind === 'now' && !sp) wait = { label: '기획서 작성 전', rank: 3 }
    if (c1.kind === 'now' && wait) c1.warn = true
  }

  // 작업
  const c2: Cell = { kind: kind(2) }
  if (c2.kind !== 'next') {
    const open = g.prs.filter((p) => !p.merged)
    const xrPr = open.find((p) => reviewOf(s, p.id))
    const fix = open.find((p) => p.reviews[p.reviews.length - 1] === '불일치')
    const pr = xrPr ?? fix ?? open[0] ?? g.prs[g.prs.length - 1]
    if (!pr) { c2.value = g.tasks.length ? `작업 ${g.tasks.length}` : '작업 없음'; c2.meta = g.tasks.length ? 'PR 전' : undefined }
    else {
      c2.value = g.prs.length > 1 ? `${prName(pr)} 외 ${g.prs.length - 1}` : prName(pr)
      const xr = reviewOf(s, pr.id)
      c2.href = xr ? reviewHref(xr) : toPath(`work/prs/${prSlug(pr.id)}`)
      if (xr) { const w = waitOf(xr.lane); c2.meta = w.label; if (c2.kind === 'now') wait = w }
      else if (pr === fix) { c2.meta = '고친 뒤 다시 검사'; if (c2.kind === 'now') wait = { label: '고친 뒤 다시 검사', rank: 2 } }
      else c2.meta = !pr.merged ? '머지 전' : pr.mergedBy === 'rule' ? '자동 머지' : pr.mergedBy === 'review' ? '교차검증 후 머지' : '사람 머지'
    }
    if (c2.kind === 'now' && wait) c2.warn = true
  }

  // 배포 (배포 전 기획서 대조 → 배포 지시)
  const c3: Cell = { kind: kind(3) }
  if (rc) {
    const st = releaseState(rc)
    c3.value = st.label
    c3.meta = `${rc.env} · PR ${rc.pr_numbers.map((n) => `#${n}`).join(', ')}`
    c3.warn = st.tone === 'amber'
    if (g.spec) c3.href = toPath(`work/specs/${g.spec.id}`)
  } else if (finished) c3.meta = '대조 기록 없음'

  // 처리
  const c4: Cell = { kind: kind(4) }
  if (finished) {
    c4.value = g.replies.length ? '회신 완료' : '완료'
    c4.meta = shortTime(last)
    c4.href = toPath(`projects/${r.id}`)
    if (g.incidents.length) {
      c4.meta = g.follow.length ? `장애 → ${g.follow.map((x) => x.id).join(', ')}` : '장애 연결'
      c4.warn = true
      c4.href = toPath(`work/aftercare/${g.incidents[0].ctx}`)
    }
  }

  // 개발이 "코드 변경이 아니다"로 돌려보냄 → 사람 확인 (요청 상세에서 마케팅·개발로 다시·닫기)
  const sp = g.spec
  if (sp && pendingReturns(s, sp.id).length && !finished) {
    wait = { label: '개발이 돌려보냄', rank: 0 }
    c2.warn = true; c2.meta = '개발이 돌려보냄'; c2.href = toPath(`projects/${r.id}`)
  } else if (sp && devDiscarded(s, sp.id)) c2.meta = '개발에서 폐기함'

  // 사람이 중단한 요청: 지금 단계에서 멈춤 (맨 위로). 개발에서 폐기했으면 기다릴 것이 없다
  if (r.paused && !finished && !(sp && devDiscarded(s, sp.id))) {
    wait = { label: '사람이 중단', rank: -1 }
    const c = [c0, c1, c2, c3, c4][stage]
    if (c) { c.warn = true; c.meta = '중단됨' }
  }

  const end = finished && last ? toMin(last) : now
  return { r, stage, finished, cells: [c0, c1, c2, c3, c4], wait, born, last, minutes: born ? end - toMin(born) : 0, dups: g.dups }
}

// 멈춘 요청 → 움직이는 요청 → 끝난 요청. 같은 묶음 안에서는 오래된 것이 위
export function sortRows(rows: PipeRow[]): PipeRow[] {
  const group = (x: PipeRow) => (x.finished ? 2 : x.wait ? 0 : 1)
  return rows.slice().sort((a, b) => group(a) - group(b) || (a.wait?.rank ?? 9) - (b.wait?.rank ?? 9) || (a.finished ? toMin(b.last ?? '') - toMin(a.last ?? '') : b.minutes - a.minutes))
}

// ---------------------------------------------------------------- 줄 펼치기
// 줄 안의 링크·버튼을 누르면 펼치기보다 그쪽이 먼저다
const inner = (t: EventTarget) => !!(t as HTMLElement).closest('a, button, input, select, textarea, label')

// ---------------------------------------------------------------- 화면
// 화면을 연 뒤 새로 들어온 요청은 맨 위에 두고 흐름바를 단계별로 채워 보여 준다 (시연용, 들어온 뒤 20초)
const seenAt = new Map<string, number>()
let booted = false
const FRESH_MS = 20000

export function Requests({ s, d, filter, item }: { s: S; d: Dispatch<Action>; filter?: string; item?: string }) {
  const toast = useToast()
  const f = parseFilter(filter)
  const all = s.requests.filter(FILTERS[0].match)
  const now = Date.now()
  for (const r of s.requests) if (!seenAt.has(r.id)) seenAt.set(r.id, booted ? now : 0)
  booted = true
  const isFresh = (id: string) => now - (seenAt.get(id) ?? 0) < FRESH_MS
  const [, tick] = useState(0)
  useEffect(() => {
    const t = setTimeout(() => tick((x) => x + 1), FRESH_MS)
    return () => clearTimeout(t)
  }, [s.requests.length])
  const checks = useAllReleaseChecks()
  const latestCheck = (r: Request) => { const sp = gather(s, r).spec; return sp ? checks.find((c) => c.spec_id === sp.id) : undefined }
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  const toggle = (id: string) => setOpen((o) => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n })
  // 개발에서 작업을 모두 폐기한 요청은 기본으로 숨긴다. 보기로 켜면 이 브라우저에 기억한다 (개발 작업 목록과 같은 토글)
  const [showDropped, setShowDropped] = useState(() => { try { return localStorage.getItem('rq-show-dropped') === '1' } catch { return false } })
  const toggleDropped = () => setShowDropped((v) => { try { localStorage.setItem('rq-show-dropped', v ? '0' : '1') } catch { /* 저장이 막혀도 이번 화면에서는 바뀐다 */ } return !v })
  const isDropped = (r: Request) => devDiscarded(s, gather(s, r).spec?.id)
  const droppedN = all.filter(isDropped).length
  const shown = (r: Request) => showDropped || !isDropped(r)
  // 글자로 찾기(제목·번호)와 요청 부서 거르기. 화면 안에서만 거른다
  const [q, setQ] = useState('')
  const [dept, setDept] = useState('')
  const depts = [...new Set(all.map((r) => r.from))]
  const hit = (r: Request) => (!dept || r.from === dept) && (!q.trim() || `${r.id} ${r.title}`.toLowerCase().includes(q.trim().toLowerCase()))
  const sorted = sortRows(all.filter(shown).filter(hit).filter(FILTERS.find((x) => x.id === f)!.match).map((r) => pipeline(s, r, latestCheck(r))))
  const rows = [...sorted.filter((x) => isFresh(x.r.id)), ...sorted.filter((x) => !isFresh(x.r.id))]
  // 같은 요청으로 연결만 한 것(REQ-15 등)은 원래 요청 줄을 가리킨다
  const target = s.requests.find((x) => x.id === item)
  const focus = target?.duplicateOf ?? item
  const stuck = rows.filter((x) => x.wait).length
  // 페이지: 목록 10줄 · 카드 12장(한 줄 4장). 필터·보기·폐기 토글을 바꾸면 첫 페이지로, 주소로 가리킨 요청은 그 페이지로
  const pg = usePage(rows, 10)
  useEffect(() => pg.setPage(0), [f, showDropped, q, dept]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!focus) return
    const at = rows.findIndex((x) => x.r.id === focus)
    if (at >= 0) pg.setPage(Math.floor(at / pg.size))
    setTimeout(() => document.getElementById(`req-${focus}`)?.scrollIntoView({ block: 'center' }), 0)
  }, [focus]) // eslint-disable-line react-hooks/exhaustive-deps
  const allOpen = pg.rows.length > 0 && pg.rows.every((x) => open.has(x.r.id))

  return (
    <div className="rq">
      <nav className="rq-filter" aria-label="요청 거르기">
        {FILTERS.map((x) => {
          const n = s.requests.filter(x.match).filter(shown).length
          return (
            <a key={x.id} href={x.id === 'all' ? toPath('requests') : toPath(`requests/${x.id}`)} className={x.id === f ? 'on' : undefined} aria-current={x.id === f ? 'page' : undefined}>
              {x.label}<span className="rq-n">{n}</span>
            </a>
          )
        })}
        {droppedN > 0 && (
          <button type="button" className={`rq-toggle${showDropped ? ' on' : ''}`} aria-pressed={showDropped} onClick={toggleDropped} title="개발에서 폐기한 요청을 목록에 보이기·숨기기">
            {showDropped ? '폐기 숨기기' : '폐기 보기'}<span className="rq-n">{droppedN}</span>
          </button>
        )}
        {stuck > 0 && <span className="rq-stuck">멈춤 {stuck}</span>}
        <input type="search" className="rq-search" placeholder="제목·번호로 찾기" value={q} onChange={(e) => setQ(e.target.value)} aria-label="요청 찾기" />
        <select className="rq-select" value={dept} onChange={(e) => setDept(e.target.value)} aria-label="요청 부서">
          <option value="">요청 부서 전체</option>
          {depts.map((x) => <option key={x} value={x}>{DEPT_NAME[x] ?? x}</option>)}
        </select>
        <button type="button" className="rq-all" aria-pressed={allOpen} onClick={() => setOpen(allOpen ? new Set() : new Set(pg.rows.map((x) => x.r.id)))}>{allOpen ? '모두 접기' : '모두 펼치기'}</button>
      </nav>

      {rows.length === 0 && <div className="empty"><b>{q ? `"${q}" 찾은 요청 없음` : `${FILTERS.find((x) => x.id === f)!.label} 없음`}</b></div>}
      {rows.length > 0 && (
      <table className="data rq-table">
        <thead>
          <tr><th>번호</th><th>제목</th><th>요청 부서</th><th>진행 단계</th><th>상태</th><th>경과</th><th className="rq-th-next">다음 할 일</th></tr>
        </thead>
        <tbody>
        {pg.rows.map((row) => {
          const { r } = row
          const next = nextOf(s, r)
          const dropped = devDiscarded(s, gather(s, r).spec?.id) // 개발에서 작업을 모두 폐기함: 회색 막대·다음 동작 없음
          const href = row.finished || dropped || next.href.startsWith(toPath('requests')) ? toPath(`projects/${r.id}`) : next.href
          const draft = !row.finished && (r.state === '접수' || (r.state === '기획 중' && !row.cells[1].href))
          const isOpen = open.has(r.id)
          const onRow = (e: MouseEvent) => { if (!inner(e.target) && !window.getSelection()?.toString()) toggle(r.id) }
          const onKey = (e: KeyboardEvent) => { if ((e.key === 'Enter' || e.key === ' ') && !inner(e.target)) { e.preventDefault(); toggle(r.id) } }
          const status = dropped ? { label: '개발에서 폐기함', tone: 'gray' } : r.paused ? { label: '중단됨', tone: 'amber' } : row.wait ? { label: row.wait.label, tone: 'amber' } : row.finished ? { label: '회신 완료', tone: 'green' } : { label: `${STAGES[row.stage]} 중`, tone: 'blue' }
          const at = row.finished ? STAGES.length - 1 : row.stage
          return (
            <Fragment key={r.id}>
            <tr id={`req-${r.id}`} tabIndex={0} aria-expanded={isOpen} onClick={onRow} onKeyDown={onKey}
              className={`rq-tr${isOpen ? ' open' : ''}${row.finished ? ' fin' : ''}${dropped ? ' dropped' : ''}${focus === r.id ? ' hl' : ''}${isFresh(r.id) ? ' fresh' : ''}`}>
              <td className="rq-td-id"><Ref s={s} id={r.id} /></td>
              <td className="rq-td-title">
                <a href={toPath(`projects/${r.id}`)} className="rq-name plain-link">{r.title}</a>
                <span className="rq-meta">
                  {isFollowUp(r) && <Tag tone="amber">후속</Tag>}
                  {row.dups.map((x) => (
                    <span key={x.id} className="rq-dup">
                      같은 요청 <Ref s={s} id={x.id} /> {DEPT_NAME[x.from]}
                      <button type="button" className="rq-unlink" title={`${x.id}을 따로 기획`} onClick={() => { d({ type: 'unlinkDuplicate', request: x.id }); toast.show(`${x.id} 따로 기획`) }}>풀기</button>
                    </span>
                  ))}
                  {byRelation(r.related).slice(0, 2).map((x) => (
                    <span key={x.id} title={x.reason}>{RELATION_LABEL[x.relation]} <Ref s={s} id={x.id} /></span>
                  ))}
                </span>
              </td>
              <td><span className={`rq-dept d-${r.from}`}>{DEPT_NAME[r.from]}</span></td>
              <td className="rq-td-steps">
                <span className={`rq-mini${dropped ? ' dropped' : ''}`} aria-label={`진행 단계 ${STAGES[at]}`}>
                  {STAGES.map((x, i) => <i key={x} className={i < at || row.finished ? 'on' : i === at ? 'now' : undefined} title={x} />)}
                </span>
                <span className="rq-mini-l">{STAGES[at]}</span>
              </td>
              <td><span className={`rq-st ${status.tone}`}>{status.label}</span></td>
              <td className="rq-td-time" title={row.born ? `접수 ${row.born}` : undefined}>{row.minutes > 0 ? duration(row.minutes) : ''}</td>
              <td className="rq-td-next">
                {!row.finished && !dropped && (
                  <button type="button" className="rq-stop" onClick={() => { d({ type: r.paused ? 'resumeRequest' : 'pauseRequest', request: r.id }); toast.show(`${r.id} ${r.paused ? '재개' : '중단'}`) }}>{r.paused ? '재개' : '중단'}</button>
                )}
                {draft && (r.from === 'hr' || r.from === 'data') && r.state === '접수' ? (
                  <Button size="small" variant="primary" onClick={() => { d({ type: 'acceptDraft', request: r.id }); toast.show(`${r.id} 채택 · 기획서 초안`) }}>채택 · 초안</Button>
                ) : draft ? (
                  <Button size="small" variant="primary" onClick={() => { d({ type: 'writeSpec', request: r.id }); toast.show(`${r.id} 기획서 초안`) }}>기획서 초안</Button>
                ) : (
                  <a href={href} className={row.finished || dropped ? 'rq-go muted' : 'rq-go'}>{(row.finished || dropped ? '진행 기록' : next.label).replace(/\s*\(.*\)$/, '')} ›</a>
                )}
              </td>
            </tr>
            {isOpen && (
              <tr className="rq-detail">
                <td />
                <td colSpan={6}>
                  <ol className="rq-steps-detail">
                    {row.cells.map((c, i) => (
                      <li key={i} className={`${c.kind}${c.warn ? ' warn' : ''}`}>
                        <span className="rq-sd-k">{STAGES[i]}</span>
                        <span className="rq-sd-v">
                          {c.value ? (c.href ? <a href={c.href} className="plain-link">{c.value}</a> : c.value) : <span className="muted">—</span>}
                          {c.meta && <span className="rq-m"> · {c.meta}</span>}
                        </span>
                      </li>
                    ))}
                  </ol>
                </td>
              </tr>
            )}
            </Fragment>
          )
        })}
        </tbody>
      </table>
      )}
      <Pager {...pg} />
    </div>
  )
}
