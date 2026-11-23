// 운영·배포와 사후관리
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Button, Notice, useToast } from '@dejafix/common-frontend'
import { type State as S } from '@/lib/planningData'
import { ActivityList, Grade, State, Stats } from '@/components/planning-common/parts'
import { Ref, convoHref, go, hrefOf, prName, prSlug, repoPrHref, toPath, idText } from '@/components/planning-common/links'
import { Flow, Pairs, Step, mergedLabel } from '@/components/planning-common/flow'
import { apiBase, deployApi, kst, type DeployCheck, type Deployment } from '@/lib/planning'
import { deployExecuted, deployRunView, deployStatusText, deployedPrCount, incidentStageText, needsToolLink, revertText, type Tone } from '@/lib/devOps'
import { currentIncidents, DejafixNotConnectedError, readableTitle, SEVERITY, type Incident as LiveIncident } from '@/lib/dejafix'
import { ApproverField, useApproverName } from '@/components/finance/ApproverName'
import { useVisibleInterval } from '@/lib/visibleInterval'
import { Link } from '@/lib/nav'

const TOOLS = '/system/tools' // 도구 연결 (배포 도구·DejaFix·data 신호원)
const DEPLOY_TOOLS = '/system/tools?item=deploy' // 도구 연결 → 배포 도구
const HOLDS = '/dev/work/holds' // 안전 확인 (사람 확인으로 보류된 되돌리기·배포 되돌리기)
const DEPLOY_POLL_MS = 15_000
const LIVE_POLL_MS = 30_000

// ---------------------------------------------------------------- 데이터 (dev 배포 기록 · DejaFix 처리 중 장애)
function useDeployments() {
  const [rows, setRows] = useState<Deployment[] | null>(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => deployApi(apiBase('dev')).list()
    .then((r) => { setRows(r); setErr('') })
    .catch((e: Error) => { setRows((old) => old ?? []); setErr(e.message) }), [])
  useVisibleInterval(load, DEPLOY_POLL_MS) // 다른 탭을 보는 동안은 쉬고, 앞 요청이 오는 중이면 건너뛴다
  return { rows, err, load }
}

// 처리 중인 장애는 DejaFix(GET /api/incidents?status=problem)에서만 받는다. 못 받으면 0건으로 지어내지 않는다
type Live = { rows: LiveIncident[] } | { notConnected: true } | { error: string }
function useLiveIncidents(): Live | null {
  const [live, setLive] = useState<Live | null>(null)
  const load = useCallback(() => currentIncidents()
    .then((r) => setLive({ rows: r.data }))
    .catch((e: unknown) => setLive(e instanceof DejafixNotConnectedError ? { notConnected: true } : { error: (e as Error).message || 'DejaFix가 응답하지 않았어요.' })), [])
  useVisibleInterval(load, LIVE_POLL_MS)
  return live
}


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
  const deps = useDeployments()
  const live = useLiveIncidents()
  return (
    <div className="flow-col">
      {req && <Notice icon="bell"><Ref s={s} id={req.id} /> {req.title} · <a href={toPath('work/ops')}>전체</a> · <a href={toPath(`projects/${req.id}`)}>프로젝트</a></Notice>}
      {(() => {
        // 처리 중인 장애는 DejaFix 현재 장애 수 (PR 연결 여부와 상관없다). 못 받으면 숫자를 만들지 않는다
        const broken = merged.filter((p) => incidentOf(p.id)).length
        const openInc = live && 'rows' in live ? live.rows.length : null
        const liveHint = !live ? '읽는 중' : 'notConnected' in live ? 'DejaFix 연결 필요' : 'error' in live ? 'DejaFix를 읽지 못함' : 'DejaFix 기준'
        const deployed = deps.rows ? deployedPrCount(deps.rows) : null
        const q = (n: number) => (n ? undefined : 'quiet' as const)
        return (
          <Stats items={[
            { label: '처리 중인 장애', value: openInc ?? '-', hint: `${liveHint} · PR 연결한 사건 ${s.incidents.length}건 · 원인 PR ${broken}건`, href: toPath('work/aftercare'), tone: openInc ? 'alert' : 'quiet' },
            { label: '머지 전', value: open.length, href: toPath('work/prs'), tone: q(open.length) },
            { label: '배포된 PR', value: deployed ?? '-', hint: '배포 도구에서 끝난 배포만', tone: q(deployed ?? 0) },
            { label: '자동 머지', value: merged.filter((p) => p.auto).length, tone: 'quiet' },
            { label: '사람 머지', value: merged.filter((p) => !p.auto).length, tone: 'quiet' },
          ]} />
        )
      })()}
      <Flow label="머지 이력 (배포와 별개)">
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
      <Deployments s={s} rows={deps.rows} err={deps.err} reload={deps.load} />
    </div>
  )
}

