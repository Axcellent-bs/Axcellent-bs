// 기획개발(planning) 화면의 목 데이터와 상태 변화.
// 실제로는 기획개발 에이전트의 /api/planning/... 응답이 들어올 자리다 (PLANNING_MOCK=1 과 같은 역할).
// 부서 간 연결 지점 이름은 공통 구성안(agent-common)과 10/22 부서별 수정 요청을 따른다.
//
// 승인 방식 (10/23 결정): "사람의 사전 승인" 대신 "AI 교차검증 + 사람 사후 감사".
//   - 기획서: 질문 없음 + 인사 기각 경고 없음 + 근거 있음 → 자동 확정. 아니면 AI 교차검증 대기열.
//   - PR: 검수 통과 + 학습된 기준(경로·줄·파일·경고 없음) 안 → 자동 머지. 아니면 AI 교차검증 대기열.
//   - 교차검증은 기획개발이 아닌 쪽(안전 레이어가 다른 부서로 보냄)이 판단한다. 기획개발은 자기 건을 승인하지 않는다.
//   - 교차검증 뒤 머지 → 관찰 기간 장애·revert 없음 → 같은 경로 N건이면 기준 제안·백테스트 → 안전 장치 안 재무 AI, 밖 사람(태윤) 승인 → 정식.
//   - 되돌릴 수 없는 일(IRREVERSIBLE)은 교차검증 대신 항상 사람이 확인한다.
//   - 사람은 사후 감사 화면에서 AI가 스스로 승인한 건을 보고, 기준을 바로 끌 수 있다.

export type Dept =
  | 'planning' | 'hr' | 'support' | 'marketing' | 'data' | 'finance'
  | 'infra' | 'orchestrator' | 'safety' | 'shop' | 'memory' | 'human'

export const DEPT_NAME: Record<Dept, string> = {
  planning: '기획개발', hr: '인사', support: '고객지원', marketing: '마케팅', data: '데이터분석',
  finance: '재무/정산', infra: '인프라(DejaFix)', orchestrator: '통합 에이전트', safety: '안전 레이어',
  shop: '판매 사이트', memory: '회사 기억', human: '사람',
}

export type ReqState = '접수' | '기획 중' | '작업 중' | '완료' | '중복 연결'
export interface Request {
  id: string
  ctx: string
  from: Dept
  via: string // 들어온 연결 지점
  title: string
  evidence: string
  state: ReqState
  spec?: string
  duplicateOf?: string
  auto?: AutoCheck // 간단 요청 판정 결과 (create_ticket으로 들어온 것만)
  flow?: boolean // 들어온 뒤 사람 클릭 없이 흐름을 이어 간다 (기획서 질문은 요청 부서 에이전트가 답함)
}

export interface Para {
  id: string // R1, C1 …
  kind: 'req' | 'done'
  text: string
  warn?: { card: string; text: string } // 인사운영 ② check_plan 결과
  incident?: string // 장애 표시 (link_incident)
}
// 누가 확정·머지했나: auto = 조건·기준으로 자동, review = AI 교차검증 승인 뒤 자동, human = 되돌릴 수 없는 일이라 사람
export type Approver = 'auto' | 'review' | 'human'
export interface Spec {
  id: string
  title: string
  request: string
  status: '초안' | '확정'
  problem: string
  goal: string
  paras: Para[]
  question?: string
  basis: string[]
  humanSentences: number
  confirmedBy?: Approver
}

export type TaskState = '진행 중' | 'PR 검수' | '머지 대기' | '완료' | 'C 보류'
export interface Task {
  id: string
  spec: string
  para: string
  title: string
  owner: string
  state: TaskState
}

export interface PR {
  id: string
  task: string
  title: string
  branch: string
  reviews: ('통과' | '불일치')[]
  gate: { result: '통과' | 'C 보류'; lines: number; files: number; paths: string }
  merged: boolean
  auto?: boolean // 사람 클릭 없이 머지됨 (기준 또는 교차검증)
  mergedBy?: 'rule' | 'review' | 'human'
  rule?: string // 자동 머지에 쓴 기준 (AR-n)
}

export interface Message { who: string; dept: Dept; text: string; decision?: string; t?: string }
// 소통 채널. spec = 문서에 붙는 스레드, team = 기획개발팀 채널, dept = 다른 부서와 주고받는 채널
export interface Thread { id: string; on: string; title: string; messages: Message[]; kind?: 'spec' | 'team' | 'dept'; peer?: Dept }

// 정리 문서: 요청·기획서·작업·PR·대화·결정을 한 장으로 모으고 사람이 고친다
export interface DocBlock { id: string; kind: 'h2' | 'p' | 'todo' | 'ref'; text: string; done?: boolean }
export interface Doc { id: string; title: string; about?: string; updated: string; blocks: DocBlock[]; by?: 'ai' | 'human' }

export interface Decision { id: string; spec: string; text: string; reason: string; alternatives: string; from: string; sentToHr: boolean }

export interface Incident {
  ctx: string
  title: string
  at: string
  candidates: { pr: string; spec: string; para: string; mergedAt: string }[]
  rollbackCheck?: { target: string; result: '승인' | '거절'; why: string }
  linked: boolean
  followUp?: string
}

export interface Activity {
  seq?: number // 시뮬레이터로 들어온 건의 순번 (새로 받은 것 표시용)
  t: string // 시각 (KST 표시용)
  ctx: string
  from: Dept
  to: Dept
  point: string // 연결 지점
  kind: 'A2A' | '이벤트' | 'execute' | 'API' | '사람'
  grade?: 'A' | 'B' | 'C' | 'read'
  text: string
}

// 기획개발 → 다른 부서로 나간 메시지·데이터 (다른 부서 페이지의 "기획개발에서 받은 것")
export interface OutMsg {
  id: number
  t: string
  ctx: string
  to: Dept
  point: string
  text: string
  read: boolean
}

// ---- AI 교차검증 대기열. lane ai = 다른 부서 검증자가 판단, lane human = 되돌릴 수 없는 일이라 사람이 판단
export type ReviewTarget = 'confirm_spec' | 'merge_pr'
export const TARGET_LABEL: Record<ReviewTarget, string> = { confirm_spec: '기획서 확정', merge_pr: 'PR 머지' }
// 통과 = 문제없음, 확인 필요 = 여기로 온 이유(막지는 않음), 걸림 = 이것이 있으면 검증자가 거절
export interface RuleCheck { label: string; result: '통과' | '확인 필요' | '걸림'; detail: string }
export interface Backtest { checked: number; covered: number; conflicts: string[] }
export interface CrossReview {
  id: string // XR-n
  t: string
  ctx: string
  target: ReviewTarget
  ref: string // SPEC-n 또는 PR #n
  title: string
  lane: 'ai' | 'human'
  irreversible?: string // 되돌릴 수 없는 일 항목 이름 (lane human)
  routeWhy: string[] // 자동으로 처리하지 않고 여기로 온 이유
  summary: string // 기획개발 AI가 정리한 근거
  recommend?: '승인' | '거절' // 기획개발 AI의 추천 (결정 아님)
  recommendWhy?: string
  checks: RuleCheck[] // 코드 규칙 검사 결과
  verifier: string // 판단하는 쪽 (기획개발이 아님)
  result?: '승인' | '거절'
  resultWhy?: string
  decidedAt?: string
}

// ---- 학습된 자동 처리 기준 (#191, 재현·태윤 조건). 학습 근거는 검토자 승인이 아니라 "머지 뒤 장애·revert 없음 + 관찰 기간".
// 같은 범위에서 관찰을 통과한 머지가 OBSERVE_N건 모이면 기준을 제안하고, 넓히는 쪽은 safety 안전 장치 안이면 재무 AI
// (review_threshold_change, #124), 밖이면 사람(태윤)이 승인한다. 좁히는 쪽(해제)은 장애·revert 연결 즉시 자동.
export interface AutoRule {
  id: string // AR-n
  action: ReviewTarget
  path: string // 경로 접두사
  lines: number // 변경 줄 상한
  files: number // 변경 파일 상한
  noWarn: boolean // 인사 기각 경고가 없는 문단만
  status: '승인 대기' | '정식' | '거절' | '해제됨'
  approver: '재무 AI' | '사람 (태윤)' // 넓히기 승인 주체
  basis: string[] // 관찰을 통과한 머지 PR
  backtest: Backtest
  runs: { pr: string; result: '정상' | '장애 연결' }[] // 이 기준으로 자동 머지한 건
  t: string // 제안한 시각
  decidedAt?: string
  decideWhy?: string
  offAt?: string
  offWhy?: string
}
// 시연값. 실제 N건·관찰 기간은 safety 기준표(사람 전용 파일, #202)에 둔다
export const OBSERVE_MIN = 2 // 머지 뒤 관찰 기간 (분)
export const OBSERVE_N = 2 // 관찰을 통과한 머지 몇 건이면 기준 제안
export const GUARD = { lines: 100, files: 5 } // safety 안전 장치: 이 안이면 재무 AI가, 밖이면 사람이 승인
// safety 보호 경로 (#203 merge_protected_paths, 시연 경로 포함). 닿으면 항상 사람 확인이고 학습으로도 풀 수 없다
export const PROTECTED_PATHS = ['shop-web/checkout/', 'shop-web/auth/', 'shop-web/api/', 'shop-api/db/', 'shop-api/service/', 'shop-api/promotions/', 'shop-api/payment/', '.github/', 'safety/', 'contracts/']
export const protectedOf = (path: string) => PROTECTED_PATHS.find((x) => path.startsWith(x))
export interface Observation { pr: string; req: string; path: string; lines: number; files: number; mergedAt: string; untilMs: number; result: '관찰 중' | '정상' | '장애 연결' }
export interface AutoRun { pr: string; req: string; t: string; lines: number; files: number; result: '정상' | '장애 연결'; rule?: string }
export interface AutoPolicy {
  rules: AutoRule[]
  observing: Observation[] // 교차검증 뒤 머지된 PR의 관찰 (학습 근거 후보)
  history: AutoRun[]
  log: { t: string; text: string; by: '사람' | '자동' | '재무 AI' }[]
}

// ---- 사후 감사: AI가 스스로 승인·자동 처리한 건 (사람이 나중에 본다)
export type AuditKind = '자동 확정' | '자동 머지' | '교차검증 승인' | '교차검증 거절' | '관찰 통과' | '기준 제안' | '학습 거부' | '기준 정식' | '기준 해제'
export interface AuditItem { t: string; ctx: string; kind: AuditKind; ref: string; what: string; rule?: string; basis: string; verifier: string; result: string }

export interface State {
  seq: number // 들어오고 나간 연동 건마다 1씩 (새로 받은 것 표시용)
  outbox: OutMsg[]
  requests: Request[]
  specs: Spec[]
  tasks: Task[]
  prs: PR[]
  threads: Thread[]
  docs: Doc[]
  decisions: Decision[]
  incidents: Incident[]
  activity: Activity[]
  reviews: CrossReview[]
  audit: AuditItem[]
  // 지표 계산용 원자료 (코드로 계산)
  draftsIn: number
  draftsAccepted: number
  warnings: number
  firstPrMinutes: number[]
  autoPolicy: AutoPolicy
}

// ---- 되돌릴 수 없는 일: 교차검증 대신 항상 사람이 확인한다 (기획서 확정·PR 머지 둘 다)
export const IRREVERSIBLE: { id: string; label: string; words: string[] }[] = [
  { id: 'payment_data_delete', label: '결제 데이터 삭제', words: ['결제 데이터 삭제', '결제 내역 삭제', '결제 기록 삭제', '주문 기록 삭제'] },
  { id: 'pii_exposure', label: '개인정보가 노출될 수 있는 변경', words: ['개인정보', '전화번호 전체', '주소 전체', '회원 정보 내보내기'] },
  { id: 'real_transfer', label: '실제 송금 연동', words: ['송금', '실계좌', '지급 연동'] },
]
export const irreversibleOf = (text: string) => IRREVERSIBLE.find((x) => x.words.some((w) => text.includes(w)))

// ---- 백테스트에 쓰는 과거 기록 (목). 실제로는 GitHub PR 이력 + 인프라 사건 연결 기록
export interface PastRecord { pr: string; t: string; path: string; lines: number; files: number; result: '정상' | '거절' | '장애 연결'; note: string }
export const PAST_RECORDS: PastRecord[] = [
  { pr: 'PR #19', t: '10-14', path: 'shop-web/checkout/', lines: 52, files: 3, result: '거절', note: '결제 화면 문구가 약관 표기와 충돌해 리뷰에서 거절' },
  { pr: 'PR #21', t: '10-16', path: 'shop-api/order/', lines: 38, files: 2, result: '장애 연결', note: '주문 API 응답 지연 사건에 연결' },
  { pr: 'PR #24', t: '10-20', path: 'shop-web/product/', lines: 12, files: 1, result: '정상', note: '교차검증 뒤 머지, 관찰 기간 장애·revert 없음 (AR-1 근거)' },
  { pr: 'PR #25', t: '10-20', path: 'shop-web/event/', lines: 8, files: 1, result: '정상', note: '교차검증 뒤 머지, 관찰 기간 장애·revert 없음 (AR-1 근거)' },
  { pr: 'PR #26', t: '10-21', path: 'shop-web/notice/', lines: 20, files: 2, result: '정상', note: 'AR-1로 자동 머지' },
  { pr: 'PR #28', t: '10-21', path: 'shop-api/payment/', lines: 14, files: 1, result: '장애 연결', note: 'ctx_inc_0001 결제 API 응답 지연' },
]
const ALLOWED_PATHS = ['shop-web/', 'shop-api/', 'docs/'] // 안전 레이어 allowed_paths (목)

