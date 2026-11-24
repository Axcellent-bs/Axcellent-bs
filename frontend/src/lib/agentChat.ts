import { SCREEN_KEEP_OUTCOMES, chatLanes, closedTimes, isBackgroundRead, mergeClosed, requestClosedTimes } from "./commFilter";
import { DEV_HOLD_ACTIONS, FOLDED_HOLD_ACTIONS, MARKETING_HOLD_ACTIONS, SNS_HOLD_ACTIONS, SNS_HOLD_PAGE, PLANNING_HOLD_ACTION, SETTLEMENT_HOLD_ACTION, SUPPORT_DELETION_HOLD_ACTIONS, type ExecutionRecord } from "./finance";
import { COMM_OUTCOME_KO } from "./labels";
import { replyLogIds } from "./commReply.ts";
import { isRedeployFailed } from "./rollbackResult";
export type { Redeploy, RollbackView } from "./rollbackResult";
import type { CommLine, CommOutcome } from "./types";
import type { WorkRequestRow } from "./workRequests";
import type { RoomChatCard } from "./chatCards.ts";
import { foldSafety, type FoldLine } from "./safetyFold.ts";
import { chatMessageCount, timelineLanes } from "./quietLog.ts";

// AI 대화방: 총괄(orchestrator)의 이슈 기록, 안전 관문(safety)의 실행 기록, 공통 로그의 부서 간 소통(dashboard-api)을
// "누가 누구에게 무엇을 보냈나" 메시지로 바꾼다. 문장은 기록된 값(skill·상태·숫자·id)으로만 만들고
// 부서가 돌려준 자유 문장(summary·note·untrusted 등)은 화면에 옮기지 않는다. 화면 없는 순수 함수만 둔다.

// ---------------------------------------------------------------- 총괄 JSON (ax-backend orchestrator models.py view())
export type StepStatus = "pending" | "running" | "held" | "waiting_human" | "waiting_async" | "completed" | "failed" | "skipped" | "rejected_by_human" | "transfer_queued";

export interface OrchStep {
  step_id: string;
  skill: string;
  label: string;
  dept: string | null;
  status: StepStatus | string;
  attempts: number;
  note: string;
  grade: string | null;
  started_at: string | null;
  finished_at: string | null;
  transferred_to: string | null;
  reason_code: string | null;
  /** 서비스가 꺼져 담당 부서 할 일함으로 넘어간 일 번호 (status "waiting_human", 업무 요청 라우터) */
  handoff_task_id?: string | null;
  /** 승인 대기·사람 할 일함 대기(초). 단계 전체 시간에서 빼면 부서가 일한 시간이다 */
  approval_wait_seconds?: number | null;
  human_wait_seconds?: number | null;
  /** 이 단계가 보통 걸리는 시간(기준 시간, 초). 지금 값은 임시다. 없으면 기본 30초 */
  promised_seconds?: number | null;
  /** 긴 작업(비동기): 마지막으로 응답한 시각, 진행 알림 횟수, 응답 사이가 가장 길게 비었던 시간(초), 마지막 진행률·메모 */
  last_response_at?: string | null;
  heartbeats?: number;
  max_silence_seconds?: number | null;
  progress_percent?: number | null;
  progress_note?: string | null;
  async_task_id?: string | null;
  /** 부서가 돌려준 출력. 배포 되돌리기의 redeploy·handoff_steps는 rollbackView(./rollbackResult)로 좁혀 읽는다 */
  output: Record<string, unknown>;
}

export interface OrchIncident {
  context_id: string;
  kind: string;
  title: string;
  component: string;
  /** 같은 유형인지 가르는 값 "구성요소:지표" (예: payment:error_rate_5xx). 지표를 모르면 null, 예전 응답은 없다 */
  event_type?: string | null;
  status: "open" | "resolved" | "degraded" | string;
  opened_at: string | null;
  closed_at: string | null;
  steps: OrchStep[];
  events: { at: string | null; message: string }[];
  summary: string[];
  metrics: Record<string, number>;
  pending_holds: { step_id: string; skill: string; label: string; review_id?: string | null; since?: string | null; expires_at?: string | null }[];
  /** 비슷한 과거 이슈에서 뽑은 참고 추천(총괄이 계산). 근거가 있으면 card_id가 그 과거 이슈 카드 */
  recommendation?: import("./adminRelay").Recommendation | null;
  /** 총괄이 이미 아는 사실로 만든 해결 내용 초안(저장 전). 아는 게 없으면 null */
  /** 시스템이 잰 이슈 시각(감지·원인 후보 도착·사람에게 넘김·정상 복귀). 모르는 시각은 null */
  timeline?: import("./duration").EventTimes | null;
  /** 이슈를 처음 연 신호: 출처(shop-api·infra·data·support)·종류·감지 시각. 예전 사건은 없다 */
  detected_by?: { source?: string; event?: string; at?: string } | null;
  /** 쇼핑몰 복구 신호 시각 (총괄 recovered_at) */
  recovered_at?: string | null;
  /** 복구 시간(metrics.mttr_seconds)의 끝 시각, 초 아래까지. 예전 총괄은 없다 */
  recovery_end_at?: string | null;
  /** 사건을 연 쇼핑몰 신호의 시험·시연 장애 종류 (switch·pg_slow·payment_version). 예전 사건은 없다 */
  fault_kind?: string | null;
  /** 부서가 넘긴 값 중 다른 부서에 쓰는 것 (복구 방법 recovery 등) */
  shared?: Record<string, unknown> | null;
  resolution_draft?: {
    outcome: "resolved" | null; root_cause: string | null; action: string | null; basis: string[];
    /** past_case: 같은 유형의 과거 이슈에서 가져옴(반복 장애), this_event: 이번 이슈에서 알게 된 것 */
    source?: "past_case" | "this_event";
    past_card_id?: string | null; similarity?: number | null;
    /** past_case일 때 이번 이슈에서 AI(데이터분석)가 찾은 원인 후보. 칸에는 넣지 않고 참고로만 보여 준다 */
    this_event_cause?: string | null;
  } | null;
}

/** POST /api/demo/incidents 본문 (main.py DemoRequest). 모두 선택 */
export interface DemoRequest {
  hold_rollback?: boolean; // 롤백이 C 등급 보류가 된다(사람 승인 대기). 총괄 AI 목 모드 옵션 이름
  fail_marketing?: boolean;
  wrong_owner?: boolean;
  infra_card_empty?: boolean;
}

