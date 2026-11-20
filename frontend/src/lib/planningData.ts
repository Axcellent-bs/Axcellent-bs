// 기획(planning)·개발(dev) 화면이 쓰는 상태 타입·액션·공용 상수.
// 상태는 기획·개발 서비스의 /api/planning/... 응답으로 채운다 (lib/planning.ts).
// 부서 간 연결 지점 이름은 공통 구성안(agent-common)과 10/22 부서별 수정 요청을 따른다.
//
// 승인 방식 (10/23 결정): "사람의 사전 승인" 대신 "AI 교차검증 + 사람 사후 감사".
//   - 기획서: 질문 없음 + 인사 기각 경고 없음 + 근거 있음 → 자동 확정. 아니면 AI 교차검증 대기열.
//   - PR: 검수 통과 + 학습된 기준(경로·줄·파일·경고 없음) 안 → 자동 머지. 아니면 AI 교차검증 대기열.
//   - 교차검증은 기획·개발이 아닌 쪽(안전 레이어가 다른 부서로 보냄)이 판단한다. 기획·개발은 자기 건을 승인하지 않는다.
//   - 교차검증 뒤 머지 → 관찰 기간 장애·revert 없음 → 같은 경로 N건이면 기준 제안·백테스트 → 안전 장치 안 안전 관문(코드 규칙), 밖 사람(태윤) 승인 → 정식.
//   - 되돌릴 수 없는 일(IRREVERSIBLE)은 교차검증 대신 항상 사람이 확인한다.
//   - 사람은 사후 감사 화면에서 AI가 스스로 승인한 건을 보고, 기준을 바로 끌 수 있다.
import type { Proposal, ProposalAction } from './proposals'

export type Dept =
  | 'planning' | 'dev' | 'hr' | 'archive' | 'support' | 'marketing' | 'data' | 'finance'
  | 'infra' | 'orchestrator' | 'safety' | 'shop' | 'memory' | 'human'

export const DEPT_NAME: Record<Dept, string> = {
  planning: '요청·기획서', dev: '개발·배포', hr: '인사', archive: '아카이브', support: '고객지원', marketing: '마케팅', data: '데이터분석',
  finance: '재무/정산', infra: '인프라(DejaFix)', orchestrator: '총괄 에이전트', safety: '안전 레이어',
  shop: '판매 사이트', memory: '회사 기억', human: '사람',
}

export type ReqState = '접수' | '기획 중' | '작업 중' | '완료' | '중복 연결' | '기각'
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
  related?: RelatedReq[] // 비슷한·충돌하는 요청 (제안, 묶기·보류는 사람)
  paused?: boolean // 사람이 중단함: 재개 전까지 다음 단계로 가지 않는다
}

/** 비슷한·충돌하는 요청 (백엔드 related.py). by: llm이 판단 / embed·lexical은 검색만 / path는 열린 PR과 같은 파일 */
export interface RelatedReq {
  id: string
  title: string
  score: number
  relation: 'same' | 'conflict' | 'update' | 'related'
  reason: string
  by: 'llm' | 'embed' | 'lexical' | 'path'
}
/** 충돌을 먼저, 그다음 같은 일·이어서 바꿈·비슷함 순으로 */
export const byRelation = (xs: RelatedReq[] = []) => [...xs].sort((a, b) => ORDER[a.relation] - ORDER[b.relation])
const ORDER: Record<RelatedReq['relation'], number> = { conflict: 0, same: 1, update: 2, related: 3 }
export const RELATION_LABEL: Record<RelatedReq['relation'], string> = { same: '같은 일', conflict: '충돌 가능', update: '이어서 바꿈', related: '비슷함' }

