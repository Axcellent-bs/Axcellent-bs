// DejaFix 서비스 화면(DejaFix/DejaFix frontend)을 데모용 정적 파일로 빌드해 public/dejafix/에 넣는다.
// 인프라 AI 화면(/infra)이 이 파일을 iframe으로 띄운다. 원본 레포는 건드리지 않고 git archive로 꺼낸 사본만 고친다.
//
//   node scripts/build-dejafix.mjs <DejaFix 레포 경로> [ref=origin/dev]
//
// 사본에서 바꾸는 것 네 가지:
// 1) main.tsx: 개발 서버에서만 켜지던 MSW 목을 VITE_DEMO_STATIC=on이면 빌드에서도 켠다(서비스 워커 주소는 base 기준).
// 2) index.html: 데모 관리자(admin) 목 세션을 미리 넣어 로그인 화면 없이 바로 열리게 한다.
// 3) Layout.tsx: 사이드바 로고를 base 기준 경로로 바꾼다.
// 4) index.html: DejaFix 자체 사이드바와 상단바를 숨긴다. 메뉴는 AXcellent 바깥 사이드바 하위 메뉴가, 시간대·AI 어시스턴트는 바깥 상단바가 맡는다(src/demo/DejaFixTools.tsx).
import { execSync } from 'node:child_process'
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const [repo, ref = 'origin/dev'] = process.argv.slice(2)
if (!repo) {
  console.error('사용법: node scripts/build-dejafix.mjs <DejaFix 레포 경로> [ref]')
  process.exit(1)
}
const run = (cmd, cwd) => execSync(cmd, { cwd, stdio: 'inherit', env: { ...process.env, VITE_ROUTER: 'hash', VITE_DEMO_STATIC: 'on' } })

const work = mkdtempSync(join(tmpdir(), 'dejafix-demo-'))
const commit = execSync(`git -C "${repo}" rev-parse --short ${ref}`).toString().trim()
// Windows tar가 C:\ 경로를 원격 주소로 읽지 않게 슬래시 경로와 --force-local을 쓴다
const slash = (p) => p.split('\\').join('/')
const tarFile = slash(join(work, 'frontend.tar'))
execSync(`git -C "${repo}" archive -o "${tarFile}" ${ref} frontend`)
execSync(`tar --force-local -xf "${tarFile}" -C "${slash(work)}"`)
const fe = join(work, 'frontend')

const mainPath = join(fe, 'src/main.tsx')
const main = readFileSync(mainPath, 'utf8')
const before = `  if (!import.meta.env.DEV || import.meta.env.VITE_MSW === 'off') return
  const { worker } = await import('./mocks/browser')
  await worker.start({ onUnhandledFrame: 'bypass' })`
if (!main.includes(before)) throw new Error('main.tsx의 MSW 시작 코드가 바뀌었습니다. 이 스크립트의 패치를 맞춰 주세요.')
writeFileSync(
  mainPath,
  main.replace(
    before,
    `  const staticDemo = import.meta.env.VITE_DEMO_STATIC === 'on'
  if ((!import.meta.env.DEV && !staticDemo) || import.meta.env.VITE_MSW === 'off') return
  const { worker } = await import('./mocks/browser')
  await worker.start({ onUnhandledFrame: 'bypass', quiet: staticDemo, serviceWorker: { url: \`\${import.meta.env.BASE_URL}mockServiceWorker.js\` } })`,
  ),
)

// 3) 사이드바 로고를 절대 경로(/logo-mark.png)에서 base 기준 경로로 바꾼다 (데모는 /dejafix/ 아래에서 열린다)
const layoutPath = join(fe, 'src/components/Layout.tsx')
writeFileSync(layoutPath, readFileSync(layoutPath, 'utf8').replace('src="/logo-mark.png"', 'src={`${import.meta.env.BASE_URL}logo-mark.png`}'))

run('npm ci --no-audit --no-fund', fe)
run('npx vite build --base ./', fe)

// 목 계정(admin)은 DejaFix mocks/handlers.ts의 시드 계정이다. 토큰도 목 형식(mock-jwt.<username>)이라 실제 서버에서는 쓸 수 없다.
const session = JSON.stringify({
  token: 'mock-jwt.admin',
  user: { id: 1, username: 'admin', role: 'admin', display_name: '관리자', email: 'admin@example.com' },
})
const indexPath = join(fe, 'dist/index.html')
writeFileSync(
  indexPath,
  readFileSync(indexPath, 'utf8').replace(
    '<meta charset="UTF-8" />',
    `<meta charset="UTF-8" />\n    <!-- AXcellent 데모: DejaFix ${commit} 목 버전. 데모 관리자 목 세션으로 바로 연다 -->\n    <script>try{if(!sessionStorage.getItem('dejafix.auth'))sessionStorage.setItem('dejafix.auth',${JSON.stringify(session)})}catch(e){}</script>
    <!-- 메뉴·상단바는 AXcellent 바깥 화면이 맡으므로 DejaFix 자체 사이드바·상단바·좁은 화면 메뉴를 숨긴다 -->
    <style>.shell,.shell.collapsed{grid-template-columns:1fr!important}.shell>.sidebar,.mobile-nav,.main>.topbar{display:none!important}</style>`,
  ),
)

const out = resolve('public/dejafix')
rmSync(out, { recursive: true, force: true })
mkdirSync(out, { recursive: true })
cpSync(join(fe, 'dist'), out, { recursive: true })
rmSync(work, { recursive: true, force: true })
console.log(`DejaFix ${commit} → public/dejafix`)