const num = (id: string) => Number(id.replace(/\D/g, '')) || 0
const nextNum = (ids: string[]) => Math.max(0, ...ids.map(num)) + 1
export const activeRules = (p: AutoPolicy) => p.rules.filter((r) => r.status === '정식')
export const ruleText = (r: { path: string; lines: number; files: number; noWarn?: boolean }) => `${r.path} ${r.lines}줄·${r.files}파일 이하${r.noWarn === false ? '' : ' · 경고 없음'}`
type Size = { path: string; lines: number; files: number }
const covers = (r: Size, x: Size) => x.path.startsWith(r.path) && x.lines <= r.lines && x.files <= r.files
export function matchRule(p: AutoPolicy, x: Size, warned: boolean): AutoRule | undefined {
  const ok = activeRules(p).filter((r) => r.action === 'merge_pr' && covers(r, x) && !(r.noWarn && warned))
  return ok.find((r) => r.status === '정식') ?? ok[0]
}

export const initialState: State = {
  seq: 0,
  outbox: [],
  requests: [
    { id: 'REQ-8', ctx: 'ctx_req_0008', from: 'support', via: 'create_ticket', title: '도서 상세의 출간일 표기 오타 수정', evidence: '같은 문의 3건 (고객지원 문의 분류)', state: '완료', spec: 'SPEC-4',
      auto: { ok: true, reasons: ['결제·주문·개인정보 경로 아님', '화면 표시만 바꾸는 요청', '인사 ② 기각 이력 없음', '예상 변경 4줄·1파일 · 기준 AR-1(shop-web/ 30줄·2파일 이하 · 경고 없음) 안 → 자동 머지'] } },
    { id: 'REQ-9', ctx: 'ctx_req_0009', from: 'data', via: 'create_ticket', title: '느린 카드사에서 결제가 실패한다', evidence: '결제 실패 코드 집계 (데이터분석)', state: '완료', spec: 'SPEC-5' },
    { id: 'REQ-12', ctx: 'ctx_req_0012', from: 'support', via: 'create_ticket', title: '결제 실패 뒤 장바구니가 비워진다', evidence: '반복 문의 42건 (고객지원 문의 분류)', state: '기획 중', spec: 'SPEC-7' },
    { id: 'REQ-13', ctx: 'ctx_req_0013', from: 'hr', via: 'ticket_draft_propose (③)', title: '결제 실패 안내 문구 전체 정비', evidence: '같은 인수인계 질문 3주 연속 반복 (인사 결정 카드 card-0142)', state: '접수' },
    { id: 'REQ-14', ctx: 'ctx_req_0014', from: 'data', via: '개선 초안 (원인 추적 루프 ③)', title: '결제 실패율이 같은 구간에서 반복 상승', evidence: 'metric.anomaly 3회, 14:00~14:10 구간 (데이터분석)', state: '접수' },
    { id: 'REQ-101', ctx: 'ctx_inc_0001', from: 'infra', via: 'link_incident (재발 방지)', title: '결제 경로 설정 변경은 카나리 배포 필수', evidence: '사건 ctx_inc_0001, 롤백으로 복구 (DejaFix)', state: '작업 중', spec: 'SPEC-6' },
    { id: 'REQ-15', ctx: 'ctx_req_0015', from: 'marketing', via: 'create_ticket', title: '장바구니 보존 문구를 공지에도 반영', evidence: 'REQ-12와 같은 대상', state: '중복 연결', duplicateOf: 'REQ-12' },
  ],
  specs: [
    {
      id: 'SPEC-7', title: '결제 실패 후 장바구니 유지', request: 'REQ-12', status: '초안',
      problem: '결제가 실패하면 장바구니가 비워져 고객이 다시 담아야 한다 (관련 문의 42건)',
      goal: '결제 실패 뒤에도 장바구니를 유지해 관련 문의를 줄인다',
      paras: [
        { id: 'R1', kind: 'req', text: '결제 실패 시 장바구니 상태 보존 (품절 상품은 품절 표시)' },
        { id: 'R2', kind: 'req', text: '실패 안내 문구에 재시도 버튼', warn: { card: 'DC-31', text: '"재시도 버튼만 추가"는 9/30 기각된 안. R1과 함께일 때만 진행' } },
        { id: 'C1', kind: 'done', text: '결제 실패를 일으킨 뒤 장바구니가 그대로 남는다' },
        { id: 'C2', kind: 'done', text: '실패 안내의 재시도 버튼을 누르면 결제 화면으로 간다' },
      ],
      question: '재고가 없어진 상품도 장바구니에 남겨 둘까요?',
      basis: ['SPEC-3 장바구니 기획서', '결제 실패 문의 통계 (데이터분석)', 'DC-31 기각 결정 카드 (인사)'],
      humanSentences: 0,
    },
    {
      id: 'SPEC-6', title: '결제 경로 설정 변경 카나리 배포', request: 'REQ-101', status: '확정', confirmedBy: 'auto',
      problem: '결제 타임아웃 설정을 한 번에 전체 적용해 장애(ctx_inc_0001)가 났다', goal: '결제 경로 설정 변경은 일부에 먼저 배포해 이상을 일찍 잡는다',
      paras: [
        { id: 'R1', kind: 'req', text: '결제 경로 설정 변경은 5% 카나리 배포 뒤 전체 적용' },
        { id: 'C1', kind: 'done', text: '카나리 단계에서 실패율이 기준을 넘으면 자동으로 되돌린다' },
      ],
      basis: ['사건 ctx_inc_0001 (DejaFix)', 'D-0 카나리 배포 결정 (사후관리)'], humanSentences: 0,
    },
    {
      id: 'SPEC-5', title: '결제 타임아웃 설정 조정', request: 'REQ-9', status: '확정', confirmedBy: 'human',
      problem: '결제 대기 시간이 짧아 느린 카드사에서 실패가 난다', goal: '결제 성공률 유지',
      paras: [
        { id: 'R3', kind: 'req', text: '결제 타임아웃 설정값 변경', incident: 'ctx_inc_0001' },
        { id: 'C5', kind: 'done', text: '느린 카드사 모의 응답에서도 결제가 완료된다' },
      ],
      basis: ['결제 실패 코드 집계 (데이터분석)'], humanSentences: 2,
    },
    {
      id: 'SPEC-4', title: '도서 상세의 출간일 표기 오타 수정', request: 'REQ-8', status: '확정', confirmedBy: 'auto',
      problem: '고객지원에서 받은 요청: 도서 상세의 출간일 표기 오타 수정', goal: '화면 표시를 요청대로 고친다 (동작 변경 없음)',
      paras: [
        { id: 'R1', kind: 'req', text: '도서 상세의 출간일 표기 오타 수정' },
        { id: 'C1', kind: 'done', text: '바뀐 화면에서 요청한 표시가 그대로 보인다' },
      ],
      basis: ['REQ-8 (고객지원)', '간단 요청 규칙 통과'], humanSentences: 0,
    },
  ],
  tasks: [
    { id: 'T-8', spec: 'SPEC-4', para: 'R1', title: '도서 상세의 출간일 표기 오타 수정', owner: '에이전트', state: '완료' },
    { id: 'T-9', spec: 'SPEC-5', para: 'R3', title: '결제 타임아웃 설정 변경', owner: '백엔드', state: '완료' },
    { id: 'T-10', spec: 'SPEC-6', para: 'R1', title: '결제 경로 설정 변경은 5% 카나리 배포 뒤 전체 적용', owner: '에이전트', state: '머지 대기' },
  ],
  prs: [
    { id: 'PR #27', task: 'T-8', title: 'R1: 도서 상세의 출간일 표기 오타 수정', branch: 'gen/t-8', reviews: ['통과'], gate: { result: '통과', lines: 4, files: 1, paths: 'shop-web/product/' }, merged: true, auto: true, mergedBy: 'rule', rule: 'AR-1' },
    { id: 'PR #28', task: 'T-9', title: 'R3: 결제 타임아웃 설정 변경', branch: 'gen/t-9-payment-timeout', reviews: ['통과'], gate: { result: '통과', lines: 14, files: 1, paths: 'shop-api/payment/' }, merged: true, mergedBy: 'human' },
    { id: 'PR #29', task: 'T-10', title: 'R1: 결제 설정 변경 카나리 배포', branch: 'gen/t-10-payment-canary', reviews: ['통과'], gate: { result: '통과', lines: 24, files: 2, paths: 'shop-api/payment/' }, merged: false },
  ],
  threads: [
    { id: 'TH-7', on: 'SPEC-7', title: 'SPEC-7 · 결제 실패 후 장바구니 유지', messages: [
      { who: '기획개발 에이전트', dept: 'planning', text: '빈 항목 질문: 재고가 없어진 상품도 장바구니에 남겨 둘까요?' },
    ] },
    { id: 'TH-5', on: 'SPEC-5', title: 'SPEC-5 · 결제 타임아웃 설정 조정', messages: [
      { who: '장재현 (데이터분석)', dept: 'data', text: '설정 변경 뒤 14:04부터 결제 실패율이 4배로 올랐습니다. 원인 판정은 인프라 쪽에서 해 주세요.' },
    ] },
    { id: 'CH-team', on: '', kind: 'team', title: '기획개발팀', messages: [
      { who: '기획개발 에이전트', dept: 'planning', t: '10-22 09:15', text: 'REQ-12 기획서 SPEC-7 초안을 올렸습니다. R2에 인사 기각 경고(DC-31)가 있어 질문 답을 받은 뒤 AI 교차검증을 거쳐 확정합니다.' },
      { who: '노예진', dept: 'planning', t: '10-22 09:18', text: '확인했어요. 확정은 교차검증에 맡기고, 저녁에 AI 승인 감사에서 한 번에 볼게요.' },
    ] },
    { id: 'CH-support', on: '', kind: 'dept', peer: 'support', title: '고객지원', messages: [
      { who: '고객지원 에이전트', dept: 'support', t: '10-22 09:40', text: '"결제 실패 뒤 장바구니 비워짐" 문의가 오늘 6건 더 들어왔습니다 (누적 48건).' },
    ] },
    { id: 'CH-marketing', on: '', kind: 'dept', peer: 'marketing', title: '마케팅', messages: [] },
    { id: 'CH-hr', on: '', kind: 'dept', peer: 'hr', title: '인사', messages: [
      { who: '인사 에이전트', dept: 'hr', t: '10-22 09:13', text: '"재시도 버튼만 추가"는 9/30 기각된 안입니다 (DC-31). 장바구니 보존과 함께일 때만 진행을 권합니다.' },
    ] },
    { id: 'CH-data', on: '', kind: 'dept', peer: 'data', title: '데이터분석', messages: [] },
    { id: 'CH-infra', on: '', kind: 'dept', peer: 'infra', title: '인프라(DejaFix)', messages: [] },
  ],
  docs: [
    { id: 'DOC-1', title: '10/22 기획개발 정리', updated: '10-22 09:30', blocks: [
      { id: 'b1', kind: 'h2', text: '오늘 할 일' },
      { id: 'b2', kind: 'todo', text: 'SPEC-7 품절 상품 처리 질문 답 받기 (고객지원)', done: false },
      { id: 'b3', kind: 'todo', text: 'SPEC-7 교차검증 결과 보고 PR 생성', done: false },
      { id: 'b4', kind: 'todo', text: 'REQ-101 카나리 배포 PR #29 교차검증 결과 확인', done: false },
      { id: 'b5', kind: 'h2', text: '메모' },
      { id: 'b6', kind: 'p', text: 'REQ-15(마케팅)는 REQ-12와 같은 요청이라 연결만 해 둠. 공지 문구는 REQ-12 완료 뒤 마케팅에 회신.' },
    ] },
  ],
  decisions: [
    { id: 'D-0', spec: 'SPEC-5', text: '결제 타임아웃은 카나리 배포 후 전체 적용', reason: '장애 재발 방지', alternatives: '바로 전체 적용', from: '사후관리', sentToHr: true },
  ],
  incidents: [
    {
      ctx: 'ctx_inc_0001', title: '결제 API 응답 지연 (p95 2,400ms)', at: '10-21 14:04',
      candidates: [{ pr: 'PR #28', spec: 'SPEC-5', para: 'R3', mergedAt: '10-21 14:02' }],
      rollbackCheck: { target: 'shop-api v2.4.1', result: '승인', why: '장애 2분 전 배포, 그 사이 PR #28 머지' },
      linked: true, followUp: 'REQ-101',
    },
  ],
  activity: [
    { t: '10-21 14:04', ctx: 'ctx_inc_0001', from: 'orchestrator', to: 'planning', point: 'incident.opened', kind: '이벤트', text: '결제 API 장애 사건 개시' },
    { t: '10-21 14:05', ctx: 'ctx_inc_0001', from: 'infra', to: 'planning', point: 'find_related_prs', kind: 'A2A', grade: 'read', text: '장애 직전 머지 PR 후보 1건 회신 (PR #28, SPEC-5 R3)' },
    { t: '10-21 14:06', ctx: 'ctx_inc_0001', from: 'data', to: 'planning', point: 'find_related_prs', kind: 'A2A', grade: 'read', text: '이상 구간 14:00~14:10 직전 배포 질의 → PR #28' },
    { t: '10-21 14:07', ctx: 'ctx_inc_0001', from: 'safety', to: 'planning', point: 'verify_rollback_target', kind: 'A2A', grade: 'read', text: 'rollback_deployment(B) 교차검증 → 승인' },
    { t: '10-21 14:09', ctx: 'ctx_inc_0001', from: 'planning', to: 'planning', point: 'link_incident', kind: 'A2A', grade: 'A', text: 'SPEC-5 R3에 장애 표시, 재발 방지 요청 REQ-101 등록' },
    { t: '10-21 14:10', ctx: 'ctx_inc_0001', from: 'infra', to: 'hr', point: 'record_case', kind: 'A2A', grade: 'A', text: 'kind rollback_executed (인프라 → 인사)' },
    { t: '10-22 09:04', ctx: 'ctx_req_0008', from: 'support', to: 'planning', point: 'create_ticket', kind: 'A2A', grade: 'A', text: '변경 요청 REQ-8 등록: 도서 상세의 출간일 표기 오타 수정' },
    { t: '10-22 09:05', ctx: 'ctx_req_0008', from: 'planning', to: 'planning', point: 'confirm_spec', kind: 'A2A', grade: 'A', text: 'SPEC-4 자동 확정 · 질문 없음·경고 없음·근거 2개 → 작업 T-8' },
    { t: '10-22 09:06', ctx: 'ctx_req_0008', from: 'planning', to: 'safety', point: 'execute(merge_pr)', kind: 'execute', grade: 'B', text: 'PR #27 기준 AR-1(shop-web/ 30줄·2파일 이하 · 경고 없음) 안 · 검수·CI 통과 → 자동 머지' },
    { t: '10-22 09:12', ctx: 'ctx_req_0012', from: 'support', to: 'planning', point: 'create_ticket', kind: 'A2A', grade: 'A', text: '반복 문의 42건으로 변경 요청 REQ-12 등록' },
    { t: '10-22 09:13', ctx: 'ctx_req_0012', from: 'planning', to: 'hr', point: 'check_plan', kind: 'A2A', grade: 'read', text: '② "재시도 버튼만 추가" 기각 이력 질의 → warn (DC-31)' },
    { t: '10-22 09:14', ctx: 'ctx_req_0012', from: 'planning', to: 'planning', point: 'write_spec', kind: 'A2A', grade: 'A', text: 'SPEC-7 초안 작성, 경고를 R2에 첨부, 질문 1개 → 답을 받은 뒤 확정 조건 확인' },
    { t: '10-22 09:20', ctx: 'ctx_req_0013', from: 'hr', to: 'planning', point: 'ticket_draft_propose', kind: 'A2A', grade: 'A', text: '③ 개선 초안 수신 → 요청함 REQ-13' },
    { t: '10-22 09:31', ctx: 'ctx_req_0014', from: 'data', to: 'planning', point: '개선 초안 (루프 ③)', kind: 'A2A', grade: 'A', text: '반복 지표 이상 초안 수신 → 요청함 REQ-14' },
    { t: '10-22 09:40', ctx: 'ctx_req_0015', from: 'marketing', to: 'planning', point: 'create_ticket', kind: 'A2A', grade: 'A', text: 'REQ-12와 같은 요청 → 새로 만들지 않고 연결' },
    { t: '10-22 10:20', ctx: 'ctx_inc_0001', from: 'planning', to: 'planning', point: 'confirm_spec', kind: 'A2A', grade: 'A', text: 'SPEC-6 자동 확정 · 질문 없음·경고 없음·근거 2개 → 작업 T-10' },
    { t: '10-22 10:24', ctx: 'ctx_inc_0001', from: 'planning', to: 'safety', point: 'execute(generate_pr)', kind: 'execute', grade: 'B', text: 'PR #29 변경 24줄·2파일·shop-api/payment/ → 코드 규칙 통과, 검수 통과' },
    { t: '10-22 10:25', ctx: 'ctx_inc_0001', from: 'planning', to: 'safety', point: 'cross_review.request', kind: 'execute', grade: 'B', text: 'PR #29 PR 머지 → AI 교차검증 요청 (자동 처리 기준 밖, 장애가 났던 경로) · 검증자 안전 레이어 → 재무/정산 review_hold' },
  ],
  reviews: [], // 맨 아래에서 PR #29 교차검증 1건을 코드 규칙으로 만들어 넣는다
  audit: [
    { t: '10-22 09:05', ctx: 'ctx_req_0008', kind: '자동 확정', ref: 'SPEC-4', what: '도서 상세의 출간일 표기 오타 수정', basis: '질문 없음·경고 없음·근거 2개', verifier: '조건 검사 (안전 레이어 execute() 기록)', result: '확정 → 작업 T-8' },
    { t: '10-22 09:06', ctx: 'ctx_req_0008', kind: '자동 머지', ref: 'PR #27', what: 'R1: 도서 상세의 출간일 표기 오타 수정', rule: 'AR-1', basis: 'shop-web/ 30줄·2파일 이하 · 경고 없음', verifier: '안전 레이어 코드 규칙 (변경 줄·파일·허용 경로)', result: '머지 · dev 배포' },
    { t: '10-22 10:20', ctx: 'ctx_inc_0001', kind: '자동 확정', ref: 'SPEC-6', what: '결제 경로 설정 변경 카나리 배포', basis: '질문 없음·경고 없음·근거 2개', verifier: '조건 검사 (안전 레이어 execute() 기록)', result: '확정 → 작업 T-10' },
  ],
  draftsIn: 2,
  draftsAccepted: 0,
  warnings: 1,
  firstPrMinutes: [38, 3],
  autoPolicy: {
    rules: [
      {
        id: 'AR-1', action: 'merge_pr', path: 'shop-web/', lines: 30, files: 2, noWarn: true, status: '정식', approver: '재무 AI',
        basis: ['PR #24', 'PR #25'], backtest: { checked: 5, covered: 3, conflicts: [] },
        runs: [{ pr: 'PR #26', result: '정상' }, { pr: 'PR #27', result: '정상' }],
        t: '10-20 16:10', decidedAt: '10-20 16:11', decideWhy: '안전 장치(100줄·5파일) 안 · 백테스트 충돌 0건',
      },
    ],
    observing: [],
    history: [
      { pr: 'PR #26', req: 'REQ-6', t: '10-21 11:20', lines: 20, files: 2, result: '정상', rule: 'AR-1' },
      { pr: 'PR #27', req: 'REQ-8', t: '10-22 09:06', lines: 4, files: 1, result: '정상', rule: 'AR-1' },
    ],
    log: [
      { t: '10-20 16:10', text: 'PR #24·PR #25 관찰 기간 장애·revert 없음 → 기준 AR-1(shop-web/ 30줄·2파일 이하 · 경고 없음) 제안, 백테스트 충돌 0건', by: '자동' },
      { t: '10-20 16:11', text: 'AR-1 안전 장치 안 → 재무 AI review_threshold_change 승인 → 정식', by: '재무 AI' },
    ],
  },
}

