import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import { dejafixProxy } from './dejafix-proxy'

// 화면은 항상 실제 AI 백엔드를 부른다 (backend/scripts/run_all.sh, .env.example). 녹화본 데모는 없다.
// 아래 프록시는 CORS가 없는 서버를 개발 서버가 대신 부르는 것이다. 포트는 ax-backend/CLAUDE.md 포트 표 기준.
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, fileURLToPath(new URL('.', import.meta.url)), ''), ...process.env }
  const proxy = {
    // 총괄(orchestrator). '^/orch/'로 한정한다. '/orch'만 쓰면 '/orchestrator' 화면 주소까지 프록시로 가 버린다
    '^/orch/': { target: env.ORCHESTRATOR_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true, rewrite: (p: string) => p.replace(/^\/orch/, '') },
    // 데이터 AI(8106). 데이터분석 AI 화면의 KPI 직접 조회용
    '/data-a2a': { target: env.DATA_PROXY_TARGET || 'http://127.0.0.1:8106', changeOrigin: true, rewrite: (p: string) => p.replace(/^\/data-a2a/, '') },
    // 기획개발 AI(8101). VITE_PLANNING_API_URL=/planning-api 로 두면 화면이 여기로 부른다
    '/planning-api': { target: env.PLANNING_PROXY_TARGET || 'http://127.0.0.1:8101', changeOrigin: true, rewrite: (p: string) => p.replace(/^\/planning-api/, '') },
    // 개발·배포 서비스(dev, 8111)
    '/dev-api': { target: env.DEV_PROXY_TARGET || 'http://127.0.0.1:8111', changeOrigin: true, rewrite: (p: string) => p.replace(/^\/dev-api/, '') },
  }
  return {
    // /dejafix-api 는 로그인까지 대신 해야 해서 단순 프록시가 아니라 플러그인이 맡는다 (dejafix-proxy.ts)
    plugins: [react(), dejafixProxy(env)],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: { port: 3090, strictPort: true, proxy },
    preview: { port: 3090, strictPort: true, proxy },
  }
})
