import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import { dejafixProxy } from './dejafix-proxy'
import { bundleTokenGuard, proxyTokens, withTokens } from './token-proxy'

// 화면은 항상 실제 AI 백엔드를 부른다 (backend/scripts/run_all.sh, .env.example). 녹화본 데모는 없다.
// 아래 프록시는 CORS가 없는 서버를 개발 서버가 대신 부르는 것이다. 포트는 ax-backend/CLAUDE.md 포트 표 기준.
// 토큰: 화면 번들에는 자리표시자만 있고, 진짜 토큰(AX_WEB_*_TOKEN, run_web.sh가 넘긴다)은 이 프록시가
// 서버 쪽에서 Authorization 헤더로 바꿔 붙인다. 서버 배포의 nginx와 같은 구조 (token-proxy.ts)
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, fileURLToPath(new URL('.', import.meta.url)), ''), ...process.env }
  const tokens = proxyTokens(env)
  const strip = (prefix: string) => (p: string) => p.replace(new RegExp(`^${prefix}`), '')
  const proxy = {
    // 총괄(orchestrator). '^/orch/'로 한정한다. '/orch'만 쓰면 '/orchestrator' 화면 주소까지 프록시로 가 버린다
    '^/orch/': withTokens({ target: env.ORCHESTRATOR_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true, rewrite: strip('/orch') }, 'human', tokens),
    // 데이터 AI(8106). 데이터분석 AI 화면의 KPI 직접 조회용. 본문은 자리표시자, 데이터 토큰은 헤더로 붙인다
    '/data-a2a': withTokens({ target: env.DATA_PROXY_TARGET || 'http://127.0.0.1:8106', changeOrigin: true, rewrite: strip('/data-a2a') }, 'data', tokens),
    // 공통 로그·화면 API(dashboard-api, 8020). run_web.sh는 VITE_DASHBOARD_API_URL=/dashboard-api 로 이 길을 쓴다
    '/dashboard-api': withTokens({ target: env.DASHBOARD_PROXY_TARGET || 'http://127.0.0.1:8020', changeOrigin: true, rewrite: strip('/dashboard-api') }, 'human', tokens),
    // 안전 관문(safety, 8001). VITE_SAFETY_API_URL=/safety-api
    '/safety-api': withTokens({ target: env.SAFETY_PROXY_TARGET || 'http://127.0.0.1:8001', changeOrigin: true, rewrite: strip('/safety-api') }, 'human', tokens),
    // 재무 AI(finance, 8105). VITE_FINANCE_AGENT_URL=/finance-api
    '/finance-api': withTokens({ target: env.FINANCE_PROXY_TARGET || 'http://127.0.0.1:8105', changeOrigin: true, rewrite: strip('/finance-api') }, 'human', tokens),
    // 기획 AI(8101). VITE_PLANNING_API_URL=/planning-api 로 두면 화면이 여기로 부른다
    '/planning-api': withTokens({ target: env.PLANNING_PROXY_TARGET || 'http://127.0.0.1:8101', changeOrigin: true, rewrite: strip('/planning-api') }, 'human', tokens),
    // 개발·배포 서비스(dev, 8111)
    '/dev-api': withTokens({ target: env.DEV_PROXY_TARGET || 'http://127.0.0.1:8111', changeOrigin: true, rewrite: strip('/dev-api') }, 'human', tokens),
    // 인사 AI(org, 8124). AI 대화방 직접 대화(/api/hr/chat). 서버에 CORS가 없어 프록시로 부른다 (결정 기록 서비스 8114는 아카이브 AI)
    '/hr-api': withTokens({ target: env.HR_PROXY_TARGET || 'http://127.0.0.1:8124', changeOrigin: true, rewrite: strip('/hr-api') }, 'human', tokens),
    // 아카이브 AI(decisions, 8114). AI 대화방 직접 대화(/api/archive/chat). 서버에 CORS가 없어 프록시로 부른다
    '/archive-api': withTokens({ target: env.ARCHIVE_PROXY_TARGET || 'http://127.0.0.1:8114', changeOrigin: true, rewrite: strip('/archive-api') }, 'human', tokens),
  }
  return {
    // /dejafix-api 는 플러그인이 맡는다: 기본은 백엔드 dejafix-proxy(8030)로 그대로, 계정을 적으면 대신 로그인 (dejafix-proxy.ts)
    // bundleTokenGuard: vite build가 진짜 토큰을 번들에 실으면 빌드를 멈춘다
    plugins: [react(), dejafixProxy(env), bundleTokenGuard(tokens)],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: { port: 3090, strictPort: true, proxy },
    preview: { port: 3090, strictPort: true, proxy },
  }
})