// ---- 상태 변화 (버튼 → 에이전트 skill → 다른 부서로 나가는 연결 지점)

// 실제 시각, KST 고정 (MM-DD HH:MM). 브라우저·버스가 어느 시간대에서 돌아도 같다.
// 시드 기록은 10-22 과거 기록이고, 새로 들어온 요청부터 지금 시각이 찍힌다
const pad = (n: number) => String(n).padStart(2, '0')
const KST_MS = 9 * 60 * 60 * 1000
function stamp(d: Date): string {
  const k = new Date(d.getTime() + KST_MS)
  return `${pad(k.getUTCMonth() + 1)}-${pad(k.getUTCDate())} ${pad(k.getUTCHours())}:${pad(k.getUTCMinutes())}`
}
function now(): string {
  return stamp(new Date())
}
export const TODAY = stamp(new Date()).slice(0, 5)

export type InboundKind =
  | 'support_ticket' | 'marketing_ticket' | 'hr_draft' | 'data_draft'
  | 'data_query' | 'infra_query' | 'orch_incident' | 'safety_verify'
  | 'support_simple' | 'marketing_simple' | 'finance_irreversible'

export type Action =
  | { type: 'inbound'; kind: InboundKind }
  | { type: 'send'; to: Dept; point: string; text: string }
  | { type: 'markRead'; to: Dept }
  | { type: 'answerQuestion'; spec: string; text: string; agent?: boolean }
  | { type: 'advance' } // 다음 단계 한 칸 (버스·화면이 일정 간격으로 보낸다)
  | { type: 'generatePrs'; spec: string }
  | { type: 'reviewPr'; pr: string }
  | { type: 'acceptDraft'; request: string }
  | { type: 'writeSpec'; request: string }
  // 교차검증·학습 기준·사람 확인
  | { type: 'decideReview'; review: string } // 시연: 검증자(다른 부서)가 판단
  | { type: 'humanDecide'; review: string; ok: boolean } // 되돌릴 수 없는 일만
  | { type: 'disableRule'; rule: string } // 기준 끄기 (승인 없이 바로)
  | { type: 'decideRule'; rule: string; ok: boolean } // 기준 넓히기 판단 (재무 AI 또는 사람, 시뮬레이터가 보낸다)
  // 소통
  | { type: 'postMessage'; thread: string; text: string }
  | { type: 'deptChat'; from: Dept }
  | { type: 'extractDecision'; thread: string; index: number }
  // 문서
  | { type: 'newDoc'; title: string }
  | { type: 'gatherDoc'; about: string; by?: 'ai' | 'human' }
  | { type: 'renameDoc'; doc: string; title: string }
  | { type: 'addBlock'; doc: string; kind: DocBlock['kind']; text?: string }
  | { type: 'editBlock'; doc: string; block: string; text: string }
  | { type: 'toggleTodo'; doc: string; block: string }
  | { type: 'removeBlock'; doc: string; block: string }

// 기획개발이 다른 부서로 보낸 활동은 모두 그 부서 창구(outbox)에도 쌓는다
export function reducer(s: State, a: Action): State {
  const next = inner(s, a)
  const added = next.activity.slice(s.activity.length).filter((x) => x.from === 'planning' && x.to !== 'planning')
  if (!added.length) return next
  let id = next.outbox.length
  return { ...next, outbox: [...next.outbox, ...added.map((x) => ({ id: ++id, t: x.t, ctx: x.ctx, to: x.to, point: x.point, text: x.text, read: false }))] }
}

const SAMPLE_TICKETS: Record<'support' | 'marketing', string[]> = {
  support: ['배송 조회 화면에서 송장 번호가 안 보인다', '쿠폰 적용 후 결제 금액이 두 번 계산된다', '결제 실패 뒤 장바구니가 비워진다'],
  marketing: ['기획전 배너가 모바일에서 잘린다', '신간 알림 신청 버튼 추가', '장바구니 보존 문구를 공지에도 반영'],
}
const SIMPLE_TICKETS: Record<'support' | 'marketing', string[]> = {
  support: ['상품 상세의 배송 안내 문구 오타 수정', '고객센터 운영 시간 안내 문구 변경', '마이페이지 메뉴 라벨 오타 수정'],
  marketing: ['신간 목록 정렬 라벨을 "최신순"으로 변경', '이벤트 페이지 링크가 지난 기획전으로 연결된다', '기획전 페이지 안내 문구 전체 교체'],
}

// ---- 간단 요청 판정: 통과하면 기획서 자동 확정 → 개발 → 머지까지 한 번에 간다 (기준 밖이면 머지 전에 AI 교차검증).
// 하나라도 걸리면 평소 흐름(기획서부터). 실제로는 안전 레이어 코드 규칙(pr_changed_lines·files·allowed_paths)이 마지막에 다시 막는다.
export interface AutoCheck { ok: boolean; reasons: string[] }
const SENSITIVE = ['결제', '주문', '쿠폰', '환불', '정산', '장바구니', '재고', '개인정보', '로그인', '권한']
const DISPLAY_ONLY = ['문구', '오타', '라벨', '링크', '배너', '안 보인다', '잘린다', '정렬']

function estimate(title: string): { lines: number; files: number; paths: string } {
  const size = title.includes('전체') ? { lines: 45, files: 3 } : /문구|오타|라벨/.test(title) ? { lines: 4, files: 1 } : { lines: 18, files: 2 }
  const paths = /결제/.test(title) ? 'shop-api/payment/' : /매뉴얼/.test(title) ? 'docs/manual/' : 'shop-web/'
  return { ...size, paths }
}

export function checkSimple(title: string, policy: AutoPolicy = initialState.autoPolicy): AutoCheck {
  const hit = SENSITIVE.find((w) => title.includes(w))
  const display = DISPLAY_ONLY.some((w) => title.includes(w))
  const rejected = title.includes('버튼만') // 인사 ② check_plan 기각 이력 (목)
  const est = estimate(title)
  const rule = matchRule(policy, { path: est.paths, lines: est.lines, files: est.files }, false)
  return {
    ok: !hit && display && !rejected,
    reasons: [
      hit ? `민감 경로 포함("${hit}") → 기획서부터 평소대로` : '결제·주문·개인정보 경로 아님',
      display ? '화면 표시만 바꾸는 요청' : '새 기능·동작 변경 → 기획서부터 평소대로',
      rejected ? '인사 ② 기각 이력 있음 → 기획서부터 평소대로' : '인사 ② 기각 이력 없음',
      rule ? `예상 변경 ${est.lines}줄·${est.files}파일 · 기준 ${rule.id}(${ruleText(rule)}) 안 → 자동 머지` : `예상 변경 ${est.lines}줄·${est.files}파일 · 자동 처리 기준 밖 → 머지 전 AI 교차검증`,
    ],
  }
}

// ---- 자동 확정·자동 머지·교차검증의 공통 부품

const ctxOfSpec = (s: State, specId: string) => s.requests.find((r) => r.id === s.specs.find((x) => x.id === specId)?.request)?.ctx ?? 'ctx_req_0012'
function prInfo(s: State, pr: PR) {
  const task = s.tasks.find((t) => t.id === pr.task)
  const spec = s.specs.find((x) => x.id === task?.spec)
  const para = spec?.paras.find((p) => p.id === task?.para)
  const req = s.requests.find((r) => r.id === spec?.request)
  return { task, spec, para, req, ctx: req?.ctx ?? 'ctx_req_0012' }
}
const specText = (sp: Spec) => [sp.title, sp.problem, sp.goal, ...sp.paras.map((p) => p.text)].join(' ')
const pendingReview = (s: State, ref: string) => s.reviews.find((r) => r.ref === ref && !r.result)

// 백테스트 대상: 목 과거 기록 + 지금 상태에서 머지된 PR (장애에 연결됐으면 장애 연결)
function pastRecords(s: State, exclude?: string): PastRecord[] {
  const live = s.prs
    .filter((p) => p.merged && p.id !== exclude && !PAST_RECORDS.some((x) => x.pr === p.id))
    .map((p): PastRecord => ({ pr: p.id, t: '', path: p.gate.paths, lines: p.gate.lines, files: p.gate.files, result: s.incidents.some((i) => i.candidates.some((c) => c.pr === p.id)) ? '장애 연결' : '정상', note: '' }))
  return [...PAST_RECORDS.filter((x) => x.pr !== exclude), ...live]
}

