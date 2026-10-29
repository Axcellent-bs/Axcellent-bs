// 운영·배포와 사후관리
import { useEffect, useState, type ReactNode } from 'react'
import { Button, Notice, useToast } from '@dejafix/common-frontend'
import { type State as S } from '@/lib/planningData'
import { ActivityList, Grade, State, Stats } from '@/components/planning-common/parts'
import { Ref, convoHref, hrefOf, prSlug, toPath } from '@/components/planning-common/links'
import { Flow, Head, Pairs, Step, mergedLabel } from '@/components/planning-common/flow'
import { apiBase, deployApi, kst, type DeployCheck, type Deployment } from '@/lib/planning'


// ---------------------------------------------------------------- 운영·배포 (머지된 PR 시간순)
export function Ops({ s, item }: { s: S; item?: string }) {
  const req = s.requests.find((r) => r.id === item)
  const specIds = req ? s.specs.filter((x) => x.id === req.spec || x.request === req.id).map((x) => x.id) : []
  const prs = req ? s.prs.filter((p) => s.tasks.some((t) => t.id === p.task && specIds.includes(t.spec))) : s.prs
  const merged = prs.filter((p) => p.merged)
  const open = prs.filter((p) => !p.merged)
  const incidentOf = (pr: string) => s.incidents.find((i) => i.candidates.some((c) => c.pr === pr))
  const mergedAt = (id: string) => s.activity.filter((a) => a.text.startsWith(`${id} `) && (a.point === 'github_merge_pull' || a.point === 'execute(merge_pr)')).pop()?.t ?? s.incidents.flatMap((i) => i.candidates).find((c) => c.pr === id)?.mergedAt ?? '-'
  return (
    <div className="flow-col">
      {req && <Notice icon="bell"><Ref s={s} id={req.id} /> {req.title} · <a href={toPath('work/ops')}>전체</a> · <a href={toPath(`projects/${req.id}`)}>프로젝트</a></Notice>}
      <Stats items={[
        { label: '배포된 PR', value: merged.length },
        { label: '자동 머지', value: merged.filter((p) => p.auto).length },
        { label: '사람 머지', value: merged.filter((p) => !p.auto).length },
        { label: '머지 전', value: open.length, href: toPath('work/prs') },
        { label: '장애 연결', value: merged.filter((p) => incidentOf(p.id)).length, href: toPath('work/aftercare') },
      ]} />
      <Flow label="배포 이력">
        {merged.slice().reverse().map((p) => {
          const i = incidentOf(p.id)
          return (
            <Step key={p.id} name={mergedAt(p.id).slice(6) || '-'} on={!i} href={toPath(`work/prs/${prSlug(p.id)}`)} title={<>{p.id} · {p.title.replace(/^[A-Z]\d+: /, '')}</>}>
              <Pairs items={[
                ['머지', mergedLabel(p)],
                ['관문', `${p.gate.result} · ${p.gate.lines}줄·${p.gate.files}파일 · ${p.gate.paths}`],
                ['장애', i ? <Ref s={s} id={i.ctx} /> : '없음'],
              ]} />
            </Step>
          )
        })}
      </Flow>
      <Deployments s={s} />
    </div>
  )
}

// ---------------------------------------------------------------- 배포 기록·배포 후 확인 (dev 서비스 REST, SSE 상태와 따로 읽는다)
type Tone = 'green' | 'amber' | 'neutral'
const DEP_STATUS: Record<Deployment['status'], [string, Tone]> = {
  requested: ['승인 대기', 'amber'], running: ['배포 중', 'neutral'], succeeded: ['배포됨', 'green'], failed: ['실패', 'amber'], rolled_back: ['롤백됨', 'neutral'],
}
const VERDICT: Record<DeployCheck['verdict'], [string, Tone]> = { ok: ['정상', 'green'], watch: ['관찰', 'neutral'], suspect: ['의심', 'amber'] }
const APPROVAL: Record<string, string> = { prod: 'prod 배포', protected_path: '보호 경로' }
const ROLLBACK = { B: '자동 롤백 가능', C: '사람 승인 필요' }
const Chip = ({ v }: { v: [string, Tone] }) => <span className={`st st-${v[1]}`}>{v[0]}</span>

