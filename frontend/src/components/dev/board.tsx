// 작업 현황 = 개발 AI 첫 화면. 모든 작업이 지금 어느 단계에 있나를 한눈에 (기획 화면 요청 탭과 같은 틀, 작업·PR 탭은 작업 1건을 깊게).
// 위: 필터 한 줄 (주소 #/tasks/<필터>) + AI 사용 합계. 아래: 작업 한 건 = 제목 한 줄 + 가로 흐름바(기획서 → 코딩 → PR·검증 → 머지) + 다음.
// 멈춘 작업(사람 확인·실패·교차검증)이 먼저. 블록 통째 클릭 = 그 작업의 작업·PR 화면.
import { DEPT_NAME, type PR, type State as S, type Task } from '@/lib/planningData'
import { Tag, rowNav } from '@/components/planning-common/parts'
import { prSlug, reviewOf, toPath } from '@/components/planning-common/links'
import { duration, shortTime, toMin, type Cell } from '@/components/planning/requests'
import { OriginTag, codingState, fromDevTeam, requestOfTask, tokens } from '@/components/dev/coding'

// ---------------------------------------------------------------- 필터
type Group = 'stuck' | 'running' | 'done' | 'stopped'
export type TaskFilter = 'all' | Group
export const TASK_FILTERS: { id: TaskFilter; label: string }[] = [
  { id: 'all', label: '전체' },
  { id: 'stuck', label: '사람 확인' },
  { id: 'running', label: '진행 중' },
  { id: 'done', label: '머지됨' },
  { id: 'stopped', label: '중단' },
]
const parseFilter = (x?: string): TaskFilter => (TASK_FILTERS.some((f) => f.id === x) ? (x as TaskFilter) : 'all')

// ---------------------------------------------------------------- 단계 계산 (순수 함수)
export const TASK_STAGES = ['기획서', '코딩', 'PR·검증', '머지'] as const
export interface TaskRow { t: Task; pr?: PR; group: Group; cells: Cell[]; next: { href: string; label: string }; born?: string; last?: string; minutes: number }

const MERGED_BY: Record<string, string> = { rule: '자동 머지', review: '교차검증 후 머지', human: '사람 머지' }
const prOf = (s: S, t: Task) => {
  const all = s.prs.filter((p) => p.task === t.id)
  return all.find((p) => !p.merged && !p.closed) ?? all[all.length - 1]
}

export function taskRow(s: S, t: Task): TaskRow {
  const pr = prOf(s, t)
  const c = t.coding
  const xr = pr && !pr.merged ? reviewOf(s, pr.id) : undefined
  const merged = !!pr?.merged || t.state === '완료'
  const canceled = c?.status === 'canceled'
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
    c1.meta = c.llmCalls ? `AI ${c.llmCalls}회 · ${tokens(c.llmTokens ?? 0)}` : c.model ?? undefined
    if (c1.kind === 'now' && (held || canceled)) { c1.warn = true; c1.meta = c.error ?? c1.meta }
  } else {
    c1.value = t.owner
    c1.meta = t.state
  }

  // PR·검증
  const c2: Cell = { kind: kind(2) }
  if (pr) {
    c2.value = pr.id
    c2.href = xr ? toPath(`work/reviews/${xr.id}`) : toPath(`work/prs/${prSlug(pr.id)}`)
    c2.meta = pr.target?.status === 'proposed' ? '레포 승인 대기' : xr ? (xr.lane === 'human' ? '사람 확인 대기' : '교차검증 대기') : lastReview === '불일치' ? '리뷰 불일치' : pr.closed ? '닫힘' : merged ? lastReview ?? '통과' : c?.status === 'waiting_human' ? '사람 확인' : '머지 전'
    if (c2.kind === 'now' && group === 'stuck') c2.warn = true
  }

  // 머지
  const c3: Cell = { kind: kind(3) }
  if (merged) { c3.value = '머지됨'; c3.meta = pr?.mergedBy ? MERGED_BY[pr.mergedBy] : undefined }

  const href = pr && !pr.closed ? toPath(`work/prs/${prSlug(pr.id)}`) : c ? toPath(`work/prs/${encodeURIComponent(t.id)}`) : toPath(`work/specs/${t.spec}/${t.para}`)
  const label = group === 'stuck' ? (xr ? '검증 보기' : '사람 확인하기') : group === 'running' ? '진행 보기' : '보기'

  const acts = s.activity.filter((a) => a.text.includes(t.id)).map((a) => a.t).sort((a, b) => toMin(a) - toMin(b))
  const born = acts[0]
  const last = acts[acts.length - 1]
  const now = s.activity.reduce((m, a) => Math.max(m, toMin(a.t)), 0)
  const end = group === 'running' || group === 'stuck' ? now : last ? toMin(last) : now
  return { t, pr, group, cells: [c0, c1, c2, c3], next: { href, label }, born, last, minutes: born ? end - toMin(born) : 0 }
}

const ORDER: Record<Group, number> = { stuck: 0, running: 1, done: 2, stopped: 3 }
// 멈춘 작업 → 진행 중 → 머지됨 → 중단. 진행 중은 오래 걸린 것이 위, 끝난 것은 최근 것이 위
export function sortTaskRows(rows: TaskRow[]): TaskRow[] {
  return rows.slice().sort((a, b) => ORDER[a.group] - ORDER[b.group] || (a.group === 'done' || a.group === 'stopped' ? toMin(b.last ?? '') - toMin(a.last ?? '') : b.minutes - a.minutes))
}

// ---------------------------------------------------------------- 화면
export function TaskBoard({ s, filter }: { s: S; filter?: string }) {
  const f = parseFilter(filter)
  const all = s.tasks.map((t) => taskRow(s, t))
  const rows = sortTaskRows(f === 'all' ? all : all.filter((x) => x.group === f))
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
        {calls > 0 && <span className="rq-usage" title="모든 작업이 지금까지 쓴 LLM 호출·토큰">AI {calls}회 · <span className="mono">{tokens(used)}</span> 토큰</span>}
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
                  <span className="mono">{t.id}</span>
                  <OriginTag s={s} t={t} />
                  {row.group === 'stuck' && <Tag tone="amber">멈춤</Tag>}
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
    </div>
  )
}
