import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

// 기획개발 화면(src/planning)과 그 화면이 쓰는 공통 컴포넌트 스타일(@dejafix/common-frontend)은
// 업무 시스템 전체에 번지지 않도록 .planning-scope 안으로만 묶는다. 나머지 화면은 지금처럼 tailwind만 쓴다.
const SCOPE = '.planning-scope'
// 공통 컴포넌트는 vendor/common-frontend에 들어 있다 (npm 링크라 실제 경로가 vendor 쪽으로 잡힌다)
const SCOPED_FILE = /[\\/](src[\\/]planning|(@dejafix|vendor)[\\/]common-frontend[\\/]src[\\/]styles)[\\/]/

function scopeSelector(sel) {
  const s = sel.trim()
  if (s === ':root' || s === 'html' || s === 'body' || s === '#root' || s === 'html,body') return SCOPE
  if (s.startsWith(SCOPE)) return s
  if (/^(:root|html|body)(?=[\s.[:])/.test(s)) return s.replace(/^(:root|html|body)/, SCOPE)
  return `${SCOPE} ${s}`
}

const planningScope = () => ({
  postcssPlugin: 'planning-scope',
  Rule(rule) {
    const file = rule.source?.input?.file ?? ''
    if (!SCOPED_FILE.test(file) || rule.__scoped) return
    if (rule.parent?.type === 'atrule' && /keyframes$/i.test(rule.parent.name)) return
    rule.selectors = rule.selectors.map(scopeSelector)
    rule.__scoped = true
  },
})
planningScope.postcss = true

export default { plugins: [planningScope(), tailwindcss(), autoprefixer()] }