const NO_IRREVERSIBLE: RuleCheck = { label: '되돌릴 수 없는 일', result: '통과', detail: `${IRREVERSIBLE.map((x) => x.label).join('·')} 해당 없음` }

// 기획서 확정 조건: 질문 없음 + 인사 기각 경고 없음 + 근거 있음
function specChecks(sp: Spec): { autoOk: boolean; checks: RuleCheck[]; route: string[] } {
  const warned = sp.paras.filter((p) => p.warn)
  const checks: RuleCheck[] = [
    { label: '남은 질문', result: sp.question ? '걸림' : '통과', detail: sp.question ? `답을 받지 않은 질문: ${sp.question}` : '남은 질문 없음' },
    { label: '근거', result: sp.basis.length ? '통과' : '걸림', detail: sp.basis.length ? `근거 ${sp.basis.length}개 · ${sp.basis.join(', ')}` : '근거 없음' },
    { label: '인사 기각 경고', result: warned.length ? '확인 필요' : '통과', detail: warned.length ? warned.map((p) => `${p.id} ${p.warn!.card}: ${p.warn!.text}`).join(' / ') : '인사 ② check_plan 경고 없음' },
  ]
  for (const p of warned) {
    const need = p.warn!.text.match(/(R\d+)과 함께/)?.[1]
    const met = Boolean(need && sp.paras.some((x) => x.id === need && x.kind === 'req'))
    checks.push({ label: `${p.id} 기각 조건`, result: met ? '통과' : '걸림', detail: met ? `조건 "${need}과 함께"를 지킴 · ${need} 문단이 같은 기획서에 있어 기각된 단독안이 아님` : '기각된 안과 같음 → 확정하면 안 됨' })
  }
  checks.push(NO_IRREVERSIBLE)
  const route = [
    ...(warned.length ? [`${warned.map((p) => p.id).join(', ')}에 인사 기각 경고가 붙어 있음`] : []),
    ...(sp.basis.length ? [] : ['근거가 없음']),
  ]
  return { autoOk: !sp.question && !warned.length && sp.basis.length > 0, checks, route }
}

function prChecks(s: State, pr: PR): { checks: RuleCheck[]; route: string[] } {
  const { para } = prInfo(s, pr)
  const size = { path: pr.gate.paths, lines: pr.gate.lines, files: pr.gate.files }
  const rule = matchRule(s.autoPolicy, size, Boolean(para?.warn))
  const rules = activeRules(s.autoPolicy).filter((r) => r.action === 'merge_pr')
  const miss = pr.reviews.filter((x) => x === '불일치').length
  const last = pr.reviews[pr.reviews.length - 1]
  const hits = pastRecords(s, pr.id).filter((x) => x.result !== '정상' && (x.path.startsWith(pr.gate.paths) || pr.gate.paths.startsWith(x.path)))
  const allowed = ALLOWED_PATHS.some((p) => pr.gate.paths.startsWith(p))
  const checks: RuleCheck[] = [
    { label: '변경 규모', result: rule ? '통과' : '확인 필요', detail: rule ? `${pr.gate.lines}줄·${pr.gate.files}파일 · 기준 ${rule.id}(${ruleText(rule)}) 안` : `${pr.gate.lines}줄·${pr.gate.files}파일 · 지금 자동 처리 기준(${rules.map((r) => `${r.id} ${ruleText(r)}`).join(', ') || '없음'})에 들지 않음` },
    { label: '허용 경로', result: allowed ? '통과' : '걸림', detail: allowed ? `${pr.gate.paths} · 안전 레이어 allowed_paths 안` : `${pr.gate.paths} · allowed_paths 밖` },
    { label: '완료 기준 검수', result: last !== '통과' ? '걸림' : miss >= 2 ? '확인 필요' : '통과', detail: last !== '통과' ? '마지막 검수 불일치' : `${pr.reviews.join(' → ')}${miss ? ` (불일치 ${miss}회 뒤 통과)` : ''} · CI 통과` },
    { label: '인사 기각 경고', result: para?.warn ? '확인 필요' : '통과', detail: para?.warn ? `${para.id} ${para.warn.card}: ${para.warn.text}` : '연결 문단에 경고 없음' },
    { label: '장애 표시 문단', result: para?.incident ? '확인 필요' : '통과', detail: para?.incident ? `${para.id}에 장애 ${para.incident} 표시` : '연결 문단에 장애 표시 없음' },
    { label: '과거 거절·장애', result: hits.length ? '확인 필요' : '통과', detail: hits.length ? `같은 경로 기록 ${hits.map((h) => `${h.pr}(${h.result}${h.note ? `, ${h.note}` : ''})`).join(', ')}` : '같은 경로의 거절·장애 기록 없음' },
    NO_IRREVERSIBLE,
  ]
  const route = [
    ...(rule ? [] : ['자동 처리 기준 밖']),
    ...(miss >= 2 ? [`검수 불일치 ${miss}회`] : []),
    ...(para?.incident ? ['장애가 표시된 문단'] : []),
    ...(para?.warn && !rule ? ['경고가 붙은 문단'] : []),
    ...(hits.some((h) => h.result === '장애 연결') ? ['장애가 났던 경로'] : []),
  ]
  return { checks, route }
}

// 검증자: 기획개발이 아닌 쪽. 돈이 걸린 경로는 재무, 기획서는 인사, 나머지 PR은 인프라 agent-review
function verifierFor(target: ReviewTarget, paths: string): string {
  if (target === 'confirm_spec') return '안전 레이어 → 인사 check_plan 재확인'
  if (/payment|order|refund|settle/.test(paths)) return '안전 레이어 → 재무/정산 review_hold'
  return '안전 레이어 → 인프라(DejaFix) agent-review'
}
const recommendOf = (checks: RuleCheck[]) => {
  const fail = checks.filter((c) => c.result === '걸림')
  const note = checks.filter((c) => c.result === '확인 필요')
  return fail.length
    ? { recommend: '거절' as const, recommendWhy: `걸린 항목: ${fail.map((c) => c.label).join(', ')}` }
    : { recommend: '승인' as const, recommendWhy: note.length ? `걸린 항목 없음. 확인할 것(${note.map((c) => c.label).join(', ')})은 근거로 설명됨` : '걸린 항목 없음' }
}

export function prReviewOf(s: State, prId: string, id: string, t: string): CrossReview | undefined {
  const pr = s.prs.find((p) => p.id === prId)
  if (!pr) return undefined
  const info = prInfo(s, pr)
  const irr = irreversibleOf([info.spec ? specText(info.spec) : '', pr.title].join(' '))
  const { checks, route } = prChecks(s, pr)
  const base = {
    id, t, ctx: info.ctx, target: 'merge_pr' as const, ref: pr.id, title: pr.title,
    summary: `${info.req ? `${info.req.id} "${info.req.title}"` : ''}의 ${info.para ? `${info.para.id} "${info.para.text}"` : pr.task} 구현. 변경 ${pr.gate.lines}줄·${pr.gate.files}파일(${pr.gate.paths}), 검수 ${pr.reviews.join(' → ')}, CI 통과.`,
  }
  if (irr) {
    return { ...base, lane: 'human', irreversible: irr.label, routeWhy: [`되돌릴 수 없는 일: ${irr.label}`], checks: [...checks.filter((c) => c !== NO_IRREVERSIBLE), { label: '되돌릴 수 없는 일', result: '걸림', detail: `${irr.label} → AI가 판단하지 않고 사람이 확인` }], verifier: '사람 (기획개발팀 팀장)' }
  }
  const prot = protectedOf(pr.gate.paths)
  if (prot) {
    // 보호 경로는 항상 C(#203)이고, C 보류 건은 #110·#190 합의대로 safety AI가 먼저 판단한다(심각한 건만 사람).
    // 학습 기준(AR-n)으로는 풀지 않는다(proposeStep에서 제외)
    const protChecks: RuleCheck[] = [...checks, { label: '보호 경로', result: '확인 필요', detail: `${prot} → C 보류, safety AI 1차 판단(#110). 금액 불일치·미등록 액션·형식 오류·무응답이면 사람` }]
    return { ...base, lane: 'ai', routeWhy: [`safety 보호 경로(#203) C: ${prot}`, ...route], checks: protChecks, verifier: '안전 레이어 → AI 1차 판단 (#110, C 보류)', ...recommendOf(protChecks) }
  }
  return { ...base, lane: 'ai', routeWhy: route, checks, verifier: verifierFor('merge_pr', pr.gate.paths), ...recommendOf(checks) }
}

function specReviewOf(s: State, specId: string, id: string, t: string): CrossReview | undefined {
  const sp = s.specs.find((x) => x.id === specId)
  if (!sp) return undefined
  const req = s.requests.find((r) => r.id === sp.request)
  const irr = irreversibleOf(specText(sp))
  const { checks, route } = specChecks(sp)
  const decisions = s.decisions.filter((x) => x.spec === sp.id)
  const base = {
    id, t, ctx: req?.ctx ?? 'ctx_req_0012', target: 'confirm_spec' as const, ref: sp.id, title: sp.title,
    summary: `${req ? `${req.id} "${req.title}"` : sp.request} 기획서. 요구사항 ${sp.paras.filter((p) => p.kind === 'req').length}개·완료 기준 ${sp.paras.filter((p) => p.kind === 'done').length}개, 근거 ${sp.basis.length}개${decisions.length ? `, 결정 ${decisions.map((x) => `${x.id} "${x.text}"`).join(', ')}` : ''}.`,
  }
  if (irr) {
    return { ...base, lane: 'human', irreversible: irr.label, routeWhy: [`되돌릴 수 없는 일: ${irr.label}`], checks: [...checks.filter((c) => c !== NO_IRREVERSIBLE), { label: '되돌릴 수 없는 일', result: '걸림', detail: `${irr.label} → AI가 판단하지 않고 사람이 확인` }], verifier: '사람 (기획개발팀 팀장)' }
  }
  return { ...base, lane: 'ai', routeWhy: route, checks, verifier: verifierFor('confirm_spec', ''), ...recommendOf(checks) }
}

function openReview(s: State, make: (id: string, t: string) => CrossReview | undefined): State {
  const id = `XR-${nextNum(s.reviews.map((r) => r.id))}`
  const t = now()
  const r = make(id, t)
  if (!r || pendingReview(s, r.ref)) return s
  const act: Activity = r.lane === 'human'
    ? { t, ctx: r.ctx, from: 'planning', to: 'safety', point: 'human_check.request', kind: 'execute', grade: 'C', text: `${r.ref} ${TARGET_LABEL[r.target]} → 되돌릴 수 없는 일(${r.irreversible})이라 교차검증 대신 사람 확인 대기 (${r.id})` }
    : { t, ctx: r.ctx, from: 'planning', to: 'safety', point: 'cross_review.request', kind: 'execute', grade: 'B', text: `${r.ref} ${TARGET_LABEL[r.target]} → AI 교차검증 요청 ${r.id} (${r.routeWhy.join(', ') || '확인 요청'}) · 검증자 ${r.verifier}` }
  return { ...s, reviews: [...s.reviews, r], activity: [...s.activity, act] }
}

// 기획서 확정 + 작업 분할. by = auto(조건 통과) / review(교차검증 승인) / human(되돌릴 수 없는 일)
function confirmSpecCore(s: State, specId: string, by: Approver, note: string): State {
  const spec = s.specs.find((x) => x.id === specId)
  if (!spec || spec.status === '확정') return s
  const req = s.requests.find((r) => r.id === spec.request)
  const ctx = req?.ctx ?? 'ctx_req_0012'
  const reqs = spec.paras.filter((p) => p.kind === 'req')
  const base = nextNum(s.tasks.map((t) => t.id))
  const tasks: Task[] = reqs.map((p, i) => ({ id: `T-${base + i}`, spec: spec.id, para: p.id, title: p.text.split(' (')[0], owner: spec.id === 'SPEC-7' ? (i === 0 ? '백엔드' : '프론트') : '에이전트', state: '진행 중' }))
  const t = now()
  const how = by === 'auto' ? '자동 확정' : by === 'review' ? '교차검증 승인 뒤 자동 확정' : '사람 확인 뒤 확정'
  const audit: AuditItem[] = by === 'auto'
    ? [{ t, ctx, kind: '자동 확정', ref: spec.id, what: spec.title, basis: note, verifier: '조건 검사 (안전 레이어 execute() 기록)', result: `확정 → 작업 ${tasks.map((x) => x.id).join(', ')}` }]
    : []
  return {
    ...s,
    specs: s.specs.map((x) => (x.id === specId ? { ...x, status: '확정', confirmedBy: by } : x)),
    tasks: [...s.tasks, ...tasks],
    requests: s.requests.map((r) => (r.spec === specId ? { ...r, state: '작업 중' } : r)),
    audit: [...s.audit, ...audit],
    activity: [
      ...s.activity,
      { t, ctx, from: 'planning', to: 'planning', point: 'confirm_spec', kind: 'A2A', grade: 'A', text: `${spec.id} ${how} · ${note}` },
      { t, ctx, from: 'planning', to: 'planning', point: 'split_tasks', kind: 'A2A', grade: 'A', text: `요구사항 ${reqs.length}개 → 작업 ${tasks.map((x) => x.id).join(', ')}` },
      { t, ctx, from: 'planning', to: 'hr', point: 'record_case', kind: 'A2A', grade: 'A', text: `① kind spec_confirmed · ${spec.id} (${how})` },
      ...(req ? [{ t, ctx, from: 'planning' as const, to: req.from, point: 'get_request_status', kind: 'A2A' as const, grade: 'read' as const, text: `${req.id} 상태 "작업 중" (기획서 ${how})` }] : []),
    ],
  }
}

// 확정 조건을 보고 자동 확정하거나 대기열로 보낸다. 질문이 남아 있으면 답을 기다린다
function trySpecAuto(s: State, specId: string): State {
  const sp = s.specs.find((x) => x.id === specId)
  if (!sp || sp.status === '확정' || pendingReview(s, sp.id)) return s
  if (irreversibleOf(specText(sp))) return openReview(s, (id, t) => specReviewOf(s, specId, id, t))
  if (sp.question) return s
  const c = specChecks(sp)
  if (c.autoOk) return confirmSpecCore(s, specId, 'auto', `질문 없음·경고 없음·근거 ${sp.basis.length}개`)
  return openReview(s, (id, t) => specReviewOf(s, specId, id, t))
}

