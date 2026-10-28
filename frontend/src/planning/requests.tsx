// 요청 탭 = 모든 요청이 지금 어느 단계에 있나를 한눈에 (프로젝트 탭은 요청 1건을 깊게).
// 위: 같은 목록을 거르는 필터 한 줄 (주소 #/requests/<필터>). 아래: 요청 한 건 = 작은 제목 한 줄 + 가로 흐름바(접수 → 기획 → 작업 → 처리) + 다음.
// 멈춘 요청(사람 확인·질문·교차검증·채택 대기)이 먼저. 블록 통째 클릭 = 다음 할 일, 제목 클릭 = 프로젝트.
import { useEffect, useState, type Dispatch } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, TODAY, type Action, type Request, type State as S } from './data'
import { Tag, rowNav } from './parts'
import { Ref, convoHref, isFollowUp, nextOf, prSlug, reviewOf } from './links'
import { gather } from './projects'

// ---------------------------------------------------------------- 필터
// 들어온 경로를 짧은 말로 (시스템 이름은 화면에 내지 않는다)
function viaLabel(via: string): string {
  if (via.startsWith('create_ticket')) return '변경 요청'
  if (via.startsWith('ticket_draft_propose')) return '개선 초안'
  if (via.startsWith('개선 초안')) return '개선 초안'
  if (via.startsWith('link_incident')) return '장애 후속'
  return via.replace(/\s*\(.*\)$/, '')
}

export type RequestFilter = 'all' | 'incoming' | 'progress' | 'done' | 'followup'
export const FILTERS: { id: RequestFilter; label: string; match: (r: Request) => boolean }[] = [
  { id: 'all', label: '전체', match: (r) => r.state !== '중복 연결' },
  { id: 'incoming', label: '들어온 요청', match: (r) => !isFollowUp(r) && r.state === '접수' },
  { id: 'progress', label: '진행 중', match: (r) => !isFollowUp(r) && (r.state === '기획 중' || r.state === '작업 중') },
  { id: 'done', label: '해결', match: (r) => !isFollowUp(r) && r.state === '완료' },
  { id: 'followup', label: '후속', match: isFollowUp },
]
export const parseFilter = (x?: string): RequestFilter => (FILTERS.some((f) => f.id === x) ? (x as RequestFilter) : 'all')
export const incomingCount = (s: S) => s.requests.filter(FILTERS[1].match).length

// ---------------------------------------------------------------- 단계 계산 (순수 함수)
export const STAGES = ['접수', '기획', '작업', '처리'] as const
export interface Cell { kind: 'past' | 'now' | 'next'; value?: string; meta?: string; href?: string; warn?: boolean }
export interface Wait { label: string; rank: number } // rank 작을수록 먼저 (사람 확인 0 · 질문 1 · 교차검증 2 · 채택·초안 3)
export interface PipeRow { r: Request; stage: number; finished: boolean; cells: Cell[]; wait?: Wait; born?: string; last?: string; minutes: number; dups: Request[] }

const toMin = (t: string) => {
  const m = /^(\d+)-(\d+) (\d+):(\d+)$/.exec(t)
  return m ? ((+m[1] * 31 + +m[2]) * 24 + +m[3]) * 60 + +m[4] : 0
}
export const shortTime = (t?: string) => (!t ? '' : t.startsWith(TODAY) ? t.slice(6) : t)
export function duration(min: number): string {
  if (min < 60) return `${Math.max(min, 0)}분`
  if (min < 1440) return `${Math.floor(min / 60)}시간${min % 60 ? ` ${min % 60}분` : ''}`
  return `${Math.floor(min / 1440)}일 ${Math.floor((min % 1440) / 60)}시간`
}
const stageOf = (r: Request) => (r.state === '접수' ? 0 : r.state === '기획 중' ? 1 : r.state === '작업 중' ? 2 : 3)
const waitOf = (lane: 'ai' | 'human') => (lane === 'human' ? { label: '사람 확인 대기', rank: 0 } : { label: '교차검증 대기', rank: 2 })

export function pipeline(s: S, r: Request): PipeRow {
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
      c1.href = xr ? `#/work/reviews/${xr.id}` : `#/work/specs/${sp.id}`
      if (xr) { const w = waitOf(xr.lane); c1.meta = w.label; if (c1.kind === 'now') wait = w }
      else if (sp.status === '확정') c1.meta = sp.confirmedBy === 'auto' ? '자동 확정' : sp.confirmedBy === 'human' ? '사람 확인 후 확정' : '교차검증 후 확정'
      else if (sp.question) {
        c1.meta = '질문 대기'
        const th = s.threads.find((t) => t.on === sp.id)
        if (th) c1.href = `#/talk/${th.id}`
        if (c1.kind === 'now') wait = { label: '질문 대기', rank: 1 }
      } else c1.meta = '초안'
    }
    if (c1.kind === 'now' && !sp) wait = { label: '기획서 초안 전', rank: 3 }
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
      c2.value = g.prs.length > 1 ? `${pr.id} 외 ${g.prs.length - 1}` : pr.id
      const xr = reviewOf(s, pr.id)
      c2.href = xr ? `#/work/reviews/${xr.id}` : `#/work/prs/${prSlug(pr.id)}`
      if (xr) { const w = waitOf(xr.lane); c2.meta = w.label; if (c2.kind === 'now') wait = w }
      else if (pr === fix) { c2.meta = '수정 후 재검수'; if (c2.kind === 'now') wait = { label: '수정 후 재검수', rank: 2 } }
      else c2.meta = !pr.merged ? '머지 전' : pr.mergedBy === 'rule' ? '자동 머지' : pr.mergedBy === 'review' ? '교차검증 후 머지' : '사람 머지'
    }
    if (c2.kind === 'now' && wait) c2.warn = true
  }

  // 처리
  const c3: Cell = { kind: kind(3) }
  if (finished) {
    c3.value = g.replies.length ? '회신 완료' : '완료'
    c3.meta = shortTime(last)
    c3.href = `#/projects/${r.id}`
    if (g.incidents.length) {
      c3.meta = g.follow.length ? `장애 → ${g.follow.map((x) => x.id).join(', ')}` : '장애 연결'
      c3.warn = true
      c3.href = `#/work/aftercare/${g.incidents[0].ctx}`
    }
  }

  const end = finished && last ? toMin(last) : now
  return { r, stage, finished, cells: [c0, c1, c2, c3], wait, born, last, minutes: born ? end - toMin(born) : 0, dups: g.dups }
}

