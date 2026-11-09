import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'

// /dejafix-api/* → DejaFix 백엔드. DejaFix는 모든 API에 로그인 토큰(JWT)을 요구한다(contracts/api.md 1. 공통 규칙).
// 토큰을 브라우저 코드에 넣지 않으려고 개발 서버가 대신 로그인하고, 받은 토큰을 붙여 부른다.
// 아이디·비밀번호는 frontend/.env 의 DEJAFIX_USERNAME·DEJAFIX_PASSWORD (VITE_ 접두사가 없어서 화면 코드에 실리지 않는다).
// 장애 대응 탭은 읽기만 하므로 GET만 넘긴다.
export function dejafixProxy(env: Record<string, string | undefined>): Plugin {
  const target = (env.DEJAFIX_PROXY_TARGET || 'http://127.0.0.1:18000').replace(/\/$/, '')
  const username = env.DEJAFIX_USERNAME
  const password = env.DEJAFIX_PASSWORD
  let token: string | null = null

  async function login(): Promise<string | null> {
    if (!username || !password) return null
    const res = await fetch(`${target}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!res.ok) return null
    const body = (await res.json()) as { data?: { token?: string } }
    return body.data?.token ?? null
  }

  const call = (path: string) =>
    fetch(`${target}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })

  async function handle(req: IncomingMessage, res: ServerResponse) {
    const send = (status: number, body: string, type = 'application/json') => {
      res.statusCode = status
      res.setHeader('Content-Type', type)
      res.end(body)
    }
    if (req.method !== 'GET') return send(405, JSON.stringify({ error: { code: 'METHOD_NOT_ALLOWED', message: '읽기(GET)만 넘깁니다' } }))
    const path = req.url || '/'
    try {
      if (!token) token = await login()
      let upstream = await call(path)
      // 토큰이 만료됐으면 한 번 다시 로그인한다
      if (upstream.status === 401 && username && password) {
        token = await login()
        upstream = await call(path)
      }
      send(upstream.status, await upstream.text(), upstream.headers.get('content-type') ?? 'application/json')
    } catch {
      send(502, JSON.stringify({ error: { code: 'DEJAFIX_UNREACHABLE', message: `DejaFix 백엔드(${target})에 연결할 수 없습니다` } }))
    }
  }

  return {
    name: 'dejafix-proxy',
    configureServer(server) {
      server.middlewares.use('/dejafix-api', (req, res) => void handle(req, res))
    },
    configurePreviewServer(server) {
      server.middlewares.use('/dejafix-api', (req, res) => void handle(req, res))
    },
  }
}
