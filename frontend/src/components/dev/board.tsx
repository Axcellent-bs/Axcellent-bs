// 작업 현황 = 개발 AI 첫 화면. 모든 작업이 지금 어느 단계에 있나를 한눈에 (기획 화면 요청 탭과 같은 틀, 작업·PR 탭은 작업 1건을 깊게).
// 위: 필터 한 줄 (주소 #/tasks/<필터>) + AI 사용 합계. 아래: 작업 한 건 = 제목 한 줄 + 가로 흐름바(기획서 → 코딩 → PR·검증 → 머지) + 다음.
// 최신순(새로 생긴 작업이 위), 20개씩 페이지. 블록 통째 클릭 = 그 작업의 작업·PR 화면.
import { useEffect, useState } from 'react'
import { DEPT_NAME, type PR, type State as S, type Task } from '@/lib/planningData'
import { Tag, rowNav } from '@/components/planning-common/parts'
import { prName, prSlug, reviewOf, toPath } from '@/components/planning-common/links'
import { duration, shortTime, toMin, type Cell } from '@/components/planning/requests'
import { OriginTag, UsageBar, codingState, fromDevTeam, pctOf, requestOfTask, tokens } from '@/components/dev/coding'
import { useCodingLimits } from '@/lib/planning'

// ---------------------------------------------------------------- 필터
type Group = 'stuck' | 'running' | 'done' | 'stopped'
export type TaskFilter = 'all' | Group
export const TASK_FILTERS: { id: TaskFilter; label: string }[] = [
  { id: 'all', label: '전체' },
  { id: 'stuck', label: '확인 필요' },
  { id: 'running', label: '진행 중' },
  { id: 'done', label: '머지됨' },
  { id: 'stopped', label: '중단' },
]
const parseFilter = (x?: string): TaskFilter => (TASK_FILTERS.some((f) => f.id === x) ? (x as TaskFilter) : 'all')

// ---------------------------------------------------------------- 단계 계산 (순수 함수)
export const TASK_STAGES = ['기획서', '코딩', 'PR·검증', '머지'] as const
export interface TaskRow { t: Task; pr?: PR; group: Group; cells: Cell[]; next: { href: string; label: string }; born?: string; last?: string; minutes: number }

const MERGED_BY: Record<string, string> = { rule: '자동 머지', review: '교차검증 후 머지', human: '사람이 머지' }
const prOf = (s: S, t: Task) => {
  const all = s.prs.filter((p) => p.task === t.id)
  return all.find((p) => !p.merged && !p.closed) ?? all[all.length - 1]
}

