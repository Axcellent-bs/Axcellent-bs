// 코딩 에이전트: AI와 대화(chat.tsx)로 시작한 작업은 파일 읽기 → 작성 → 리뷰 → PR까지 간다.
// 진행 상태는 Task.coding으로 SSE를 타고 온다
import { useState, type FormEvent } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, RELATION_LABEL, byRelation, type CodingRun, type CodingStatus, type Request, type State as S, type Task } from '@/lib/planningData'
import { DEV_API, PlanningApiError, approveReview, devTaskApi, planningApi, useCodingLimits } from '@/lib/planning'
import { State, Tag } from '@/components/planning-common/parts'
import { Step } from '@/components/planning-common/flow'
import './usage.css'
import { Ref } from '@/components/planning-common/links'

const api = devTaskApi(DEV_API)
const actions = planningApi(DEV_API)

export const CODING_LABEL: Record<CodingStatus, string> = {
  queued: '대기', claimed: '가져감', reading: '파일 읽는 중', writing: '코드 작성 중', reviewing: '리뷰 중',
  opened: 'PR 열림', merged: '머지됨', waiting_human: '사람 확인', failed: '실패', canceled: '중단됨', covered: '반영됨',
}
const DONE: CodingStatus[] = ['opened', 'merged', 'covered']
/** 토큰 수를 짧게: 950 · 12.3k · 1.2M */
export const tokens = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : `${n}`)
export const pctOf = (used: number, limit: number) => Math.round((used / Math.max(limit, 1)) * 100)

/** 쓴 양 / 한도 + 비율 막대. 80% 넘으면 경고색 */
export function UsageBar({ used, limit, label, title, wide }: { used: number; limit: number; label?: string; title?: string; wide?: boolean }) {
  const pct = pctOf(used, limit)
  return (
    <span className={`usage${pct >= 80 ? ' warn' : ''}${wide ? ' wide' : ''}`} title={title}>
      {label && <span>{label}</span>}
      <span className="usage-bar" aria-hidden><span style={{ width: `${Math.min(100, pct)}%`, minWidth: used > 0 ? 3 : 0 }} /></span>
      <b>{pct}%</b>
      <span className="mono usage-n">{tokens(used)}/{tokens(limit)}</span>
    </span>
  )
}
/** 에이전트가 지금 일하는 중인 상태 (옆에 깜빡이는 점) */
const RUNNING: CodingStatus[] = ['queued', 'claimed', 'reading', 'writing', 'reviewing']
export const isRunning = (s?: CodingStatus | string) => RUNNING.includes(s as CodingStatus)
/** 상태 글자. 진행 중이면 앞 점이 깜빡인다 */
export const CodingLabel = ({ c }: { c: CodingRun }) => <State v={codingState(c)} live={isRunning(c.status)} />
const STUCK: CodingStatus[] = ['failed', 'waiting_human']
/** 사람이 중단할 수 있는 상태 (머지됨·중단됨이 아니면) */
export const canCancel = (s?: CodingStatus) => !!s && s !== 'merged' && s !== 'canceled' && s !== 'covered'

/** 작업 중단 버튼. 열린 PR은 서버가 닫고 브랜치를 지운다 */
export function CancelButton({ taskId, status }: { taskId: string; status?: CodingStatus }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  if (!canCancel(status)) return null
  const cancel = async () => {
    if (!window.confirm(`${taskId} 작업을 중단할까요? 열린 PR은 닫고 브랜치를 지워요.`)) return
    setBusy(true)
    try {
      await api.cancel(taskId)
      toast.show(`${taskId} 중단`)
    } catch (x) {
      toast.show(x instanceof PlanningApiError ? x.message : '개발·배포 서버에 연결할 수 없어요')
    } finally {
      setBusy(false)
    }
  }
  return <Button size="small" onClick={cancel} disabled={busy}>{busy ? '중단하는 중…' : '작업 중단'}</Button>
}

/** 중단한 작업 재개 버튼. 레포의 최신 코드를 다시 읽고 처음부터 돈다 */
export function ResumeButton({ taskId, status }: { taskId: string; status?: CodingStatus }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  if (status !== 'canceled') return null
  const resume = async () => {
    setBusy(true)
    try {
      await api.retry(taskId)
      toast.show(`${taskId} 재개`)
    } catch (x) {
      toast.show(x instanceof PlanningApiError ? x.message : '개발·배포 서버에 연결할 수 없어요')
    } finally {
      setBusy(false)
    }
  }
  return <Button size="small" onClick={resume} disabled={busy}>{busy ? '재개하는 중…' : '재개'}</Button>
}

