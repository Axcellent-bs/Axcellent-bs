import type { Plugin, ProxyOptions } from 'vite'

// 로컬 개발 서버(backend/scripts/run_web.sh)가 진짜 토큰을 **서버 쪽에서** 붙인다.
// 서버 배포에서 앞단 nginx가 자리표시자를 진짜 토큰으로 바꿔 붙이는 것과 같은 구조다.
// - 화면 번들에는 자리표시자만 들어간다(VITE_*_TOKEN = 아래 *_PLACEHOLDER)
// - 진짜 토큰은 VITE_ 접두사가 없는 AX_WEB_*_TOKEN으로만 개발 서버(node)에 넘어오고, 화면 코드에는 실리지 않는다
// - 화면이 로그인 세션 토큰(`axs1.`)을 보내면 그대로 넘기고 덮어쓰지 않는다(사람별 토큰 설계 2단계)
// - 다른 사이트가 이 개발 서버로 보낸 요청에는 붙이지 않는다(Origin·Sec-Fetch-Site가 이 화면이 아니면)

/** 사람 토큰 자리표시자. 화면은 `Authorization: Bearer <이 값>`을 보낸다 */
export const HUMAN_PLACEHOLDER = 'human-via-nginx'
/** 관리자(admin) 토큰 자리표시자. 총괄 시연 시작·기획 형상관리 연결 */
export const ADMIN_PLACEHOLDER = 'admin-via-nginx'
/** 데이터 AI 직접 조회 자리표시자. 본문 meta.token에 들어가고 데이터 서버는 헤더로 검증한다 (src/lib/env.ts DATA_A2A_PLACEHOLDER, agent-template PLACEHOLDER_TOKEN과 같은 값) */
export const DATA_PLACEHOLDER = 'data-via-nginx'
/** 서버 로그인 세션 토큰 앞부분 (src/lib/humanSession.ts) */
export const SESSION_PREFIX = 'axs1.'

/** 진짜 토큰을 받는 환경변수 이름 (run_web.sh가 backend/.data/web.env에서 읽어 넘긴다) */
export const TOKEN_ENV = { human: 'AX_WEB_HUMAN_TOKEN', admin: 'AX_WEB_ADMIN_TOKEN', data: 'AX_WEB_DATA_A2A_TOKEN' } as const

export interface ProxyTokens { human?: string; admin?: string; data?: string }
type Env = Record<string, string | undefined>
type Headers = Record<string, string | string[] | undefined>

/** 경로 종류: 사람·관리자 쓰기가 오가는 서비스(human), 데이터 AI A2A(data) */
export type TokenRoute = 'human' | 'data'

export function proxyTokens(env: Env): ProxyTokens {
  const read = (name: string) => (env[name] ?? '').trim() || undefined
  return { human: read(TOKEN_ENV.human), admin: read(TOKEN_ENV.admin), data: read(TOKEN_ENV.data) }
}

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v)

/** 이 개발 서버가 띄운 화면에서 온 요청인지. 브라우저가 다른 사이트라고 알리면 아니다 */
export function fromThisPage(headers: Headers): boolean {
  const site = one(headers['sec-fetch-site'])
  if (site && site !== 'same-origin' && site !== 'none') return false
  const origin = one(headers.origin)
  if (!origin) return true
  const host = one(headers.host)
  try {
    return !!host && new URL(origin).host === host
  } catch {
    return false
  }
}

/**
 * 백엔드로 넘길 Authorization 값. undefined면 받은 헤더를 그대로 둔다.
 * - 세션 토큰(`axs1.`)은 그대로
 * - human 경로: 사람·관리자 자리표시자만 진짜 토큰으로 바꾼다
 * - data 경로: 헤더가 없거나 데이터 자리표시자면 데이터 토큰을 붙인다(본문은 자리표시자)
 */
export function proxyAuthorization(route: TokenRoute, headers: Headers, tokens: ProxyTokens): string | undefined {
  const incoming = one(headers.authorization)?.trim() ?? ''
  const bearer = /^Bearer\s+(\S+)$/i.exec(incoming)?.[1] ?? ''
  if (bearer.startsWith(SESSION_PREFIX)) return undefined
  if (!fromThisPage(headers)) return undefined
  let real: string | undefined
  if (route === 'data') real = !incoming || bearer === DATA_PLACEHOLDER ? tokens.data : undefined
  else if (bearer === HUMAN_PLACEHOLDER) real = tokens.human
  else if (bearer === ADMIN_PLACEHOLDER) real = tokens.admin
  return real ? `Bearer ${real}` : undefined
}

/** 프록시 설정 하나에 토큰 붙이기를 더한다 */
export function withTokens(options: ProxyOptions, route: TokenRoute, tokens: ProxyTokens): ProxyOptions {
  if (!tokens.human && !tokens.admin && !tokens.data) return options
  return {
    ...options,
    configure: (proxy, opts) => {
      options.configure?.(proxy, opts)
      proxy.on('proxyReq', (proxyReq, req) => {
        const auth = proxyAuthorization(route, req.headers, tokens)
        if (auth !== undefined) proxyReq.setHeader('Authorization', auth)
      })
    },
  }
}

/** 빌드 결과물에 진짜 토큰이 들어간 파일 이름들 (토큰 값은 돌려주지 않는다) */
export function findTokenLeaks(files: { name: string; text: string }[], tokens: ProxyTokens): { file: string; env: string }[] {
  const leaks: { file: string; env: string }[] = []
  for (const key of ['human', 'admin', 'data'] as const) {
    const secret = tokens[key]
    if (!secret) continue
    for (const f of files) if (f.text.includes(secret)) leaks.push({ file: f.name, env: TOKEN_ENV[key] })
  }
  return leaks
}

/** `vite build`가 진짜 토큰(AX_WEB_*_TOKEN 값)을 번들에 실으면 빌드를 멈춘다 */
export function bundleTokenGuard(tokens: ProxyTokens): Plugin {
  return {
    name: 'bundle-token-guard',
    apply: 'build',
    generateBundle(_options, bundle) {
      const files = Object.values(bundle).map((o) => ({
        name: o.fileName,
        text: o.type === 'chunk' ? o.code : typeof o.source === 'string' ? o.source : new TextDecoder().decode(o.source),
      }))
      const leaks = findTokenLeaks(files, tokens)
      if (leaks.length) this.error(`번들에 진짜 토큰이 들어갔어요: ${leaks.map((l) => `${l.file}(${l.env})`).join(', ')}. VITE_*_TOKEN에는 자리표시자만 넣으세요`)
    },
  }
}
