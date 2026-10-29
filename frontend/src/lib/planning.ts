// 기획개발 백엔드(planning 에이전트, 8101) REST·SSE와 화면 상태(State)를 잇는다.
// 백엔드는 snake_case·ISO 시각, 화면은 camelCase·KST "MM-DD HH:mm"이라 여기서 한 번만 바꾼다.
// 백엔드에 없는 값(학습 기준·감사 목록)은 빈 값으로 두고 지어내지 않는다.
import { useCallback, useEffect, useRef, useState } from 'react'
import { RELAY_TOKEN, SAFETY_HUMAN_TOKEN } from '@/lib/env'
import type { Action, Activity, AuditItem, ChatThread, CrossReview, Doc, Incident, OutMsg, PR, Request, State, Thread } from '@/lib/planningData'

export const PLANNING_API = (import.meta.env.VITE_PLANNING_API_URL as string | undefined) ?? 'http://localhost:8101'
/** 개발·배포 서비스 에이전트(dev, 8111). 업무 로직은 기획과 같은 planning 도메인이라 같은 REST(`/api/planning`)를 쓴다 */
export const DEV_API = (import.meta.env.VITE_DEV_API_URL as string | undefined) ?? 'http://localhost:8111'
/** 화면 서비스(요청·기획서 / 개발·배포)마다 자기 에이전트를 부른다 */
export const apiBase = (part: 'planning' | 'dev') => (part === 'dev' ? DEV_API : PLANNING_API)

type Json = Record<string, unknown>
const camel = (k: string) => k.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase())
function camelize(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(camelize)
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [camel(k), camelize(x)]))
  return v
}

