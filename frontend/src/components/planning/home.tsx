// 기획 홈: 받은 요청·제안 최근 20개(메일함처럼 안 연 것은 밝게, 사람이 눌러야 할 것은 표시) + 그 아래 최근 활동 피드 + 오른쪽 두 흐름 단계별 개수
import { useEffect, useState } from 'react'
import { Pager, usePage } from '@/components/planning-common/pager'
import { DEPT_NAME, TODAY, type Dept, type State as S } from '@/lib/planningData'
import { PROPOSAL_STAGES, PROPOSAL_SKILL_LABEL, proposalEnded, proposalOpen, proposalStage } from '@/lib/proposals'
import { useAllReleaseChecks } from '@/lib/planning'
import { Grade, Point, Tag, rowNav } from '@/components/planning-common/parts'
import { LinkedLines, convoHref, toPath } from '@/components/planning-common/links'
import { STAGES, pipeline, sortRows } from './requests'

interface Todo { key: string; rank: number; kind: string; from: Dept; id: string; title: string; label: string; href: string; wait: boolean; at: string }

// 연 것 기록 (이 브라우저). 처음엔 확인할 것만 "새로 옴", 나머지는 읽은 것으로 시작한다
const SEEN_KEY = 'ax.planning.home.seen'
const loadSeen = (): Set<string> | null => { try { const v = localStorage.getItem(SEEN_KEY); return v ? new Set(JSON.parse(v) as string[]) : null } catch { return null } }
const saveSeen = (xs: Set<string>) => { try { localStorage.setItem(SEEN_KEY, JSON.stringify([...xs].slice(-400))) } catch { /* 저장이 막혀도 이번 화면에서는 반영 */ } }