// PR 머지 + 작업 완료 + 요청 닫기. by = rule(학습 기준) / review(교차검증 승인) / human(되돌릴 수 없는 일)
function mergeCore(s: State, prId: string, by: 'rule' | 'review' | 'human', ruleId?: string, reviewId?: string): State {
  const pr = s.prs.find((p) => p.id === prId)
  if (!pr || pr.merged) return s
  const info = prInfo(s, pr)
  const ctx = info.ctx
  const t = now()
  const tasks = s.tasks.map((x) => (x.id === pr.task ? { ...x, state: '완료' as TaskState } : x))
  const specId = info.spec?.id
  const allDone = tasks.filter((x) => x.spec === specId).every((x) => x.state === '완료')
  const pol = s.autoPolicy
  const rule = ruleId ? pol.rules.find((r) => r.id === ruleId) : undefined
  const how = by === 'rule' ? `기준 ${rule?.id}(${rule ? ruleText(rule) : ''}) 안 · 검수·CI 통과 → 자동 머지` : by === 'review' ? `교차검증 승인(${reviewId}) 뒤 자동 머지 · ${OBSERVE_MIN}분 관찰 시작` : '사람 확인 뒤 머지'
  const act: Activity[] = [
    { t, ctx, from: 'planning', to: 'safety', point: 'execute(merge_pr)', kind: 'execute', grade: 'B', text: `${pr.id} ${how}` },
    { t, ctx, from: 'planning', to: 'shop', point: 'github_merge_pull', kind: 'API', text: `${pr.id} 머지 → dev 배포` },
    { t, ctx, from: 'planning', to: 'hr', point: 'record_case', kind: 'A2A', grade: 'A', text: `① kind pr_merged · ${pr.id}` },
  ]
  if (allDone && info.req) {
    act.push({ t, ctx, from: 'planning', to: 'hr', point: 'record_case', kind: 'A2A', grade: 'A', text: `① kind request_closed · ${info.req.id}` })
    act.push({ t, ctx, from: 'planning', to: info.req.from, point: 'get_request_status', kind: 'A2A', grade: 'read', text: `${info.req.id} "${info.req.title}" 완료 · ${pr.id} 머지 (${by === 'human' ? '사람 확인' : '사람 클릭 없이 자동 처리'})` })
  }
  const audit: AuditItem[] = []
  if (by === 'rule' && rule) {
    audit.push({ t, ctx, kind: '자동 머지', ref: pr.id, what: pr.title, rule: rule.id, basis: ruleText(rule), verifier: '안전 레이어 코드 규칙 (변경 줄·파일·허용 경로)', result: '머지 · dev 배포' })
  }
  const autoPolicy: AutoPolicy = by === 'rule' && rule
    ? {
        ...pol,
        rules: pol.rules.map((r) => (r.id === rule.id ? { ...r, runs: [...r.runs, { pr: pr.id, result: '정상' }] } : r)),
        history: [...pol.history, { pr: pr.id, req: info.req?.id ?? '-', t, lines: pr.gate.lines, files: pr.gate.files, result: '정상', rule: rule.id }],
      }
    : by === 'review'
      ? { ...pol, observing: [...pol.observing, { pr: pr.id, req: info.req?.id ?? '-', path: pr.gate.paths, lines: pr.gate.lines, files: pr.gate.files, mergedAt: t, untilMs: Date.now() + OBSERVE_MIN * 60_000, result: '관찰 중' }] }
      : pol
  return {
    ...s,
    autoPolicy,
    prs: s.prs.map((p) => (p.id === prId ? { ...p, merged: true, auto: by !== 'human', mergedBy: by, rule: rule?.id } : p)),
    tasks,
    requests: allDone ? s.requests.map((r) => (r.spec === specId ? { ...r, state: '완료' } : r)) : s.requests,
    audit: [...s.audit, ...audit],
    activity: [...s.activity, ...act],
  }
}

// 검수 통과 PR: 기준 안이면 자동 머지, 아니면 교차검증(되돌릴 수 없는 일은 사람)으로
function tryMerge(s: State, prId: string): State {
  const pr = s.prs.find((p) => p.id === prId)
  if (!pr || pr.merged || pr.reviews[pr.reviews.length - 1] !== '통과' || pendingReview(s, pr.id)) return s
  const info = prInfo(s, pr)
  const irr = irreversibleOf([info.spec ? specText(info.spec) : '', pr.title].join(' '))
  const miss = pr.reviews.filter((x) => x === '불일치').length
  const rule = irr ? undefined : matchRule(s.autoPolicy, { path: pr.gate.paths, lines: pr.gate.lines, files: pr.gate.files }, Boolean(info.para?.warn))
  if (rule && miss < 2 && !info.para?.incident) return mergeCore(s, prId, 'rule', rule.id)
  return openReview(s, (id, t) => prReviewOf(s, prId, id, t))
}

// PR 만들기 (generate_pr). SPEC-7은 시연용 규모, 나머지는 제목으로 어림한다. 검수 통과분은 바로 머지 판단으로
function genPrs(s: State, specId: string, merge = true): State {
  const todo = s.tasks.filter((t) => t.spec === specId && t.state === '진행 중')
  if (!todo.length) return s
  const ctx = ctxOfSpec(s, specId)
  const special = specId === 'SPEC-7'
  const n = nextNum(s.prs.map((p) => p.id))
  const prs: PR[] = todo.map((t, i) => {
    const est = special ? (i === 0 ? { lines: 86, files: 3, paths: 'shop-api/cart/' } : { lines: 41, files: 2, paths: 'shop-web/checkout/' }) : estimate(t.title)
    return {
      id: `PR #${n + i}`, task: t.id, title: `${t.para}: ${t.title}`, branch: `gen/${t.id.toLowerCase()}`,
      reviews: [special && i === 1 ? '불일치' : '통과'], gate: { result: '통과', lines: est.lines, files: est.files, paths: est.paths }, merged: false,
    }
  })
  let next: State = {
    ...s,
    tasks: s.tasks.map((t) => { const p = prs.find((x) => x.task === t.id); return p ? { ...t, state: p.reviews[0] === '통과' ? '머지 대기' : 'PR 검수' } : t }),
    prs: [...s.prs, ...prs],
    firstPrMinutes: [...s.firstPrMinutes, special ? 52 : 3],
    activity: [
      ...s.activity,
      ...prs.flatMap((p): Activity[] => {
        const t = now()
        return [
          { t, ctx, from: 'planning', to: 'safety', point: 'execute(generate_pr)', kind: 'execute', grade: 'B', text: `${p.id} 변경 ${p.gate.lines}줄·${p.gate.files}파일·${p.gate.paths} → 코드 규칙 통과` },
          { t, ctx, from: 'planning', to: 'shop', point: 'github_create_pull', kind: 'API', text: `${p.id} 생성 (${p.branch}, 라벨 generated-pr)` },
          { t, ctx, from: 'planning', to: 'planning', point: 'review_pr', kind: 'A2A', grade: 'A', text: `${p.id} 검수 ${p.reviews[0]}${p.reviews[0] === '불일치' ? ' (완료 기준 C2)' : ', CI 통과'}` },
        ]
      }),
    ],
  }
  if (merge) for (const p of prs) if (p.reviews[0] === '통과') next = tryMerge(next, p.id)
  return next
}

// 관찰 기간이 끝난 머지: 장애·revert 연결이 없으면 정상 (학습 근거가 된다)
function observeStep(s: State): State {
  const pol = s.autoPolicy
  const due = pol.observing.find((o) => o.result === '관찰 중' && o.untilMs <= Date.now())
  if (!due) return s
  const t = now()
  return {
    ...s,
    autoPolicy: { ...pol, observing: pol.observing.map((o) => (o === due ? { ...o, result: '정상' } : o)), log: [...pol.log, { t, text: `${due.pr} 머지 뒤 ${OBSERVE_MIN}분 관찰 · 장애·revert 연결 없음 → 학습 근거`, by: '자동' }] },
    audit: [...s.audit, { t, ctx: 'auto_policy', kind: '관찰 통과', ref: due.pr, what: `${due.path} ${due.lines}줄·${due.files}파일`, basis: `머지 ${due.mergedAt} · 관찰 ${OBSERVE_MIN}분`, verifier: '자동 (장애·revert 연결 기록)', result: '정상' }],
  }
}

// 같은 경로에서 관찰을 통과한 머지가 OBSERVE_N건 모이면 기준을 제안한다 (백테스트 → 안전 장치 안이면 재무 AI, 밖이면 사람)
function proposeStep(s: State): State {
  const pol = s.autoPolicy
  const used = new Set(pol.rules.flatMap((r) => r.basis))
  const clean = pol.observing.filter((o) => o.result === '정상' && !used.has(o.pr) && !protectedOf(o.path))
  const byPath = new Map<string, Observation[]>()
  for (const o of clean) byPath.set(o.path, [...(byPath.get(o.path) ?? []), o])
  const size = (g: Observation[]) => ({ path: g[0].path, lines: Math.max(...g.map((x) => x.lines)), files: Math.max(...g.map((x) => x.files)) })
  const group = [...byPath.values()].find((g) => g.length >= OBSERVE_N && !matchRule(pol, size(g), false))
  if (!group) return s
  const cand = { ...size(group), noWarn: true }
  const records = pastRecords(s)
  const covered = records.filter((x) => covers(cand, x))
  const conflicts = covered.filter((x) => x.result !== '정상')
  const backtest: Backtest = { checked: records.length, covered: covered.length, conflicts: conflicts.map((x) => `${x.pr} ${x.result}`) }
  const inGuard = cand.lines <= GUARD.lines && cand.files <= GUARD.files
  const approver: AutoRule['approver'] = inGuard ? '재무 AI' : '사람 (태윤)'
  const id = `AR-${nextNum(pol.rules.map((x) => x.id))}`
  const t = now()
  const basis = group.map((x) => x.pr)
  const refused = conflicts.length ? `백테스트 충돌: ${conflicts.map((x) => `${x.pr}(${x.result})`).join(', ')}도 이 기준이면 자동 머지됐을 것` : undefined
  const rule: AutoRule = { id, action: 'merge_pr', ...cand, status: refused ? '거절' : '승인 대기', approver, basis, backtest, runs: [], t, ...(refused ? { decidedAt: t, decideWhy: refused } : {}) }
  const head = `${basis.join('·')} 관찰 통과 → 기준 ${id}(${ruleText(cand)}) 제안 · 과거 ${backtest.checked}건 대조`
  const ask: Activity = inGuard
    ? { t, ctx: 'auto_policy', from: 'planning', to: 'finance', point: 'review_threshold_change', kind: 'A2A', grade: 'B', text: `기준 ${id}(${ruleText(cand)}) 넓히기 요청 · 근거 ${basis.join(', ')} 관찰 통과 · 안전 장치(${GUARD.lines}줄·${GUARD.files}파일) 안` }
    : { t, ctx: 'auto_policy', from: 'planning', to: 'safety', point: 'threshold_change.request', kind: 'A2A', grade: 'C', text: `기준 ${id}(${ruleText(cand)}) 넓히기 요청 · 안전 장치 밖이라 사람(태윤) 승인` }
  return {
    ...s,
    autoPolicy: { ...pol, rules: [...pol.rules, rule], log: [...pol.log, { t, text: refused ? `${head} → 학습 거부 · ${refused}` : `${head}, 충돌 0건 → ${approver} 승인 요청`, by: '자동' }] },
    audit: [...s.audit, { t, ctx: 'auto_policy', kind: refused ? '학습 거부' : '기준 제안', ref: id, what: `PR 머지 · ${ruleText(cand)}`, rule: id, basis: `${basis.join(', ')} 관찰 통과 · 백테스트 ${backtest.checked}건`, verifier: '백테스트 (코드 규칙)', result: refused ?? `${approver} 승인 대기` }],
    activity: refused ? s.activity : [...s.activity, ask],
  }
}

// 기준 넓히기 판단: 재무 AI(안전 장치 안) 또는 사람(태윤, 밖). 다른 부서 시뮬레이터가 보낸다
function decideRule(s: State, ruleId: string, ok: boolean): State {
  const r = s.autoPolicy.rules.find((x) => x.id === ruleId)
  if (!r || r.status !== '승인 대기') return s
  const t = now()
  const ai = r.approver === '재무 AI'
  const why = ok ? (ai ? `안전 장치(${GUARD.lines}줄·${GUARD.files}파일) 안 · 백테스트 충돌 0건` : '사람(태윤) 확인') : '넓히지 않음'
  return {
    ...s,
    autoPolicy: { ...s.autoPolicy, rules: s.autoPolicy.rules.map((x) => (x.id === ruleId ? { ...x, status: ok ? '정식' : '거절', decidedAt: t, decideWhy: why } : x)), log: [...s.autoPolicy.log, { t, text: `${ruleId} ${r.approver} ${ok ? '승인 → 정식' : '거절'} (${why})`, by: ai ? '재무 AI' : '사람' }] },
    audit: [...s.audit, { t, ctx: 'auto_policy', kind: ok ? '기준 정식' : '학습 거부', ref: ruleId, what: `PR 머지 · ${ruleText(r)}`, rule: ruleId, basis: r.basis.join(', '), verifier: ai ? '재무 review_threshold_change' : '사람 (태윤)', result: ok ? '정식' : '거절' }],
    activity: [...s.activity, { t, ctx: 'auto_policy', from: ai ? 'finance' : 'human', to: 'planning', point: ai ? 'review_threshold_change' : '사람 확인', kind: ai ? 'A2A' : '사람', text: `기준 ${ruleId} ${ok ? '승인' : '거절'} (${why})` }],
  }
}

