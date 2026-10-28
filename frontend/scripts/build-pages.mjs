// GitHub Pages용 빌드: 하위 경로(/Axcellent-bs/)로 빌드하고, 새로고침한 하위 주소도 앱이 받도록 404.html을 만든다.
import { execSync } from 'node:child_process'
import { copyFileSync } from 'node:fs'

execSync('npx tsc --noEmit -p . && npx vite build', { stdio: 'inherit', env: { ...process.env, DEMO_BASE: '/Axcellent-bs/' } })
copyFileSync('dist/index.html', 'dist/404.html')