export function Home({ s }: { s: S }) {
  const checks = useAllReleaseChecks()
  const live = s.requests.filter((r) => r.state !== '중복 연결' && r.state !== '기각')
  const rows = sortRows(live.map((r) => pipeline(s, r, checks.find((c) => c.request_id === r.id))))
  const props = s.proposals ?? []

  // 요청이 들어온 시각 = 그 요청 사건(ctx)의 첫 활동 ("MM-DD HH:mm")
  const firstAt = (ctx: string) => s.activity.reduce((m, x) => (x.ctx === ctx && x.t && (!m || x.t < m) ? x.t : m), '')
  const todos: Todo[] = [
    ...rows.map((x) => ({
      key: x.r.id, rank: x.wait?.rank ?? 9, kind: '요청', from: x.r.from, id: x.r.id, title: x.r.title, href: `projects/${x.r.id}`,
      wait: !!x.wait && !x.finished, label: x.wait && !x.finished ? x.wait.label : x.finished ? '완료' : STAGES[x.stage] ?? x.r.state, at: firstAt(x.r.ctx),
    })),
    ...props.map((p) => ({
      key: p.id, rank: 1, kind: '제안', from: p.from as Dept, id: p.id, title: p.title, href: `proposals/${p.id}`,
      wait: proposalOpen(p), label: proposalOpen(p) ? `${PROPOSAL_SKILL_LABEL[p.skill] ?? p.skill} · 근거 ${p.facts.length}` : p.status, at: p.created ?? '',
    })),
  ].sort((a, b) => (a.at === b.at ? b.id.localeCompare(a.id, undefined, { numeric: true }) : a.at < b.at ? 1 : -1)).slice(0, 20)
  const waiting = todos.filter((x) => x.wait).length

  const [seen, setSeen] = useState<Set<string>>(() => loadSeen() ?? new Set())
  useEffect(() => { if (loadSeen() === null) { const init = new Set(todos.filter((x) => !x.wait).map((x) => x.key)); setSeen(init); saveSeen(init) } }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const markSeen = (keys: string[]) => { const next = new Set([...seen, ...keys]); setSeen(next); saveSeen(next) }
  const unread = todos.filter((x) => !seen.has(x.key))
  const pg = usePage(todos, 10)

  const reqCount = STAGES.map((_, i) => rows.filter((x) => !x.finished && x.stage === i).length)
  reqCount[STAGES.length - 1] = rows.filter((x) => x.finished).length
  const propCount = PROPOSAL_STAGES.map((_, i) => props.filter((p) => proposalStage(p) === i && !proposalEnded(p)).length)

  return (
    <div className="home-wrap">
      <section className="home-todo">
        <h3>
          받은 요청·제안
          {unread.length > 0 && <span className="home-new">새로 {unread.length}</span>}
          {waiting > 0 && <span className="home-n" title="사람이 확인해야 하는 것">확인할 것 {waiting}</span>}
          {unread.length > 0 && <button type="button" className="home-readall" onClick={() => markSeen(unread.map((x) => x.key))}>모두 읽음</button>}
        </h3>
        {!todos.length ? <div className="empty"><b>없음</b></div> : (
          <ol className="sl-list">
            {pg.rows.map((t) => {
              const nav = rowNav(toPath(t.href), `${t.id} 열기`)
              const isNew = !seen.has(t.key)
              const { onClick, onKeyDown } = nav
              return (
                <li key={t.key} {...nav} onClick={(e) => { markSeen([t.key]); onClick?.(e) }}
                  onKeyDown={(e) => { if (e.key === 'Enter') markSeen([t.key]); onKeyDown?.(e) }}
                  className={`sl-item home-item${isNew ? ' is-new' : ''} ${nav.className ?? ''}`}>
                  <span><Tag tone={t.kind === '제안' ? 'green' : 'neutral'}>{t.kind}</Tag></span>
                  <span className="sl-title">
                    {isNew && <span className="home-dot" aria-label="새로 옴" />}
                    <span className={`rq-dept d-${t.from}`}>{DEPT_NAME[t.from]}</span>
                    <span className="mono muted sl-id">{t.id}</span>
                    <b title={t.title}>{t.title}</b>
                  </span>
                  <span>{t.wait ? <Tag tone="amber">{t.label}</Tag> : <span className="muted small">{t.label}</span>}</span>
                  <span className="muted small home-at">{t.at}</span>
                </li>
              )
            })}
          </ol>
        )}
        <Pager {...pg} />
      </section>

      <div className="home-flows">
        <Flow title="요청 → 개발" href="requests" stages={STAGES} counts={reqCount} />
        <Flow title="제안 → 실행" href="proposals" stages={PROPOSAL_STAGES} counts={propCount} />
      </div>

      <section className="home-recent">
        <h3>최근 활동 <span className="muted small home-recent-sub">AI·사람이 한 일 · 누르면 그 대화로</span></h3>
        <ol className="feed">
          {feedDays(s.activity.slice(-12).reverse()).map(([day, items]) => (
            <li key={day} className="feed-day">
              <div className="feed-day-h">{day === TODAY ? `오늘 · ${day}` : day}</div>
              <ol>
                {items.map((a, i) => {
                  const nav = rowNav(convoHref(a.ctx), '이 요청·사건의 대화 보기')
                  const who = a.kind === '사람' || a.from === 'human' ? 'human' : a.from
                  return (
                    <li key={i} {...nav} className={`feed-item ${nav.className ?? ''}`}>
                      <span className={`feed-av d-${who}`} aria-hidden>{AV[who] ?? (DEPT_NAME[a.from] ?? a.from).slice(0, 1)}</span>
                      <div className="feed-body">
                        <div className="feed-head">
                          <b>{who === 'human' ? '사람' : DEPT_NAME[a.from] ?? a.from}</b>
                          <span className="feed-to">→ {DEPT_NAME[a.to] ?? a.to}</span>
                          <Point>{a.point}</Point>
                          <Grade g={a.grade} />
                          <span className="feed-time">{a.t?.slice(6)}</span>
                        </div>
                        <div className="feed-text"><LinkedLines s={s} text={a.text} /></div>
                      </div>
                    </li>
                  )
                })}
              </ol>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}

function Flow({ title, href, stages, counts }: { title: string; href: string; stages: readonly string[]; counts: number[] }) {
  return (
    <a className="home-flow" href={toPath(href)}>
      <b>{title}</b>
      <ol>
        {stages.map((st, i) => (
          <li key={st} className={counts[i] ? 'on' : ''}><span>{st}</span><b>{counts[i]}</b></li>
        ))}
      </ol>
    </a>
  )
}

// 피드 아이콘 글자 (부서 이름 첫 글자가 어색한 것만)
const AV: Record<string, string> = { human: '사람', planning: '기획', orchestrator: '총괄' }

// 활동을 날짜(MM-DD)별로 묶는다 (최근 날짜 먼저, 받은 순서 그대로)
function feedDays(xs: S['activity']): [string, S['activity']][] {
  const out: [string, S['activity']][] = []
  for (const a of xs) {
    const day = (a.t ?? '').slice(0, 5) || '날짜 없음'
    const last = out[out.length - 1]
    if (last && last[0] === day) last[1].push(a)
    else out.push([day, [a]])
  }
  return out
}
