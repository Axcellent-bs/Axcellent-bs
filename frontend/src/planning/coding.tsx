// 코딩 에이전트: AI와 대화(chat.tsx)로 시작한 작업은 파일 읽기 → 작성 → 리뷰 → PR까지 간다.
// 진행 상태는 Task.coding으로 SSE를 타고 온다
import { useState } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import type { CodingRun, CodingStatus, State as S, Task } from './data'
import { DEV_API, PlanningApiError, devTaskApi } from './api'
import { State, Tag } from './parts'
import { Step } from './flow'

const api = devTaskApi(DEV_API)

export const CODING_LABEL: Record<CodingStatus, string> = {
  queued: '대기', reading: '파일 읽는 중', writing: '코드 작성 중', reviewing: '리뷰 중',
  opened: 'PR 열림', merged: '머지됨', waiting_human: '사람 확인', failed: '실패',
}
const DONE: CodingStatus[] = ['opened', 'merged']
const STUCK: CodingStatus[] = ['failed', 'waiting_human']

/** 개발팀이 직접 넣은 기획에서 나온 작업인가 */
export function fromDevTeam(s: S, t?: Task): boolean {
  if (!t) return false
  const spec = s.specs.find((x) => x.id === t.spec)
  if (spec?.origin === 'dev_team') return true
  const req = s.requests.find((r) => r.id === spec?.request) as { via?: string } | undefined
  return req?.via === 'dev_team'
}

export const DevTeamTag = () => <Tag>개발팀 기획</Tag>

/** 목록 줄·머리에 쓰는 코딩 상태 글자 */
export const codingState = (c: CodingRun) => CODING_LABEL[c.status] ?? c.status

/** 작업·PR 흐름 맨 위 "코딩 에이전트" 단계 */
export function CodingStep({ task }: { task: Task }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const c = task.coding
  if (!c) return null
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
    <Step name="코딩 에이전트" on={DONE.includes(c.status)} title={<State v={codingState(c)} />}>
      <span className="tags">
        {c.model && <Tag><span className="mono">{c.model}</span></Tag>}
        {c.attempts > 1 && <Tag>{c.attempts}회차</Tag>}
        {!!files.length && <Tag>{files.length}파일 · <span className="mono">+{add} −{del}</span></Tag>}
        {c.review && <Tag tone={c.review.verdict === '통과' ? 'green' : 'amber'}>리뷰 {c.review.verdict}</Tag>}
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
      {c.error && <p className="tag-amber">{c.error}</p>}
      {STUCK.includes(c.status) && <Button size="small" onClick={retry} disabled={busy}>{busy ? '보내는 중…' : '다시 시도'}</Button>}
    </Step>
  )
}