// ---------------------------------------------------------------- 이름표
export const DEPT_KO: Record<string, string> = {
  orchestrator: "총괄 AI", planning: "기획", dev: "개발", support: "고객지원", marketing: "마케팅", hr: "인사", archive: "아카이브",
  finance: "재무", data: "데이터분석", infra: "인프라", safety: "안전 처리 로그", memory: "회사 기억", "shop-api": "회사 사이트",
  "dashboard-api": "AXcellent 화면", operator: "운영자(사람)",
};
// 백엔드는 서비스 단위로 나뉘어 있다(backend/README.md). 서비스 이름 → 부서.
// backend/shared/agent-template/src/ax_agent_template/services.py의 SERVICES(서비스 id → 부서)와 같게 둔다.
// 서비스 id와 부서 이름이 같은 것(orchestrator·finance·data·planning·dev)은 넣지 않는다
const SERVICE_DEPT: Record<string, string> = {
  inquiry: "support", claims: "support", settlement: "support", reviews: "support", coupon: "marketing",
  handover: "hr", decisions: "archive", org: "hr",
  incident: "infra", hosts: "infra",
};
/** 서비스 이름 → 부서 (서비스가 아니면 그대로). AI 대화방 이름과 AI 오피스 자리가 같은 규칙을 쓴다 */
export const deptOf = (name: string): string => SERVICE_DEPT[name] ?? name;
// 서비스 설명(마우스를 올리면 보이는 이름). services.py 마지막 칸(화면 이름)과 같다
const SERVICE_KO: Record<string, string> = {
  inquiry: "고객 문의", claims: "클레임·보상", settlement: "판매자 정산", reviews: "리뷰·지식", coupon: "공지·쿠폰",
  handover: "인수인계", decisions: "결정 기록", org: "조직 변동",
  dev: "개발·배포", planning: "요청·기획서", incident: "인프라", hosts: "PC·서버 연결",
};
const NOT_AGENT = new Set(["orchestrator", "memory", "shop-api", "dashboard-api", "operator"]);
/** 말하는 쪽 이름. 부서·서비스는 "{부서} AI", 총괄·안전 관문 등은 그대로 */
export function speaker(dept: string | null | undefined): string {
  if (!dept) return "담당 미정";
  const key = deptOf(dept);
  const ko = DEPT_KO[key] ?? dept;
  return NOT_AGENT.has(key) || key === "infra" ? ko : `${ko} AI`;
}
/** 서비스 이름(마우스를 올리면 보이는 설명용). 부서와 같으면 비운다 */
export const serviceKo = (name: string | null | undefined) => (name ? SERVICE_KO[name] ?? null : null);

export const INCIDENT_STATUS_KO: Record<string, string> = { open: "AI가 처리 중", resolved: "해결됨", degraded: "사람 확인 필요" };

// 이슈 이름. 총괄은 "payment 장애 감지 (error_rate_5xx)"처럼 코드로 주므로 사람이 읽는 말로 바꾼다
const COMPONENT_KO: Record<string, string> = { payment: "결제", api: "판매 사이트", db: "주문 DB", search: "검색", shipping: "배송" };
// 제목은 "결제가 서버 오류로 자꾸 실패해요"처럼 한 문장. 코드(5xx·p95)는 마우스를 올리면 보인다
const METRIC_KO: Record<string, string> = {
  error_rate_5xx: "서버 오류로 자꾸 실패해요", p95_latency_ms: "응답이 느려졌어요", order_fail_rate: "주문 실패가 늘었어요", payment_fail_rate: "실패가 늘었어요",
};
const SUBJECT_KO: Record<string, string> = { payment: "결제가", api: "회사 사이트가", db: "주문 DB가", search: "검색이", shipping: "배송이" };
/** 보류 건을 결정하는 화면. 환불·판매자 정산 지급은 고객지원, 쿠폰은 마케팅, 나머지는 재무 화면의 보류 큐다 */
/** 보류(C 등급)를 사람이 결정하는 화면. 화면의 보류 칸이 받는 일과 같아야 한다
 *  (환불·고객지원 삭제 /queue, 판매자 정산 /settlements, 마케팅 5종 /marketing, 개발 4종 /dev/work/holds, 나머지는 재무 보류 큐).
 *  마케팅 4종(공지·SNS·영상·캠페인)이 /finance로 가면 재무 보류 큐가 빼고 보여서 결정할 곳이 없었다 (11/17) */
export function holdPage(action: string | null | undefined, target?: unknown): string {
  // 환불과 개인정보·기한 지난 기록 삭제는 고객지원 보상 큐(/queue)에서 결정한다 (#1229)
  if (action === "refund_request" || (!!action && (SUPPORT_DELETION_HOLD_ACTIONS as readonly string[]).includes(action))) return "/queue";
  if (action === SETTLEMENT_HOLD_ACTION) return "/settlements";
  if (action && (SNS_HOLD_ACTIONS as readonly string[]).includes(action)) return SNS_HOLD_PAGE;
  if (action && (MARKETING_HOLD_ACTIONS as readonly string[]).includes(action)) return "/marketing";
  // 담당 화면이 접힌 일은 안전 처리 로그 화면에 "담당 화면 없음(접힘)"으로만 보인다
  // PR 만들기·머지·되돌리기·자동 머지 기준은 개발 화면 안전 확인 탭에서 결정한다 (#1232)
  if (action && (DEV_HOLD_ACTIONS as readonly string[]).includes(action)) return "/dev/work/holds";
  // 기획서 확정은 그 기획서 화면의 "교차검증 대기" 칸에서 결정한다. 기획서 번호는 관문 요청의
  // `params.target`이다(ax_workflow `_execute`). 번호를 모르면 기획서 목록으로 보낸다
  if (action === PLANNING_HOLD_ACTION) {
    return typeof target === "string" && target ? `/planning/work/specs/${encodeURIComponent(target)}` : "/planning/work/specs";
  }
  if (holdFolded(action)) return "/safety";
  return "/finance";
}

/** 담당 화면이 접혀 결정할 곳이 없는 보류인가 (인수인계). 기한이 지나면 안전 규칙이 자동 취소한다 */
export function holdFolded(action: string | null | undefined): boolean {
  return !!action && (FOLDED_HOLD_ACTIONS as readonly string[]).includes(action);
}
/** 총괄 제목 끝의 "(metric)"에서 지표 코드를 꺼낸다 */
function metricOf(inc: Pick<OrchIncident, "title">): string | null {
  return /\(([^)]+)\)\s*$/.exec(inc.title)?.[1] ?? null;
}
/** "오후 5:44" */
export const clockKo = (iso: string) => new Date(iso).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
/** 결과의 id·번호 값 (말풍선에는 빼고 툴팁의 코드 정보로만) */
function idsOf(output: Record<string, unknown> | null | undefined): string[] {
  if (!output) return [];
  return Object.entries(output)
    .filter(([k, v]) => ID_KEY.test(k) && typeof v === "string" && SAFE_TOKEN.test(v))
    .map(([k, v]) => `${k}=${v as string}`);
}
const rawOf = (...parts: (string | null | undefined | false)[]) => parts.filter(Boolean).join(" · ") || undefined;

