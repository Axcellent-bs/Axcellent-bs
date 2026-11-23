// 개발 화면(교차검증·지표·작업 현황)의 순수 계산. node --test (devView.test.ts).
// 출처는 개발·기획 서버 상태(GET /state·SSE)와 교차검증 대기열(GET /review-queue)뿐이다. 없는 판정·추천·학습 기준은 만들지 않는다.
import { DEPT_NAME, RULE_LABEL, type Action, type Activity, type AuditItem, type CrossReview, type Dept, type State, type Task } from './planningData.ts'

// ---------------------------------------------------------------- 사람 버튼 → 서버 액션

/** 운영 결정 액션 (백엔드 api.py ADMIN_ACTIONS: approve_promotion·reject_promotion) */
export const ADMIN_ACTION_TYPES: ReadonlySet<string> = new Set(['approvePromotion', 'rejectPromotion'])
/**
 * 액션을 보낼 토큰. 운영 결정은 로그인 세션이 있으면 그 세션 토큰(서버 세션 모드는 로그인한 회사 관리자만 받고
 * 그 사람을 기록한다, auth.session_operator), 없으면 예전처럼 운영자(admin) 토큰. 나머지는 사람 토큰
 */
export const tokenFor = (a: Action, signedIn = false): 'admin' | 'human' =>
  ADMIN_ACTION_TYPES.has(a.type) && !signedIn ? 'admin' : 'human'

/**
 * 내 Claude 작업의 맡긴 사람·가져간 사람 표시. 세션 모드 서버는 계정 id(`acct:<계정 id>`)로 적는다.
 * 계정 id는 그대로 보이지 않는다: 내 계정이면 "나", 남이면 "팀원". 예전 이름(토큰 이름)은 그대로
 */
export function workerLabel(name: string | null | undefined, me: string | null | undefined): string | null {
  if (!name) return null
  if (!name.startsWith('acct:')) return name
  return me && name === `acct:${me}` ? '나' : '팀원'
}

/**
 * 교차검증 대기열 버튼을 서버 액션으로 바꾼다.
 * "확인하고 진행"은 대기열이 알려 준 decide_with(확정·머지)로. 보류는 서버가 받는 액션이 없어 보낼 것이 없다.
 */
export function routeAction(a: Action, reviews: (CrossReview & { decideWith?: Action })[]): { send: Action; human: boolean } | { error: string } {
  if (a.type === 'decideReview' || (a.type === 'humanDecide' && a.ok)) {
    const send = reviews.find((x) => x.id === a.review)?.decideWith
    return send ? { send, human: true } : { error: '이 검증 건을 처리할 서버 액션이 없습니다' }
  }
  if (a.type === 'humanDecide') return { error: '보류는 아직 서버에서 받지 않습니다. 기다리면 대기열에 남습니다' }
  return { send: a, human: false }
}

// ---------------------------------------------------------------- 교차검증 화면

/**
 * 지금 이 건이 어디에 있나. 근거(총괄·데이터분석·같은 경로 장애)는 머지를 시도할 때 이미 다 모인다.
 * 그 뒤에 따로 판정하는 AI 검증자는 없고, 결정은 사람 버튼(확인하고 진행 → 안전 관문)이다.
 * timeout: 화면 SSE가 열려 있을 때만 서버가 오래된 교차검증 대기를 사람 확인으로 바꾼다 (escalate_waits)
 */
export function reviewStage(r: Pick<CrossReview, 'lane' | 'evidence'>): { label: string; timeout: boolean } {
  if (r.lane === 'human') return { label: '사람 확인', timeout: false }
  if (r.evidence?.length) return { label: '근거 모음 완료 · 사람 판단', timeout: false }
  return { label: '규칙에 걸려 멈춤 · 사람 판단', timeout: true }
}

const count = (v: unknown) => (typeof v === 'number' ? v : Number(v ?? 0))
type Params = Record<string, unknown>
// 판정에 쓴 값 (autocheck.py spec_params·pr_params). 근거 줄(cross_check)과 겹치는 값·작업 id는 보이지 않는다
const PARAM_LINES: ((p: Params) => string | undefined)[] = [
  (p) => ('changed_lines' in p || 'changed_files' in p ? `${count(p.changed_lines)}줄 · ${count(p.changed_files)}파일 바뀜` : undefined),
  (p) => {
    const xs = Array.isArray(p.changed_paths) ? (p.changed_paths as unknown[]).map(String) : []
    return xs.length ? `경로 ${xs.slice(0, 3).join(', ')}${xs.length > 3 ? ` 외 ${xs.length - 3}개` : ''}` : undefined
  },
  (p) => (count(p.review_mismatch_count) > 0 ? `리뷰 불일치 ${count(p.review_mismatch_count)}회` : undefined),
  (p) => (count(p.linked_incident_count) > 0 ? `장애 났던 문단 ${count(p.linked_incident_count)}곳` : undefined),
  (p) => (count(p.open_questions) > 0 ? `열린 질문 ${count(p.open_questions)}개` : undefined),
  (p) => (count(p.warnings) > 0 ? `인사 기각 경고 ${count(p.warnings)}개` : undefined),
  (p) => ('basis_count' in p ? `근거 ${count(p.basis_count)}건` : undefined),
  (p) => (count(p.irreversible_count) > 0 ? `되돌릴 수 없는 영역 ${count(p.irreversible_count)}곳` : undefined),
]
export const paramLines = (p: Params): string[] => PARAM_LINES.map((f) => f(p)).filter((x): x is string => !!x)