function Deployments({ s }: { s: S }) {
  const toast = useToast()
  const [rows, setRows] = useState<Deployment[] | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let live = true
    deployApi(apiBase('dev')).list()
      .then((r) => { if (live) { setRows(r); setErr('') } })
      .catch((e: Error) => { if (live) { setRows([]); setErr(e.message) } })
    return () => { live = false }
  }, [tick])
  const run = (id: string, kind: 'approve' | 'check', done: string) => {
    setBusy(`${id}:${kind}`)
    deployApi(apiBase('dev'))[kind](id)
      .then(() => { toast.show(done); setTick((n) => n + 1) })
      .catch((e: Error) => toast.show(e.message))
      .finally(() => setBusy(''))
  }
  if (rows === null) return null
  return (
    <section className="flow-sec">
      <h2>배포 · 배포 후 확인</h2>
      {err && <p className="muted small">{err}</p>}
      {rows.length === 0 ? <div className="empty"><b>배포 기록 없음</b></div> : (
        <Flow label="배포 기록">
          {rows.map((dep) => {
            const c = dep.check
            const rb = c?.rollback
            return (
              <Step key={dep.id} name={kst(dep.at).slice(6) || '-'} on={dep.status === 'succeeded' && c?.verdict !== 'suspect'} title={<>{dep.id} · {dep.version ?? '-'} · {dep.env} <Chip v={DEP_STATUS[dep.status] ?? [dep.status, 'neutral']} /></>}>
                <Pairs items={[
                  ['PR', dep.pr_numbers.map((n, i) => <span key={n}>{i > 0 && ', '}<Ref s={s} id={`PR #${n}`} /></span>)],
                  ...(dep.approval_reasons.length ? [['승인', <>{dep.approval_reasons.map((r) => APPROVAL[r] ?? r).join(' · ')}{dep.protected_paths.length > 0 && <span className="mono small"> {dep.protected_paths.join(', ')}</span>}{dep.approved_by && ` · ${dep.approved_by}`}</>] as [string, ReactNode]] : []),
                  ['확인', c ? <><Chip v={VERDICT[c.verdict] ?? [c.verdict, 'neutral']} /> · {c.window_minutes}분 · {kst(c.at)}</> : '없음'],
                  ...(c ? [['근거', <ul>{c.evidence.map((e) => <li key={e}>{e}</li>)}</ul>] as [string, ReactNode]] : []),
                  ...(rb ? [['롤백', <><Grade g={rb.grade} /> {ROLLBACK[rb.grade]} <span className="muted small mono">후보 {rb.suspect_count} · 확신도 낮음 {rb.low_confidence} · 되돌릴 수 없음 {rb.irreversible_count} · 이전 롤백 {rb.prior_rollbacks}</span></>] as [string, ReactNode]] : []),
                ]} />
                <div className="actions">
                  {dep.status === 'requested' && <Button size="small" variant="primary" disabled={!!busy} onClick={() => run(dep.id, 'approve', `${dep.id} 배포 승인`)}>배포 승인</Button>}
                  {dep.status !== 'requested' && dep.status !== 'running' && <Button size="small" disabled={!!busy} onClick={() => run(dep.id, 'check', `${dep.id} 배포 후 확인`)}>배포 후 확인 다시 보기</Button>}
                </div>
              </Step>
            )
          })}
        </Flow>
      )}
    </section>
  )
}

// ---------------------------------------------------------------- 사후관리 (DejaFix 연동). 사건마다 세로 흐름
export function Aftercare({ s, item }: { s: S; item?: string }) {
  useEffect(() => {
    if (item) document.getElementById(`inc-${item}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [item])
  const acts = s.activity.filter((a) => a.ctx.startsWith('ctx_inc_'))
  return (
    <div className="flow-col">
      {s.incidents.map((i) => {
        const follow = s.requests.find((r) => r.id === i.followUp)
        const c0 = i.candidates[0]
        return (
          <section key={i.ctx} id={`inc-${i.ctx}`} className={`flow-sec${item === i.ctx ? ' card-focus' : ''}`}>
            <Head kicker={<><a href={convoHref(i.ctx)} className="mono plain-link">{i.ctx}</a> · {i.at}</>} title={i.title} side={<State v={i.linked ? '완료' : '접수'} />} />
            <Flow>
              <Step name="개시" on href={convoHref(i.ctx)} title={`${i.at} · 통합 에이전트`} />
              <Step name="후보" on={i.candidates.length > 0} title={i.candidates.map((c) => c.pr).join(', ') || '없음'}>
                <ul>{i.candidates.map((c) => <li key={c.pr}><Ref s={s} id={c.pr} /> · <a href={toPath(`work/specs/${c.spec}/${c.para}`)} className="plain-link">{c.spec} {c.para}</a> · {c.mergedAt} 머지</li>)}</ul>
              </Step>
              <Step name="롤백" on={!!i.rollbackCheck} title={i.rollbackCheck ? `${i.rollbackCheck.result} · ${i.rollbackCheck.target}` : '없음'}>
                {i.rollbackCheck && <p className="muted">{i.rollbackCheck.why}</p>}
              </Step>
              <Step name="문단" on={i.linked} href={c0 ? toPath(`work/specs/${c0.spec}/${c0.para}`) : undefined} title={i.candidates.map((c) => `${c.spec} ${c.para}`).join(', ') || '-'} />
              <Step name="후속" on={!!follow} href={follow ? hrefOf(s, follow.id) : undefined} title={follow ? `${follow.id} · ${follow.state}` : '없음'}>
                {follow && <p>{follow.title}</p>}
              </Step>
            </Flow>
          </section>
        )
      })}
      <details className="proj-activity">
        <summary>사건 활동 {acts.length}</summary>
        <ActivityList s={s} items={acts.slice().reverse()} />
      </details>
    </div>
  )
}
