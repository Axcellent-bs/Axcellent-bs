// 백엔드 없이 도는 데모: 화면이 부르는 fetch·EventSource를 녹화본(fixtures.json)으로 대신한다.
// 녹화본은 ax-backend 서비스들을 목 모드(*_MOCK=1)로 띄우고 GET 응답을 그대로 저장한 것이다.
// 쓰기(POST 등)는 저장하지 않고 성공 응답만 돌려준다. 그래서 버튼을 눌러도 화면 데이터는 바뀌지 않는다.
import raw from './fixtures.json'

type Fixture = { status: number; body: unknown }
const FIXTURES = raw as Record<string, Fixture>

/** 포트·경로 앞부분으로 어느 서비스 요청인지 정한다 (원래 대시보드의 env.ts 기본 주소 기준) */
const BY_PORT: Record<string, string> = {
  '8020': 'dash', '8001': 'safety', '8105': 'fin', '8010': 'relay', '8101': 'planning', '8000': 'orch', '8106': 'data',
}

function route(input: string): { svc: string; path: string } | null {
  const m = /^https?:\/\/(?:localhost|127\.0\.0\.1):(\d+)(\/.*)?$/.exec(input)
  if (m) return BY_PORT[m[1]] ? { svc: BY_PORT[m[1]], path: m[2] ?? '/' } : null
  if (input.startsWith('/orch/')) return { svc: 'orch', path: input.slice('/orch'.length) }
  if (input.startsWith('/data-a2a/')) return { svc: 'data', path: input.slice('/data-a2a'.length) }
  return null
}

/** 같은 경로의 다른 id 녹화본으로 대신한다: /conversations/7 → /conversations/1 */
function similar(method: string, svc: string, path: string): Fixture | undefined {
  const shape = (p: string) => p.split('?')[0].split('/').map((s) => (/^(\d+|ctx_[\w-]+|buy_\w+)$/.test(s) ? ':id' : s)).join('/')
  const want = shape(path)
  const key = Object.keys(FIXTURES).find((k) => {
    const [m, s, p] = k.split(' ')
    return m === method && s === svc && shape(p) === want
  })
  return key ? FIXTURES[key] : undefined
}

function lookup(method: string, svc: string, path: string): Fixture {
  const exact = FIXTURES[`${method} ${svc} ${path}`] ?? FIXTURES[`${method} ${svc} ${path.split('?')[0]}`]
  if (exact) return exact
  const near = similar(method, svc, path)
  if (near) return near
  if (method === 'GET') return { status: 200, body: { data: [], meta: { demo: true } } }
  return { status: 200, body: { data: { ok: true, demo: true }, meta: { demo: true } } }
}

const json = (f: Fixture) =>
  new Response(f.status === 204 ? null : JSON.stringify(f.body), {
    status: f.status,
    headers: { 'Content-Type': 'application/json' },
  })

const realFetch = window.fetch.bind(window)
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const hit = route(url)
  if (!hit) return realFetch(input, init)
  const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase()
  await new Promise((r) => setTimeout(r, 120)) // 로딩 표시가 한 번은 보이게
  return json(lookup(method, hit.svc, hit.path))
}

/** 기획개발 화면은 SSE(`/api/planning/events`)로 전체 상태를 받는다. 녹화한 상태를 한 번 흘려 준다 */
class DemoEventSource extends EventTarget {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  readonly CONNECTING = 0
  readonly OPEN = 1
  readonly CLOSED = 2
  readyState = 0
  readonly url: string
  readonly withCredentials = false
  onopen: ((e: Event) => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null

  constructor(url: string | URL) {
    super()
    this.url = String(url)
    const hit = route(this.url)
    setTimeout(() => {
      this.readyState = 1
      const open = new Event('open')
      this.onopen?.(open)
      this.dispatchEvent(open)
      if (hit?.svc === 'planning') {
        const state = FIXTURES['GET planning /api/planning/state']?.body as { data?: unknown } | undefined
        this.dispatchEvent(new MessageEvent('state', { data: JSON.stringify(state?.data ?? {}) }))
      }
    }, 150)
  }

  close() {
    this.readyState = 2
  }
}
window.EventSource = DemoEventSource as unknown as typeof EventSource
