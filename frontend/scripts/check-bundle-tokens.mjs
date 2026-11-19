// 빌드 결과물에 진짜 토큰이 실리지 않는지 본다: npm run check:bundle-tokens
// 1) run_web.sh처럼 진짜 토큰(AX_WEB_*_TOKEN)을 가짜 표식 값으로 주고 빌드한다 → 표식·AX_WEB_ 이름이 번들에 없어야 하고,
//    화면 쪽 값(VITE_*_TOKEN)은 자리표시자로 들어가 있어야 한다
// 2) 실수로 VITE_*_TOKEN에 진짜 토큰을 넣고 빌드하면 → 빌드가 멈춰야 한다(vite.config.ts의 bundleTokenGuard)
// 표식 값은 이 스크립트가 만든 난수라 비밀값이 아니다. 결과물은 임시 폴더에 쓰고 지운다(dist/는 건드리지 않는다).
import { spawnSync } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const vite = join(root, 'node_modules', 'vite', 'bin', 'vite.js')
const mark = (name) => `leakcheck-${name}-${randomBytes(12).toString('hex')}`
const real = { AX_WEB_HUMAN_TOKEN: mark('human'), AX_WEB_ADMIN_TOKEN: mark('admin'), AX_WEB_DATA_A2A_TOKEN: mark('data') }
const placeholders = { VITE_SAFETY_HUMAN_TOKEN: 'human-via-nginx', VITE_SHOP_ADMIN_TOKEN: 'admin-via-nginx', VITE_DATA_A2A_TOKEN: 'data-via-nginx' }

function build(extraEnv) {
  const out = mkdtempSync(join(tmpdir(), 'ax-bundle-check-'))
  const env = { ...process.env }
  for (const k of Object.keys(env)) if (/^(VITE_.*TOKEN|AX_WEB_.*TOKEN)$/.test(k)) delete env[k]
  const r = spawnSync(process.execPath, [vite, 'build', '--outDir', out, '--emptyOutDir', '--logLevel', 'error'], {
    cwd: root, env: { ...env, ...extraEnv }, encoding: 'utf-8',
  })
  return { out, status: r.status, log: `${r.stdout ?? ''}${r.stderr ?? ''}` }
}

function filesOf(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    return statSync(p).isDirectory() ? filesOf(p) : [p]
  })
}

const problems = []

// 1) run_web.sh와 같은 환경
const ok = build({ ...real, ...placeholders })
if (ok.status !== 0) problems.push(`run_web.sh와 같은 환경의 빌드가 실패했어요:\n${ok.log}`)
else {
  const text = filesOf(ok.out).map((p) => readFileSync(p, 'latin1')).join('\n')
  for (const [name, value] of Object.entries(real)) if (text.includes(value)) problems.push(`번들에 ${name} 값이 들어 있어요`)
  if (text.includes('AX_WEB_')) problems.push('번들에 AX_WEB_ 이름이 들어 있어요')
  for (const [name, value] of Object.entries(placeholders)) if (!text.includes(value)) problems.push(`번들에 ${name} 자리표시자가 없어요`)
}
rmSync(ok.out, { recursive: true, force: true })

// 2) VITE_*_TOKEN에 진짜 토큰을 넣은 실수 → 빌드가 멈춰야 한다
const bad = build({ ...real, ...placeholders, VITE_SAFETY_HUMAN_TOKEN: real.AX_WEB_HUMAN_TOKEN })
if (bad.status === 0) problems.push('VITE_SAFETY_HUMAN_TOKEN에 진짜 토큰을 넣었는데 빌드가 멈추지 않았어요')
else if (!bad.log.includes('AX_WEB_HUMAN_TOKEN')) problems.push(`빌드는 멈췄지만 번들 토큰 검사 때문이 아니에요:\n${bad.log}`)
if (bad.log.includes(real.AX_WEB_HUMAN_TOKEN)) problems.push('빌드 오류 문구에 토큰 값이 찍혔어요')
rmSync(bad.out, { recursive: true, force: true })

if (problems.length) {
  console.error(`번들 토큰 검사 실패:\n- ${problems.join('\n- ')}`)
  process.exit(1)
}
console.log('번들 토큰 검사 통과: 진짜 토큰은 번들에 없고 자리표시자만 들어가요. VITE_*_TOKEN에 진짜 토큰을 넣으면 빌드가 멈춰요')