export interface Para {
  id: string // R1, C1 …
  kind: 'req' | 'done'
  text: string
  warn?: { card: string; text: string } // 인사총무 ② check_plan 결과
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
  origin?: string // 'dev_team' = 개발팀이 직접 넣은 기획
  hrCheck?: 'ok' | 'warn' | 'unknown' // 인사 ② check_plan 실제 결과 (없으면 묻지 않음)
  bodyMd?: string // 자유 작성 칸 (마크다운). 유사·충돌 판단·인사 확인에도 쓴다
  extra?: Record<string, string> // 양식에만 있는 칸 (lib/specTemplate.ts)
}

export type TaskState = '진행 중' | 'PR 검수' | '머지 대기' | '완료' | 'C 보류' | '중단' // 중단: 사람 중단·돌려보냄·폐기 (서버는 이미 쓴다)
export interface Task {
  id: string
  spec: string
  para: string
  title: string
  owner: string
  state: TaskState
  coding?: CodingRun // 코딩 에이전트 실행 상태 (dev 서비스)
  discarded?: boolean // 사람이 폐기함 (멈춘 작업만). 작업 목록에서 숨길 수 있다
  discardedBy?: string | null
  discardedAt?: string | null
}

// 코딩 에이전트 한 번의 실행. SSE로 단계가 바뀐다
export type CodingStatus = 'queued' | 'claimed' | 'reading' | 'writing' | 'reviewing' | 'opened' | 'merged' | 'waiting_human' | 'failed' | 'canceled' | 'covered' | 'returned'
// 작업을 누가 코딩하나: 서버 AI(API 키) · 개발자 본인 Claude(구독, MCP로 가져감)
export type CodingMode = 'agent' | 'subscription'
export interface CodingFile { path: string; additions: number; deletions: number; reason: string }
export interface CodingRun {
  status: CodingStatus
  model: string | null
  files: CodingFile[]
  summary: string | null
  review: { verdict: '통과' | '불일치'; notes: string[] } | null
  error: string | null
  attempts: number
  updatedAt?: string
  pr?: string | null
  instruction?: string | null // 사람이 대기 중인 PR에 준 수정 지시
  skipQueue?: boolean // '지금 진행': 같은 파일 작업이 있어도 기다리지 않음
  waitingFor?: string[] // 작업 대기열: 같은 파일을 먼저 고치는 앞 작업
  mergeConflict?: boolean // 머지 충돌: 다시 시도하면 PR을 닫고 최신 코드로 다시 씀
  picked?: string[] // 사람이 직접 고른 고칠 파일 (AI가 고르지 않음)
  transient?: boolean // 잠깐 생긴 오류로 멈춤 (에이전트가 잠시 뒤 스스로 다시 함)
  autoRetries?: number // 에이전트가 스스로 다시 한 횟수
  risk?: 'low' | 'medium' | 'high' | null // 바뀐 파일로 정한 위험도 (낮음: 리뷰 생략 · 높음: 사람 확인 후 머지)
  riskWhy?: string | null
  llmCalls?: number // 이 작업이 지금까지 부른 LLM 횟수 (다시 시도 포함)
  llmTokens?: number // 이 작업이 지금까지 쓴 토큰 (입력 + 출력)
  mode?: CodingMode
  connection?: string | null // 올릴 레포 연결
  claimedBy?: string | null // 구독형: 가져간 사람
  textWarnings?: string[] // 작업 글에 에이전트를 노린 지시로 보이는 것 (있으면 사람 확인 뒤 가져간다)
  textOkBy?: string | null // 작업 글을 확인한 사람
  assignee?: string | null // 구독형: 맡긴 사람 (그 사람의 Claude만 가져간다)
  holdKey?: string | null // 실행 관문이 PR 열기(generate_pr)를 보류한 키
  coveredBy?: string[] // covered: 이 작업을 이미 반영한 앞 작업 PR (PR 없이 완료)
  ciHold?: boolean // CI 실패로 사람 확인: 서버가 CI를 다시 보고 통과하면 자동 머지를 다시 판단
  returnedTo?: string | null // 코드 변경이 아니라고 본 일의 맞을 것 같은 부서 (returned·사람 확인)
  returnedReason?: string | null
  holdReview?: string | null // 그 보류 건 (사람이 승인하면 같은 키로 PR을 연다)
  claimedAt?: string | null
  leaseUntil?: string | null // 이때까지 진행 보고가 없으면 대기열로 돌아간다
  progress?: string | null // 구독형: 개발자 Claude가 마지막으로 보고한 진행 글
}