export function taskRow(s: S, t: Task, runTokens = 600_000): TaskRow {
  const pr = prOf(s, t)
  const c = t.coding
  const xr = pr && !pr.merged ? reviewOf(s, pr.id) : undefined
  const merged = !!pr?.merged || t.state === '완료'
  // 중단·기획에 돌려보냄·폐기는 멈춘 작업 (돌려보낸 일은 기획이 받은 요청에서 다시 정한다)
  const canceled = c?.status === 'canceled' || c?.status === 'returned' || !!t.discarded
  const held = c?.status === 'waiting_human' || c?.status === 'failed' || t.state === 'C 보류'
  const lastReview = pr?.reviews[pr.reviews.length - 1]
  const group: Group = merged ? 'done' : canceled ? 'stopped' : held || xr || pr?.target?.status === 'proposed' || lastReview === '불일치' ? 'stuck' : 'running'
  const stage = merged ? 3 : pr && !pr.closed ? 2 : 1
  const kind = (i: number): Cell['kind'] => (merged || i < stage ? 'past' : i === stage ? 'now' : 'next')

  // 기획서: 개발팀이 직접 쓴 기획인지, 다른 팀 요청에서 왔는지
  const req = requestOfTask(s, t)
  const c0: Cell = { kind: 'past', value: t.spec, meta: fromDevTeam(s, t) ? '개발팀 기획' : req ? `${DEPT_NAME[req.from]} 요청` : t.para, href: toPath(`work/specs/${t.spec}/${t.para}`) }

  // 코딩: 개발 AI 진행 (AI 없이 사람이 맡은 작업은 담당자)
  const c1: Cell = { kind: kind(1) }
  if (c) {
    c1.value = codingState(c)
    c1.meta = c.mode === 'subscription' ? c.progress ?? '내 Claude' : c.llmCalls ? `AI ${c.llmCalls}회 · 토큰 ${pctOf(c.llmTokens ?? 0, runTokens)}% (${tokens(c.llmTokens ?? 0)})` : c.model ?? undefined
    if (c1.kind === 'now' && (held || canceled)) { c1.warn = true; c1.meta = c.error ?? c1.meta }
  } else {
    c1.value = t.owner
    c1.meta = t.state
  }

  // PR·검증
  const c2: Cell = { kind: kind(2) }
  if (pr) {
    c2.value = prName(pr)
    c2.href = xr ? toPath(`work/reviews/${xr.id}`) : toPath(`work/prs/${prSlug(pr.id)}`)
    c2.meta = pr.target?.status === 'proposed' ? '레포 승인 대기' : xr ? (xr.lane === 'human' ? '사람 확인 대기' : '교차검증 대기') : lastReview === '불일치' ? '리뷰에서 걸림' : pr.closed ? '닫힘' : merged ? lastReview ?? '통과' : c?.status === 'waiting_human' ? '확인 필요' : '머지 기다림'
    if (c2.kind === 'now' && group === 'stuck') c2.warn = true
  }

  // 머지
  const c3: Cell = { kind: kind(3) }
  if (merged) { c3.value = '머지됨'; c3.meta = pr?.mergedBy ? MERGED_BY[pr.mergedBy] : undefined }

  const href = pr && !pr.closed ? toPath(`work/prs/${prSlug(pr.id)}`) : c ? toPath(`work/prs/${encodeURIComponent(t.id)}`) : toPath(`work/specs/${t.spec}/${t.para}`)
  const label = group === 'stuck' ? (xr ? '검토 보기' : '확인하기') : group === 'running' ? '진행 보기' : '보기'

  const acts = s.activity.filter((a) => a.text.includes(t.id)).map((a) => a.t).sort((a, b) => toMin(a) - toMin(b))
  const born = acts[0]
  const last = acts[acts.length - 1]
  const now = s.activity.reduce((m, a) => Math.max(m, toMin(a.t)), 0)
  const end = group === 'running' || group === 'stuck' ? now : last ? toMin(last) : now
  return { t, pr, group, cells: [c0, c1, c2, c3], next: { href, label }, born, last, minutes: born ? end - toMin(born) : 0 }
}

// 최신순: 새로 생긴 작업이 위 (같으면 최근에 움직인 것이 위). 상태로 보려면 위 필터를 쓴다
export function sortTaskRows(rows: TaskRow[]): TaskRow[] {
  return rows.slice().sort((a, b) => toMin(b.born ?? '') - toMin(a.born ?? '') || toMin(b.last ?? '') - toMin(a.last ?? ''))
}

// ---------------------------------------------------------------- 화면
const PAGE_SIZE = 20
const DISCARDED_KEY = 'axcellent-dev-show-discarded'
const readShowDiscarded = () => { try { return localStorage.getItem(DISCARDED_KEY) === '1' } catch { return false } }
const writeShowDiscarded = (on: boolean) => { try { localStorage.setItem(DISCARDED_KEY, on ? '1' : '0') } catch { /* 저장이 막혀도 이번 화면에서는 바뀐다 */ } }