// ---------------------------------------------------------------- 배포 기록·배포 후 확인 (dev 서비스 REST, SSE 상태와 따로 읽는다)
// 승인한 배포는 개발 AI가 도구 연결의 배포 도구(인프라 AI 경유)로 한 번 보낸다. 못 보냈으면 이유를 그대로 보인다
const VERDICT: Record<DeployCheck['verdict'], [string, Tone]> = { ok: ['정상', 'green'], watch: ['지켜보는 중', 'neutral'], suspect: ['문제 의심', 'amber'] }
const APPROVAL: Record<string, string> = { prod: '운영 서버 배포', protected_path: '보호 파일 포함' }
const ROLLBACK = { B: '자동으로 되돌릴 수 있음', C: '되돌리려면 사람 승인 필요' }
const Chip = ({ v }: { v: [string, Tone] }) => <span className={`st st-${v[1]}`}>{v[0]}</span>

function Deployments({ s, rows, err, reload }: { s: S; rows: Deployment[] | null; err: string; reload: () => Promise<unknown> }) {
  const toast = useToast()
  const [busy, setBusy] = useState('')
  // 승인자: 로그인했으면 계정 이름, 공용 토큰이면 적어 낸 이름 (안전 확인 큐와 같은 규칙). 없으면 승인 버튼이 꺼진다
  const [approver, setApprover, approverName] = useApproverName()
  const run = (id: string, kind: 'approve' | 'dispatch' | 'check' | 'refresh', done: string) => {
    const api = deployApi(apiBase('dev'))
    if ((kind === 'approve' || kind === 'dispatch') && !approverName) { toast.show('승인자 이름을 먼저 적으세요.'); return }
    setBusy(`${id}:${kind}`)
    ;(kind === 'approve' ? api.approve(id, approverName ?? '') : kind === 'dispatch' ? api.dispatch(id, approverName ?? '') : kind === 'refresh' ? api.refresh(id) : api.check(id))
      .then(() => { toast.show(done); void reload() })
      .catch((e: Error) => toast.show(e.message))
      .finally(() => setBusy(''))
  }
  if (rows === null) return null
  // 사람이 눌러야 하는 배포 (승인 대기 · 승인 이유 없음 · 승인했지만 보내지 않음)
  const waiting = rows.some((d) => d.status === 'requested' || d.status === 'ready' || d.run?.state === 'not_sent')
  return (
    <section className="flow-sec">
      <h2>배포 · 배포 후 확인</h2>
      {err && <p className="muted small">{err}</p>}
      {waiting && <ApproverField id="deploy-approver" value={approver} onChange={setApprover} small />}
      {rows.length === 0 ? <div className="empty"><b>배포 기록 없음</b></div> : (
        <Flow label="배포 기록">
          {rows.map((dep) => {
            const c = dep.check
            const rb = c?.rollback
            const executed = deployExecuted(dep)
            const rv = deployRunView(dep.run)
            return (
              <Step key={dep.id} name={kst(dep.at).slice(6) || '-'} on={executed && c?.verdict !== 'suspect'} title={<><span className="mono idc" title={dep.id}>{idText(dep.id)}</span> {dep.version ?? '-'} · {dep.env} <Chip v={deployStatusText(dep)} /></>}>
                <Pairs items={[
                  ['PR', dep.pr_numbers.map((n, i) => <span key={n}>{i > 0 && ', '}<Ref s={s} id={`PR #${n}`} /></span>)],
                  ...(dep.status === 'requested' || dep.status === 'ready' ? [['실행 대상', <><b className="mono">{dep.version ?? '버전 모름'}</b> · 운영 배포 도구로 실행 <span className="muted small">(배포 실행은 사람이 눌러야 해요)</span></>] as [string, ReactNode]] : []),
                  ...(dep.approval_reasons.length ? [['승인', <>{dep.approval_reasons.map((r) => APPROVAL[r] ?? r).join(' · ')}{dep.protected_paths.length > 0 && <span className="mono small"> {dep.protected_paths.join(', ')}</span>}{dep.approved_by && ` · ${dep.approved_by}`}</>] as [string, ReactNode]] : []),
                  ...(rv ? [['실행', <>
                    <Chip v={[rv.text, rv.tone]} />
                    {rv.url && <> · <a href={rv.url} target="_blank" rel="noreferrer" className="plain-link">실행 보기</a></>}
                    {rv.needsTool && <> · <Link href={DEPLOY_TOOLS} className="plain-link">도구 연결에서 배포 도구 설정</Link></>}
                    {rv.held && <> · <Link href={HOLDS} className="plain-link">안전 확인 보기</Link></>}
                    {dep.run?.checked_at && <span className="muted small"> · {kst(dep.run.checked_at)} 확인</span>}
                  </>] as [string, ReactNode]]
                    : !executed && dep.status !== 'requested' && dep.status !== 'ready' ? [['실행', <>{dep.note || '실제 배포는 안 함'}</>] as [string, ReactNode]] : []),
                  ['확인', c ? <><Chip v={VERDICT[c.verdict] ?? [c.verdict, 'neutral']} /> · {c.window_minutes}분 · {kst(c.at)}</> : executed ? '없음' : '실제 배포 뒤에 해요'],
                  ...(c ? [['근거', <><ul>{c.evidence.map((e) => <li key={e}>{e}</li>)}</ul>{needsToolLink(c) && <Link href={TOOLS} className="plain-link">도구 연결에서 신호원(DejaFix·data) 잇기</Link>}</>] as [string, ReactNode]] : []),
                  ...(rb ? [['롤백', <><Grade g={rb.grade} /> {ROLLBACK[rb.grade]} <span className="muted small mono">원인 후보 {rb.suspect_count} · 확신도 낮음 {rb.low_confidence} · 되돌릴 수 없음 {rb.irreversible_count} · 이전 롤백 {rb.prior_rollbacks}</span> · <Link href={HOLDS} className="plain-link">안전 확인 보기</Link></>] as [string, ReactNode]] : []),
                ]} />
                <div className="actions">
                  {(dep.status === 'requested' || dep.status === 'ready') && <Button size="small" variant="primary" disabled={!!busy || !approverName} title={approverName ? `${dep.version ?? '버전 모름'} — 운영 배포 도구로 실행` : '승인자 이름을 먼저 적으세요'} onClick={() => run(dep.id, 'approve', `${dep.id} ${dep.version ?? ''} 승인함 · 결과는 실행 줄에서 확인`)}>{dep.status === 'ready' ? '배포 실행' : '배포 승인'} · <span className="mono">{dep.version ?? '버전 모름'}</span></Button>}
                  {rv?.canSend && <Button size="small" variant="primary" disabled={!!busy || !approverName} title={`${dep.version ?? '버전 모름'} — 운영 배포 도구로 실행`} onClick={() => run(dep.id, 'dispatch', `${dep.id} 배포 실행 · 결과는 실행 줄에서 확인`)}>배포 실행 · <span className="mono">{dep.version ?? '버전 모름'}</span></Button>}
                  {rv?.canRefresh && dep.status === 'running' && <Button size="small" disabled={!!busy} onClick={() => run(dep.id, 'refresh', `${dep.id} 실행 상태 다시 읽음`)}>실행 상태 새로 보기</Button>}
                  {executed && dep.status !== 'running' && <Button size="small" disabled={!!busy} onClick={() => run(dep.id, 'check', `${dep.id} 배포 후 확인`)}>배포 후 확인 다시 하기</Button>}
                </div>
              </Step>
            )
          })}
        </Flow>
      )}
    </section>
  )
}