// 시연: 검증자(기획개발이 아닌 쪽)가 규칙 검사 결과로 판단한다. 걸린 항목이 하나라도 있으면 거절
function decideReview(s: State, reviewId: string): State {
  const r = s.reviews.find((x) => x.id === reviewId)
  if (!r || r.result || r.lane !== 'ai') return s
  const fail = r.checks.filter((c) => c.result === '걸림')
  const result = fail.length ? '거절' : '승인'
  const why = fail.length ? `걸린 항목: ${fail.map((c) => c.label).join(', ')}` : '규칙 검사에 걸린 항목 없음'
  const t = now()
  let next: State = {
    ...s,
    reviews: s.reviews.map((x) => (x.id === r.id ? { ...x, result, resultWhy: why, decidedAt: t } : x)),
    audit: [...s.audit, { t, ctx: r.ctx, kind: result === '승인' ? '교차검증 승인' : '교차검증 거절', ref: r.ref, what: `${TARGET_LABEL[r.target]} · ${r.title}`, basis: r.routeWhy.join(', ') || '-', verifier: r.verifier, result: `${result} · ${why}` }],
    activity: [...s.activity, { t, ctx: r.ctx, from: 'safety', to: 'planning', point: 'cross_review.result', kind: 'A2A', grade: 'read', text: `${r.id} ${r.ref} ${TARGET_LABEL[r.target]} → ${r.verifier} 판단 ${result} (${why}) · 기획개발은 자기 건을 승인하지 않음` }],
  }
  if (result === '거절') {
    if (r.target === 'merge_pr') next = { ...next, tasks: next.tasks.map((x) => (x.id === next.prs.find((p) => p.id === r.ref)?.task ? { ...x, state: 'PR 검수' } : x)) }
    return next
  }
  if (r.target === 'confirm_spec') return confirmSpecCore(next, r.ref, 'review', `${r.id} 승인 (${r.verifier})`)
  return mergeCore(next, r.ref, 'review', undefined, r.id)
}

// 되돌릴 수 없는 일: 사람이 확인하고 진행하거나 보류한다
function humanDecide(s: State, reviewId: string, ok: boolean): State {
  const r = s.reviews.find((x) => x.id === reviewId)
  if (!r || r.result || r.lane !== 'human') return s
  const t = now()
  const next: State = {
    ...s,
    reviews: s.reviews.map((x) => (x.id === r.id ? { ...x, result: ok ? '승인' : '거절', resultWhy: ok ? '사람이 확인하고 진행' : '사람이 보류', decidedAt: t } : x)),
    activity: [...s.activity, { t, ctx: r.ctx, from: 'human', to: 'planning', point: '사람 확인', kind: '사람', text: `팀장 노예진이 ${r.ref} ${TARGET_LABEL[r.target]} ${ok ? '진행' : '보류'} (되돌릴 수 없는 일: ${r.irreversible})` }],
  }
  if (!ok) return next
  return r.target === 'confirm_spec' ? confirmSpecCore(next, r.ref, 'human', `${r.id} 사람 확인`) : mergeCore(next, r.ref, 'human', undefined, r.id)
}

// 기준 끄기: 사람이 누르면 승인 없이 바로 해제 (좁히는 쪽은 승인이 필요 없다)
function disableRule(s: State, ruleId: string): State {
  const rule = s.autoPolicy.rules.find((r) => r.id === ruleId)
  if (!rule || rule.status === '해제됨') return s
  const t = now()
  return {
    ...s,
    autoPolicy: { ...s.autoPolicy, rules: s.autoPolicy.rules.map((r) => (r.id === ruleId ? { ...r, status: '해제됨', offAt: t, offWhy: '사람이 끔 (승인 없이 바로)' } : r)), log: [...s.autoPolicy.log, { t, text: `${ruleId} 해제 (사람이 끔)`, by: '사람' }] },
    audit: [...s.audit, { t, ctx: 'auto_policy', kind: '기준 해제', ref: ruleId, what: ruleText(rule), rule: ruleId, basis: '사후 감사에서 끔', verifier: '사람', result: '해제됨' }],
    activity: [
      ...s.activity,
      { t, ctx: 'auto_policy', from: 'human', to: 'planning', point: '기준 끄기', kind: '사람', text: `팀장 노예진이 기준 ${ruleId}(${ruleText(rule)})를 끔` },
      { t, ctx: 'auto_policy', from: 'planning', to: 'safety', point: 'auto_policy.updated', kind: '이벤트', text: `기준 ${ruleId} 해제 → 이 범위의 PR은 다시 AI 교차검증으로` },
    ],
  }
}

// 요청에 기획서 초안을 붙인다 (write_spec). 채택할 때, 또는 기획 중인데 기획서가 없을 때
function draftSpecFor(s: State, r: Request, adopted: boolean): State {
  const n = nextNum(s.specs.map((x) => x.id))
  const specId = `SPEC-${n}`
  const spec: Spec = {
    id: specId, title: r.title, request: r.id, status: '초안',
    problem: `${DEPT_NAME[r.from]}에서 받은 요청: ${r.evidence}`, goal: `${r.title}`,
    paras: [
      { id: 'R1', kind: 'req', text: r.title },
      { id: 'C1', kind: 'done', text: '바뀐 뒤 같은 문의·이상 신호가 다시 생기지 않는다' },
    ],
    question: '바꿀 범위(화면 문구만 / 동작까지)를 어디까지로 할까요?',
    basis: [`${r.id} (${DEPT_NAME[r.from]})`, r.evidence], humanSentences: 0,
  }
  const t = now()
  return {
    ...s,
    draftsAccepted: s.draftsAccepted + (adopted ? 1 : 0),
    specs: [...s.specs, spec],
    requests: s.requests.map((x) => (x.id === r.id ? { ...x, state: '기획 중', spec: specId } : x)),
    threads: [...s.threads, { id: `TH-${n}`, on: specId, title: `${specId} · ${r.title}`, kind: 'spec', messages: [{ who: '기획개발 에이전트', dept: 'planning', t, text: `빈 항목 질문: ${spec.question}` }] }],
    activity: [
      ...s.activity,
      ...(adopted ? [{ t, ctx: r.ctx, from: 'planning' as const, to: 'planning' as const, point: 'review_ticket_draft', kind: 'A2A' as const, grade: 'A' as const, text: `${r.id} 채택` }] : []),
      { t, ctx: r.ctx, from: 'planning', to: 'planning', point: 'write_spec', kind: 'A2A', grade: 'A', text: `${specId} 초안 작성, 질문 1개 → 답을 받으면 확정 조건 확인` },
      { t, ctx: r.ctx, from: 'planning', to: r.from, point: 'spec_question', kind: 'A2A', grade: 'read', text: `${specId} 빈 항목 질문: ${spec.question}` },
    ],
  }
}

// 질문 없는 기획서를 바로 쓰고(write_spec) 확정 조건으로 넘긴다 (간단 요청·되돌릴 수 없는 요청)
function writeDirectSpec(s: State, req: Request, paras: Para[], goal: string, basis: string[]): { s: State; specId: string } {
  const specId = `SPEC-${nextNum(s.specs.map((x) => x.id))}`
  const spec: Spec = { id: specId, title: req.title, request: req.id, status: '초안', problem: `${DEPT_NAME[req.from]}에서 받은 요청: ${req.title}`, goal, paras, basis, humanSentences: 0 }
  const t = now()
  return {
    specId,
    s: {
      ...s,
      specs: [...s.specs, spec],
      requests: s.requests.map((r) => (r.id === req.id ? { ...r, spec: specId, state: '기획 중' } : r)),
      activity: [
        ...s.activity,
        { t, ctx: req.ctx, from: 'planning', to: 'hr', point: 'check_plan', kind: 'A2A', grade: 'read', text: '② 기각 이력 질의 → 없음' },
        { t, ctx: req.ctx, from: 'planning', to: 'planning', point: 'write_spec', kind: 'A2A', grade: 'A', text: `${specId} 작성 (요구사항 ${paras.filter((p) => p.kind === 'req').length}, 완료 기준 ${paras.filter((p) => p.kind === 'done').length}, 질문 0)` },
      ],
    },
  }
}

function inbound(s: State, kind: InboundKind): State {
  const seq = s.seq + 1
  const t = now()
  const ctxReq = `ctx_req_${String(20 + seq).padStart(4, '0')}`
  const act = (x: Omit<Activity, 't' | 'seq'>): Activity => ({ ...x, t, seq })
  const merged = s.prs.filter((p) => p.merged)
  const lastMerged = merged[merged.length - 1]
  const base = { ...s, seq }

  if (kind === 'support_ticket' || kind === 'marketing_ticket' || kind === 'support_simple' || kind === 'marketing_simple') {
    const from: Dept = kind.startsWith('support') ? 'support' : 'marketing'
    const pool = kind.endsWith('simple') ? SIMPLE_TICKETS[from] : SAMPLE_TICKETS[from]
    // 간단 요청은 아직 안 보낸 것부터, 일반 요청은 돌아가며 (중복 연결 시연용)
    const used = new Set(s.requests.map((r) => r.title))
    const title = kind.endsWith('simple') ? (pool.find((x) => !used.has(x)) ?? `${pool[seq % pool.length]} (${seq}차)`) : pool[seq % pool.length]
    const same = s.requests.find((r) => r.title === title)
    const dupId = same ? (same.duplicateOf ?? same.id) : undefined
    const id = `REQ-${20 + seq}`
    const auto = dupId ? undefined : checkSimple(title, s.autoPolicy)
    const req: Request = { id, ctx: ctxReq, from, via: 'create_ticket', title, evidence: `${DEPT_NAME[from]} 시뮬레이터에서 보냄`, state: dupId ? '중복 연결' : '접수', duplicateOf: dupId, auto, flow: !dupId }
    const created = act({ ctx: ctxReq, from, to: 'planning', point: 'create_ticket', kind: 'A2A', grade: 'A', text: dupId ? `"${title}" → 같은 요청 ${dupId}에 연결 (linked_duplicate)` : `변경 요청 ${id} 등록: ${title}` })
    const received: State = { ...base, requests: [...s.requests, req], activity: [...s.activity, created] }
    if (!auto) {
      return { ...received, activity: [...received.activity, act({ ctx: ctxReq, from: 'planning', to: from, point: 'get_request_status', kind: 'A2A', grade: 'read', text: `${id}는 ${dupId}에 연결됨` })] }
    }
    const judged: State = {
      ...received,
      activity: [...received.activity, { t, ctx: ctxReq, from: 'planning', to: 'planning', point: 'classify_request', kind: 'A2A', grade: 'read', text: auto.ok ? '간단 요청 판정 → 사람 클릭 없이 끝까지 처리 (기준 밖이면 머지 전 AI 교차검증)' : `일반 요청 · ${auto.reasons.filter((r) => r.includes('평소대로')).join(', ')}` }],
    }
    if (auto.ok) return judged
    return { ...judged, activity: [...judged.activity, { t, ctx: ctxReq, from: 'planning', to: from, point: 'get_request_status', kind: 'A2A', grade: 'read', text: `${id} 접수됨 → 기획서 작성` }] }
  }
  if (kind === 'finance_irreversible') {
    const id = `REQ-${20 + seq}`
    const req: Request = { id, ctx: ctxReq, from: 'finance', via: 'create_ticket', title: '정산 끝난 주문의 결제 데이터 삭제 기능', evidence: '보관 기한 지난 결제 기록 정리 요청 (재무/정산 시뮬레이터)', state: '접수' }
    const received: State = { ...base, requests: [...s.requests, req], activity: [...s.activity, act({ ctx: ctxReq, from: 'finance', to: 'planning', point: 'create_ticket', kind: 'A2A', grade: 'A', text: `변경 요청 ${id} 등록: ${req.title}` })] }
    const w = writeDirectSpec(received, req, [
      { id: 'R1', kind: 'req', text: '정산 끝난 주문 중 보관 기한이 지난 결제 데이터 삭제' },
      { id: 'C1', kind: 'done', text: '삭제 전 백업과 삭제 대상 목록이 남는다' },
    ], '보관 기한이 지난 결제 데이터를 정리한다', [`${id} (재무/정산)`, '재무 보관 기한 기준 (목)'])
    return trySpecAuto(w.s, w.specId)
  }
  if (kind === 'hr_draft' || kind === 'data_draft') {
    const from: Dept = kind === 'hr_draft' ? 'hr' : 'data'
    const id = `REQ-${20 + seq}`
    const title = from === 'hr' ? '반복되는 인수인계 질문을 운영 매뉴얼에 반영' : '같은 시간대 결제 실패율 반복 상승 원인 개선'
    return {
      ...base,
      draftsIn: s.draftsIn + 1,
      requests: [...s.requests, { id, ctx: ctxReq, from, via: from === 'hr' ? 'ticket_draft_propose (③)' : '개선 초안 (원인 추적 루프 ③)', title, evidence: `${DEPT_NAME[from]} 시뮬레이터에서 보냄`, state: '접수', flow: true }],
      activity: [...s.activity, act({ ctx: ctxReq, from, to: 'planning', point: from === 'hr' ? 'ticket_draft_propose' : '개선 초안 (루프 ③)', kind: 'A2A', grade: 'A', text: `개선 초안 수신 → 요청함 ${id}` })],
    }
  }
  if (kind === 'data_query' || kind === 'infra_query') {
    const from: Dept = kind === 'data_query' ? 'data' : 'infra'
    const ctx = s.incidents[0]?.ctx ?? 'ctx_inc_0001'
    const found = lastMerged ? `${lastMerged.id} (${lastMerged.title})` : '후보 없음'
    return {
      ...base,
      activity: [
        ...s.activity,
        act({ ctx, from, to: 'planning', point: 'find_related_prs', kind: 'A2A', grade: 'read', text: from === 'data' ? '이상 구간 직전 배포가 있었나 질의' : '장애 시각 직전 머지 PR 질의' }),
        act({ ctx, from: 'planning', to: from, point: 'find_related_prs', kind: 'A2A', grade: 'read', text: `관련 PR 후보 회신: ${found} · 원인 판정은 하지 않음` }),
      ],
    }
  }
  if (kind === 'orch_incident') {
    const n = s.incidents.length + 1
    const ctx = `ctx_inc_${String(n).padStart(4, '0')}`
    const pr = lastMerged ?? s.prs[0]
    const task = s.tasks.find((x) => x.id === pr.task)
    const followId = `REQ-${100 + n}`
    // 자동 머지한 PR이 장애에 연결되면 그 PR을 통과시킨 기준(또는 그 PR에서 배운 기준)을 바로 해제한다 (끄는 쪽은 승인 없이)
    const pol = s.autoPolicy
    const hit = pr.auto ? pol.rules.filter((r) => (r.status === '정식' || r.status === '승인 대기') && (r.id === pr.rule || r.basis.includes(pr.id))) : []
    const autoPolicy: AutoPolicy = pr.auto
      ? {
          ...pol,
          rules: pol.rules.map((r) => (hit.includes(r) ? { ...r, status: '해제됨', offAt: t, offWhy: `${pr.id}가 사건 ${ctx}에 연결`, runs: r.runs.map((x) => (x.pr === pr.id ? { ...x, result: '장애 연결' } : x)) } : r)),
          history: pol.history.map((h) => (h.pr === pr.id ? { ...h, result: '장애 연결' } : h)),
          observing: pol.observing.map((o) => (o.pr === pr.id ? { ...o, result: '장애 연결' } : o)),
          log: [...pol.log, ...hit.map((r) => ({ t, text: `${pr.id}(자동 머지)가 사건 ${ctx}에 연결 → 기준 ${r.id} 즉시 해제`, by: '자동' as const }))],
        }
      : pol
    const offAct: Activity[] = hit.map((r) => act({ ctx, from: 'planning', to: 'safety', point: 'auto_policy.updated', kind: '이벤트', text: `자동 머지 ${pr.id}가 장애에 연결 → 기준 ${r.id}(${ruleText(r)}) 즉시 해제 (끄는 쪽은 승인 없이)` }))
    const offAudit: AuditItem[] = hit.map((r) => ({ t, ctx, kind: '기준 해제', ref: r.id, what: ruleText(r), rule: r.id, basis: `${pr.id} 장애 연결 (${ctx})`, verifier: '자동 (장애 연결)', result: '해제됨' }))
    return {
      ...base,
      autoPolicy,
      audit: [...s.audit, ...offAudit],
      incidents: [{ ctx, title: `결제 API 응답 지연 (시뮬레이터 ${n}회차)`, at: t, candidates: [{ pr: pr.id, spec: task?.spec ?? '-', para: task?.para ?? '-', mergedAt: t }], linked: true, followUp: followId }, ...s.incidents],
      specs: s.specs.map((sp) => (sp.id === task?.spec ? { ...sp, paras: sp.paras.map((p) => (p.id === task?.para ? { ...p, incident: ctx } : p)) } : sp)),
      requests: [...s.requests, { id: followId, ctx, from: 'infra', via: 'link_incident (재발 방지)', title: `${pr.id} 관련 재발 방지 검토`, evidence: `사건 ${ctx}`, state: '접수' }],
      activity: [
        ...s.activity,
        act({ ctx, from: 'orchestrator', to: 'planning', point: 'incident.opened', kind: '이벤트', text: `사건 ${ctx} 개시 (infra.anomaly_detected 감지)` }),
        act({ ctx, from: 'orchestrator', to: 'planning', point: 'link_incident', kind: 'A2A', grade: 'A', text: `사건을 ${pr.id}에 연결 요청` }),
        act({ ctx, from: 'planning', to: 'orchestrator', point: 'link_incident', kind: 'A2A', grade: 'A', text: `${task?.spec ?? '-'} ${task?.para ?? '-'}에 장애 표시, 재발 방지 요청 ${followId} 등록` }),
        ...offAct,
      ],
    }
  }
  // safety_verify: infra rollback_deployment(B)의 교차검증
  const ok = Boolean(lastMerged)
  const ctx = s.incidents[0]?.ctx ?? 'ctx_inc_0001'
  return {
    ...base,
    activity: [
      ...s.activity,
      act({ ctx, from: 'safety', to: 'planning', point: 'verify_rollback_target', kind: 'A2A', grade: 'read', text: 'infra rollback_deployment(B) 교차검증 요청' }),
      act({ ctx, from: 'planning', to: 'safety', point: 'verify_rollback_target', kind: 'A2A', grade: 'read', text: ok ? `승인 · 장애 직전 머지 ${lastMerged!.id} 확인 (deploy_before_incident)` : '거절 · 기간 안 배포 없음 (no_deploy_in_window) → completed + rejected_by_review' }),
    ],
  }
}

