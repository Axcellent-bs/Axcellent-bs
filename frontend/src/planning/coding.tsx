// 직접 개발: 개발팀이 기획을 바로 넣으면 코딩 에이전트가 파일 읽기 → 작성 → 리뷰 → PR까지 간다.
// 진행 상태는 Task.coding으로 SSE를 타고 온다
import { useEffect, useState, type FormEvent } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import type { CodingRun, CodingStatus, State as S, Task } from './data'
import { DEV_API, PlanningApiError, devTaskApi, vcsApi, type VcsConnection } from './api'
import { go } from './links'
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

// 쉼표·줄바꿈으로 나눈다
const split = (x: string) => x.split(/[,\n]/).map((p) => p.trim()).filter(Boolean)

/** 헤더의 "직접 개발" 창 */
export function DevTaskDialog({ onClose, onTools }: { onClose: () => void; onTools: () => void }) {
  const toast = useToast()
  const [conns, setConns] = useState<VcsConnection[]>([])
  const [f, setF] = useState({ title: '', plan: '', done: '', paths: '', connection: '' })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<{ code: string; message: string } | null>(null)

  useEffect(() => {
    vcsApi(DEV_API).list().then(setConns).catch(() => setConns([]))
  }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!f.plan.trim()) return setErr({ code: 'INVALID_INPUT', message: '기획 내용을 넣어 주세요.' })
    setBusy(true)
    setErr(null)
    try {
      const paths = split(f.paths)
      const r = await api.create({
        title: f.title.trim(), plan: f.plan.trim(),
        ...(f.done.trim() ? { done: f.done.trim() } : {}),
        ...(paths.length ? { paths } : {}),
        ...(f.connection ? { connection: f.connection } : {}),
      })
      toast.show('코딩 에이전트가 시작했어요')
      onClose()
      go(`#/work/prs/${encodeURIComponent(r.task_id)}`)
    } catch (x) {
      const code = x instanceof PlanningApiError ? x.code : 'NETWORK'
      const message = code === 'LLM_NOT_CONFIGURED' ? 'LLM 설정 필요 (backend/.env)'
        : x instanceof PlanningApiError ? x.message : '개발·배포 서버에 연결할 수 없어요'
      setErr({ code, message })
      toast.show(message)
    } finally {
      setBusy(false)
    }
  }

  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 pt-16" onClick={onClose} role="presentation">
      <div role="dialog" aria-modal="true" aria-label="직접 개발" className="w-full max-w-2xl space-y-4 rounded-2xl p-5 shadow-xl"
        style={{ background: 'rgb(var(--canvas))' }} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">직접 개발</h2>
          <button type="button" className="btn-quiet px-2 py-1" onClick={onClose} aria-label="닫기">✕</button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <input className="field" placeholder="제목" value={f.title} onChange={set('title')} aria-label="제목" />
          <textarea className="field" rows={6} required placeholder="기획 내용" value={f.plan} onChange={set('plan')} aria-label="기획 내용" />
          <textarea className="field" rows={3} placeholder="완료 기준 (한 줄에 하나)" value={f.done} onChange={set('done')} aria-label="완료 기준" />
          <div className="grid gap-3 md:grid-cols-2">
            <textarea className="field font-mono" rows={2} placeholder="바꿀 경로 힌트 (예: shop-web/, src/)" value={f.paths} onChange={set('paths')} aria-label="바꿀 경로 힌트" />
            <select className="field font-mono" value={f.connection} onChange={set('connection')} aria-label="대상 레포">
              <option value="">대상 레포 · AI가 고름</option>
              {conns.map((c) => <option key={c.id} value={c.id}>{c.repo}</option>)}
            </select>
          </div>
          {err && (
            <div className="flex flex-wrap items-center gap-2 text-sm text-red-700">
              <span>{err.code === 'NO_VCS' ? '연결된 레포 없음' : err.message}</span>
              {err.code === 'NO_VCS' && <button type="button" className="btn-ghost" onClick={() => { onClose(); onTools() }}>개발 도구 연결</button>}
            </div>
          )}
          <div className="flex gap-2">
            <button className="btn-primary px-5" disabled={busy || !f.plan.trim()}>{busy ? '보내는 중…' : '코딩 시작'}</button>
            <button type="button" className="btn-ghost" onClick={onClose}>취소</button>
          </div>
        </form>
      </div>
    </div>
  )
}

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
