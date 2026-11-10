// 운영·배포와 사후관리
import { useEffect, useState, type ReactNode } from 'react'
import { Button, Notice, useToast } from '@dejafix/common-frontend'
import { type State as S } from '@/lib/planningData'
import { ActivityList, Grade, State, Stats } from '@/components/planning-common/parts'
import { Ref, convoHref, go, hrefOf, prName, prSlug, toPath, idText } from '@/components/planning-common/links'
import { Flow, Pairs, Step, mergedLabel } from '@/components/planning-common/flow'
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
  // 머지 시각: 활동 기록은 "MM-DD HH:MM", 사건 기록은 ISO라 한 모양("MM-DD HH:MM")으로 맞춘다
  const shownAt = (t: string) => (t === '-' ? '-' : /\d{4}-\d{2}-\d{2}T/.test(t) ? kst(t) : t)
  return (
    <div className="flow-col">
      {req && <Notice icon="bell"><Ref s={s} id={req.id} /> {req.title} · <a href={toPath('work/ops')}>전체</a> · <a href={toPath(`projects/${req.id}`)}>프로젝트</a></Notice>}
      {(() => {
        // 장애는 사건 단위로 센다 (장애 대응 탭과 같은 단위). 아직 처리 중인 사건만 빨갛게
        const broken = merged.filter((p) => incidentOf(p.id)).length
        const openInc = s.incidents.filter((i) => !i.linked).length
        const q = (n: number) => (n ? undefined : 'quiet' as const)
        return (
          <Stats items={[
            { label: '처리 중인 장애', value: openInc, hint: `지난 장애 ${s.incidents.length}건 · 원인 PR ${broken}건`, href: toPath('work/aftercare'), tone: openInc ? 'alert' : 'quiet' },
            { label: '머지 전', value: open.length, href: toPath('work/prs'), tone: q(open.length) },
            { label: '배포된 PR', value: merged.length, tone: q(merged.length) },
            { label: '자동 머지', value: merged.filter((p) => p.auto).length, tone: 'quiet' },
            { label: '사람 머지', value: merged.filter((p) => !p.auto).length, tone: 'quiet' },
          ]} />
        )
      })()}
      <Flow label="배포 이력">
        {merged.slice().reverse().map((p) => {
          const i = incidentOf(p.id)
          return (
            <Step key={p.id} name={shownAt(mergedAt(p.id))} on={!i} href={toPath(`work/prs/${prSlug(p.id)}`)} title={<><span className="mono idc" title={p.id}>{prName(p)}</span> {p.title.replace(/^[A-Z]\d+: /, '')}</>}>
              <Pairs items={[
                ['머지', mergedLabel(p)],
                ['범위 점검', <>{p.gate.result} · {p.gate.lines}줄·{p.gate.files}파일 · <span className="mono">{p.gate.paths}</span></>],
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
const VERDICT: Record<DeployCheck['verdict'], [string, Tone]> = { ok: ['정상', 'green'], watch: ['지켜보는 중', 'neutral'], suspect: ['문제 의심', 'amber'] }
const APPROVAL: Record<string, string> = { prod: '운영 서버 배포', protected_path: '보호 파일 포함' }
const ROLLBACK = { B: '자동으로 되돌릴 수 있음', C: '되돌리려면 사람 승인 필요' }
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
              <Step key={dep.id} name={kst(dep.at).slice(6) || '-'} on={dep.status === 'succeeded' && c?.verdict !== 'suspect'} title={<><span className="mono idc" title={dep.id}>{idText(dep.id)}</span> {dep.version ?? '-'} · {dep.env} <Chip v={DEP_STATUS[dep.status] ?? [dep.status, 'neutral']} /></>}>
                <Pairs items={[
                  ['PR', dep.pr_numbers.map((n, i) => <span key={n}>{i > 0 && ', '}<Ref s={s} id={`PR #${n}`} /></span>)],
                  ...(dep.approval_reasons.length ? [['승인', <>{dep.approval_reasons.map((r) => APPROVAL[r] ?? r).join(' · ')}{dep.protected_paths.length > 0 && <span className="mono small"> {dep.protected_paths.join(', ')}</span>}{dep.approved_by && ` · ${dep.approved_by}`}</>] as [string, ReactNode]] : []),
                  ['확인', c ? <><Chip v={VERDICT[c.verdict] ?? [c.verdict, 'neutral']} /> · {c.window_minutes}분 · {kst(c.at)}</> : '없음'],
                  ...(c ? [['근거', <ul>{c.evidence.map((e) => <li key={e}>{e}</li>)}</ul>] as [string, ReactNode]] : []),
                  ...(rb ? [['롤백', <><Grade g={rb.grade} /> {ROLLBACK[rb.grade]} <span className="muted small mono">원인 후보 {rb.suspect_count} · 확신도 낮음 {rb.low_confidence} · 되돌릴 수 없음 {rb.irreversible_count} · 이전 롤백 {rb.prior_rollbacks}</span></>] as [string, ReactNode]] : []),
                ]} />
                <div className="actions">
                  {dep.status === 'requested' && <Button size="small" variant="primary" disabled={!!busy} onClick={() => run(dep.id, 'approve', `${dep.id} 배포 승인`)}>배포 승인</Button>}
                  {dep.status !== 'requested' && dep.status !== 'running' && <Button size="small" disabled={!!busy} onClick={() => run(dep.id, 'check', `${dep.id} 배포 후 확인`)}>배포 후 확인 다시 하기</Button>}
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
const INC_STEPS = ['접수', '원인 PR', '되돌리기', '기획 연결', '후속 요청'] as const

export function Aftercare({ s, item }: { s: S; item?: string }) {
  // 사건 카드 4열(최신이 위). 카드를 누르면 오른쪽에 그 사건의 흐름·활동이 뜬다
  const incidents = s.incidents.slice().sort((a, b) => b.at.localeCompare(a.at))
  const sel = incidents.find((i) => i.ctx === item)
  useEffect(() => {
    if (!sel) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && go('work/aftercare')
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [sel])
  return (
    <>
      {!incidents.length ? <div className="empty"><b>장애 없음</b></div> : (
        <div className="inc-grid">
          {incidents.map((i) => {
            // 오른쪽 흐름(Flow)과 같은 규칙: 뒤 단계가 켜졌으면 앞 단계도 켠다
            const raw = [true, i.candidates.length > 0, !!i.rollbackCheck, i.linked, !!i.followUp]
            const last = raw.lastIndexOf(true)
            const done = raw.map((on, n) => on || n < last)
            return (
              <a key={i.ctx} href={toPath(`work/aftercare/${i.ctx}`)} className={`inc-card${sel?.ctx === i.ctx ? ' on' : ''}`}>
                <span className="inc-card-top"><span className="mono idc" title={i.ctx}>{idText(i.ctx)}</span><State v={i.linked ? '완료' : '접수'} /></span>
                <b className="inc-card-title">{i.title}</b>
                <ol className="inc-steps" aria-label="흐름">
                  {INC_STEPS.map((x, n) => <li key={x} className={done[n] ? 'on' : ''}>{x}</li>)}
                </ol>
                <span className="inc-card-meta">{i.at} · 원인 PR {i.candidates.map((c) => c.pr).join(', ') || '없음'}{i.rollbackCheck ? ` · 롤백 ${i.rollbackCheck.result}` : ''}</span>
              </a>
            )
          })}
        </div>
      )}
      {sel && <IncidentPanel s={s} i={sel} />}
    </>
  )
}

function IncidentPanel({ s, i }: { s: S; i: S['incidents'][number] }) {
  const follow = s.requests.find((r) => r.id === i.followUp)
  const c0 = i.candidates[0]
  const acts = s.activity.filter((a) => a.ctx === i.ctx)
  const [newest, setNewest] = useState(true) // 활동 순서: 최신순 · 오래된순
  return (
    <aside className="chat-drawer inc-drawer" aria-label={`${i.ctx} 장애`}>
      <div className="inc-drawer-head">
        <div><span className="mono small"><a href={convoHref(i.ctx)} className="mono idc">{i.ctx}</a> · {i.at}</span><h2>{i.title}</h2></div>
        <a href={toPath('work/aftercare')} className="btn-quiet px-2 py-1" aria-label="닫기">✕</a>
      </div>
      <div className="inc-drawer-body">
        <Flow>
          <Step name="접수" on href={convoHref(i.ctx)} title={`${i.at} · 통합 에이전트`} />
          <Step name="원인 PR" on={i.candidates.length > 0} title={i.candidates.map((c) => c.pr).join(', ') || '없음'}>
            <ul>{i.candidates.map((c) => <li key={c.pr}><Ref s={s} id={c.pr} /> · <a href={toPath(`work/specs/${c.spec}/${c.para}`)} className="plain-link" title={`${c.spec} ${c.para}`}>{idText(c.spec)} {c.para}</a> · {c.mergedAt} 머지</li>)}</ul>
          </Step>
          <Step name="되돌리기" on={!!i.rollbackCheck} title={i.rollbackCheck ? `${i.rollbackCheck.result} · ${i.rollbackCheck.target}` : '없음'}>
            {i.rollbackCheck && <p className="muted">{i.rollbackCheck.why}</p>}
          </Step>
          <Step name="기획 연결" on={i.linked} href={c0 && i.linked ? toPath(`work/specs/${c0.spec}/${c0.para}`) : undefined} title={i.linked ? i.candidates.map((c) => `${c.spec} ${c.para}`).join(', ') || '-' : '아직 안 이음'} />
          <Step name="후속 요청" on={!!follow} href={follow ? hrefOf(s, follow.id) : undefined} title={follow ? `${idText(follow.id)} · ${follow.state}` : '없음'}>
            {follow && <p>{follow.title}</p>}
          </Step>
        </Flow>
        <div className="inc-drawer-sub">
          <h3>활동 {acts.length}</h3>
          <div className="seg-toggle" role="group" aria-label="활동 순서">
            <button type="button" aria-pressed={newest} onClick={() => setNewest(true)}>최신순</button>
            <button type="button" aria-pressed={!newest} onClick={() => setNewest(false)}>오래된순</button>
          </div>
        </div>
        <ActivityList s={s} items={newest ? acts.slice().reverse() : acts} />
      </div>
    </aside>
  )
}
