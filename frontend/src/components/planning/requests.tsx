// 요청 탭 = 모든 요청이 지금 어느 단계에 있나를 한눈에 (프로젝트 탭은 요청 1건을 깊게).
// 위: 같은 목록을 거르는 필터 한 줄 (주소 #/requests/<필터>). 아래: 요청 한 건 = 작은 제목 한 줄 + 가로 흐름바(접수 → 기획 → 작업 → 배포 → 처리) + 다음.
// 멈춘 요청(사람 확인·질문·교차검증·채택 대기)이 먼저. 줄 클릭 = 단계별 자세히 펼치기, 제목 클릭 = 프로젝트, "다음 ›" = 다음 할 일.
import { useEffect, useState, type Dispatch, type KeyboardEvent, type MouseEvent } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, RELATION_LABEL, TODAY, byRelation, type Action, type Activity, type CodingStatus, type PR, type Request, type State as S } from '@/lib/planningData'
import { useAllReleaseChecks, type ReleaseCheck } from '@/lib/planning'
import { Tag } from '@/components/planning-common/parts'
import { Ref, convoHref, isFollowUp, nextOf, prName, prSlug, reviewHref, reviewOf, toPath } from '@/components/planning-common/links'
import { gather } from '@/components/planning/projects'
import { releaseState } from '@/components/planning/specs'

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
  { id: 'followup', label: '후속', match: isFollowUp },
]
export const parseFilter = (x?: string): RequestFilter => (FILTERS.some((f) => f.id === x) ? (x as RequestFilter) : 'all')
export const incomingCount = (s: S) => s.requests.filter(FILTERS[1].match).length

// ---------------------------------------------------------------- 단계 계산 (순수 함수)
export const STAGES = ['접수', '기획', '작업', '배포', '처리'] as const
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
const waitOf = (lane: 'ai' | 'human') => (lane === 'human' ? { label: '사람 확인 대기', rank: 0 } : { label: '교차검증 대기', rank: 2 })

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
      c2.value = g.prs.length > 1 ? `${prName(pr)} 외 ${g.prs.length - 1}` : prName(pr)
      const xr = reviewOf(s, pr.id)
      c2.href = xr ? reviewHref(xr) : toPath(`work/prs/${prSlug(pr.id)}`)
      if (xr) { const w = waitOf(xr.lane); c2.meta = w.label; if (c2.kind === 'now') wait = w }
      else if (pr === fix) { c2.meta = '수정 후 재검수'; if (c2.kind === 'now') wait = { label: '수정 후 재검수', rank: 2 } }
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

  const end = finished && last ? toMin(last) : now
  return { r, stage, finished, cells: [c0, c1, c2, c3, c4], wait, born, last, minutes: born ? end - toMin(born) : 0, dups: g.dups }
}

// 멈춘 요청 → 움직이는 요청 → 끝난 요청. 같은 묶음 안에서는 오래된 것이 위
export function sortRows(rows: PipeRow[]): PipeRow[] {
  const group = (x: PipeRow) => (x.finished ? 2 : x.wait ? 0 : 1)
  return rows.slice().sort((a, b) => group(a) - group(b) || (a.wait?.rank ?? 9) - (b.wait?.rank ?? 9) || (a.finished ? toMin(b.last ?? '') - toMin(a.last ?? '') : b.minutes - a.minutes))
}

// ---------------------------------------------------------------- 펼친 줄: 단계마다 같은 열에 자세히
// 줄 안의 링크·버튼을 누르면 펼치기보다 그쪽이 먼저다
const inner = (t: EventTarget) => !!(t as HTMLElement).closest('a, button, input, select, textarea, label')
const HR_CHECK = { ok: ['인사 기록 겹침 없음', 'green'], warn: ['인사 기록 경고', 'amber'], unknown: ['인사 기록 확인 못 함', 'amber'] } as const
const CODING: Partial<Record<CodingStatus, string>> = {
  queued: '코딩 대기', claimed: '개발자 Claude 작업 중', reading: '코드 읽는 중', writing: '코드 쓰는 중', reviewing: '검수 중',
  opened: 'PR 열림', merged: '머지', waiting_human: '사람 확인', failed: '실패', canceled: '취소', covered: '앞 작업이 반영',
}
// 같은 지점·같은 말이 연달아 오면 한 줄로 (×n)
function squash(acts: Activity[]): { a: Activity; n: number }[] {
  const out: { a: Activity; n: number }[] = []
  for (const a of acts) {
    const last = out[out.length - 1]
    if (last && last.a.point === a.point && last.a.text === a.text) last.n += 1
    else out.push({ a, n: 1 })
  }
  return out
}
const prMerge = (p: PR) => (!p.merged ? (p.closed ? '닫힘' : '머지 전') : p.mergedBy === 'rule' ? '자동 머지' : p.mergedBy === 'review' ? '교차검증 후 머지' : '사람 머지')

