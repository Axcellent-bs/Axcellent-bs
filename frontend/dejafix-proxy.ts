import type { Plugin } from 'vite'
import { proxyAuthorization, proxyTokens } from './token-proxy.ts'

// /dejafix-api/* → DejaFix. 장애 대응 탭은 읽기만 하므로 GET만 넘긴다.
// 기본은 백엔드 dejafix-proxy(8030, run_all.sh가 띄움)로 브라우저의 Authorization(사람 토큰)과 함께 그대로 넘긴다
// (그 프록시가 토큰을 확인한다). 사람 토큰 자리표시자(human-via-nginx)는 다른 human 경로처럼 진짜 토큰(AX_WEB_HUMAN_TOKEN)으로
// 바꾸고, 로그인 세션 토큰(axs1.)은 그대로 둔다 (token-proxy.ts). 그 프록시가 도구 연결 보관함
// (회원·서버 관리 › DejaFix 연결)의 계정으로 로그인하므로 여기서는 로그인하지 않는다 (서버 배포의 nginx와 같은 길).
// DEJAFIX_PROXY_TARGET에 DejaFix 백엔드를 직접 적고 DEJAFIX_USERNAME·DEJAFIX_PASSWORD를 넣으면
// 예전처럼 개발 서버가 대신 로그인한다 (VITE_ 접두사가 없어서 화면 코드에 실리지 않는다).
export const DEFAULT_TARGET = 'http://127.0.0.1:8030'

type Env = Record<string, string | undefined>
// node:http의 요청·응답 중 여기서 쓰는 부분만 (시험에서 가짜로 넘길 수 있게)
type Req = { method?: string; url?: string; headers?: Record<string, string | string[] | undefined> }
type Res = { statusCode: number; setHeader(name: string, value: string): unknown; end(body: string): unknown }

/** `/dejafix-api` 아래 요청 하나를 넘기는 함수. 계정이 없으면 로그인하지 않고 그대로 넘긴다 */
export function dejafixHandler(env: Env): (req: Req, res: Res) => Promise<void> {
  const target = (env.DEJAFIX_PROXY_TARGET || DEFAULT_TARGET).replace(/\/$/, '')
  const username = env.DEJAFIX_USERNAME
  const password = env.DEJAFIX_PASSWORD
  const tokens = proxyTokens(env)
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

  // 기본(백엔드 dejafix-proxy): 브라우저가 붙인 사람 토큰을 그대로 넘긴다 (프록시가 확인한다).
  // DejaFix 직접 로그인(옛 방식, 계정을 적었을 때)만 자기 로그인 토큰을 쓴다
  const call = (path: string, browserAuth: string | undefined) => {
    const auth = username && password ? (token ? `Bearer ${token}` : undefined) : browserAuth
    return fetch(`${target}${path}`, { headers: auth ? { Authorization: auth } : {} })
  }

  return async function handle(req: Req, res: Res) {
    const send = (status: number, body: string, type = 'application/json') => {
      res.statusCode = status
      res.setHeader('Content-Type', type)
      res.end(body)
    }
    if (req.method !== 'GET') return send(405, JSON.stringify({ error: { code: 'METHOD_NOT_ALLOWED', message: '읽기(GET)만 넘깁니다' } }))
    const path = req.url || '/'
    const raw = req.headers?.authorization
    const browserAuth = (req.headers && proxyAuthorization('human', req.headers, tokens)) ?? (Array.isArray(raw) ? raw[0] : raw)
    try {
      if (!token) token = await login()
      let upstream = await call(path, browserAuth)
      // 토큰이 만료됐으면 한 번 다시 로그인한다
      if (upstream.status === 401 && username && password) {
        token = await login()
        upstream = await call(path, browserAuth)
      }
      send(upstream.status, await upstream.text(), upstream.headers.get('content-type') ?? 'application/json')
    } catch {
      send(502, JSON.stringify({ error: { code: 'DEJAFIX_UNREACHABLE', message: `DejaFix 백엔드(${target})에 연결할 수 없습니다` } }))
    }
  }
}

export function dejafixProxy(env: Env): Plugin {
  const handle = dejafixHandler(env)
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
