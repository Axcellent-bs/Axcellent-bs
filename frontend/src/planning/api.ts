// 기획개발 백엔드(planning 에이전트, 8101) REST·SSE와 화면 상태(State)를 잇는다.
// 백엔드는 snake_case·ISO 시각, 화면은 camelCase·KST "MM-DD HH:mm"이라 여기서 한 번만 바꾼다.
// 백엔드에 없는 값(학습 기준·감사 목록)은 빈 값으로 두고 지어내지 않는다.
import type { Action, Activity, AuditItem, CrossReview, Doc, Incident, OutMsg, PR, Request, State, Thread } from './data'

export const PLANNING_API = (import.meta.env.VITE_PLANNING_API_URL as string | undefined) ?? 'http://localhost:8101'

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
    lane: (q.params.irreversible_count ?? 0) > 0 ? 'human' : 'ai',
    routeWhy: q.rules,
    summary: Object.entries(q.params).map(([k, v]) => `${k} ${v}`).join(' · '),
    checks: [],
    verifier: VERIFIER[q.action],
    result: done,
    decideWith: q.decide_with,
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

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${PLANNING_API}/api/planning${path}`, { cache: 'no-store', ...init })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = (body as { error?: { code?: string; message?: string } }).error
    throw new PlanningApiError(err?.code ?? `HTTP_${res.status}`, err?.message ?? `기획개발 서버 응답 ${res.status}`)
  }
  return (body as { data: T }).data
}

export const planningApi = {
  state: () => call<Json>('/state'),
  reviewQueue: () => call<QueueItem[]>('/review-queue'),
  action: (a: Action) => call<Json>('/actions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(a) }),
  eventsUrl: () => `${PLANNING_API}/api/planning/events`,
}