export interface PR {
  id: string
  task: string
  title: string
  branch: string
  reviews: ('통과' | '불일치')[]
  gate: { result: '통과' | 'C 보류'; lines: number; files: number; paths: string }
  merged: boolean
  closed?: boolean // 머지 없이 닫힘 (사람이 중단·형상관리에서 닫음)
  auto?: boolean // 사람 클릭 없이 머지됨 (기준 또는 교차검증)
  mergedBy?: 'rule' | 'review' | 'human'
  rule?: string // 자동 머지에 쓴 기준 (AR-n)
  target?: RepoTarget // 레포가 2개 이상일 때 AI가 고른 대상 레포 (사람 승인 전에는 PR을 열지 않는다)
  url?: string | null // 형상관리(깃허브)에 열린 실제 PR 주소
  vcsNumber?: number | null // 형상관리 변경 번호 (레포 PR 화면으로 잇는다)
  crossEvidence?: CrossEvidence[] // 마지막 교차검증에서 다른 부서에 받은 근거 (통과·머지 뒤에도 남음)
  crossCheckedAt?: string | null
}

// PR을 올릴 레포. 연결 1개면 자동 승인, 2개 이상이면 AI 제안 → 사람 승인
export interface RepoTarget {
  connectionId: string
  repo: string
  status: 'proposed' | 'approved' | 'rejected'
  confidence: 'high' | 'mid' | 'low'
  reasons: string[] // "AI: "로 시작하면 LLM 판단
  candidates: { connectionId: string; repo: string; score: number }[]
  decidedBy: string | null
  decidedAt: string | null
}

export interface Message { who: string; dept: Dept; text: string; decision?: string; t?: string }
// 소통 채널. spec = 문서에 붙는 스레드, team = 기획팀 채널, dept = 다른 부서와 주고받는 채널
export interface Thread { id: string; on: string; title: string; messages: Message[]; kind?: 'spec' | 'team' | 'dept'; peer?: Dept }

// 정리 문서: 요청·기획서·작업·PR·대화·결정을 한 장으로 모으고 사람이 고친다
export interface DocBlock { id: string; kind: 'h2' | 'p' | 'todo' | 'ref'; text: string; done?: boolean }
export interface Doc { id: string; title: string; about?: string; updated: string; created?: string; blocks: DocBlock[]; by?: 'ai' | 'human' }

export interface Decision { id: string; spec: string; text: string; reason: string; alternatives: string; from: string; sentToHr: boolean; hrRecord?: 'ok' | 'failed' | 'held' }

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

// 기획·개발 → 다른 부서로 나간 메시지·데이터 (다른 부서 페이지의 "기획에서 받은 것")
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
  summary: string // 기획·개발 AI가 정리한 근거
  recommend?: '승인' | '거절' // 기획·개발 AI의 추천 (결정 아님)
  recommendWhy?: string
  checks: RuleCheck[] // 코드 규칙 검사 결과
  verifier: string // 판단하는 쪽 (기획·개발이 아님)
  result?: '승인' | '거절'
  resultWhy?: string
  decidedAt?: string
  at?: string // 대기 시작 (ISO, 서버 대기열에서 옴). 없으면 t로 계산
  evidence?: CrossEvidence[] // 다른 부서에서 받은 교차검증 근거
}