// 멈춘 요청 → 움직이는 요청 → 끝난 요청. 같은 묶음 안에서는 오래된 것이 위
export function sortRows(rows: PipeRow[]): PipeRow[] {
  const group = (x: PipeRow) => (x.finished ? 2 : x.wait ? 0 : 1)
  return rows.slice().sort((a, b) => group(a) - group(b) || (a.wait?.rank ?? 9) - (b.wait?.rank ?? 9) || (a.finished ? toMin(b.last ?? '') - toMin(a.last ?? '') : b.minutes - a.minutes))
}

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
  const sorted = sortRows(all.filter(FILTERS.find((x) => x.id === f)!.match).map((r) => pipeline(s, r)))
  const rows = [...sorted.filter((x) => isFresh(x.r.id)), ...sorted.filter((x) => !isFresh(x.r.id))]
  // 같은 요청으로 연결만 한 것(REQ-15 등)은 원래 요청 줄을 가리킨다
  const target = s.requests.find((x) => x.id === item)
  const focus = target?.duplicateOf ?? item
  useEffect(() => {
    if (focus) document.getElementById(`req-${focus}`)?.scrollIntoView({ block: 'center' })
  }, [focus])
  const stuck = rows.filter((x) => x.wait).length

  return (
    <div className="rq">
      <nav className="rq-filter" aria-label="요청 거르기">
        {FILTERS.map((x) => {
          const n = s.requests.filter(x.match).length
          return (
            <a key={x.id} href={x.id === 'all' ? '#/requests' : `#/requests/${x.id}`} className={x.id === f ? 'on' : undefined} aria-current={x.id === f ? 'page' : undefined}>
              {x.label}<span className="rq-n">{n}</span>
            </a>
          )
        })}
        {stuck > 0 && <span className="rq-stuck">멈춤 {stuck}</span>}
      </nav>

      <div className="rq-head" aria-hidden>
        {STAGES.map((x) => <span key={x}>{x}</span>)}
        <span>다음</span>
      </div>

      {rows.length === 0 && <div className="empty"><b>{FILTERS.find((x) => x.id === f)!.label} 없음</b></div>}
      <ol className="rq-list">
        {rows.map((row) => {
          const { r } = row
          const next = nextOf(s, r)
          const href = row.finished || next.href.startsWith('#/requests') ? `#/projects/${r.id}` : next.href
          const nav = rowNav(href, row.finished ? '프로젝트 보기' : next.label)
          const draft = !row.finished && (r.state === '접수' || (r.state === '기획 중' && !row.cells[1].href))
          return (
            <li key={r.id} id={`req-${r.id}`} {...nav} className={`rq-item ${nav.className ?? ''}${row.finished ? ' fin' : ''}${focus === r.id ? ' hl' : ''}${isFresh(r.id) ? ' fresh' : ''}`}>
              <div className="rq-title">
                <a href={`#/projects/${r.id}`} className="rq-name plain-link">{r.title}</a>
                <span className="rq-meta">
                  <span className="mono">{r.id}</span>
                  <span>{DEPT_NAME[r.from]}</span>
                  {isFollowUp(r) && <Tag tone="amber">후속</Tag>}
                  {row.dups.map((x) => <span key={x.id}>같은 요청 <Ref s={s} id={x.id} /> {DEPT_NAME[x.from]}</span>)}
                  {row.minutes > 0 && <span title={row.born ? `접수 ${row.born}` : undefined}>{row.finished ? `${duration(row.minutes)} 걸림` : `${duration(row.minutes)} 경과`}</span>}
                </span>
              </div>
              <ol className="rq-track" aria-label="진행 단계">
                {row.cells.map((c, i) => (
                  <li key={i} className={`rq-c ${c.kind}${c.warn ? ' warn' : ''}${row.finished && i === 3 ? ' end' : ''}`}>
                    <span className="rq-dot" aria-hidden />
                    <span className="sr-only">{STAGES[i]}</span>
                    {c.value && (c.href ? <a href={c.href} className="rq-v plain-link" title={c.value}>{c.value}</a> : <span className="rq-v" title={c.value}>{c.value}</span>)}
                    {c.meta && <span className="rq-m">{c.meta}</span>}
                  </li>
                ))}
              </ol>
              <div className="rq-next">
                {draft && (r.from === 'hr' || r.from === 'data') && r.state === '접수' ? (
                  <Button size="small" variant="primary" onClick={() => { d({ type: 'acceptDraft', request: r.id }); toast.show(`${r.id} 채택 · 기획서 초안`) }}>채택 · 초안</Button>
                ) : draft ? (
                  <Button size="small" variant="primary" onClick={() => { d({ type: 'writeSpec', request: r.id }); toast.show(`${r.id} 기획서 초안`) }}>기획서 초안</Button>
                ) : (
                  <a href={href} className={row.finished ? 'rq-go muted' : 'rq-go'}>{(row.finished ? '프로젝트' : next.label).replace(/\s*\(.*\)$/, '')} ›</a>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