/** 방 이름: "결제가 서버 오류로 자꾸 실패해요" */
export function incidentTitle(inc: Pick<OrchIncident, "title" | "component">): string {
  // 고객지원 처리 결과 이슈는 총괄이 사람이 읽는 제목("고객 문의 #1 처리 결과 기록")을 이미 준다
  if (inc.component.startsWith("support-inquiry-")) return inc.title;
  // 지금 총괄은 사람이 읽는 제목을 바로 준다(옛 기록만 "payment 장애 감지 (error_rate_5xx)" 형식)
  if (!inc.title.includes("장애 감지")) return inc.title;
  const comp = COMPONENT_KO[inc.component] ?? inc.component;
  const metric = metricOf(inc);
  const subject = SUBJECT_KO[inc.component] ?? `${comp}이(가)`;
  return metric && METRIC_KO[metric] ? `${subject} ${METRIC_KO[metric]}` : `${comp}에 문제가 생겼어요`;
}

// skill·액션 이름표는 lib/skillNames.ts (node --test로 바로 시험한다)
export { actionKo, skillKo } from "./skillNames.ts";
import { actionKo, skillKo } from "./skillNames.ts";
const GRADE_KO: Record<string, string> = { A: "A등급 · 바로 실행", B: "B등급 · 다른 부서가 확인 후 실행", C: "C등급 · 사람 승인" };
export const gradeKo = (g: string | null | undefined) => (g && g !== "read" ? GRADE_KO[g] ?? `${g}등급` : "조회");
/** 총괄 단계 이름 끝의 "(marketing)"·"(B)" 같은 영어 꼬리표를 뗀다 (말하는 쪽 이름에 이미 부서가 있다) */
export const stepLabel = (label: string) => label.replace(/\s*\((?:[a-z-]+|[ABC])\)\s*/g, " ").replace(/\s+/g, " ").trim();

const HOLD_WHY: Record<string, string> = {
  over_threshold: "금액 기준을 넘음", unregistered_action: "등록되지 않은 일", amount_mismatch: "적어 낸 금액과 계산이 다름",
  verify_rejected: "재무 AI가 거절함", verify_timeout: "확인하는 쪽이 응답하지 않음", schema_invalid: "요청 내용이 잘못됨",
  review_timeout: "기한 안에 결정이 없어 자동 취소",
};
export const holdWhy = (code: string | null | undefined) => (code ? HOLD_WHY[code] ?? code : "");

// ---------------------------------------------------------------- 메시지 모양
export type Tone = "request" | "ok" | "warn" | "fail" | "muted";

export interface Fact { label: string; value: string }

export type ChatItem =
  | { kind: "msg"; id: string; at: string; room: string; from: string; to: string; fromKey: string; central: boolean; text: string; tag?: string; raw?: string; tone: Tone; facts: Fact[]; repeat?: number; lastAt?: string; human?: boolean
      /** 사람과 부서 AI 직접 대화에서만: 개발 AI 계획 카드, 기획에 넘김 줄, "이대로 진행"을 보낼 받는 곳 */
      plan?: { title: string; done: string[] }; handoff?: { requestId: string | null }; goKey?: string
      /** 총괄 말풍선 아래 붙일 고객 의뢰 진행 카드의 의뢰 번호 (wr_…, 그 방에서 처음 나온 줄에만) */
      requestCard?: string
      /** 마케팅 대화의 이미지 시트·스토리보드 카드(올린 캐릭터·결과) */
      creative?: { kind: "creative"; record_id: string; role: "attached" | "result"; has_storyboard: boolean; has_script?: boolean }
      /** 데이터분석 분석 의뢰·보고서 카드(1:1과 같은 ChatCardView, 11/23). 서버 메시지 id·그 대화 id와 함께 */
      chatCard?: RoomChatCard
      /** 받는 쪽 원래 이름(공통 로그 to_agent). AI 오피스가 부서끼리 걸어가는 데 쓴다 */
      toKey?: string
      /** 안전 관문 execution_id (실행 기록·공통 로그 줄). 안전 확인 줄 합치기(lib/safetyFold.ts)가 묶는 기준 */
      execId?: string
      /** 안전 관문 실행 기록 줄: 실행 요청(request) · 검증·실행 결과(check) */
      safetyRole?: "request" | "check"
      /** 이 말풍선에 합친 안전 확인 통과 줄 ("🛡 안전 확인 통과" 칩, 눌러서 펼침) */
      safety?: FoldLine[]
      /** 작업 기록 줄 (lib/commFilter.ts chatLane). 대화 칸에서 접히고 펼치면 회색 작은 말풍선. 안 읽은 수·방 미리보기에서 뺀다 */
      lane?: "log"
      /** 문장 뒤 작은 표시 (부서가 긴 작업으로 받았다고 답한 줄: "긴 작업 진행 중", lib/commReply.ts) */
      note?: string }
  | { kind: "system"; id: string; at: string; room: string; text: string }
  | { kind: "hold"; id: string; at: string; room: string; title: string; detail: string; state: "pending" | "approved" | "rejected" | "timeout"; contextId?: string; stepId?: string; link?: string; decideAt?: string
      /** 결정을 기다리는 부서 (사건 단계가 없을 때만) */
      dept?: string }
  | { kind: "separator"; id: string; at: string; room: string; text: string; raw?: string; variant: "day" | "topic" };

export const SAFETY_ROOM = "safety";
/** 총괄 이슈에 묶이지 않는 공통 로그 소통 (부서끼리 직접 주고받은 A2A) */
export const DIRECT_ROOM = "direct";
export const ALL_ROOM = "all";