/** 교차검증 근거 한 줄 (백엔드 cross_check.EvidenceLine). ok가 false면 걸렸거나 못 모음 */
export interface CrossEvidence {
  source: 'orchestrator' | 'data' | 'planning'
  label: string
  value: string
  ok: boolean
}
export const EVIDENCE_SOURCE: Record<CrossEvidence['source'], string> = { orchestrator: '총괄 AI', data: '데이터분석', planning: '개발 AI' }

// 자동 처리하지 않은 이유 (safety thresholds.toml 규칙 키 → 화면 이름)
export const RULE_LABEL: Record<string, string> = {
  pr_changed_lines: '300줄 초과',
  pr_changed_files: '20파일 초과',
  pr_allowed_paths: '허용 경로 밖',
  pr_review_mismatch: '리뷰 불일치 2회',
  pr_linked_incident: '장애 났던 경로',
  irreversible: '되돌릴 수 없는 영역',
  merge_cross_active_incident: '진행 중인 사건과 겹침',
  merge_cross_data_verdict: '최근 이상 판정',
  merge_cross_recent_incident: '최근 7일 같은 경로 장애',
  merge_cross_evidence_missing: '근거 못 모음',
  spec_open_question: '열린 질문',
  spec_rejected_warning: '경고',
  spec_no_basis: '근거 없음',
  spec_open_conflict: '충돌하는 요청 진행 중',
  spec_hr_unchecked: '인사 기록 확인 못 함',
  request_paused: '요청이 사람 중단 중', // 개발 서버가 중단 중인 요청의 PR 자동 머지를 멈출 때 (dev service _try_auto_merge)
}

// 교차검증 대기를 사람 확인으로 넘기기까지 (분). 백엔드 PLANNING_CROSS_CHECK_TIMEOUT_MIN 기본값과 같다
export const CROSS_CHECK_TIMEOUT_MIN = 10

// ---- 학습된 자동 처리 기준 (#191, 재현·태윤 조건). 학습 근거는 검토자 승인이 아니라 "머지 뒤 장애·revert 없음 + 관찰 기간".
// 같은 범위에서 관찰을 통과한 머지가 OBSERVE_N건 모이면 기준을 제안하고, 넓히는 쪽은 safety 안전 장치 안이면 안전 관문(코드 규칙)
// (execute(auto_policy_update), 재무는 의견만 낸다), 밖이면 사람(태윤)이 승인한다. 좁히는 쪽(해제)은 장애·revert 연결 즉시 자동.
export interface AutoRule {
  id: string // AR-n
  action: ReviewTarget
  path: string // 경로 접두사
  lines: number // 변경 줄 상한
  files: number // 변경 파일 상한
  noWarn: boolean // 인사 기각 경고가 없는 문단만
  status: '승인 대기' | '정식' | '거절' | '해제됨'
  approver: '안전 관문' | '사람 (태윤)' // 넓히기 승인 주체. 재무는 의견만 내고 승인하지 않는다
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
export const GUARD = { lines: 100, files: 5 } // safety 안전 장치: 이 안이면 안전 관문(코드 규칙)이, 밖이면 사람이 승인
// safety 보호 경로 (#203 merge_protected_paths, 시연 경로 포함). 닿으면 항상 사람 확인이고 학습으로도 풀 수 없다
export const PROTECTED_PATHS = ['shop-web/checkout/', 'shop-web/auth/', 'shop-web/api/', 'shop-api/db/', 'shop-api/service/', 'shop-api/promotions/', 'shop-api/payment/', '.github/', 'safety/', 'contracts/']
export const protectedOf = (path: string) => PROTECTED_PATHS.find((x) => path.startsWith(x))
export interface Observation { pr: string; req: string; path: string; lines: number; files: number; mergedAt: string; untilMs: number; result: '관찰 중' | '정상' | '장애 연결' }
export interface AutoRun { pr: string; req: string; t: string; lines: number; files: number; result: '정상' | '장애 연결'; rule?: string }
export interface AutoPolicy {
  rules: AutoRule[] // 경로별 학습 기준: 백엔드에 아직 없어 늘 비어 있다 (화면은 준비 중)
  observing: Observation[] // 교차검증 뒤 머지된 PR의 관찰 (학습 근거 후보). 백엔드에 아직 없다
  history: AutoRun[]
  log: { t: string; text: string; by: '사람' | '자동' | '안전 관문' }[]
  // 백엔드 AutoPolicy (workflow-core models.py): 단계·허용 경로·연속 정상·승급 제안
  level?: number
  paths?: string
  streak?: number
  proposal?: AutoProposal | null
}
/** 연속 정상이 기준에 닿아 서버가 낸 한 단계 승급 제안 (승인은 운영자, approve_promotion) */
export interface AutoProposal { to: number; t: string; why: string }

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
  chats?: ChatThread[] // AI와 대화 (dev 서비스 SSE에 있을 때만)
  proposals?: Proposal[] // 기획 제안 (데이터분석·마케팅 인사이트 → 제안 → 마케팅)
}

