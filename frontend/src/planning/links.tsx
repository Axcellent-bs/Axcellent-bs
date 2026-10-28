// 페이지 사이 이동: 화면에 나오는 ID(요청·기획서·작업·PR·사건·문서·채널·결정·부서)를 그 상세 화면 주소로 바꾼다.
// 주소는 #/<메뉴>/<가로탭>/<항목>[/<하위 항목>]이라 뒤로 가기·새 탭·주소 공유가 된다.
import type { ReactNode } from 'react'
import type { Dept, Request, State } from './data'

// 후속 요청 = 장애 뒤 재발 방지(link_incident)처럼 끝난 일에서 이어진 요청
export const isFollowUp = (r: Request) => r.via.includes('재발 방지')
// 요청 탭 필터 주소. 같은 요청으로 연결만 한 것은 원래 요청 줄이 보이는 '전체'로
export function requestView(r: Request): 'all' | 'incoming' | 'progress' | 'done' | 'followup' {
  if (r.state === '중복 연결') return 'all'
  if (isFollowUp(r)) return 'followup'
  if (r.state === '접수') return 'incoming'
  if (r.state === '기획 중' || r.state === '작업 중') return 'progress'
  return 'done'
}

// PR id에 "#"과 공백이 있어 주소에서는 PR-28로 쓴다
export const prSlug = (id: string) => id.replace('PR #', 'PR-')
export const prFromSlug = (x?: string) => x?.replace(/^PR-/, 'PR #')

export const go = (href: string) => { location.hash = href }
export const deptHref = (d: Dept) => `#/metrics/activity/${d}`
// 사건·요청 하나(context_id)에서 에이전트끼리 주고받은 대화
export const convoHref = (ctx: string) => `#/talk/${ctx}`

// 요청의 기획서 (요청에 spec이 없어도 기획서가 요청을 가리키면 그것)
export const specOf = (s: State, r: Request) => s.specs.find((x) => x.id === r.spec || x.request === r.id)

// 요청 상태별 "다음에 할 일" 화면. 목록 줄을 누르거나 상세의 첫 버튼이 여기로 간다.
// 확정·머지는 자동(조건·기준) 또는 AI 교차검증이라, 사람이 할 일은 대기열 확인·질문 답·되돌릴 수 없는 일 확인뿐이다
export const reviewOf = (s: State, ref: string) => s.reviews.find((x) => x.ref === ref && !x.result)
export function nextOf(s: State, r: Request): { href: string; label: string } {
  if (r.state === '중복 연결' && r.duplicateOf) return { href: `#/projects/${r.duplicateOf}`, label: `원래 요청 ${r.duplicateOf} 보기` }
  const spec = specOf(s, r)
  if (r.state === '접수') return { href: `#/requests/${requestView(r)}/${r.id}`, label: r.from === 'hr' || r.from === 'data' ? '채택 여부 정하기' : '기획서 초안 만들기' }
  if (r.state === '기획 중') {
    if (!spec) return { href: `#/requests/${requestView(r)}/${r.id}`, label: '기획서 만들기' }
    const xr = reviewOf(s, spec.id)
    if (xr) return { href: `#/work/reviews/${xr.id}`, label: xr.lane === 'human' ? '사람 확인하기 (되돌릴 수 없는 일)' : '확정 교차검증 보기' }
    const th = s.threads.find((t) => t.on === spec.id)
    if (spec.question) return { href: th ? `#/talk/${th.id}` : `#/work/specs/${spec.id}`, label: '질문 답 받기 (답이 오면 자동 확정)' }
    return { href: `#/work/specs/${spec.id}`, label: '기획서 보기' }
  }
  if (r.state === '작업 중') {
    const tasks = spec ? s.tasks.filter((t) => t.spec === spec.id).map((t) => t.id) : []
    const open = s.prs.filter((p) => tasks.includes(p.task) && !p.merged)
    const waiting = open.map((p) => reviewOf(s, p.id)).find(Boolean)
    if (waiting) return { href: `#/work/reviews/${waiting.id}`, label: waiting.lane === 'human' ? `${waiting.ref} 사람 확인하기` : `${waiting.ref} 머지 교차검증 보기` }
    const fix = open.find((p) => p.reviews[p.reviews.length - 1] === '불일치' || s.reviews.some((x) => x.ref === p.id && x.result === '거절'))
    if (fix) return { href: `#/work/prs/${prSlug(fix.id)}`, label: `${fix.id} 수정 후 재검수` }
    const pr = open[0] ?? s.prs.find((p) => tasks.includes(p.task))
    return pr ? { href: `#/work/prs/${prSlug(pr.id)}`, label: `${pr.id} 보기` } : { href: spec ? `#/work/specs/${spec.id}` : '#/work/prs', label: 'PR 만들기' }
  }
  return { href: `#/projects/${r.id}`, label: '프로젝트 보기' }
}

export function hrefOf(s: State, id: string): string | undefined {
  if (id.startsWith('REQ-')) {
    const r = s.requests.find((x) => x.id === id)
    return r && `#/requests/${requestView(r)}/${id}`
  }
  if (id.startsWith('SPEC-')) return s.specs.some((x) => x.id === id) ? `#/work/specs/${id}` : undefined
  if (id.startsWith('PR #')) return `#/work/prs/${prSlug(id)}`
  if (id.startsWith('T-')) {
    const pr = s.prs.find((p) => p.task === id)
    if (pr) return `#/work/prs/${prSlug(pr.id)}`
    const t = s.tasks.find((x) => x.id === id)
    return t && `#/work/specs/${t.spec}/${t.para}`
  }
  if (id.startsWith('ctx_inc_')) return `#/work/aftercare/${id}`
  if (id.startsWith('ctx_')) {
    const r = s.requests.find((x) => x.ctx === id)
    return r ? `#/projects/${r.id}` : convoHref(id)
  }
  if (id.startsWith('DOC-')) return s.docs.some((x) => x.id === id) ? `#/docs/${id}` : undefined
  if (id.startsWith('TH-') || id.startsWith('CH-')) return `#/talk/${id}`
  if (id.startsWith('XR-')) return s.reviews.some((x) => x.id === id) ? `#/work/reviews/${id}` : undefined
  if (id.startsWith('AR-')) return s.autoPolicy.rules.some((x) => x.id === id) ? `#/metrics/autonomy/${id}` : undefined
  if (id.startsWith('D-')) {
    const x = s.decisions.find((y) => y.id === id)
    return x && `#/work/specs/${x.spec}`
  }
  return undefined
}

export function Ref({ s, id, children, className }: { s: State; id: string; children?: ReactNode; className?: string }) {
  const href = hrefOf(s, id)
  if (!href) return <span className={className ?? 'mono'}>{children ?? id}</span>
  return <a href={href} className={className ?? 'ref mono'}>{children ?? id}</a>
}

// 문장 안의 ID를 링크로 바꾼다 (규칙으로만, 없는 ID는 글자 그대로)
const ID_RE = /(?<![A-Za-z0-9_])(REQ-\d+|SPEC-\d+|T-\d+|PR #\d+|ctx_(?:req|inc)_\d+|DOC-\d+|TH-\d+|D-\d+|XR-\d+|AR-\d+)/g
export function Linked({ s, text }: { s: State; text: string }) {
  const parts = text.split(ID_RE)
  return <>{parts.map((p, i) => (i % 2 ? <Ref key={i} s={s} id={p} /> : p))}</>
}