export function TaskBoard({ s, filter }: { s: S; filter?: string }) {
  const f = parseFilter(filter)
  const lim = useCodingLimits()
  // 폐기한 작업은 기본으로 숨긴다. 보기로 켜면 이 브라우저에 기억한다
  const [showDiscarded, setShowDiscarded] = useState(readShowDiscarded)
  const toggleDiscarded = () => setShowDiscarded((v) => { writeShowDiscarded(!v); return !v })
  const discarded = s.tasks.filter((t) => t.discarded).length
  const all = s.tasks.filter((t) => showDiscarded || !t.discarded).map((t) => taskRow(s, t, lim.runTokens))
  const sorted = sortTaskRows(f === 'all' ? all : all.filter((x) => x.group === f))
  // 페이지: 한 번에 PAGE_SIZE개. 필터를 바꾸면 첫 페이지로
  const [page, setPage] = useState(0)
  useEffect(() => setPage(0), [f])
  const pages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  const cur = Math.min(page, pages - 1)
  const rows = sorted.slice(cur * PAGE_SIZE, (cur + 1) * PAGE_SIZE)
  const calls = s.tasks.reduce((n, t) => n + (t.coding?.llmCalls ?? 0), 0)
  const used = s.tasks.reduce((n, t) => n + (t.coding?.llmTokens ?? 0), 0)

  return (
    <div className="rq">
      <nav className="rq-filter" aria-label="작업 거르기">
        {TASK_FILTERS.map((x) => {
          const n = x.id === 'all' ? all.length : all.filter((r) => r.group === x.id).length
          return (
            <a key={x.id} href={x.id === 'all' ? toPath('tasks') : toPath(`tasks/${x.id}`)} className={x.id === f ? 'on' : undefined} aria-current={x.id === f ? 'page' : undefined}>
              {x.label}<span className="rq-n">{n}</span>
            </a>
          )
        })}
        {discarded > 0 && (
          <button type="button" className={`rq-toggle${showDiscarded ? ' on' : ''}`} aria-pressed={showDiscarded} onClick={toggleDiscarded} title="폐기한 작업을 목록에 보이기·숨기기">
            {showDiscarded ? '폐기 숨기기' : '폐기 보기'}<span className="rq-n">{discarded}</span>
          </button>
        )}
        {calls > 0 && <span className="rq-usage"><UsageBar used={used} limit={lim.trial} label={`AI ${calls}회 · 서버 AI 체험`} title="모든 작업이 쓴 토큰 / 서버 AI 체험 한도" /></span>}
      </nav>

      <div className="rq-head" aria-hidden>
        {TASK_STAGES.map((x) => <span key={x}>{x}</span>)}
        <span>다음</span>
      </div>

      {rows.length === 0 && <div className="empty"><b>{TASK_FILTERS.find((x) => x.id === f)!.label} 작업 없음</b></div>}
      <ol className="rq-list">
        {rows.map((row) => {
          const { t } = row
          const nav = rowNav(row.next.href, row.next.label)
          const fin = row.group === 'done' || row.group === 'stopped'
          return (
            <li key={t.id} {...nav} className={`rq-item ${nav.className ?? ''}${fin ? ' fin' : ''}`}>
              <div className="rq-title">
                <a href={row.next.href} className="rq-name plain-link">{t.title}</a>
                <span className="rq-meta">
                  <span className="mono idc">{t.id}</span>
                  <OriginTag s={s} t={t} />
                  {row.group === 'stuck' && <Tag tone="amber">확인 필요</Tag>}
                  {t.discarded && <Tag>폐기</Tag>}
                  {t.coding?.status === 'returned' && <Tag tone="amber">기획에 돌려보냄</Tag>}
                  {row.minutes > 0 && <span title={row.born ? `시작 ${row.born}` : undefined}>{fin ? `${duration(row.minutes)} 걸림` : `${duration(row.minutes)} 경과`}</span>}
                  {fin && row.last && <span>{shortTime(row.last)}</span>}
                </span>
              </div>
              <ol className="rq-track" aria-label="진행 단계">
                {row.cells.map((c, i) => (
                  <li key={i} className={`rq-c ${c.kind}${c.warn ? ' warn' : ''}${row.group === 'done' && i === 3 ? ' end' : ''}`}>
                    <span className="rq-dot" aria-hidden />
                    <span className="sr-only">{TASK_STAGES[i]}</span>
                    {c.value && (c.href ? <a href={c.href} className="rq-v plain-link" title={c.value}>{c.value}</a> : <span className="rq-v" title={c.value}>{c.value}</span>)}
                    {c.meta && <span className="rq-m" title={c.meta}>{c.meta}</span>}
                  </li>
                ))}
              </ol>
              <div className="rq-next">
                <a href={row.next.href} className={fin ? 'rq-go muted' : 'rq-go'}>{row.next.label} ›</a>
              </div>
            </li>
          )
        })}
      </ol>
      {pages > 1 && (
        <nav className="flex items-center justify-center gap-1 pt-3 text-sm" aria-label="페이지">
          <button type="button" className="btn-quiet px-2 py-1" disabled={cur === 0} onClick={() => setPage(cur - 1)} aria-label="이전 페이지">‹</button>
          {Array.from({ length: pages }, (_, n) => (
            <button key={n} type="button" className={n === cur ? 'btn-primary px-2.5 py-1 !text-white' : 'btn-quiet px-2.5 py-1'} aria-current={n === cur ? 'page' : undefined} onClick={() => setPage(n)}>{n + 1}</button>
          ))}
          <button type="button" className="btn-quiet px-2 py-1" disabled={cur === pages - 1} onClick={() => setPage(cur + 1)} aria-label="다음 페이지">›</button>
          <span className="ml-2 text-xs text-ink-500">{sorted.length}개 중 {cur * PAGE_SIZE + 1}–{Math.min(sorted.length, (cur + 1) * PAGE_SIZE)}</span>
        </nav>
      )}
    </div>
  )
}
