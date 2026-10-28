// "이 코드 왜 이렇게 했지?" 역추적: 코드 줄 → PR → 작업 → 기획서 문단 → 결정 → 요청 → 근거.
// 모든 연결은 문단 id로 이어져 있어서 코드 규칙으로만 따라간다 (LLM 없음).
import type { PR, State } from './data'

export interface CodeLine { path: string; line: number; text: string }
export interface TraceStep { kind: '코드' | 'PR' | '작업' | '문단' | '결정' | '기각 이력' | '기획서' | '요청' | '근거' | '장애'; title: string; detail?: string; href?: string }

// 목 데이터에는 실제 diff가 없어 PR이 바꾼 대표 줄을 문단에 맞춰 만든다. 실제로는 github_get_pull의 변경 줄이 들어온다
export function codeOf(s: State, pr: PR): CodeLine[] {
  const task = s.tasks.find((t) => t.id === pr.task)
  const key = `${task?.spec}:${task?.para}`
  if (key === 'SPEC-5:R3') return [{ path: 'shop-api/payment/config.py', line: 12, text: 'PAYMENT_TIMEOUT_MS = 8000' }]
  if (key === 'SPEC-7:R1') return [
    { path: 'shop-api/cart/service.py', line: 41, text: 'if payment.failed: keep_cart(cart, mark_sold_out=True)' },
    { path: 'shop-api/cart/service.py', line: 42, text: 'return CartResult(kept=True, reason="payment_failed")' },
  ]
  if (key === 'SPEC-7:R2') return [{ path: 'shop-web/checkout/FailNotice.tsx', line: 18, text: '<RetryButton to="/checkout" keepCart />' }]
  return [{ path: `${pr.gate.paths}pages/notice.tsx`, line: 7, text: `label: "${task?.title ?? pr.title}"` }]
}

export function traceOf(s: State, prId: string, line?: CodeLine): TraceStep[] {
  const pr = s.prs.find((p) => p.id === prId)
  if (!pr) return []
  const task = s.tasks.find((t) => t.id === pr.task)
  const spec = s.specs.find((x) => x.id === task?.spec)
  const para = spec?.paras.find((p) => p.id === task?.para)
  const req = s.requests.find((r) => r.id === spec?.request)
  const decisions = s.decisions.filter((x) => x.spec === spec?.id)
  const steps: TraceStep[] = []
  const code = line ?? codeOf(s, pr)[0]
  steps.push({ kind: '코드', title: `${code.path}:${code.line}`, detail: code.text })
  steps.push({ kind: 'PR', title: `${pr.id} ${pr.title}`, detail: `${pr.branch} · ${pr.merged ? (pr.mergedBy === 'rule' ? `기준 ${pr.rule}로 자동 머지` : pr.mergedBy === 'review' ? '교차검증 승인 뒤 자동 머지' : '사람 머지') : '머지 전'} · 검수 ${pr.reviews.join(' → ')}`, href: '#/work/prs' })
  if (task) steps.push({ kind: '작업', title: `${task.id} ${task.title}`, detail: `상태 ${task.state}`, href: '#/work/prs' })
  if (para && spec) {
    steps.push({ kind: '문단', title: `${spec.id} ${para.id}`, detail: para.text, href: '#/work/specs' })
    if (para.warn) steps.push({ kind: '기각 이력', title: para.warn.card, detail: para.warn.text })
    if (para.incident) steps.push({ kind: '장애', title: para.incident, detail: '이 문단이 장애에 연결됨 (link_incident)', href: '#/work/aftercare' })
  }
  for (const x of decisions) steps.push({ kind: '결정', title: `${x.id} ${x.text}`, detail: `이유: ${x.reason} · 검토한 대안: ${x.alternatives} · 출처: ${x.from}` })
  if (spec) steps.push({ kind: '기획서', title: `${spec.id} ${spec.title}`, detail: `문제: ${spec.problem} · 목표: ${spec.goal}${spec.confirmedBy === 'auto' ? ' · 조건(질문·경고 없음, 근거 있음)으로 자동 확정' : spec.confirmedBy === 'review' ? ' · AI 교차검증 승인 뒤 확정' : ''}`, href: '#/work/specs' })
  if (req) steps.push({ kind: '요청', title: `${req.id} ${req.title}`, detail: `${req.from === 'human' ? '사람' : req.via} · ${req.evidence}`, href: '#/requests/incoming' })
  if (spec?.basis.length) steps.push({ kind: '근거', title: `근거 ${spec.basis.length}건`, detail: spec.basis.join(' / ') })
  return steps
}

// 어시스턴트 답변용 한 문단
export function traceText(steps: TraceStep[]): string {
  if (!steps.length) return ''
  const pick = (k: TraceStep['kind']) => steps.filter((x) => x.kind === k)
  const [code] = pick('코드')
  const parts = [`${code.title}의 \`${code.detail}\`는 ${pick('PR')[0]?.title}에서 들어왔습니다.`]
  const para = pick('문단')[0]
  if (para) parts.push(`이 PR은 ${para.title} "${para.detail}"를 구현한 것입니다.`)
  const warn = pick('기각 이력')[0]
  if (warn) parts.push(`이 문단에는 기각 이력 ${warn.title}(${warn.detail})가 붙어 있습니다.`)
  const dec = pick('결정')
  if (dec.length) parts.push(`관련 결정은 ${dec.map((d) => `${d.title} (${d.detail})`).join(', ')}입니다.`)
  const req = pick('요청')[0]
  if (req) parts.push(`출발점은 ${req.title}이고 근거는 ${req.detail?.split(' · ')[1] ?? '기록 없음'}입니다.`)
  return parts.join(' ')
}