// ---------------------------------------------------------------- 안전한 결과 값만 고르기
const FIELD_KO: Record<string, string> = {
  failed_order_count: "실패 주문", affected_customer_count: "영향 고객", damage_amount: "결제 실패(미결제) 금액", affected_customer_ids: "영향 고객 id",
  suspects: "의심 배포", impact_scope: "영향 범위", status: "판정", rolled_back_to: "되돌린 버전", candidates: "후보 PR", linked_paras: "연결",
  notice_id: "공지 id", eligible_customer_ids: "보상 대상", amount_per_customer: "1인 금액", issued: "지급", total_amount: "총액",
  failed_orders: "실패 주문", affected_customers: "영향 고객", lost_amount: "매출 손실", compensation_coupon_count: "보상 쿠폰",
  compensation_total: "보상 합계", total_loss: "총 손실", recovered: "복구", infra_metrics_ok: "지표 정상", card_id: "기록 id",
  channel_code: "게시 위치", state: "상태", match: "금액 일치", pr_number: "코드 변경 번호", severity: "심각도", grade: "등급",
  kind: "종류", incidents: "이슈", auto_handled_rate: "자동 처리율", escalation_count: "사람에게 넘김", count: "건수",
  requested: "요청", unit_value: "1건 금액", reason_code: "사유", coupon_count: "쿠폰", outcome: "결과",
  avg_handle_seconds: "평균 처리 시간(초)", related_prs: "관련 PR", by_category: "분류별",
  refund_total: "환불 합계", money_out_total: "빠져나간 돈",
};
/** 코드 값 → 사람이 읽는 말 */
const VALUE_KO: Record<string, string> = {
  published: "게시됨", draft: "초안", banner: "사이트 배너", popup: "팝업", email: "메일", resolved: "해결됨", open: "진행 중",
  pass: "통과", fail: "실패", approved: "승인", rejected: "거절", held: "보류", high: "높음", mid: "보통", low: "낮음",
  suspected: "의심됨", confirmed: "확정", unknown: "확인할 수 없음", rolled_back: "되돌림", recovered: "복구됨", degraded: "일부 미완료",
  completed: "완료", failed: "실패", skipped: "건너뜀", critical: "매우 높음",
  // 구성요소
  payment: "결제", api: "판매 사이트", db: "주문 DB", search: "검색", shipping: "배송",
  // 공지·보상 종류
  incident_opened: "장애 시작 알림", incident_resolved: "복구 알림", payment_failure: "결제 장애 보상", card_created: "기록 만듦",
  undetermined: "원인 미확정",
};
/** id·번호는 사람에게 의미가 적어 말풍선에 넣지 않는다 (툴팁의 코드 정보로만) */
const ID_KEY = /(^|_)id$|_ids$/;
const MONEY = /amount|total|loss|_won$/;
/** 부서가 쓴 자유 문장일 수 있는 키 — 절대 옮기지 않는다 */
const UNSAFE_KEYS = new Set(["untrusted", "summary", "note", "message", "text", "reason", "explanation", "question", "body", "title", "description"]);
/** id·버전·코드 모양만 통과 (공백·문장 불가) */
const SAFE_TOKEN = /^[A-Za-z0-9_.:#-]{1,40}$/;
const MAX_FACTS = 4;

const fmtNum = (n: number) => n.toLocaleString("ko-KR");

export function safeFacts(output: Record<string, unknown> | null | undefined): Fact[] {
  if (!output) return [];
  const facts: Fact[] = [];
  for (const [key, value] of Object.entries(output)) {
    if (facts.length >= MAX_FACTS) break;
    if (key.startsWith("_") || UNSAFE_KEYS.has(key) || ID_KEY.test(key)) continue;
    // 이름표가 없는 코드 이름은 말풍선에 띄우지 않는다 (마우스를 올리면 보이는 코드 정보에는 남는다)
    const label = FIELD_KO[key];
    if (!label) continue;
    if (typeof value === "number" && Number.isFinite(value)) {
      const shown = /_rate$/.test(key) && value <= 1 ? `${Math.round(value * 100)}%` : MONEY.test(key) || key === "unit_value" ? `${fmtNum(value)}원` : fmtNum(value);
      facts.push({ label, value: shown });
    } else if (typeof value === "boolean") {
      facts.push({ label, value: value ? "예" : "아니오" });
    } else if (typeof value === "string" && SAFE_TOKEN.test(value)) {
      facts.push({ label, value: VALUE_KO[value] ?? value });
    } else if (Array.isArray(value) && value.length > 0) {
      // 목록은 개수만. 단, 안전한 토큰 몇 개는 보여 준다 (PR 번호·버전 등)
      const tokens = value
        .map((v) => (typeof v === "number" ? `#${v}` : typeof v === "string" && SAFE_TOKEN.test(v) ? VALUE_KO[v] ?? v : pickToken(v)))
        .filter((v): v is string => !!v);
      const shown = value.length <= 3 && tokens.length === value.length ? tokens.join(", ") : `${fmtNum(value.length)}건`;
      facts.push({ label, value: shown });
    }
  }
  return facts;
}

/** {number: 412} → "#412", {service, version} → "payment-api@v2.4.1" */
function pickToken(v: unknown): string | null {
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  if (typeof o.number === "number") return `#${o.number}`;
  const svc = typeof o.service === "string" && SAFE_TOKEN.test(o.service) ? o.service : null;
  const ver = typeof o.version === "string" && SAFE_TOKEN.test(o.version) ? o.version : null;
  if (svc && ver) return `${svc}@${ver}`;
  return svc ?? ver;
}

// ---------------------------------------------------------------- 총괄 이슈 → 메시지
const CENTRAL = "orchestrator";

function transferFrom(inc: OrchIncident, step: OrchStep): string | null {
  // 엔진은 이관하면 step.dept를 새 부서로 바꾼다. 원래 부서는 이슈 "label: A → B 이관(...)"에만 남는다.
  const ev = inc.events.find((e) => e.message.startsWith(`${step.label}: `) && e.message.includes(" 이관("));
  const m = ev?.message.match(/: (\S+) → (\S+) 이관/);
  return m ? m[1] : null;
}

function resultLine(step: OrchStep): { text: string; tone: Tone } {
  // 사람 승인 뒤 다시 보낸 1회는 재시도가 아니다 (engine._wait_human이 attempts를 올린다)
  const resumed = step.reason_code === "human_input" && step.status === "completed" ? 1 : 0;
  const retries = Math.max(step.attempts - 1 - resumed, 0);
  const retryTxt = retries > 0 ? ` (재시도 ${retries}회)` : "";
  switch (step.status) {
    case "completed":
      if (step.transferred_to) return { text: `이관받아 처리 완료했습니다.`, tone: "ok" };
      if (step.output.handled_by === "human") return { text: "담당 부서 사람이 처리해서 이어 갑니다.", tone: "ok" };
      return { text: `${stepLabel(step.label)} 완료했습니다.${retryTxt}`, tone: "ok" };
    case "failed": {
      if (step.note.startsWith("canceled")) return { text: `시간 안에 끝내지 못했습니다 — 시간 초과${retryTxt}.`, tone: "fail" };
      if (step.note.startsWith("이관")) return { text: `소관이 아니라고 답했고, 이관할 부서를 찾지 못했습니다.`, tone: "fail" };
      // 다시 배포 실패는 재시도하지 않는다(배포가 두 번 돌 수 있다). 처리 순서는 장애 상세 단계 표에 있다
      if (isRedeployFailed(step.note)) return { text: "자동으로 되돌리지 못해 담당자에게 처리 순서를 넘겼어요.", tone: "fail" };
      return { text: `실패했습니다${retries > 0 ? ` — ${retries}회 재시도 후 중단` : ""}.`, tone: "fail" };
    }
    case "rejected_by_human":
      if (step.handoff_task_id) {
        return { text: step.note === "사람 처리 시간 초과로 자동 거절" ? "담당 부서 처리 기한이 지나 자동 거절되었습니다." : "담당 부서 사람이 처리하지 않기로 했습니다.", tone: "warn" };
      }
      return { text: step.note === "시간 초과로 자동 거절" ? "결정 기한이 지나 자동 거절되었습니다." : "사람이 거절해 진행하지 않았습니다.", tone: "warn" };
    case "held":
      return { text: "C 등급이라 사람 승인을 기다립니다. 보류 중입니다.", tone: "warn" };
    case "waiting_human":
      return { text: "담당 부서 사람의 처리를 기다립니다.", tone: "warn" };
    case "waiting_async":
      return { text: "부서가 긴 작업을 하는 중입니다. 진행 알림이 오면 이어 갑니다.", tone: "muted" };
    case "skipped":
      return { text: "건너뛰었습니다.", tone: "muted" };
    default:
      return { text: step.status, tone: "muted" };
  }
}

const SKIP_WHY: [RegExp, string][] = [
  [/^확인할 수 없음 \(.+ 서비스가 꺼져 있음\)/, "그 서비스가 꺼져 있어 확인할 수 없어 건너뜀"],
  [/^선행 단계 미완료/, "앞 단계가 끝나지 않아 건너뜀"],
  [/Agent Card에 skill 없음/, "맡을 부서를 찾지 못해 건너뜀 (Agent Card에 skill 없음)"],
  [/allowed_requesters/, "총괄 AI가 요청할 수 없는 skill이라 건너뜀"],
  [/^고객 자료 대기/, "고객 자료를 기다려 보류"],
  [/^확신도 높은 원인 후보가 있어/, "확신도 높은 원인 후보가 있어 원인 확인 요청 없이 건너뜀"],
];

export function incidentItems(inc: OrchIncident): ChatItem[] {
  const room = inc.context_id;
  const out: ChatItem[] = [];
  const opened = inc.opened_at ?? inc.events[0]?.at ?? "";
  out.push({ kind: "separator", id: `${room}:sep`, at: opened, room, text: `${incidentTitle(inc)}${opened ? ` · ${clockKo(opened)} 시작` : ""}`, raw: `이슈 번호 ${inc.context_id}`, variant: "topic" });
  out.push({ kind: "system", id: `${room}:open`, at: opened, room, text: inc.component.startsWith("support-inquiry-")
      ? `고객지원 AI가 문의를 해결 종료해서 총괄 AI가 "${incidentTitle(inc)}" 이슈를 열고 재무·아카이브에 넘겼어요.`
      : `회사 사이트가 "${incidentTitle(inc)}" 신호를 보내서 총괄 AI가 이슈를 열었어요.` });

  for (const step of inc.steps) {
    const sid = `${room}:${step.step_id}`;
    if (step.status === "skipped" && !step.dept) {
      const why = SKIP_WHY.find(([re]) => re.test(step.note))?.[1] ?? "건너뜀";
      out.push({ kind: "system", id: `${sid}:skip`, at: step.finished_at ?? opened, room, text: `${stepLabel(step.label)} — ${why}` });
      continue;
    }
    if (!step.started_at) continue;
    const original = step.transferred_to ? transferFrom(inc, step) ?? step.dept : step.dept;
    out.push({
      kind: "msg", id: `${sid}:req`, at: step.started_at, room, from: speaker(CENTRAL), to: speaker(original), fromKey: CENTRAL, central: true,
      text: `${stepLabel(step.label)} 요청합니다.`, tag: gradeKo(step.grade), raw: rawOf(`skill: ${step.skill}`), tone: "request", facts: [],
    });
    if (step.status === "skipped") {
      const why = SKIP_WHY.find(([re]) => re.test(step.note))?.[1] ?? "건너뜀";
      out.push({ kind: "system", id: `${sid}:skip`, at: step.finished_at ?? step.started_at, room, text: `${stepLabel(step.label)} — ${why}` });
      continue;
    }
    if (step.transferred_to && original) {
      out.push({
        kind: "msg", id: `${sid}:transfer`, at: step.started_at, room, from: speaker(original), to: speaker(CENTRAL), fromKey: original, central: false,
        text: `소관이 아닙니다. 이관 대상을 찾아 주세요.`, tag: "소관 아님", raw: rawOf(`skill: ${step.skill}`, step.reason_code), tone: "warn", facts: [],
      });
      out.push({
        kind: "msg", id: `${sid}:req2`, at: step.started_at, room, from: speaker(CENTRAL), to: speaker(step.transferred_to), fromKey: CENTRAL, central: true,
        text: `${stepLabel(step.label)} — ${speaker(original)}에서 이관합니다.`, tag: "이관", raw: rawOf(`skill: ${step.skill}`), tone: "request", facts: [],
      });
    }
    const wasHeld = step.reason_code === "human_input";
    const pending = inc.pending_holds.some((h) => h.step_id === step.step_id);
    if (wasHeld) {
      const holdAt = heldAt(inc, step) ?? step.started_at;
      out.push({
        kind: "msg", id: `${sid}:heldmsg`, at: holdAt, room, from: speaker(step.dept), to: speaker(CENTRAL), fromKey: step.dept ?? "", central: false,
        text: `C 등급이라 사람 승인이 필요합니다.${step.note && SAFE_TOKEN.test(step.note) ? ` (${holdWhy(step.note)})` : ""}`, tag: gradeKo("C"), raw: rawOf(`skill: ${step.skill}`, step.note), tone: "warn", facts: [],
      });
      const state: "pending" | "approved" | "rejected" | "timeout" = pending
        ? "pending"
        : step.status === "rejected_by_human"
          ? step.note === "시간 초과로 자동 거절" ? "timeout" : "rejected"
          : "approved";
      out.push({
        kind: "hold", id: `${sid}:hold`, at: holdAt, room, contextId: inc.context_id, stepId: step.step_id, state, decideAt: holdPage(step.skill),
        title: stepLabel(step.label), detail: `${speaker(step.dept)} · ${skillKo(step.skill)}`,
      });
      if (pending) continue;
    }
    if (step.handoff_task_id) {
      // 받는 서비스가 꺼져 있어 담당 부서 사람에게 넘어감 (C 등급 승인 대기와 다르다)
      const rawUnit = typeof step.output.assignee_unit === "string" ? step.output.assignee_unit : "담당 부서";
      const unit = rawUnit === "unassigned" ? "담당 미정" : rawUnit;
      // 총괄이 스스로 끝내지 못해 사람에게 넘긴 단계(transfer_queued)도 받은 요청에 올라간다
      const queued = step.status === "transfer_queued";
      out.push({
        kind: "msg", id: `${sid}:handoff`, at: step.started_at, room, from: speaker(step.dept), to: speaker(CENTRAL), fromKey: step.dept ?? "", central: false,
        text: queued
          ? `사람의 받은 요청에 올렸습니다 (${unit}). 사람이 확인하면 결과를 이 이슈에 남깁니다.`
          : `이 서비스는 켜져 있지 않아 ${unit} 할 일함으로 넘겼습니다. 사람이 처리하면 이어 갑니다.`,
        tag: queued ? "받은 요청" : "사람 처리", raw: rawOf(`skill: ${step.skill}`, step.handoff_task_id), tone: "warn", facts: [],
      });
      if (step.status === "waiting_human") continue;
    }
    if (step.finished_at && step.status !== "held") {
      const r = resultLine(step);
      const who = step.dept;
      out.push({
        kind: "msg", id: `${sid}:res`, at: step.finished_at, room, from: speaker(who), to: speaker(CENTRAL), fromKey: who ?? "", central: false,
        text: r.text, raw: rawOf(`skill: ${step.skill}`, ...idsOf(step.output)), tone: r.tone, facts: step.status === "completed" ? safeFacts(step.output) : [],
      });
    }
  }

  if (inc.closed_at) {
    const m = inc.metrics ?? {};
    const parts = [
      m.steps_total != null ? `${m.steps_completed ?? 0}/${m.steps_total}단계 완료` : null,
      m.retries ? `재시도 ${m.retries}회` : null,
      m.transfers ? `이관 ${m.transfers}건` : null,
      m.held ? `사람 판단 ${m.held}건` : null,
      m.mttr_seconds != null ? `걸린 시간 ${m.mttr_seconds}초` : null,
    ].filter(Boolean);
    out.push({ kind: "system", id: `${room}:close`, at: inc.closed_at, room, text: `이슈 종료 — ${INCIDENT_STATUS_KO[inc.status] ?? inc.status}${parts.length ? ` · ${parts.join(" · ")}` : ""}` });
  }
  return out;
}

function heldAt(inc: OrchIncident, step: OrchStep): string | null {
  return inc.events.find((e) => e.message === `${step.label}: C 등급 보류 — 승인 대기열에 올림`)?.at ?? null;
}

/** 재시도처럼 단계 메시지에 안 드러나는 이슈만 시스템 줄로. (요청·완료·종료 이슈는 메시지와 겹친다) */
export function incidentEventItems(inc: OrchIncident): ChatItem[] {
  return inc.events
    .map((e, i) => ({ e, i }))
    .filter(({ e }) => / 재시도 \d+$/.test(e.message) || /: 사람이 승인 — 재개$/.test(e.message) || /^같은 이(슈|벤트)에 합침/.test(e.message))
    .map(({ e, i }) => {
      if (/^같은 이(슈|벤트)에 합침/.test(e.message)) {
        return { kind: "system" as const, id: `${inc.context_id}:ev${i}`, at: e.at ?? inc.opened_at ?? "", room: inc.context_id, text: "같은 장애 신호가 다시 들어와 진행 중인 이 이슈에 합쳤습니다" };
      }
      const label = stepLabel(e.message.split(": ")[0]);
      const retry = e.message.match(/(마감 초과|실패)\(?[^)]*\)?, 재시도 (\d+)$/);
      const text = retry
        ? `${label} — ${retry[1] === "마감 초과" ? "시간 초과" : "실패"}, ${retry[2]}번째 재시도`
        : `${label} — 사람이 승인해 다시 요청합니다`;
      return { kind: "system" as const, id: `${inc.context_id}:ev${i}`, at: e.at ?? inc.opened_at ?? "", room: inc.context_id, text };
    });
}