// ---- AI와 대화: 개발 에이전트와 대화로 계획을 세우고, "진행해"면 기획에 요청으로 넘긴다.
// 대화는 개발 작업을 직접 띄우지 않는다. 기획에서 사람이 기획서를 확정하면 개발로 간다 (11/09)
export type ChatStatus = 'talking' | 'handed' | 'started' | 'blocked' | 'waiting' | 'answering' | 'closed'
export type ChatKind = 'text' | 'question' | 'plan' | 'started' | 'waiting' | 'handoff' | 'error'
export interface ChatPlan { title: string; plan: string; done: string[]; paths: string[] }

// ---- 데이터분석 1:1 대화 카드 (data_chat_get·data_chat_send 메시지의 card, 공통 계약).
// 서버 모양(snake_case) 그대로 둔다: 경쟁사 이름 표(competitor_names)의 키가 인스타 아이디라 바꾸면 안 된다 (lib/directChat.ts toThread)
export type ChatCardSource = 'request_text' | 'preset'
export interface TrendRequestCard {
  kind: 'trend_request'
  /** 의뢰 문장 그대로 (untrusted, 글자로만) */
  request_text: string
  brief: {
    product: string | null
    target_gender: 'f' | 'm' | null
    target_ages: string[]
    format: string | null
    competitors: string[]
    keywords: string[]
    /** 인스타 아이디 → 이름 (없으면 {}) */
    competitor_names: Record<string, string>
    sources: { product: ChatCardSource; target: ChatCardSource; format: ChatCardSource; competitors: ChatCardSource; keywords: ChatCardSource }
  }
  preset: string | null
  /** "인스타 약 45번·네이버 약 6번·AI 2번" */
  cost_note: string
  /** 의뢰문에서 칸을 못 뽑았을 때 이유 */
  brief_reason: string | null
  /** 이미 접수된 카드면 그 보고서 id (예전 응답은 없음). 탭 기억보다 서버 값이 우선 */
  accepted_report_id?: string | null
}
export interface TrendReportProgressCard { kind: 'trend_report_progress'; report_id: string }
export interface TrendReportDoneCard {
  kind: 'trend_report_done'
  report_id: string
  status: 'queued' | 'running' | 'done' | 'partial' | 'failed' | 'interrupted'
  summary_line: string | null
  /** 최대 3 */
  top_evidence: string[]
  /** "/data/trend#request?id=<id>" */
  report_path: string
}
/** 마케팅 1:1 대화: 올린 캐릭터(attached)와 그 이미지 시트·스토리보드 결과(result). 기록은 /marketing/creative-records/{id} */
export interface CreativeCard {
  kind: 'creative'
  record_id: string
  role: 'attached' | 'result'
  has_storyboard: boolean
}
export type ChatCard = TrendRequestCard | TrendReportProgressCard | TrendReportDoneCard | CreativeCard

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  at: string
  kind: ChatKind
  plan?: ChatPlan | null
  taskId?: string | null
  /** 데이터분석 1:1 대화 카드 (예전 메시지·다른 부서는 없음·null) */
  card?: ChatCard | null
}
export interface ChatThread {
  id: string
  title: string
  status: ChatStatus
  messages: ChatMessage[]
  taskId: string | null
  requestId?: string | null // 기획에 넘긴 요청 (REQ-…)
  connection: string | null
  waitingFor?: string[] // 끝나기를 기다리는 요청 (충돌하는 요청이 끝나면 시작)
  mode?: CodingMode // 이 대화가 띄울 작업을 누가 코딩하나
  account?: string | null // 개발 화면에서 대화를 연 계정 id
  createdAt: string
  updatedAt: string
}