// ---------------------------------------------------------------- 사후관리. 위: DejaFix 처리 중 장애, 아래: PR에 연결한 개발 사건(세로 흐름)
const INC_STEPS = ['접수', '원인 PR', '되돌리기', 'PR·기획 연결', '후속 요청'] as const

function LiveIncidents({ live }: { live: Live | null }) {
  return (
    <section className="flow-sec" aria-label="지금 처리 중인 장애">
      <h2>지금 처리 중인 장애 <span className="muted small">DejaFix</span></h2>
      {!live ? <p className="muted small">DejaFix에서 장애 목록을 읽는 중…</p>
        : 'notConnected' in live ? <Notice icon="bell">DejaFix가 연결되지 않아 처리 중인 장애를 받지 못해요. <Link href={TOOLS} className="plain-link">도구 연결에서 DejaFix 잇기</Link></Notice>
        : 'error' in live ? <Notice tone="warn">DejaFix에서 장애 목록을 읽지 못했어요: {live.error}</Notice>
        : !live.rows.length ? <div className="empty"><b>처리 중인 장애 없음</b></div>
        : (
          <ul className="space-y-1">
            {live.rows.map((i) => {
              const sev = SEVERITY[i.severity.level]
              return (
                <li key={i.id}>
                  <span className={`chip ${sev?.tone ?? ''}`}>{sev?.label ?? i.severity.label}</span>{' '}
                  <Link href={`/infra/${i.id}`} className="plain-link" title={i.title}><b>{readableTitle(i.title)}</b></Link>{' '}
                  <span className="muted small">{i.host.name ?? '호스트 모름'} · {kst(i.started_at)} 시작</span>
                </li>
              )
            })}
          </ul>
        )}
    </section>
  )
}