// ---------------------------------------------------------------- 안전 관문 실행 기록 → 메시지
export function executionItems(rec: ExecutionRecord, room: string = SAFETY_ROOM): ChatItem[] {
  const d = rec.decision;
  const req = rec.request;
  const id = `exe:${rec.execution_id}`;
  const amount = d.computed_amount != null ? ` (금액 ${fmtNum(d.computed_amount)}원)` : "";
  // 안전 확인 줄 합치기 기준. C 등급(사람 승인)은 합치지 않고 지금처럼 하나하나 보인다
  // 작업 기록(⑤ 안전 처리 통과): 실행됨 · C 등급 아님 · 사건 흐름 밖 · 안전 처리 로그 방이 아님 → 대화 칸에서 접는다
  const passLog = d.status === "executed" && d.grade !== "C" && room !== SAFETY_ROOM && !(req.context_id ?? "").startsWith("ctx_inc_");
  const fold = { ...(d.grade === "C" ? {} : { execId: rec.execution_id }), ...(passLog ? { lane: "log" as const } : {}) };
  const out: ChatItem[] = [{
    kind: "msg", id: `${id}:req`, at: rec.created_at, room, from: speaker(req.requester), to: speaker("safety"), fromKey: req.requester, central: false,
    text: `${actionKo(req.action)} 실행 요청${amount}`, tag: "안전 처리 로그", raw: rawOf(`action: ${req.action}`, req.context_id), tone: "request", facts: [],
    ...fold, safetyRole: "request",
  }];
  if (d.verifier) {
    const verdict =
      d.hold_reason === "verify_rejected" || d.status === "rejected" ? { t: "검증 거절", tone: "fail" as Tone }
      : d.hold_reason === "verify_timeout" ? { t: "제때 답하지 못함", tone: "warn" as Tone }
      : d.status === "verifying" ? { t: "검증 중", tone: "muted" as Tone }
      : d.hold_reason === "amount_mismatch" ? { t: "금액 불일치", tone: "warn" as Tone }
      : { t: "검증 통과", tone: "ok" as Tone };
    out.push({
      kind: "msg", id: `${id}:verify`, at: rec.created_at, room, from: verifierName(d.verifier), to: speaker("safety"), fromKey: d.verifier, central: false,
      text: verdict.t, tag: d.verifier === "finance" ? "금액 다시 계산" : undefined, raw: d.verifier === "finance" ? "skill: verify_amount" : undefined, tone: verdict.tone, facts: [],
      ...fold, safetyRole: "check",
    });
  }
  const res: Record<string, { t: string; tone: Tone }> = {
    executed: { t: "실행됨", tone: "ok" }, held: { t: "보류 — 사람 결정 대기", tone: "warn" }, rejected: { t: "거절", tone: "fail" },
    verifying: { t: "검증 기다리는 중", tone: "muted" }, failed: { t: "실행 오류", tone: "fail" },
  };
  const r = { ...(res[d.status] ?? { t: d.status, tone: "muted" as Tone }) };
  const why = d.hold_reason ? ` · ${holdWhy(d.hold_reason)}` : "";
  // C 등급인데 실행됨 = 보류됐다가 결정(사람 또는 재무 AI)을 받고 실행된 것
  if (d.status === "executed" && d.grade === "C") r.t = "보류 후 결정을 받아 실행됨";
  out.push({
    kind: "msg", id: `${id}:res`, at: rec.created_at, room, from: speaker("safety"), to: speaker(req.requester), fromKey: "safety", central: true,
    text: `${r.t} (${d.grade}등급)${why}`, raw: rawOf(d.execution_id), tone: r.tone, facts: [],
    ...fold, safetyRole: "check",
  });
  if (d.status === "held") {
    out.push({
      kind: "hold", id: `${id}:hold`, at: rec.created_at, room, state: "pending", link: holdPage(req.action, req.params?.target),
      title: `${speaker(req.requester)}의 ${actionKo(req.action)}${amount}`, detail: `담당 화면(${holdPage(req.action, req.params?.target)})에서 결정합니다`,
    });
  }
  return out;
}

