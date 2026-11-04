// 기획 홈: 사람이 눌러야 할 것 한 목록 + 두 흐름(요청 → 개발, 제안 → 마케팅) 단계별 개수 + 최근 활동
import { DEPT_NAME, type Dept, type State as S } from '@/lib/planningData'
import { PROPOSAL_STAGES, PROPOSAL_SKILL_LABEL, proposalEnded, proposalOpen, proposalStage } from '@/lib/proposals'
import { useAllReleaseChecks } from '@/lib/planning'
import { ActivityList, Tag, rowNav } from '@/components/planning-common/parts'
import { toPath } from '@/components/planning-common/links'
import { STAGES, pipeline, sortRows } from './requests'

interface Todo { key: string; rank: number; kind: string; from: Dept; id: string; title: string; label: string; href: string }

export function Home({ s }: { s: S }) {
  const checks = useAllReleaseChecks()
  const live = s.requests.filter((r) => r.state !== '중복 연결' && r.state !== '기각')
  const rows = sortRows(live.map((r) => pipeline(s, r, checks.find((c) => c.request_id === r.id))))
  const props = s.proposals ?? []

  const todos: Todo[] = [
    ...rows.filter((x) => x.wait && !x.finished).map((x) => ({
      key: x.r.id, rank: x.wait!.rank, kind: '요청', from: x.r.from, id: x.r.id, title: x.r.title, label: x.wait!.label, href: `projects/${x.r.id}`,
    })),
    ...props.filter(proposalOpen).map((p) => ({
      key: p.id, rank: 1, kind: '제안', from: p.from as Dept, id: p.id, title: p.title, label: `${PROPOSAL_SKILL_LABEL[p.skill] ?? p.skill} · 근거 ${p.facts.length}`, href: `proposals/${p.id}`,
    })),
  ].sort((a, b) => a.rank - b.rank)

  const reqCount = STAGES.map((_, i) => rows.filter((x) => !x.finished && x.stage === i).length)
  reqCount[STAGES.length - 1] = rows.filter((x) => x.finished).length
  const propCount = PROPOSAL_STAGES.map((_, i) => props.filter((p) => proposalStage(p) === i && !proposalEnded(p)).length)

  return (
    <div className="home-wrap">
      <section className="home-todo">
        <h3>확인할 것 <span className="home-n">{todos.length}</span></h3>
        {!todos.length ? <div className="empty"><b>없음</b></div> : (
          <ol className="sl-list">
            {todos.map((t) => {
              const nav = rowNav(toPath(t.href), `${t.id} 열기`)
              return (
                <li key={t.key} {...nav} className={`sl-item home-item ${nav.className ?? ''}`}>
                  <span><Tag tone={t.kind === '제안' ? 'green' : 'neutral'}>{t.kind}</Tag></span>
                  <span className="sl-title">
                    <span className={`rq-dept d-${t.from}`}>{DEPT_NAME[t.from]}</span>
                    <span className="mono muted sl-id">{t.id}</span>
                    <b title={t.title}>{t.title}</b>
                  </span>
                  <span><Tag tone="amber">{t.label}</Tag></span>
                </li>
              )
            })}
          </ol>
        )}
      </section>

      <div className="home-flows">
        <Flow title="요청 → 개발" href="requests" stages={STAGES} counts={reqCount} />
        <Flow title="제안 → 실행" href="proposals" stages={PROPOSAL_STAGES} counts={propCount} />
      </div>

      <section className="home-recent">
        <h3>최근 활동</h3>
        <ActivityList s={s} items={s.activity.slice(-8).reverse()} compact />
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