// ---- 소통·문서

const DEPT_CHAT: Partial<Record<Dept, string[]>> = {
  support: ['배송 조회 송장 번호 문의가 하루 10건 넘게 들어옵니다. 우선순위 올려 주실 수 있나요?', 'REQ-12 처리 상태 고객에게 안내해도 될까요?'],
  marketing: ['다음 주 기획전 배너 문구 확정본 공유드려요. 장바구니 보존 공지는 REQ-12 완료 뒤에 낼게요.', '신간 알림 버튼 기획 일정이 궁금합니다.'],
  hr: ['이번 주 인수인계 질문 중 "결제 실패 안내"가 3번 반복됐습니다. 운영 매뉴얼 반영 초안 보낼게요.', '결정 카드 DC-44에 SPEC-7 결정 이유를 붙였습니다.'],
  data: ['결제 실패율이 14:00~14:10에 또 올랐습니다. 직전 배포가 있었는지 확인 부탁드려요.', '장바구니 이탈률 지표 정의 공유드립니다 (코드로 계산).'],
  infra: ['shop-api v2.4.1 롤백 후 p95가 정상 범위로 돌아왔습니다.', '카나리 배포 설정 초안 준비됐습니다. REQ-101에 붙여 주세요.'],
}

function nextBlockId(doc: Doc): string {
  const max = doc.blocks.reduce((m, b) => Math.max(m, Number(b.id.slice(1)) || 0), 0)
  return `b${max + 1}`
}

function patchDoc(s: State, id: string, f: (d: Doc) => Doc): State {
  return { ...s, docs: s.docs.map((d) => (d.id === id ? { ...f(d), updated: now() } : d)) }
}

// 요청 하나에 걸린 것(기획서 문단·작업·PR·대화·결정·장애)을 모아 정리 문서 블록으로 만든다
export function gatherBlocks(s: State, about: string): DocBlock[] {
  const r = s.requests.find((x) => x.id === about)
  const out: Omit<DocBlock, 'id'>[] = []
  if (!r) return []
  out.push({ kind: 'h2', text: '요청' })
  out.push({ kind: 'ref', text: `${r.id} · ${DEPT_NAME[r.from]} · ${r.title} · 상태 ${r.state}` })
  out.push({ kind: 'p', text: `근거: ${r.evidence}${r.duplicateOf ? ` · ${r.duplicateOf}에 연결된 요청` : ''}` })
  const spec = s.specs.find((x) => x.id === (r.spec ?? s.requests.find((y) => y.id === r.duplicateOf)?.spec))
  const tasks = spec ? s.tasks.filter((t) => t.spec === spec.id) : []
  const prs = s.prs.filter((p) => tasks.some((t) => t.id === p.task))
  const thread = spec ? s.threads.find((t) => t.on === spec.id) : undefined
  const decisions = spec ? s.decisions.filter((x) => x.spec === spec.id) : []
  const incidents = spec ? s.incidents.filter((i) => i.candidates.some((c) => c.spec === spec.id)) : []
  if (spec) {
    out.push({ kind: 'h2', text: `기획 문서 ${spec.id} · ${spec.title} (${spec.status})` })
    out.push({ kind: 'p', text: `문제: ${spec.problem} / 목표: ${spec.goal}` })
    for (const p of spec.paras) out.push({ kind: 'ref', text: `${p.id} ${p.kind === 'req' ? '요구사항' : '완료 기준'} · ${p.text}${p.warn ? ` · 경고 ${p.warn.card}` : ''}${p.incident ? ` · 장애 ${p.incident}` : ''}` })
  }
  if (tasks.length) {
    out.push({ kind: 'h2', text: '작업·PR' })
    for (const t of tasks) out.push({ kind: 'ref', text: `${t.id} ${t.title} · ${t.state}` })
    for (const p of prs) out.push({ kind: 'ref', text: `${p.id} ${p.title} · 검수 ${p.reviews[p.reviews.length - 1]} · ${p.merged ? '머지됨' : '열림'}${p.auto ? ' (자동)' : ''}` })
  }
  if (thread?.messages.length || decisions.length) {
    out.push({ kind: 'h2', text: '대화·결정' })
    for (const m of thread?.messages ?? []) out.push({ kind: 'p', text: `${m.who}: ${m.text}` })
    for (const x of decisions) out.push({ kind: 'ref', text: `${x.id} 결정 · ${x.text} · 이유 ${x.reason} · 대안 ${x.alternatives}` })
  }
  if (incidents.length) {
    out.push({ kind: 'h2', text: '장애 연결' })
    for (const i of incidents) out.push({ kind: 'ref', text: `${i.ctx} ${i.title} · 관련 PR ${i.candidates.map((c) => c.pr).join(', ')}${i.followUp ? ` · 재발 방지 ${i.followUp}` : ''}` })
  }
  out.push({ kind: 'h2', text: '남은 일' })
  if (spec?.question) out.push({ kind: 'todo', text: `질문 답 받기: ${spec.question}`, done: false })
  if (spec?.status === '초안') out.push({ kind: 'todo', text: `${spec.id} 기획서 확정 (질문 답 → 자동 확정 또는 AI 교차검증)`, done: false })
  for (const p of prs.filter((x) => !x.merged)) out.push({ kind: 'todo', text: `${p.id} 검수·머지 (기준 안이면 자동, 밖이면 교차검증)`, done: false })
  out.push({ kind: 'todo', text: `${DEPT_NAME[r.from]}에 결과 회신`, done: r.state === '완료' })
  return out.map((b, i) => ({ ...b, id: `b${i + 1}` }))
}

function talkAndDocs(s: State, a: Action): State | null {
  switch (a.type) {
    case 'postMessage': {
      const th = s.threads.find((t) => t.id === a.thread)
      if (!th || !a.text.trim()) return s
      const t = now()
      const ctx = th.on ? (s.requests.find((r) => r.spec === th.on)?.ctx ?? `thread:${th.id}`) : `thread:${th.id}`
      return {
        ...s,
        seq: th.peer ? s.seq + 1 : s.seq,
        threads: s.threads.map((x) => (x.id === th.id ? { ...x, messages: [...x.messages, { who: '노예진', dept: 'planning', text: a.text.trim(), t }] } : x)),
        activity: th.peer
          ? [...s.activity, { t, ctx, from: 'planning', to: th.peer, point: 'message', kind: 'A2A', text: a.text.trim() }]
          : s.activity,
      }
    }
    case 'deptChat': {
      const pool = DEPT_CHAT[a.from]
      const th = s.threads.find((t) => t.peer === a.from)
      if (!pool || !th) return s
      const seq = s.seq + 1
      const t = now()
      const text = pool[th.messages.filter((m) => m.dept === a.from).length % pool.length]
      return {
        ...s,
        seq,
        threads: s.threads.map((x) => (x.id === th.id ? { ...x, messages: [...x.messages, { who: `${DEPT_NAME[a.from]} 에이전트`, dept: a.from, text, t }] } : x)),
        activity: [...s.activity, { seq, t, ctx: `thread:${th.id}`, from: a.from, to: 'planning', point: 'message', kind: 'A2A', text }],
      }
    }
    case 'extractDecision': {
      const th = s.threads.find((t) => t.id === a.thread)
      const m = th?.messages[a.index]
      if (!th || !m || m.decision) return s
      const id = `D-${s.decisions.length}`
      return {
        ...s,
        threads: s.threads.map((x) => (x.id === th.id ? { ...x, messages: x.messages.map((y, i) => (i === a.index ? { ...y, decision: id } : y)) } : x)),
        decisions: [...s.decisions, { id, spec: th.on || '-', text: m.text, reason: `${m.who} 의견 (${th.title})`, alternatives: '-', from: `소통 ${th.title}`, sentToHr: true }],
        activity: [
          ...s.activity,
          { t: now(), ctx: `thread:${th.id}`, from: 'planning', to: 'planning', point: 'extract_decision', kind: 'A2A', grade: 'A', text: `${th.title} 메시지에서 결정 ${id} 추출` },
          { t: now(), ctx: `thread:${th.id}`, from: 'planning', to: 'hr', point: 'record_case', kind: 'A2A', grade: 'A', text: `① kind decision_extracted · ${id}` },
        ],
      }
    }
    case 'newDoc': {
      const id = `DOC-${s.docs.length + 1}`
      return { ...s, docs: [...s.docs, { id, title: a.title || '제목 없는 문서', updated: now(), blocks: [{ id: 'b1', kind: 'p', text: '' }] }] }
    }
    case 'gatherDoc': {
      const r = s.requests.find((x) => x.id === a.about)
      if (!r) return s
      const id = `DOC-${s.docs.length + 1}`
      const blocks = gatherBlocks(s, a.about)
      return {
        ...s,
        docs: [...s.docs, { id, title: `${r.id} 관련 정리 · ${r.title}`, about: r.id, updated: now(), blocks, by: a.by ?? 'human' }],
        activity: [...s.activity, { t: now(), ctx: r.ctx, from: a.by === 'ai' ? 'planning' : 'human', to: 'planning', point: 'gather_doc', kind: a.by === 'ai' ? 'A2A' : '사람', grade: 'read', text: `${a.by === 'ai' ? 'AI 어시스턴트 요청으로 ' : ''}${r.id}에 걸린 문서·작업·PR·대화·결정을 ${id}로 정리 (블록 ${blocks.length}개)` }],
      }
    }
    case 'renameDoc':
      return patchDoc(s, a.doc, (d) => ({ ...d, title: a.title }))
    case 'addBlock':
      return patchDoc(s, a.doc, (d) => ({ ...d, blocks: [...d.blocks, { id: nextBlockId(d), kind: a.kind, text: a.text ?? '', done: a.kind === 'todo' ? false : undefined }] }))
    case 'editBlock':
      return patchDoc(s, a.doc, (d) => ({ ...d, blocks: d.blocks.map((b) => (b.id === a.block ? { ...b, text: a.text } : b)) }))
    case 'toggleTodo':
      return patchDoc(s, a.doc, (d) => ({ ...d, blocks: d.blocks.map((b) => (b.id === a.block ? { ...b, done: !b.done } : b)) }))
    case 'removeBlock':
      return patchDoc(s, a.doc, (d) => ({ ...d, blocks: d.blocks.filter((b) => b.id !== a.block) }))
  }
  return null
}