function verifierName(v: string) {
  return v === "finance" ? "재무 AI" : speaker(v);
}

// ---------------------------------------------------------------- 공통 로그 소통 → 메시지
const COMM_TONE: Record<CommOutcome, Tone> = { completed: "ok", rejected: "fail", held: "warn", needs_human: "warn", in_progress: "request" };

/** 소통 한 줄이 들어갈 방: 총괄 이슈와 context_id가 같으면 그 이슈 방, 아니면 "부서 간 직통" */
export function commRoom(line: CommLine, incidentIds: ReadonlySet<string>): string {
  return line.context_id && incidentIds.has(line.context_id) ? line.context_id : DIRECT_ROOM;
}

/** 총괄 이슈 방에서 총괄이 보내거나 받은 줄인데 같은 skill 단계가 이미 있으면 총괄 기록이 같은 일을 보여 준다 */
function coveredByIncident(line: CommLine, inc: OrchIncident | undefined): boolean {
  if (!inc || !line.skill) return false;
  if (line.from_agent !== CENTRAL && line.to_agent !== CENTRAL) return false;
  return inc.steps.some((s) => s.skill === line.skill);
}

/**
 * 공통 로그 한 줄 → 말풍선. 문장은 skill·결과 값으로만 만든다(payload는 dashboard-api가 이미 뺐다).
 * `execution_id`가 안전 관문 기록과 같으면 `dupOf`에 그 id를 넣는다 — 전체 방에서는 빼고, 이슈 방에서는 연결 표시만 단다.
 */