export function Aftercare({ s, item }: { s: S; item?: string }) {
  const live = useLiveIncidents()
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
      <LiveIncidents live={live} />
      <section className="flow-sec" aria-label="PR에 연결한 사건">
      <h2>PR에 연결한 사건</h2>
      <p className="muted small">개발 AI가 장애를 원인 후보 PR·기획서 문단에 이은 기록이에요. 연결했다고 장애가 복구된 것은 아니에요.</p>
      {!incidents.length ? <div className="empty"><b>PR에 연결한 사건 없음</b></div> : (
        <div className="inc-grid">
          {incidents.map((i) => {
            // 오른쪽 흐름(Flow)과 같은 규칙: 뒤 단계가 켜졌으면 앞 단계도 켠다
            const raw = [true, i.candidates.length > 0, !!i.rollbackCheck || !!i.revert, i.linked, !!i.followUp]
            const last = raw.lastIndexOf(true)
            const done = raw.map((on, n) => on || n < last)
            return (
              <a key={i.ctx} href={toPath(`work/aftercare/${i.ctx}`)} className={`inc-card${sel?.ctx === i.ctx ? ' on' : ''}`}>
                <span className="inc-card-top"><span className="mono idc" title={i.ctx}>{idText(i.ctx)}</span><State v={incidentStageText(i.linked)} /></span>
                <b className="inc-card-title">{i.title}</b>
                <ol className="inc-steps" aria-label="흐름">
                  {INC_STEPS.map((x, n) => <li key={x} className={done[n] ? 'on' : ''}>{x}</li>)}
                </ol>
                <span className="inc-card-meta">{i.at} · 원인 PR {i.candidates.map((c) => c.pr).join(', ') || '없음'}{i.rollbackCheck ? ` · 롤백 ${i.rollbackCheck.result}` : ''}{i.revert ? ` · ${revertText(i.revert)}` : ''}</span>
              </a>
            )
          })}
        </div>
      )}
      </section>
      {sel && <IncidentPanel s={s} i={sel} />}
    </>
  )
}

function IncidentPanel({ s, i }: { s: S; i: S['incidents'][number] }) {
  const follow = s.requests.find((r) => r.id === i.followUp)
  const c0 = i.candidates[0]
  const specOk = (c: { spec: string; para: string }) => c.spec !== '-' && c.para !== '-' // 문단을 모르면 링크를 걸지 않는다
  const rv = i.revert
  const acts = s.activity.filter((a) => a.ctx === i.ctx)
  const [newest, setNewest] = useState(true) // 활동 순서: 최신순 · 오래된순
  return (
    <aside className="chat-drawer inc-drawer" aria-label={`${i.ctx} 장애`}>
      <div className="inc-drawer-head">
        <div><span className="mono small"><a href={convoHref(i.ctx)} className="mono idc">{i.ctx}</a> · {i.at} · <a href={convoHref(i.ctx)} className="plain-link">사건 대화 보기</a></span><h2>{i.title}</h2></div>
        <a href={toPath('work/aftercare')} className="btn-quiet px-2 py-1" aria-label="닫기">✕</a>
      </div>
      <div className="inc-drawer-body">
        <Flow>
          <Step name="접수" on href={convoHref(i.ctx)} title={`${i.at} · 총괄 에이전트`} />
          <Step name="원인 PR" on={i.candidates.length > 0} title={i.candidates.map((c) => c.pr).join(', ') || '없음'}>
            <ul>{i.candidates.map((c) => <li key={c.pr}><Ref s={s} id={c.pr} /> · {specOk(c) ? <a href={toPath(`work/specs/${c.spec}/${c.para}`)} className="plain-link" title={`${c.spec} ${c.para}`}>{idText(c.spec)} {c.para}</a> : <span className="muted">기획서 문단 모름</span>} · {kst(c.mergedAt) || c.mergedAt} 머지</li>)}</ul>
          </Step>
          <Step name="되돌리기" on={!!i.rollbackCheck || !!rv} title={[i.rollbackCheck && `배포 롤백 교차검증 ${i.rollbackCheck.result} · ${i.rollbackCheck.target}`, rv && revertText(rv)].filter(Boolean).join(' · ') || '없음'}>
            {i.rollbackCheck && <p className="muted">{i.rollbackCheck.why}</p>}
            {rv && <p>{rv.revertPr != null && <><a href={repoPrHref(rv.revertPr, rv.connectionId)} className="plain-link">되돌리기 PR #{rv.revertPr} 보기</a> · </>}{rv.status === 'held' && <><Link href={HOLDS} className="plain-link">안전 확인 보기</Link> · </>}<span className="muted small">{kst(rv.at)}</span></p>}
          </Step>
          <Step name="PR·기획 연결" on={i.linked} href={c0 && i.linked && specOk(c0) ? toPath(`work/specs/${c0.spec}/${c0.para}`) : undefined} title={i.linked ? i.candidates.map((c) => (specOk(c) ? `${c.spec} ${c.para}` : `${c.pr} (문단 모름)`)).join(', ') || 'PR 없음' : '아직 안 이음'} />
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