/** 자동으로 처리하지 않은 이유. 모르는 규칙 키는 일반 문구 한 번만, 규칙 없이 안전 관문이 막았으면 그 사실 */
export function routeWhyOf(rules: string[], gateStatus: string | null | undefined): string[] {
  const known = rules.filter((k) => RULE_LABEL[k]).map((k) => RULE_LABEL[k])
  const why = [...new Set(known)]
  if (rules.some((k) => !RULE_LABEL[k])) why.push('그 밖의 자동 처리 규칙')
  if (!why.length && gateStatus) why.push(gateStatus === 'held' ? '안전 관문이 보류' : `안전 관문 ${gateStatus}`)
  return why
}

// ---------------------------------------------------------------- AI 단독 처리 (사후 감사)

const PR_RE = /PR #\d+/
const SPEC_RE = /SPEC-[A-Z]?\d+/
const BY_RULE = '안전 관문 (코드 규칙)'

/**
 * 사람 클릭 없이 처리한 일. 서버가 이미 남긴 기록만 쓴다:
 * - 활동 execute(merge_pr) (자동 머지일 때만 남는다. "사람이 …"는 뺀다) → 자동 머지, 그 PR에 교차검증 근거가 있으면 교차검증 승인
 * - 활동 audit.auto_confirmed → 기획서 자동 확정
 * - 자동 범위 변경 기록(log) 중 by 자동: 승급 제안 → 기준 제안, 자동 축소 → 기준 해제
 */
export function auditOf(s: Pick<State, 'prs' | 'activity' | 'autoPolicy'>): AuditItem[] {
  const out: AuditItem[] = []
  for (const a of s.activity) {
    if (a.point === 'execute(merge_pr)' && !a.text.startsWith('사람이')) {
      const ref = a.text.match(PR_RE)?.[0]
      if (!ref) continue
      const p = s.prs.find((x) => x.id === ref)
      const ev = p?.crossEvidence ?? []
      const h = s.autoPolicy.history.find((x) => x.pr === ref)
      out.push({
        t: a.t, ctx: a.ctx, kind: ev.length ? '교차검증 승인' : '자동 머지', ref, what: p?.title ?? '',
        basis: ev.length ? `교차검증 근거 ${ev.length}건 · ${a.text}` : a.text, verifier: BY_RULE,
        result: h ? h.result : p?.merged ? '머지됨' : p?.closed ? '닫힘' : '머지 전',
      })
    } else if (a.point === 'audit.auto_confirmed') {
      out.push({ t: a.t, ctx: a.ctx, kind: '자동 확정', ref: a.text.match(SPEC_RE)?.[0] ?? '', what: '', basis: a.text, verifier: BY_RULE, result: '확정' })
    }
  }
  for (const l of s.autoPolicy.log) {
    if (l.by !== '자동') continue
    const kind = l.text.includes('승급 제안') ? '기준 제안' : l.text.includes('자동 축소') ? '기준 해제' : undefined
    if (kind) out.push({ t: l.t, ctx: '', kind, ref: '자동 범위', what: '', basis: l.text, verifier: kind === '기준 제안' ? '연속 정상 건수 (코드)' : '장애 연결 (코드)', result: kind === '기준 제안' ? '관리자 승인 대기' : '범위 줄임' })
  }
  return out
}

// ---------------------------------------------------------------- 에이전트 활동

export type Part = 'dev' | 'planning'
/** 모르는 이름은 "내부" (부서가 아닌 값이 들어와도 빈 글자가 되지 않게) */
export const deptLabel = (x: string): string => (DEPT_NAME as Record<string, string>)[x] ?? '내부'

/**
 * 이 쪽(other와 짝인 x)이 화면의 자기 부서인가.
 * 개발 서버는 기획과 같이 쓰는 코드에서 자기 활동을 예전 이름 planning으로 남긴다. 그래서 개발 화면에서는
 * planning도 자기로 보되, 상대가 dev인 줄(dev↔planning 넘기기)에서는 planning이 진짜 기획 서비스다.
 */
const isSelf = (x: string, other: string, part: Part) => (part === 'dev' ? x === 'dev' || (x === 'planning' && other !== 'dev') : x === 'planning')

export function activityDir(a: Pick<Activity, 'from' | 'to'>, part: Part): '보냄' | '받음' | '내부' {
  const f = isSelf(a.from, a.to, part)
  const t = isSelf(a.to, a.from, part)
  return f && !t ? '보냄' : t && !f ? '받음' : '내부'
}

/** 자기 부서가 아닌 상대 (나온 순서) */
export function activityPeers(items: Pick<Activity, 'from' | 'to'>[], part: Part): Dept[] {
  const out: Dept[] = []
  for (const a of items) {
    for (const [x, other] of [[a.from, a.to], [a.to, a.from]] as const) {
      if (!isSelf(x, other, part) && !out.includes(x)) out.push(x)
    }
  }
  return out
}

// ---------------------------------------------------------------- 작업 현황

/** 멈춘 작업: 코딩 중단·기획에 돌려보냄·폐기, 그리고 코딩 기록 없이 사람이 맡았다 PR이 닫혀 '중단'이 된 작업 */
export const isStoppedTask = (t: Pick<Task, 'state' | 'discarded' | 'coding'>): boolean =>
  t.coding?.status === 'canceled' || t.coding?.status === 'returned' || !!t.discarded || t.state === '중단'