export function commItem(line: CommLine, room: string, safetyIds: ReadonlySet<string>, reply = false): Extract<ChatItem, { kind: "msg" }> & { dupOf?: string } {
  const skill = line.skill ?? "무슨 일인지 기록 없음";
  const label = COMM_OUTCOME_KO[line.outcome] ?? line.outcome;
  const dupOf = line.execution_id && safetyIds.has(line.execution_id) ? line.execution_id : undefined;
  const tag = ["공통 로그 기록", dupOf ? "안전 관문 기록과 같은 건" : null].filter(Boolean).join(" · ");
  const raw = rawOf(
    line.skill ? `skill: ${line.skill}` : null,
    [line.from_agent, line.to_agent].filter(Boolean).join(" → "),
    line.execution_id,
    line.context_id,
  );
  const holdHint = line.outcome === "held" && !dupOf ? ` — 안전 관문 보류 큐(${holdPage(line.skill)})에서 결정` : "";
  // 받았다는 답장(같은 일·같은 의뢰의 A → B 부탁에 B → A 진행 중)은 부탁이 아니다: "받았어요" + 작은 표시, 걸어가지 않게 tone muted
  const answered = reply && line.outcome === "in_progress" && line.from_agent !== "safety";
  return {
    kind: "msg", id: `log:${line.log_id}`, at: line.occurred_at, room,
    from: speaker(line.from_agent), to: line.to_agent ? speaker(line.to_agent) : "받는 쪽 미기록",
    fromKey: line.from_agent, central: line.from_agent === CENTRAL, ...(line.to_agent ? { toKey: line.to_agent } : {}),
    text: `${commSentence(line.skill ? skillKo(line.skill) : skill, line.outcome, label, line.from_agent, answered)}${holdHint}`, tag, raw,
    tone: answered ? "muted" : COMM_TONE[line.outcome] ?? "muted", facts: [], dupOf,
    ...(line.execution_id ? { execId: line.execution_id } : {}),
    ...(answered ? { note: "긴 작업 진행 중" } : {}),
  };
}

/** 공통 로그 한 줄을 대화체로. 요청은 부탁, 결과는 알림 */
function commSentence(what: string, outcome: string, label: string, from?: string | null, reply = false): string {
  // 안전 관문 기록은 "부탁"이 아니라 실행해도 되는지 판정하는 쪽이다
  if (from === "safety") {
    if (outcome === "in_progress") return `${what} — 실행해도 되는지 확인하고 있어요`;
    if (outcome === "completed") return `${what} — 실행해도 돼요`;
  }
  switch (outcome) {
    case "in_progress": return reply ? `${what} 받았어요` : `${what} 부탁해요`;
    case "completed": return `${what} 끝났어요`;
    case "rejected": return `${what} — 할 수 없어서 거절했어요`;
    case "held": return `${what} — 보류했어요 (사람 결정 대기)`;
    case "needs_human": return `${what} — 사람 확인이 필요해요`;
    default: return `${what}: ${label}`;
  }
}

/** 소통 줄을 방별 말풍선으로. 총괄 기록과 겹치는 줄은 빼고, 안전 관문과 겹치는 줄은 `dupOf`를 단다 */
// 대화 창구 읽기(목록·상세)는 화면이 다시 읽을 때마다 남아서 기록에서 뺀다. 원본 공통 로그는 그대로
export const CHAT_READ_SKILL = /_chat_(list|get)$/;

// 화면(dashboard-api)이 데이터를 읽어 오는 기록은 화면이 열려 있는 동안 계속 쌓여서 기록에서 뺀다. 원본 공통 로그는 그대로
// 화면이 보낸 대화(*_chat_send·*_chat_close)도 뺀다: 사람 대화는 대화 창구에서 말풍선으로 따로 보이므로 같은 일이 두 번 보인다.
// 하루 한 번 보관 기간 청소(purge_expired_inquiries·purge_expired_claims)도 성공하면 뺀다.
const SCREEN = "dashboard-api";

