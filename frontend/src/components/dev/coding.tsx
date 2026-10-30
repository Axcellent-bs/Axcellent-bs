// 코딩 에이전트: AI와 대화(chat.tsx)로 시작한 작업은 파일 읽기 → 작성 → 리뷰 → PR까지 간다.
// 진행 상태는 Task.coding으로 SSE를 타고 온다
import { useState, type FormEvent } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, RELATION_LABEL, byRelation, type CodingRun, type CodingStatus, type Request, type State as S, type Task } from '@/lib/planningData'
import { DEV_API, PlanningApiError, devTaskApi, planningApi } from '@/lib/planning'
import { State, Tag } from '@/components/planning-common/parts'
import { Step } from '@/components/planning-common/flow'
import { Ref } from '@/components/planning-common/links'

const api = devTaskApi(DEV_API)
const actions = planningApi(DEV_API)

export const CODING_LABEL: Record<CodingStatus, string> = {
  queued: '대기', reading: '파일 읽는 중', writing: '코드 작성 중', reviewing: '리뷰 중',
  opened: 'PR 열림', merged: '머지됨', waiting_human: '사람 확인', failed: '실패', canceled: '중단됨',
}
const DONE: CodingStatus[] = ['opened', 'merged']
/** 에이전트가 지금 일하는 중인 상태 (옆에 깜빡이는 점) */
const RUNNING: CodingStatus[] = ['queued', 'reading', 'writing', 'reviewing']
export const isRunning = (s?: CodingStatus | string) => RUNNING.includes(s as CodingStatus)
/** 상태 글자. 진행 중이면 앞 점이 깜빡인다 */
export const CodingLabel = ({ c }: { c: CodingRun }) => <State v={codingState(c)} live={isRunning(c.status)} />
const STUCK: CodingStatus[] = ['failed', 'waiting_human']
/** 사람이 중단할 수 있는 상태 (머지됨·중단됨이 아니면) */
export const canCancel = (s?: CodingStatus) => !!s && s !== 'merged' && s !== 'canceled'

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
export const codingState = (c: CodingRun) => CODING_LABEL[c.status] ?? c.status

/** 작업·PR 흐름 맨 위 "코딩 에이전트" 단계 */
export function CodingStep({ task }: { task: Task }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const c = task.coding
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
  return (
    <Step name="코딩 에이전트" on={DONE.includes(c.status)} title={<CodingLabel c={c} />}>
      <span className="tags">
        {c.model && <Tag><span className="mono">{c.model}</span></Tag>}
        {c.attempts > 1 && <Tag>{c.attempts}회차</Tag>}
        {!!files.length && <Tag>{files.length}파일 · <span className="mono">+{add} −{del}</span></Tag>}
        {c.review && <Tag tone={c.review.verdict === '통과' ? 'green' : 'amber'}>리뷰 {c.review.verdict}</Tag>}
        {c.instruction && <Tag>사람 지시</Tag>}
      </span>
      {c.summary && <p>{c.summary}</p>}
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
        {(STUCK.includes(c.status) || c.status === 'canceled') && !waitingPr && <Button size="small" onClick={retry} disabled={busy}>{busy ? '보내는 중…' : c.status === 'canceled' ? '재개' : '다시 시도'}</Button>}
        <CancelButton taskId={task.id} status={c.status} />
      </span>
    </Step>
  )
}