// ---- 되돌릴 수 없는 일: 교차검증 대신 항상 사람이 확인한다 (기획서 확정·PR 머지 둘 다)
export const IRREVERSIBLE: { id: string; label: string; words: string[] }[] = [
  { id: 'payment_data_delete', label: '결제 데이터 삭제', words: ['결제 데이터 삭제', '결제 내역 삭제', '결제 기록 삭제', '주문 기록 삭제'] },
  { id: 'pii_exposure', label: '개인정보가 노출될 수 있는 변경', words: ['개인정보', '전화번호 전체', '주소 전체', '회원 정보 내보내기'] },
  { id: 'real_transfer', label: '실제 송금 연동', words: ['송금', '실계좌', '지급 연동'] },
]
export const irreversibleOf = (text: string) => IRREVERSIBLE.find((x) => x.words.some((w) => text.includes(w)))

/** 간단 요청 판정 결과 (서버가 붙여 보낸다) */
export interface AutoCheck { ok: boolean; reasons: string[] }
/** 자동 채우기 미리보기 (서버 preview 응답 모양) */
export interface FillPreview { problem: string; goal: string; reqs: string[]; done: string[]; answer: string; extra?: Record<string, string>; by: string }

export const activeRules = (p: AutoPolicy) => p.rules.filter((r) => r.status === '정식')
export const ruleText = (r: { path: string; lines: number; files: number; noWarn?: boolean }) => `${r.path} ${r.lines}줄·${r.files}파일 이하${r.noWarn === false ? '' : ' · 경고 없음'}`

// ---- 액션 (버튼 → 에이전트 skill → 다른 부서로 나가는 연결 지점)

// 실제 시각, KST 고정 (MM-DD HH:MM). 브라우저·버스가 어느 시간대에서 돌아도 같다.
const pad = (n: number) => String(n).padStart(2, '0')
const KST_MS = 9 * 60 * 60 * 1000
function stamp(d: Date): string {
  const k = new Date(d.getTime() + KST_MS)
  return `${pad(k.getUTCMonth() + 1)}-${pad(k.getUTCDate())} ${pad(k.getUTCHours())}:${pad(k.getUTCMinutes())}`
}
export const TODAY = stamp(new Date()).slice(0, 5)

export type InboundKind =
  | 'support_ticket' | 'marketing_ticket' | 'hr_draft' | 'data_draft'
  | 'data_query' | 'infra_query' | 'orch_incident' | 'safety_verify'
  | 'support_simple' | 'marketing_simple' | 'finance_irreversible'