/** 화면(dashboard-api)이 주고받은 줄 중 기록에서 뺄 줄인가. 실패·보류 등(SCREEN_KEEP_OUTCOMES)은 남긴다(대화 칸에서는 chatLane ④가 작업 기록으로 접는다) */
export function isScreenRead(line: CommLine): boolean {
  if (line.from_agent !== SCREEN && line.to_agent !== SCREEN) return false;
  return !SCREEN_KEEP_OUTCOMES.has(line.outcome);
}

// 부서끼리 조회만 주고받은 줄(사건·요청 밖, 또는 사건이 닫힌 뒤)은 lib/commFilter.ts가 가른다
export { closedTimes, isBackgroundRead };

/** requests: 고객 의뢰 목록(사람 토큰이 있을 때만). 끝난 의뢰(ctx_wr_)의 뒤 조회는 사건처럼 거른다 */
/** cardContexts: 이 방에 의뢰 진행 카드가 보이는 의뢰의 context (그 의뢰 안 부서 호출은 작업 기록으로 접는다, chatLane ①) */
export function commItems(comms: CommLine[], incidents: OrchIncident[], executions: ExecutionRecord[], requests: WorkRequestRow[] = [], cardContexts: ReadonlySet<string> = new Set()) {
  const byId = new Map(incidents.map((i) => [i.context_id, i]));
  const ids = new Set(byId.keys());
  const safetyIds = new Set(executions.map((e) => e.execution_id));
  const closedAt = mergeClosed(closedTimes(incidents), requestClosedTimes(requests));
  const kept = comms.filter((line) => !CHAT_READ_SKILL.test(line.skill ?? "") && !isScreenRead(line) && !isBackgroundRead(line, closedAt));
  const lanes = chatLanes(kept, { cardContexts });
  // 답장 짝은 거르기 전 전체 줄에서 찾는다 (앞의 부탁 줄이 화면에서 빠져도 답장은 답장이다)
  const replies = replyLogIds(comms);
  return kept
    .map((line) => ({ line, room: commRoom(line, ids) }))
    .filter(({ line, room }) => !coveredByIncident(line, byId.get(room)))
    .map(({ line, room }) => {
      const it = commItem(line, room, safetyIds, replies.has(line.log_id));
      return lanes.get(line.log_id) === "log" ? { ...it, lane: "log" as const } : it;
    });
}

// ---------------------------------------------------------------- 합치기
const time = (iso: string) => (iso ? new Date(iso).getTime() : 0);

/** 방 하나(또는 전체)의 타임라인. 시간순, 같은 시각이면 만들어진 순서를 지킨다 */
export function buildTimeline(incidents: OrchIncident[], executions: ExecutionRecord[], comms: CommLine[], room: string, requests: WorkRequestRow[] = [], cardContexts: ReadonlySet<string> = new Set()): ChatItem[] {
  const items: ChatItem[] = [];
  for (const inc of incidents) {
    if (room !== ALL_ROOM && room !== inc.context_id) continue;
    items.push(...incidentItems(inc), ...incidentEventItems(inc));
  }
  // 안전 관문 실행 기록(사람 승인 대기 카드 포함): 전체 대화방·안전 관문 방은 모두, 장애·이슈 방은 그 사건 것만
  const inIncident = room !== ALL_ROOM && room !== SAFETY_ROOM;
  const recs = [...executions]
    .filter((rec) => !inIncident || rec.request.context_id === room)
    .sort((a, b) => time(a.created_at) - time(b.created_at));
  for (const rec of recs) items.push(...executionItems(rec, room));
  const shownExecs = new Set(recs.map((r) => r.execution_id));
  // 안전 처리 로그 방은 줄을 접지 않는다 (원본 그대로)
  for (const c of commItems(comms, incidents, executions, requests, room === SAFETY_ROOM ? new Set() : cardContexts)) {
    // 안전 관문 기록을 같이 보이는 방에서는 같은 건(execution_id 일치)을 두 번 보이지 않는다
    if (room === ALL_ROOM ? !c.dupOf : room === c.room && !(c.dupOf && shownExecs.has(c.dupOf))) {
      const { dupOf: _dup, ...msg } = c;
      items.push(msg);
    }
  }
  const indexed = items.map((it, i) => ({ it, i }));
  indexed.sort((a, b) => time(a.it.at) - time(b.it.at) || a.i - b.i);
  // 통과한 안전 확인 줄은 요청 말풍선의 "🛡 안전 확인 통과" 칩으로 합친다. 안전 처리 로그 방은 하나하나 본다
  const sorted = indexed.map((x) => x.it);
  return withDaySeparators(collapseRepeats(room === SAFETY_ROOM ? sorted : foldSafety(sorted)));
}

// 같은 AI가 같은 상대에게 같은 말을 짧은 시간 안에 되풀이하면(쿠폰 28장 지급, 화면 새로고침마다 다시 물은 영향 분석 등)
// 첫 말풍선 하나에 "×N"으로 묶는다. 스크롤만 길어지고 새 정보가 없어서다.
const REPEAT_WINDOW_MS = 2 * 60_000;
function collapseRepeats(items: ChatItem[]): ChatItem[] {
  const out: ChatItem[] = [];
  const last = new Map<string, Extract<ChatItem, { kind: "msg" }>>();
  for (const it of items) {
    if (it.kind !== "msg") { out.push(it); continue; }
    const key = [it.fromKey, it.to, it.text, it.tone, it.lane ?? ""].join("|");
    const prev = last.get(key);
    if (prev && time(it.at) - time(prev.lastAt ?? prev.at) <= REPEAT_WINDOW_MS) {
      prev.repeat = (prev.repeat ?? 1) + 1;
      prev.lastAt = it.at;
      continue;
    }
    const copy = { ...it };
    last.set(key, copy);
    out.push(copy);
  }
  return out;
}

/** 날짜가 바뀌는 곳에 "10월 23일 (목) · 오전" 구분선 */
function withDaySeparators(items: ChatItem[]): ChatItem[] {
  const out: ChatItem[] = [];
  let last = "";
  for (const it of items) {
    if (it.at) {
      const d = new Date(it.at);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}-${d.getHours() < 12 ? "am" : "pm"}`;
      if (key !== last) {
        last = key;
        out.push({ kind: "separator", id: `day:${key}:${it.id}`, at: it.at, room: it.room, text: dayLabel(d), variant: "day" });
      }
    }
    out.push(it);
  }
  return out;
}

function dayLabel(d: Date) {
  const date = d.toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "short" });
  return `${date} · ${d.getHours() < 12 ? "오전" : "오후"}`;
}

/** 방 목록에 쓰는 메시지 수 (말풍선만) */
export function messageCount(items: ChatItem[]) {
  return chatMessageCount(items); // 작업 기록 줄은 세지 않는다 (lib/quietLog.ts)
}

// 대화 줄 / 작업 기록 줄 나누기는 lib/quietLog.ts (node --test로 바로 시험한다)
export { timelineLanes };