function RowMore({ s, r, rc, minutes, finished }: { s: S; r: Request; rc?: ReleaseCheck; minutes: number; finished: boolean }) {
  const g = gather(s, r)
  const sp = g.spec
  const nReq = sp?.paras.filter((p) => p.kind === 'req').length ?? 0
  const nDone = sp?.paras.filter((p) => p.kind === 'done').length ?? 0
  const rel = byRelation(r.related)
  const acts = squash(s.activity.filter((a) => a.ctx === r.ctx)).slice(-6).reverse()
  const st = rc ? releaseState(rc) : undefined
  return (
    <div className="rq-more">
      <div className="rq-mc">
        <span><span className="k">보낸 곳</span> {DEPT_NAME[r.from]} · {viaLabel(r.via)}</span>
        <span className="k">근거</span>
        <span className="rq-clamp" title={r.evidence}>{r.evidence || '없음'}</span>
      </div>
      <div className="rq-mc">
        {!sp ? <span className="muted">기획서 없음</span> : (
          <>
            <a href={toPath(`work/specs/${sp.id}`)} className="plain-link rq-clamp" title={sp.title}><span className="mono">{sp.id}</span> {sp.title}</a>
            <span>요구사항 {nReq} · 완료 기준 {nDone}</span>
            <span className="rq-tags">
              <Tag tone={sp.status === '확정' ? 'green' : undefined}>{sp.status === '확정' ? (sp.confirmedBy ? { auto: '자동 확정', review: '교차검증 후 확정', human: '사람 확정' }[sp.confirmedBy] : '확정') : '초안'}</Tag>
              {sp.hrCheck && <Tag tone={HR_CHECK[sp.hrCheck][1]}>{HR_CHECK[sp.hrCheck][0]}</Tag>}
              {sp.question && <Tag tone="amber">질문 대기</Tag>}
            </span>
          </>
        )}
        {rel.length > 0 && (
          <span className="rq-tags">
            {rel.map((x) => <span key={x.id} title={x.reason}><Tag tone={x.relation === 'conflict' ? 'amber' : undefined}>{RELATION_LABEL[x.relation]}</Tag> <Ref s={s} id={x.id} /></span>)}
          </span>
        )}
      </div>
      <div className="rq-mc">
        {!g.tasks.length ? <span className="muted">작업 없음</span> : (
          <ul>
            {g.tasks.map((t) => {
              const prs = g.prs.filter((p) => p.task === t.id)
              return (
                <li key={t.id}>
                  <span className="mono">{t.id}</span> {t.para} · {t.coding ? CODING[t.coding.status] ?? t.state : t.state}
                  {prs.map((p) => <span key={p.id}> · <a href={toPath(`work/prs/${prSlug(p.id)}`)} className="plain-link">{prName(p)}</a> {prMerge(p)}</span>)}
                </li>
              )
            })}
          </ul>
        )}
      </div>
      <div className="rq-mc">
        {!rc || !st ? <span className="muted">{finished ? '대조 기록 없음' : '작업이 끝나면 대조'}</span> : (
          <>
            <span className="rq-tags"><Tag tone={st.tone} title={st.title}>{st.label}</Tag></span>
            <span className="rq-tags">
              {rc.items.map((i) => {
                const ok = i.status === 'covered' && i.content === 'match'
                return <Tag key={i.para_id} tone={ok ? 'green' : 'amber'} title={i.content_notes.join('\n') || undefined}>{i.para_id} {ok ? '기획서대로' : i.status !== 'covered' ? '작업 미완료' : i.content === 'mismatch' ? '기획서와 다름' : '검수 없음'}</Tag>
              })}
            </span>
            <span className="muted">{rc.env} · {rc.check_id}</span>
          </>
        )}
      </div>
      <div className="rq-mc">
        <span>{finished ? `${duration(minutes)} 걸림` : `${duration(minutes)} 경과`}</span>
        {g.replies.length > 0 && <span>요청 부서에 회신 {g.replies.length}건</span>}
        {g.incidents.map((i) => <a key={i.ctx} href={toPath(`work/aftercare/${i.ctx}`)} className="plain-link">장애 {i.ctx}</a>)}
        {g.follow.map((x) => <span key={x.id}>후속 <Ref s={s} id={x.id} /></span>)}
      </div>
      {acts.length > 0 && (
        <ol className="rq-acts" aria-label="최근 활동">
          {acts.map(({ a, n }, k) => (
            <li key={k}><span className="mono">{shortTime(a.t)}</span> <span className="rq-pt">{a.point}</span> {a.text}{n > 1 && <span className="muted"> ×{n}</span>}</li>
          ))}
        </ol>
      )}
    </div>
  )
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
  const checks = useAllReleaseChecks()
  const latestCheck = (r: Request) => { const sp = gather(s, r).spec; return sp ? checks.find((c) => c.spec_id === sp.id) : undefined }
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  const toggle = (id: string) => setOpen((o) => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const sorted = sortRows(all.filter(FILTERS.find((x) => x.id === f)!.match).map((r) => pipeline(s, r, latestCheck(r))))
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
            <a key={x.id} href={x.id === 'all' ? toPath('requests') : toPath(`requests/${x.id}`)} className={x.id === f ? 'on' : undefined} aria-current={x.id === f ? 'page' : undefined}>
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
          const href = row.finished || next.href.startsWith(toPath('requests')) ? toPath(`projects/${r.id}`) : next.href
          const draft = !row.finished && (r.state === '접수' || (r.state === '기획 중' && !row.cells[1].href))
          const isOpen = open.has(r.id)
          const onRow = (e: MouseEvent) => { if (!inner(e.target) && !window.getSelection()?.toString()) toggle(r.id) }
          const onKey = (e: KeyboardEvent) => { if ((e.key === 'Enter' || e.key === ' ') && !inner(e.target)) { e.preventDefault(); toggle(r.id) } }
          return (
            <li key={r.id} id={`req-${r.id}`} role="button" tabIndex={0} aria-expanded={isOpen} onClick={onRow} onKeyDown={onKey}
              className={`rq-item${isOpen ? ' open' : ''}${row.finished ? ' fin' : ''}${focus === r.id ? ' hl' : ''}${isFresh(r.id) ? ' fresh' : ''}`}>
              <div className="rq-title">
                <a href={toPath(`projects/${r.id}`)} className="rq-name plain-link">{r.title}</a>
                <span className="rq-meta">
                  <span className="mono">{r.id}</span>
                  <span>{DEPT_NAME[r.from]}</span>
                  {isFollowUp(r) && <Tag tone="amber">후속</Tag>}
                  {row.dups.map((x) => (
                    <span key={x.id} className="rq-dup">
                      같은 요청 <Ref s={s} id={x.id} /> {DEPT_NAME[x.from]}
                      <button type="button" className="rq-unlink" title={`${x.id}을 따로 기획`} onClick={() => { d({ type: 'unlinkDuplicate', request: x.id }); toast.show(`${x.id} 따로 기획`) }}>풀기</button>
                    </span>
                  ))}
                  {byRelation(r.related).slice(0, 3).map((x) => (
                    <span key={x.id} title={x.reason}>
                      <Tag tone={x.relation === 'conflict' ? 'amber' : undefined}>{RELATION_LABEL[x.relation]}</Tag> <Ref s={s} id={x.id} />
                    </span>
                  ))}
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
              {isOpen && <RowMore s={s} r={r} rc={latestCheck(r)} minutes={row.minutes} finished={row.finished} />}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