const pad = (n: number) => String(n).padStart(2, '0')
/** ISO 시각 → KST "MM-DD HH:mm". 비어 있으면 빈 문자열 */
export function kst(iso?: unknown): string {
  if (typeof iso !== 'string' || !iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return iso
  const k = new Date(t + 9 * 3_600_000)
  return `${pad(k.getUTCMonth() + 1)}-${pad(k.getUTCDate())} ${pad(k.getUTCHours())}:${pad(k.getUTCMinutes())}`
}

export const EMPTY_STATE: State = {
  seq: 0,
  outbox: [],
  requests: [],
  specs: [],
  tasks: [],
  prs: [],
  threads: [],
  docs: [],
  decisions: [],
  incidents: [],
  activity: [],
  reviews: [],
  audit: [],
  draftsIn: 0,
  draftsAccepted: 0,
  warnings: 0,
  firstPrMinutes: [],
  autoPolicy: { rules: [], observing: [], history: [], log: [] },
}

/** `GET /api/planning/review-queue` 한 줄 */
export interface QueueItem {
  kind: 'spec' | 'pr'
  id: string
  title: string
  action: 'confirm_spec' | 'merge_pr'
  state: string
  rules: string[]
  params: Record<string, number>
  at?: string
  decide_with?: Action
}

const VERIFIER: Record<QueueItem['action'], string> = { confirm_spec: '인사 check_plan', merge_pr: '데이터분석 · 통합 에이전트' }

function toReview(q: QueueItem, ctxOf: (ref: string) => string): CrossReview & { decideWith?: Action } {
  const done = /승인|확정|머지/.test(q.state) ? '승인' : /거절/.test(q.state) ? '거절' : undefined
  return {
    id: `XR-${q.id}`,
    t: kst(q.at),
    ctx: ctxOf(q.id),
    target: q.action,
    ref: q.id,
    title: q.title,
    lane: (q.params.irreversible_count ?? 0) > 0 || q.state.includes('사람') ? 'human' : 'ai',
    routeWhy: q.rules,
    summary: Object.entries(q.params).map(([k, v]) => `${k} ${v}`).join(' · '),
    checks: [],
    verifier: VERIFIER[q.action],
    result: done,
    decideWith: q.decide_with,
    at: q.at,
  }
}

/** 백엔드 상태(`GET /api/planning/state`·SSE `state`)와 교차검증 대기열 → 화면 상태 */
export function fromBackend(raw: Json, queue: QueueItem[]): State {
  const s = camelize(raw) as Json
  const list = <T,>(k: string) => (Array.isArray(s[k]) ? (s[k] as Json[]) : []) as unknown as T[]
  const requests = list<Json>('requests').map((r) => ({ ...r, ctx: r.contextId ?? '' }) as unknown as Request)
  const prs = list<Json>('prs').map((p) => ({
    ...p,
    mergedBy: p.merged ? (p.autoReview ? 'review' : p.auto ? 'rule' : 'human') : undefined,
  }) as unknown as PR)
  const ctxOf = (ref: string): string => {
    const spec = ref.startsWith('SPEC') ? ref : undefined
    const task = (s.tasks as Json[] | undefined)?.find((t) => prs.find((p) => p.id === ref)?.task === t.id)
    const specId = spec ?? (task?.spec as string | undefined)
    const req = (s.specs as Json[] | undefined)?.find((x) => x.id === specId)?.request
    return requests.find((r) => r.id === req)?.ctx ?? ''
  }
  const policy = (s.autoPolicy ?? {}) as Json
  return {
    ...EMPTY_STATE,
    seq: typeof s.seq === 'number' ? s.seq : 0,
    outbox: list<Json>('outbox').map((m) => ({ ...m, t: kst(m.at) || (m.t as string) || '', ctx: m.contextId ?? m.ctx ?? '' }) as unknown as OutMsg),
    requests,
    specs: list('specs'),
    tasks: list('tasks'),
    prs,
    threads: list<Json>('threads').map((th) => ({
      ...th,
      messages: ((th.messages as Json[]) ?? []).map((m) => ({ ...m, t: kst(m.at) || undefined, decision: m.decision ?? undefined })),
    }) as unknown as Thread),
    docs: list<Json>('docs').map((d) => ({ ...d, updated: kst(d.updatedAt), about: d.about ?? undefined }) as unknown as Doc),
    decisions: list('decisions'),
    incidents: list<Json>('incidents').map((i) => ({ ...i, ctx: i.contextId ?? '', at: kst(i.openedAt) }) as unknown as Incident),
    activity: list<Json>('activity').map((a) => ({ ...a, t: kst(a.at), ctx: a.contextId ?? '', seq: a.seq ?? undefined, grade: a.grade ?? undefined }) as unknown as Activity),
    reviews: queue.map((q) => toReview(q, ctxOf)),
    audit: [] as AuditItem[], // 백엔드에 감사 목록 API가 아직 없다
    draftsIn: Number(s.draftsIn ?? 0),
    draftsAccepted: Number(s.draftsAccepted ?? 0),
    warnings: Number(s.warnings ?? 0),
    firstPrMinutes: (s.firstPrMinutes as number[]) ?? [],
    ...(Array.isArray(s.chats) ? { chats: toThreads(raw.chats) } : {}),
    autoPolicy: {
      rules: [], // 관찰 기반 학습 기준은 백엔드에 아직 없다 (FR-ATL-007~009)
      observing: [],
      history: ((policy.history as Json[]) ?? []).map((h) => ({ ...h, t: kst(h.at) || (h.t as string) || '' })) as unknown as State['autoPolicy']['history'],
      log: ((policy.log as Json[]) ?? []).map((l) => ({ ...l, t: kst(l.at) })) as unknown as State['autoPolicy']['log'],
    },
  }
}

export class PlanningApiError extends Error {
  readonly code: string
  constructor(code: string, message: string) {
    super(message)
    this.code = code
  }
}

async function call<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${base}/api/planning${path}`, { cache: 'no-store', ...init })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = (body as { error?: { code?: string; message?: string } }).error
    throw new PlanningApiError(err?.code ?? `HTTP_${res.status}`, err?.message ?? `기획개발 서버 응답 ${res.status}`)
  }
  return (body as { data: T }).data
}

// 쓰기 토큰 (백엔드 auth.py): 사람 결정·요청 접수·대화는 사람 토큰, 초기화·형상관리 연결은 운영자(admin) 토큰
type Who = 'human' | 'admin'
const post = (body: unknown = {}, who: Who = 'human'): RequestInit => {
  const token = who === 'admin' ? RELAY_TOKEN : SAFETY_HUMAN_TOKEN
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  }
}

export const planningApi = (base: string) => ({
  state: () => call<Json>(base, '/state'),
  reviewQueue: () => call<QueueItem[]>(base, '/review-queue'),
  action: (a: Action) => call<Json>(base, '/actions', post(a)),
  eventsUrl: () => `${base}/api/planning/events`,
})

// ---- 배포 기록·배포 후 확인 (dev 서비스 `/api/planning/deployments`). SSE 상태와 따로 읽는다
export interface RollbackSuggestion { suspect_count: number; low_confidence: number; irreversible_count: number; prior_rollbacks: number; grade: 'B' | 'C'; rules: string[] }
export interface DeployCheck { deployment: string; at: string; window_minutes: number; verdict: 'ok' | 'watch' | 'suspect'; evidence: string[]; candidate_prs: string[]; rollback: RollbackSuggestion | null }
export interface Deployment {
  id: string; pr_numbers: number[]; version: string | null; env: 'dev' | 'prod'; at: string
  status: 'requested' | 'running' | 'succeeded' | 'failed' | 'rolled_back'
  approved_by: string | null; approval_reasons: string[]; protected_paths: string[]; check: DeployCheck | null
}

export const deployApi = (base: string) => ({
  list: () => call<unknown>(base, '/deployments').then((d) => (Array.isArray(d) ? (d as Deployment[]).filter((x) => x && typeof x.id === 'string') : [])),
  approve: (id: string) => call<Deployment>(base, `/deployments/${encodeURIComponent(id)}/approve`, post()),
  check: (id: string) => call<Deployment>(base, `/deployments/${encodeURIComponent(id)}/check`, post()),
})

// ---- 형상관리 연결 목록 (dev 서비스 `/api/planning/vcs/connections`). 레포 여러 개를 잇고 PR마다 대상 레포를 고른다
export interface VcsConnection {
  id: string; kind: string; repo: string; base_url: string; base_branch: string; description: string
  paths: string[]; mount?: string; token_set: boolean; token_hint: string; source: 'screen' | 'env'
}
export interface VcsConnectionInput { id?: string; kind: string; repo: string; base_url?: string; base_branch?: string; token?: string; description?: string; paths?: string[]; mount?: string }
export interface VcsTestResult { ok: boolean; code: string | null; message: string; checked_at: string; connection_id: string }

// 녹화 데모(목)는 모르는 주소에 빈 배열·{ok:true}를 준다. 연결 모양이 아닌 값은 버린다
const toConnections = (d: unknown): VcsConnection[] =>
  Array.isArray(d) ? (d as VcsConnection[]).filter((c) => c && typeof c.id === 'string' && typeof c.repo === 'string').map((c) => ({ ...c, paths: Array.isArray(c.paths) ? c.paths : [] })) : []

export const vcsApi = (base: string) => {
  const list = () => call<unknown>(base, '/vcs/connections').then(toConnections)
  // POST 응답이 목록이 아니면(목 등) 다시 읽는다
  const after = (d: unknown) => (Array.isArray(d) ? toConnections(d) : list())
  return {
    list,
    save: (c: VcsConnectionInput) => call<unknown>(base, '/vcs/connections', post(c, 'admin')).then(after),
    remove: (id: string) => call<unknown>(base, `/vcs/connections/${encodeURIComponent(id)}/delete`, post({}, 'admin')).then(after),
    test: (id: string) => call<VcsTestResult>(base, `/vcs/connections/${encodeURIComponent(id)}/test`, post({}, 'admin')),
  }
}

// ---- 직접 개발 (dev 서비스 `/api/planning/dev-tasks`). 개발팀이 기획을 바로 넣으면 코딩 에이전트가 PR까지 만든다
export interface DevTaskInput { title: string; plan: string; done?: string; paths?: string[]; connection?: string }
export interface DevTaskStarted { request_id: string; spec_id: string; task_id: string; status: 'queued' }

export const devTaskApi = (base: string) => ({
  // 목(녹화 데모)은 POST에 {ok:true}만 준다. task_id가 없으면 실패로 본다
  create: (x: DevTaskInput) => call<Partial<DevTaskStarted>>(base, '/dev-tasks', post(x)).then((d) => {
    if (!d || typeof d.task_id !== 'string') throw new PlanningApiError('NO_TASK', '개발·배포 서버가 작업을 만들지 않았어요')
    return d as DevTaskStarted
  }),
  retry: (taskId: string) => call<unknown>(base, `/dev-tasks/${encodeURIComponent(taskId)}/retry`, post()),
  cancel: (taskId: string) => call<unknown>(base, `/dev-tasks/${encodeURIComponent(taskId)}/cancel`, post()),
  revise: (taskId: string, note: string) => call<unknown>(base, `/dev-tasks/${encodeURIComponent(taskId)}/revise`, post({ note })),
})

// ---- AI와 대화 (dev 서비스 `/api/planning/dev-chat`). 답이 오면 바뀐 대화 전체를 돌려준다 (5~30초)
export interface ChatSend { thread_id?: string; message: string; connection?: string }

// 대화 모양이 아닌 값(목의 {ok:true} 등)은 버린다
const isThread = (x: unknown): x is Json => !!x && typeof x === 'object' && typeof (x as Json).id === 'string' && Array.isArray((x as Json).messages)
const toThread = (x: Json): ChatThread => camelize(x) as ChatThread
export const toThreads = (d: unknown): ChatThread[] => (Array.isArray(d) ? d.filter(isThread).map(toThread) : [])

export const chatApi = (base: string) => {
  const one = (d: unknown) => {
    if (!isThread(d)) throw new PlanningApiError('NO_THREAD', '개발·배포 서버가 대화를 돌려주지 않았어요')
    return toThread(d)
  }
  return {
    list: () => call<unknown>(base, '/dev-chat').then(toThreads),
    get: (id: string) => call<unknown>(base, `/dev-chat/${encodeURIComponent(id)}`).then(one),
    send: (x: ChatSend) => call<unknown>(base, '/dev-chat', post(x)).then(one),
  }
}

// ---- 레포 작업 공간 (dev 서비스 `/api/planning/repo/*`). 연결된 레포의 PR·브랜치를 GitHub를 열지 않고 다룬다
export type RepoPrState = 'open' | 'merged' | 'closed'
export type RepoCheckState = 'passed' | 'failed' | 'pending'
export interface RepoInternal { id: string; task: string; merged: boolean; closed: boolean }
export interface RepoPr {
  number: number; title: string; state: RepoPrState; branch: string; base: string; author: string; body?: string; draft: boolean
  created_at: string | null; updated_at: string | null; merged_at: string | null; closed_at: string | null; url: string | null
  comments: number | null; checks: RepoCheckState | null; internal?: RepoInternal | null
}
export interface RepoMeta { connection: string; repo: string; base_branch: string; read_only: boolean; issues?: boolean }
export type RepoIssueState = 'open' | 'closed'
export interface RepoIssue {
  number: number; title: string; state: RepoIssueState; author: string; body?: string; labels: string[]; assignees: string[]
  comments: number | null; created_at: string | null; updated_at: string | null; closed_at: string | null; url: string | null
  reason: string | null // completed · not_planned
}
export interface RepoIssueDetail extends RepoMeta { issue: RepoIssue; comments: RepoComment[] }
export interface RepoFile { path: string; status: string; additions: number; deletions: number; patch: string | null; previous_path: string | null; truncated: boolean }
export interface RepoCommit { sha: string; message: string; author: string; at: string | null }
export interface RepoComment { id: string; author: string; body: string; at: string | null }
export interface RepoPrDetail extends RepoMeta {
  pr: RepoPr; internal: RepoInternal | null
  diff: { number: number; files: RepoFile[]; truncated: boolean; total_files: number }
  commits: RepoCommit[]; comments: RepoComment[]
  checks: { state: RepoCheckState; runs: { name: string; state: RepoCheckState }[] } | null
}
export interface RepoBranch {
  name: string; sha: string; protected: boolean; default: boolean; base: boolean; last_commit_at: string | null
  ahead: number | null; behind: number | null; pr: { number: number; state: RepoPrState; title: string } | null
}
export type MergeMethod = 'merge' | 'squash' | 'rebase'
// 커밋 그래프: 최신이 먼저, 자식이 늘 부모보다 앞. refs = 이 커밋이 끝인 브랜치, prs = 이 커밋에 달린 PR
export interface RepoGraphPr { number: number; state: RepoPrState; title: string; branch: string; internal: string | null }
export interface RepoGraphCommit { sha: string; parents: string[]; message: string; author: string; at: string | null; refs: string[]; prs: RepoGraphPr[] }
export interface RepoGraph extends RepoMeta {
  commits: RepoGraphCommit[]; heads: Record<string, string>; selected: string[]
  branches: { name: string; sha: string; default: boolean; base: boolean }[]; truncated: boolean; limit: number
}
export interface RepoCommitDetail {
  sha: string; parents: string[]; message: string; author: string; at: string | null; url: string | null
  files: RepoFile[]; total_files: number
}

const qs = (x: Record<string, string | number | undefined>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(x)) if (v !== undefined && v !== '') p.set(k, String(v))
  const t = p.toString()
  return t ? `?${t}` : ''
}

export const repoApi = (base: string, connection?: string) => {
  const c = connection || undefined
  const pr = (n: number, act: string) => `/repo/prs/${n}/${act}${qs({ connection: c })}`
  return {
    prs: (state: 'all' | RepoPrState = 'all', q?: string, limit = 100) =>
      call<RepoMeta & { items: RepoPr[] }>(base, `/repo/prs${qs({ connection: c, state, q, limit })}`),
    pr: (n: number) => call<RepoPrDetail>(base, `/repo/prs/${n}${qs({ connection: c })}`),
    branches: () => call<RepoMeta & { items: RepoBranch[] }>(base, `/repo/branches${qs({ connection: c })}`),
    merge: (n: number, method: MergeMethod) => call<{ pr: RepoPr; via: 'internal' | 'repo' }>(base, pr(n, 'merge'), post({ method })),
    close: (n: number) => call<{ pr: RepoPr }>(base, pr(n, 'close'), post()),
    reopen: (n: number) => call<{ pr: RepoPr }>(base, pr(n, 'reopen'), post()),
    comment: (n: number, body: string) => call<{ comment: RepoComment }>(base, pr(n, 'comment'), post({ body })),
    revert: (n: number) => call<{ revert: { number: number; url: string | null; branch: string } }>(base, pr(n, 'revert'), post()),
    graph: (branches: string[] | null, limit: number) =>
      call<RepoGraph>(base, `/repo/graph${qs({ connection: c, branches: branches?.length ? branches.join(',') : undefined, limit })}`),
    commit: (sha: string) => call<RepoMeta & { commit: RepoCommitDetail }>(base, `/repo/commits/${encodeURIComponent(sha)}${qs({ connection: c })}`),
    deleteBranch: (name: string) => call<{ deleted: string }>(base, `/repo/branches/delete${qs({ connection: c })}`, post({ name })),
    issues: (state: 'all' | RepoIssueState = 'all', q?: string, limit = 100) =>
      call<RepoMeta & { items: RepoIssue[] }>(base, `/repo/issues${qs({ connection: c, state, q, limit })}`),
    issue: (n: number) => call<RepoIssueDetail>(base, `/repo/issues/${n}${qs({ connection: c })}`),
    commentIssue: (n: number, body: string) => call<{ comment: RepoComment }>(base, `/repo/issues/${n}/comment${qs({ connection: c })}`, post({ body })),
  }
}

// ── 화면 상태 (예전 store.ts) ──
// 기획개발 화면 상태는 기획개발 백엔드(planning 에이전트) 한 곳에서 온다.
// SSE `GET /api/planning/events`로 바뀔 때마다 전체 상태를 받고, 사람 버튼은 `POST /api/planning/actions`로 보낸다.
// 화면 안에서 상태를 지어내거나 혼자 진행시키지 않는다. 서버가 꺼져 있으면 빈 화면과 "연결 안 됨"을 보여 준다.

/** 액션이 실패하면 화면(PlanningPage)이 토스트로 알린다 */
export const ACTION_ERROR_EVENT = 'planning:action-error'
const report = (message: string) => window.dispatchEvent(new CustomEvent(ACTION_ERROR_EVENT, { detail: message }))

export function useSharedStore(base: string): [State, (a: Action) => void, boolean] {
  const [state, setState] = useState<State>(EMPTY_STATE)
  const [connected, setConnected] = useState(false)
  const api = planningApi(base)
  const reviews = useRef(state.reviews)
  reviews.current = state.reviews

  useEffect(() => {
    let alive = true
    const apply = async (raw: Record<string, unknown>) => {
      const queue = await api.reviewQueue().catch(() => [] as QueueItem[])
      if (alive) setState(fromBackend(raw, queue))
    }
    const es = new EventSource(api.eventsUrl())
    es.addEventListener('state', (e) => {
      setConnected(true)
      void apply(JSON.parse((e as MessageEvent).data) as Record<string, unknown>)
    })
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(es.readyState === EventSource.OPEN)
    return () => {
      alive = false
      es.close()
    }
  }, [base]) // eslint-disable-line react-hooks/exhaustive-deps

  const dispatch = useCallback((a: Action) => {
    let send: Action | undefined = a
    // 교차검증 대기열의 "승인"은 백엔드가 알려 준 확정·머지 액션(decide_with)으로 보낸다
    if (a.type === 'decideReview' || (a.type === 'humanDecide' && a.ok)) {
      const r = reviews.current.find((x) => x.id === a.review) as { decideWith?: Action } | undefined
      send = r?.decideWith
      if (!send) return report('이 검증 건을 처리할 서버 액션이 없습니다')
    } else if (a.type === 'humanDecide') {
      return report('보류는 아직 서버에서 받지 않습니다. 기다리면 대기열에 남습니다')
    }
    api.action(send).catch((e: unknown) => {
      if (e instanceof PlanningApiError && e.code === 'UNKNOWN_ACTION') report('이 기능은 아직 기획개발 서버에 연결되지 않았습니다')
      else if (e instanceof PlanningApiError) report(e.message)
      else report('기획개발 서버에 연결할 수 없습니다')
    })
  }, [base]) // eslint-disable-line react-hooks/exhaustive-deps

  return [state, dispatch, connected]
}