function inner(s: State, a: Action): State {
  const talked = talkAndDocs(s, a)
  if (talked) return talked
  switch (a.type) {
    case 'inbound':
      return inbound(s, a.kind)
    case 'advance':
      return step(s)
    case 'send':
      return { ...s, seq: s.seq + 1, activity: [...s.activity, { t: now(), ctx: 'ctx_req_0012', from: 'planning', to: a.to, point: a.point, kind: 'A2A', text: a.text }] }
    case 'markRead':
      return { ...s, outbox: s.outbox.map((m) => (m.to === a.to ? { ...m, read: true } : m)) }
    case 'answerQuestion': {
      const spec = s.specs.find((x) => x.id === a.spec)
      if (!spec || !spec.question) return s
      const req = s.requests.find((r) => r.id === spec.request)
      const special = spec.id === 'SPEC-7'
      const who = special ? '박지아 (고객지원)' : `${DEPT_NAME[req?.from ?? 'support']} ${a.agent ? '에이전트' : '담당'}`
      const ctx = req?.ctx ?? 'ctx_req_0012'
      const id = `D-${s.decisions.length}`
      const th = s.threads.map((t) =>
        t.on === a.spec ? { ...t, messages: [...t.messages, { who, dept: (req?.from ?? 'support') as Dept, text: a.text, decision: id }] } : t,
      )
      const answered: State = {
        ...s,
        threads: th,
        specs: s.specs.map((x) => (x.id === a.spec ? { ...x, question: undefined, paras: special ? x.paras.map((p) => (p.id === 'R1' ? { ...p, text: '결제 실패 시 장바구니 상태 보존 (품절 상품은 남기고 품절 표시)' } : p)) : x.paras } : x)),
        decisions: [...s.decisions, special
          ? { id, spec: a.spec, text: '품절 상품은 장바구니에 남기고 품절 표시', reason: '고객지원 의견: 다시 담는 불편이 더 큼', alternatives: '품절 상품 자동 삭제', from: '스레드 TH-7', sentToHr: true }
          : { id, spec: a.spec, text: `범위: ${a.text}`, reason: `${who} 답변`, alternatives: '동작까지 변경', from: `스레드 ${s.threads.find((t) => t.on === a.spec)?.id ?? '-'}`, sentToHr: true }],
        activity: [
          ...s.activity,
          { t: now(), ctx, from: 'planning', to: 'planning', point: 'extract_decision', kind: 'A2A', grade: 'A', text: `스레드에서 결정 ${id} 추출, 질문 닫음 → 확정 조건 다시 확인` },
          { t: now(), ctx, from: 'planning', to: 'hr', point: 'record_case', kind: 'A2A', grade: 'A', text: `① kind decision_extracted · ${id} (결정·이유·대안)` },
        ],
      }
      return trySpecAuto(answered, a.spec)
    }
    case 'generatePrs': {
      const spec = s.specs.find((x) => x.id === a.spec)
      if (!spec || spec.status !== '확정') return s
      return genPrs(s, a.spec)
    }
    case 'reviewPr': {
      const pr = s.prs.find((p) => p.id === a.pr)
      if (!pr || pr.merged || pendingReview(s, pr.id)) return s
      const ctx = prInfo(s, pr).ctx
      const next: State = {
        ...s,
        prs: s.prs.map((p) => (p.id === a.pr ? { ...p, reviews: [...p.reviews, '통과'] } : p)),
        tasks: s.tasks.map((t) => (pr.task === t.id ? { ...t, state: '머지 대기' } : t)),
        activity: [...s.activity, { t: now(), ctx, from: 'planning', to: 'planning', point: 'review_pr', kind: 'A2A', grade: 'A', text: `${a.pr} 수정 커밋 후 재검수 통과` }],
      }
      return tryMerge(next, a.pr)
    }
    case 'acceptDraft': {
      const r = s.requests.find((x) => x.id === a.request)
      if (!r || r.state !== '접수') return s
      return draftSpecFor(s, r, true)
    }
    case 'writeSpec': {
      const r = s.requests.find((x) => x.id === a.request)
      if (!r || s.specs.some((x) => x.id === r.spec || x.request === r.id)) return s
      return draftSpecFor(s, r, false)
    }
    case 'decideReview':
      return decideReview(s, a.review)
    case 'humanDecide':
      return humanDecide(s, a.review, a.ok)
    case 'disableRule':
      return disableRule(s, a.rule)
    case 'decideRule':
      return decideRule(s, a.rule, a.ok)
    default:
      return s
  }
}

// ---- 기획개발의 연결 지점 대장 (연동 화면). 공통 구성안 4·8·9장 + 수정 요청 기준
export interface Port {
  point: string
  dir: 'in' | 'out'
  peer: Dept
  kind: Activity['kind']
  grade: 'A' | 'B' | 'C' | 'read' | '-'
  priority: 'required' | 'recommended' | 'optional'
  what: string
}
export const PORTS: Port[] = [
  { point: 'find_related_prs', dir: 'in', peer: 'infra', kind: 'A2A', grade: 'read', priority: 'required', what: '장애 시각 직전 머지 PR·문단 후보 회신 (원인 판정 안 함)' },
  { point: 'find_related_prs', dir: 'in', peer: 'data', kind: 'A2A', grade: 'read', priority: 'required', what: '이상 구간 직전 배포가 있었나' },
  { point: 'link_incident', dir: 'in', peer: 'orchestrator', kind: 'A2A', grade: 'A', priority: 'required', what: '사건을 PR·문단에 연결, 재발 방지 요청 등록. 자동 머지 PR이면 그 기준을 바로 해제' },
  { point: 'verify_rollback_target', dir: 'in', peer: 'safety', kind: 'A2A', grade: 'read', priority: 'required', what: 'infra rollback_deployment(B) 교차검증 승인·거절' },
  { point: 'cross_review.result', dir: 'in', peer: 'safety', kind: 'A2A', grade: 'read', priority: 'required', what: '기획서 확정·PR 머지 교차검증 결과 (검증자는 기획개발이 아닌 부서)' },
  { point: 'incident.opened / resolved', dir: 'in', peer: 'orchestrator', kind: '이벤트', grade: '-', priority: 'required', what: '사건 개시·종료 알림' },
  { point: 'create_ticket', dir: 'in', peer: 'support', kind: 'A2A', grade: 'A', priority: 'recommended', what: '변경 요청 등록, 같은 요청은 연결 (모든 부서가 호출 가능)' },
  { point: 'get_request_status', dir: 'in', peer: 'support', kind: 'A2A', grade: 'read', priority: 'recommended', what: '요청 부서가 처리 상태 조회' },
  { point: 'ticket_draft_propose → review_ticket_draft', dir: 'in', peer: 'hr', kind: 'A2A', grade: 'A', priority: 'optional', what: '③ 인사운영 개선 초안 수신, 채택 결정' },
  { point: '개선 초안 (원인 추적 루프 ③)', dir: 'in', peer: 'data', kind: 'A2A', grade: 'A', priority: 'optional', what: '반복 지표 이상 → 개선 요청 초안' },
  { point: 'list_in_progress', dir: 'in', peer: 'hr', kind: 'A2A', grade: 'read', priority: 'optional', what: '④ 담당자의 진행 중 문서·작업·PR 조회 (인수인계)' },
  { point: 'record_case', dir: 'out', peer: 'hr', kind: 'A2A', grade: 'A', priority: 'recommended', what: '① spec_confirmed · pr_merged · decision_extracted · request_closed' },
  { point: 'check_plan', dir: 'out', peer: 'hr', kind: 'A2A', grade: 'read', priority: 'recommended', what: '② 기획서 생성·보완 때 기각 안 질의' },
  { point: 'execute(generate_pr)', dir: 'out', peer: 'safety', kind: 'execute', grade: 'B', priority: 'recommended', what: '코드 규칙 검증 pr_changed_lines·files·allowed_paths, 초과면 C 보류' },
  { point: 'cross_review.request', dir: 'out', peer: 'safety', kind: 'execute', grade: 'B', priority: 'required', what: '자동 조건·기준 밖인 기획서 확정·PR 머지를 다른 부서 검증자에게 (자기 승인 금지)' },
  { point: 'human_check.request', dir: 'out', peer: 'safety', kind: 'execute', grade: 'C', priority: 'required', what: '되돌릴 수 없는 일(결제 데이터 삭제·개인정보 노출·실제 송금)은 사람 확인 대기' },
  { point: 'github_create_pull', dir: 'out', peer: 'shop', kind: 'API', grade: '-', priority: 'recommended', what: 'gen/ 브랜치 PR, 라벨 generated-pr' },
  { point: 'execute(merge_pr)', dir: 'out', peer: 'safety', kind: 'execute', grade: 'B', priority: 'recommended', what: '학습 기준 안이면 자동 머지, 교차검증 승인 뒤 자동 머지 (보호 경로·되돌릴 수 없는 일은 사람)' },
  { point: 'auto_policy.learned / updated', dir: 'out', peer: 'safety', kind: '이벤트', grade: '-', priority: 'optional', what: '학습 기준 등록·정식 전환·해제를 기준표에 반영' },
  { point: 'memory_search_card', dir: 'out', peer: 'memory', kind: 'API', grade: 'read', priority: 'recommended', what: '기획서 근거 검색 (과거 기획서·결정·문의)' },
]

// ---- 시드: PR #29(재발 방지 카나리 배포)는 자동 처리 기준 밖이고 장애가 났던 경로라 교차검증 대기 1건으로 시작한다
{
  const seed = prReviewOf(initialState, 'PR #29', 'XR-1', '10-22 10:25')
  if (seed) initialState.reviews = [seed]
}

// 사람 클릭 없이 다음 단계로: 액션마다 더 진행할 게 없을 때까지 돌린다.
// 멈추는 곳은 사람 확인(되돌릴 수 없는 일)·교차검증 거절·검수 불일치 2회뿐이다
// 기획서 질문에 요청 부서 에이전트가 보내는 답 (다른 부서 시뮬레이터가 보낸다)
export const DEPT_ANSWER: Partial<Record<Dept, string>> = {
  support: '반복 문의가 난 동작까지 고쳐 주세요. 문구만 바꾸면 같은 문의가 계속 와요.',
  marketing: '판매 화면 표시만 바꿔 주세요. 공지는 머지 뒤 저희가 올릴게요.',
  hr: '운영 매뉴얼의 해당 절만 고쳐 주세요. 다른 절은 그대로 둡니다.',
  data: '이상이 반복된 시간대의 원인 경로까지 고쳐 주세요. 지표는 머지 뒤 저희가 다시 봅니다.',
  finance: '보관 기한이 지난 건만 대상입니다.',
}
function step(s: State): State {
  const moved = (n: State) => n !== s
  for (const r of s.requests) {
    if (r.state !== '접수' || r.spec || r.duplicateOf) continue
    const req = { ...r, flow: true }
    const flowing = { ...s, requests: s.requests.map((x) => (x.id === r.id ? req : x)) }
    if (r.auto?.ok) {
      return writeDirectSpec(flowing, req, [
        { id: 'R1', kind: 'req', text: r.title },
        { id: 'C1', kind: 'done', text: '바뀐 화면에서 요청한 표시가 그대로 보인다' },
      ], '화면 표시를 요청대로 고친다 (동작 변경 없음)', [`${r.id} (${DEPT_NAME[r.from]})`, '간단 요청 규칙 통과']).s
    }
    return draftSpecFor(flowing, req, !r.via.startsWith('create_ticket'))
  }
  for (const sp of s.specs) {
    const r = s.requests.find((x) => x.id === sp.request)
    if (r?.flow && sp.status !== '확정' && !s.reviews.some((x) => x.ref === sp.id)) {
      const n = trySpecAuto(s, sp.id)
      if (moved(n)) return n
    }
    // PR 만들기는 요청 출처와 상관없이 확정된 기획서면 언제나 이어 간다 (시드 요청·사람 확정 건 포함)
    if (sp.status === '확정' && s.tasks.some((x) => x.spec === sp.id && x.state === '진행 중' && !s.prs.some((p) => p.task === x.id))) return genPrs(s, sp.id, false)
  }
  for (const p of s.prs) {
    if (p.merged || pendingReview(s, p.id)) continue
    const last = p.reviews[p.reviews.length - 1]
    const rejected = s.reviews.filter((x) => x.ref === p.id && x.result === '거절').length
    if (last === '통과' && !rejected) {
      const n = tryMerge(s, p.id)
      if (moved(n)) return n
    }
    // 교차검증 거절은 수정 커밋 후 한 번 자동으로 다시 올린다. 두 번 거절되면 사람에게 남긴다
    if (last === '통과' && rejected === 1) return inner(s, { type: 'reviewPr', pr: p.id })
    if (last === '불일치' && p.reviews.filter((x) => x === '불일치').length < 2) return inner(s, { type: 'reviewPr', pr: p.id })
  }
  const o = observeStep(s)
  if (moved(o)) return o
  return proposeStep(s)
}
// 화면·버스가 다음 칸을 보낼지: 더 진행할 게 있으면 true
export const canAdvance = (s: State) => step(s) !== s
function advance(s: State): State {
  let cur = s
  for (let i = 0; i < 40; i++) {
    const next = step(cur)
    if (next === cur) return cur
    cur = next
  }
  return cur
}
Object.assign(initialState, advance(initialState))