/** 개발팀이 직접 넣은 기획에서 나온 작업인가 */
export function fromDevTeam(s: S, t?: Task): boolean {
  if (!t) return false
  const spec = s.specs.find((x) => x.id === t.spec)
  if (spec?.origin === 'dev_team') return true
  const req = s.requests.find((r) => r.id === spec?.request) as { via?: string } | undefined
  return req?.via === 'dev_team'
}

export const DevTeamTag = () => <Tag>개발팀 기획</Tag>

/** 작업 → 기획서 → 요청 */
export function requestOfTask(s: S, t?: Task): Request | undefined {
  const spec = s.specs.find((x) => x.id === t?.spec)
  return s.requests.find((r) => r.id === spec?.request)
}

/** 작업이 어디서 왔는지: 개발팀이 직접 넣은 기획이면 "개발팀 기획", 다른 팀 요청이면 "고객지원 요청" 같은 꼬리표 */
export function OriginTag({ s, t }: { s: S; t?: Task }) {
  if (!t) return null
  if (fromDevTeam(s, t)) return <DevTeamTag />
  const req = requestOfTask(s, t)
  return req ? <Tag>{DEPT_NAME[req.from] ?? req.from} 요청</Tag> : null
}

/** 작업의 요청과 비슷한·충돌하는 요청 (제안). 충돌만 주황, 마우스를 올리면 판단 근거 */
export function RelatedTags({ s, t }: { s: S; t?: Task }) {
  const rel = byRelation(requestOfTask(s, t)?.related)
  return (
    <>
      {rel.slice(0, 3).map((x) => (
        <span key={x.id} title={x.reason}> · <Tag tone={x.relation === 'conflict' ? 'amber' : undefined}>{RELATION_LABEL[x.relation]}</Tag> <Ref s={s} id={x.id} /></span>
      ))}
    </>
  )
}

/** 목록 줄·머리에 쓰는 코딩 상태 글자 */
export const codingState = (c: CodingRun) =>
  c.status === 'queued' && c.waitingFor?.length ? `차례 기다림 (${c.waitingFor.join(', ')} 뒤)`
    : c.status === 'queued' && c.mode === 'subscription' ? '내 Claude 대기'
    : c.status === 'claimed' ? `가져감 · ${c.claimedBy ?? '개발자'}`
    : CODING_LABEL[c.status] ?? c.status

