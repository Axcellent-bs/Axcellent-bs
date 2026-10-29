// 기획 지표: 지표 요약·부서 연동
import { type Dispatch } from 'react'
import { DEPT_NAME, PORTS, TODAY, activeRules, type Action, type AuditItem, type State as S } from '@/lib/planningData'
import { ActivityList, Grade, Point, Stats, Tag } from '@/components/planning-common/parts'
import { toPath } from '@/components/planning-common/links'
import { Side } from '@/components/planning-common/flow'
import { SendWindow } from '@/components/planning/depts'

interface P { s: S; d: Dispatch<Action> }

// ---------------------------------------------------------------- 지표 (모두 코드로 계산, 각 숫자는 원천 화면으로)
export function Metrics({ s }: { s: S }) {
  const prs = s.prs
  const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : '-')
  const avgPr = s.firstPrMinutes.length ? `${Math.round(s.firstPrMinutes.reduce((a, b) => a + b, 0) / s.firstPrMinutes.length)}분` : '-'
  const human = s.specs.reduce((a, x) => a + x.humanSentences, 0) + s.threads.flatMap((t) => t.messages).filter((m) => m.dept !== 'planning').length
  const today = s.audit.filter((x) => x.t.startsWith(TODAY))
  const n = (k: AuditItem['kind']) => today.filter((x) => x.kind === k).length
  return (
    <div className="flow-col">
      <section className="flow-sec">
        <h2>기획·개발</h2>
        <Stats items={[
          { label: '요청 → 첫 PR', value: avgPr, hint: '평균', href: toPath('work/prs') },
          { label: '완료 기준 일치', value: pct(prs.filter((p) => p.reviews[p.reviews.length - 1] === '통과').length, prs.length), hint: `PR ${prs.length}`, href: toPath('work/prs') },
          { label: '검수 1회 통과', value: pct(prs.filter((p) => p.reviews[0] === '통과').length, prs.length), hint: `PR ${prs.length}`, href: toPath('work/prs') },
          { label: '사람이 쓴 문장', value: human, href: toPath('talk') },
          { label: '기각 안 경고', value: s.warnings, hint: 'check_plan', href: toPath('work/specs/SPEC-7/R2') },
          { label: '초안 채택', value: pct(s.draftsAccepted, s.draftsIn), hint: `초안 ${s.draftsIn}`, href: toPath('requests/incoming') },
        ]} />
      </section>
      <section className="flow-sec">
        <h2>자동 처리 · 오늘</h2>
        <Stats items={[
          { label: '자동 확정', value: n('자동 확정'), href: toPath('metrics/audit') },
          { label: '자동 머지', value: n('자동 머지'), href: toPath('metrics/audit') },
          { label: '교차검증 대기', value: s.reviews.filter((x) => !x.result).length, href: toPath('work/reviews') },
          { label: '교차검증 승인 / 거절', value: `${n('교차검증 승인')} / ${n('교차검증 거절')}`, href: toPath('metrics/audit') },
          { label: '사용 중 기준', value: activeRules(s.autoPolicy).length, href: toPath('metrics/autonomy') },
        ]} />
      </section>
    </div>
  )
}

// ---------------------------------------------------------------- 부서 연동 (부서별 연결 지점 · 오늘 호출 수)
export function Links({ s, d }: P) {
  const count = (p: (typeof PORTS)[number]) => s.activity.filter((a) => a.point.startsWith(p.point.split(' ')[0]) && (p.dir === 'in' ? a.from === p.peer || (p.peer === 'support' && a.from === 'marketing') : a.to === p.peer)).length
  const peers = Array.from(new Set(PORTS.map((p) => p.peer)))
  return (
    <div className="flow-col">
      <SendWindow d={d} />
      <div className="peer-grid">
        {peers.map((peer) => {
          const ports = PORTS.filter((p) => p.peer === peer)
          return (
            <Side key={peer} title={DEPT_NAME[peer]} href={toPath(`metrics/activity/${peer}`)} sum={`연결 ${ports.length} · 오늘 ${ports.reduce((n, p) => n + count(p), 0)}건`}>
              <ul className="peer-rows">
                {ports.map((p) => (
                  <li key={`${p.dir}|${p.point}`} title={p.what}>
                    <span><Point>{p.point}</Point>{p.priority === 'required' && <> <Tag tone="green">필수</Tag></>}</span>
                    <span className="muted">{p.dir === 'in' ? '받음' : '보냄'}</span>
                    <span><Grade g={p.grade} /></span>
                    <span className="num">{count(p)}</span>
                  </li>
                ))}
              </ul>
            </Side>
          )
        })}
      </div>
      <details className="proj-activity">
        <summary>전체 활동 로그 {s.activity.length}</summary>
        <ActivityList s={s} items={[...s.activity].reverse()} />
      </details>
    </div>
  )
}