export type Action =
  | ProposalAction
  | { type: 'inbound'; kind: InboundKind }
  | { type: 'send'; to: Dept; point: string; text: string }
  | { type: 'markRead'; to: Dept }
  | { type: 'answerQuestion'; spec: string; text: string; agent?: boolean }
  | { type: 'advance' } // 다음 단계 한 칸 (버스·화면이 일정 간격으로 보낸다)
  | { type: 'generatePrs'; spec: string }
  | { type: 'reviewPr'; pr: string }
  | { type: 'approveRepo'; pr: string; connection?: string } // connection 없으면 제안된 레포로 승인
  | { type: 'rejectRepo'; pr: string }
  | { type: 'acceptDraft'; request: string }
  | { type: 'writeSpec'; request: string }
  | { type: 'unlinkDuplicate'; request: string }
  | { type: 'rejectSpec'; spec: string; reason: string } // 확인 대기 기획서 거절 (승인은 humanDecide)
  | { type: 'deleteDoc'; doc: string } // 정리 문서 지우기
  | { type: 'importNotion'; url: string } // 노션 페이지를 문서로 가져오기
  | { type: 'pauseRequest'; request: string; reason?: string } // 사람이 요청을 중단
  | { type: 'resumeRequest'; request: string } // 중단한 요청 재개
  | { type: 'deleteSpec'; spec: string } // 확정 전 기획서 초안 지우기 (요청은 접수로)
  | { type: 'fillSpec'; spec: string; overwrite?: boolean } // 자유 작성으로 필수 칸 채우기 (소제목 + 기획 LLM)
  | { type: 'editSpec'; spec: string; title?: string; problem?: string; goal?: string; paras?: Record<string, string>; body_md?: string; reqs?: string[]; dones?: string[]; extra?: Record<string, string> } // 확정 전 기획서를 사람이 고침 (칸에서 벗어날 때 저장)
  | { type: 'sendReleaseOrder'; check: string; note?: string } // 배포 전 대조가 사람 확인이면 사람이 배포 지시
  // 교차검증·학습 기준·사람 확인
  | { type: 'decideReview'; review: string } // 시연: 검증자(다른 부서)가 판단
  | { type: 'mergePr'; pr: string } // 사람 머지 (서버 액션, 누구나)
  | { type: 'humanDecide'; review: string; ok: boolean } // 되돌릴 수 없는 일만
  | { type: 'disableRule'; rule: string } // 기준 끄기 (승인 없이 바로)
  | { type: 'decideRule'; rule: string; ok: boolean } // 기준 넓히기 판단 (안전 관문 또는 사람)
  | { type: 'approvePromotion' } // 자동 처리 범위 승급 제안 승인 (운영자 토큰, 백엔드 approve_promotion)
  | { type: 'rejectPromotion' } // 승급 제안 보류 (운영자 토큰, 연속 건수 다시 셈)
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
  // 개발이 돌려보낸 작업 (서버 returned_actions.py)
  | { type: 'returnedToMarketing'; task: string }
  | { type: 'returnedToDev'; task: string; reason: string }
  | { type: 'dismissReturned'; task: string }

/** 지운 문서가 있어도 번호가 겹치지 않게 가장 큰 번호 + 1 */
export const nextDocId = (s: State) => `DOC-${Math.max(0, ...s.docs.map((x) => Number(x.id.split('-')[1]) || 0)) + 1}`
// ---- 기획·개발 서비스의 연결 지점 대장 (연동 화면). 공통 구성안 4·8·9장 + 수정 요청 기준
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
  { point: 'cross_review.result', dir: 'in', peer: 'safety', kind: 'A2A', grade: 'read', priority: 'required', what: '기획서 확정·PR 머지 교차검증 결과 (검증자는 기획·개발이 아닌 부서)' },
  { point: 'incident.opened / resolved', dir: 'in', peer: 'orchestrator', kind: '이벤트', grade: '-', priority: 'required', what: '사건 개시·종료 알림' },
  { point: 'create_ticket', dir: 'in', peer: 'support', kind: 'A2A', grade: 'A', priority: 'recommended', what: '변경 요청 등록, 같은 요청은 연결 (여러 부서가 호출 가능)' },
  { point: 'get_request_status', dir: 'in', peer: 'support', kind: 'A2A', grade: 'read', priority: 'recommended', what: '요청 부서가 처리 상태 조회' },
  { point: 'ticket_draft_propose → review_ticket_draft', dir: 'in', peer: 'hr', kind: 'A2A', grade: 'A', priority: 'optional', what: '③ 인사총무 개선 초안 수신, 채택 결정' },
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