/** 작업·PR 흐름 맨 위 "코딩 에이전트" 단계 */
export function CodingStep({ task, on }: { task: Task; on?: boolean }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const c = task.coding
  const lim = useCodingLimits()
  if (!c) return null
  // 열린 PR로 사람 확인을 기다리면: 다시 시도 대신 수정 지시·사람 머지
  const waitingPr = c.status === 'waiting_human' && !!c.pr
  const act = async (run: () => Promise<unknown>, done: string) => {
    setBusy(true)
    try {
      await run()
      toast.show(done)
      return true
    } catch (x) {
      toast.show(x instanceof PlanningApiError ? x.message : '개발·배포 서버에 연결할 수 없어요')
      return false
    } finally {
      setBusy(false)
    }
  }
  const revise = async (e: FormEvent) => {
    e.preventDefault()
    if (note.trim() && await act(() => api.revise(task.id, note.trim()), `${c.pr} 수정 지시 보냄`)) setNote('')
  }
  const merge = () => {
    if (c.pr && window.confirm(`${c.pr}를 머지할까요? 자동 머지 기준 밖이라 사람이 머지한 것으로 기록돼요.`)) {
      void act(() => actions.action({ type: 'mergePr', pr: c.pr! }), `${c.pr} 머지`)
    }
  }
  const files = c.files ?? []
  const add = files.reduce((n, x) => n + (x.additions || 0), 0)
  const del = files.reduce((n, x) => n + (x.deletions || 0), 0)
  const retry = async () => {
    setBusy(true)
    try {
      await api.retry(task.id)
      toast.show(`${task.id} 다시 시작`)
    } catch (x) {
      toast.show(x instanceof PlanningApiError ? x.message : '개발·배포 서버에 연결할 수 없어요')
    } finally {
      setBusy(false)
    }
  }
  // 개발자 Claude 대기열에서 아무도 안 가져감: 서버 AI로 넘길 수 있다
  const unclaimed = c.mode === 'subscription' && c.status === 'queued' && !c.claimedBy
  const toAgent = () => act(() => api.toAgent(task.id), `${task.id} 서버 AI로 넘김`)
  // 바뀐 내용이 없어 멈춤: 앞 작업이 이미 반영했으면 사람이 PR 없이 완료로 닫는다
  const noChange = !c.pr && (c.status === 'failed' || c.status === 'waiting_human') && (c.error ?? '').includes('바뀐 내용이 없음')
  const closeCovered = () => {
    if (window.confirm(`${task.id}를 앞 작업이 이미 반영한 것으로 닫을까요? PR 없이 완료돼요.`)) {
      void act(() => api.closeCovered(task.id), `${task.id} 반영됨으로 닫음`)
    }
  }
  // 실행 관문이 PR 열기를 보류함 (보호 경로·한도 등): 사람이 승인하면 같은 코드로 PR을 연다
  const heldPr = c.status === 'waiting_human' && !c.pr && (!!c.holdKey || (c.error ?? '').includes('generate_pr를 허락하지 않음 (held)'))
  const approveHold = () => act(async () => {
    const note = `${task.id} PR 열기 보류를 사람이 확인하고 승인`
    let ok = false
    if (c.holdReview) {
      try { await approveReview(c.holdReview, note); ok = true } catch { /* 만료됐으면 새로 받는다 */ }
    }
    if (!ok) {
      const r = await api.renewHold(task.id)
      if (r.review_id) await approveReview(r.review_id, note)
    }
    await api.retry(task.id, true)
  }, `${task.id} 보류 승인 → PR 여는 중`)
  return (
    <Step name="코딩 에이전트" on={!!on || DONE.includes(c.status)} title={<CodingLabel c={c} />}>
      <span className="tags">
        {c.mode === 'subscription' ? <Tag>내 Claude{c.claimedBy ? ` · ${c.claimedBy}` : ''}</Tag> : c.model && <Tag><span className="mono">{c.model}</span></Tag>}
        {c.attempts > 1 && <Tag>{c.attempts}회차</Tag>}
        {!!c.llmCalls && <Tag><UsageBar used={c.llmTokens ?? 0} limit={lim.runTokens} label={`AI ${c.llmCalls}/${lim.runCalls}회 · 토큰`} title="코딩 실행 한 번의 한도 대비 (다시 시도까지 합친 양)" /></Tag>}
        {!!files.length && <Tag>{files.length}파일 · <span className="mono">+{add} −{del}</span></Tag>}
        {c.review && <Tag tone={c.review.verdict === '통과' ? 'green' : 'amber'}>리뷰 {c.review.verdict}</Tag>}
        {c.instruction && <Tag>사람 지시</Tag>}
        {c.ciHold && c.status === 'waiting_human' && <Tag tone="amber">CI 다시 보는 중</Tag>}
        {c.status === 'covered' && <Tag tone="green">PR 없음 · {c.coveredBy?.length ? `${c.coveredBy.join(', ')}가 반영` : '앞 작업이 반영'}</Tag>}
      </span>
      {c.summary && <p>{c.summary}</p>}
      {c.mode === 'subscription' && c.progress && <p className="muted">{c.progress}</p>}
      {!!files.length && (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>파일</th><th className="num">변경</th><th>이유</th></tr></thead>
            <tbody>
              {files.map((x) => (
                <tr key={x.path}>
                  <td className="mono">{x.path}</td>
                  <td className="num mono"><span className="tag-green">+{x.additions}</span> <span className="tag-amber">−{x.deletions}</span></td>
                  <td className="small">{x.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!!c.review?.notes.length && <ul>{c.review.notes.map((n, i) => <li key={i} className="muted">{n}</li>)}</ul>}
      {c.instruction && <p className="muted">지시: {c.instruction}</p>}
      {c.error && <p className="tag-amber">{c.error}</p>}
      {waitingPr && (
        <form className="flex gap-2" onSubmit={revise}>
          <input className="field flex-1" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} placeholder="수정 지시 (같은 PR에 커밋)" aria-label="수정 지시" />
          <Button size="small" type="submit" disabled={busy || !note.trim()}>수정 지시</Button>
        </form>
      )}
      <span className="tags">
        {waitingPr && <Button size="small" variant="primary" onClick={merge} disabled={busy}>머지</Button>}
        {heldPr && <Button size="small" variant="primary" onClick={() => void approveHold()} disabled={busy}>{busy ? '보내는 중…' : '승인하고 PR 열기'}</Button>}
        {(STUCK.includes(c.status) || c.status === 'canceled') && !waitingPr && <Button size="small" onClick={retry} disabled={busy}>{busy ? '보내는 중…' : c.status === 'canceled' ? '재개' : c.mergeConflict ? '최신 코드로 다시 쓰기' : '다시 시도'}</Button>}
        {unclaimed && <Button size="small" onClick={() => void toAgent()} disabled={busy}>서버 AI로 넘기기</Button>}
        {noChange && <Button size="small" onClick={closeCovered} disabled={busy}>반영됨으로 닫기</Button>}
        <CancelButton taskId={task.id} status={c.status} />
      </span>
    </Step>
  )
}
