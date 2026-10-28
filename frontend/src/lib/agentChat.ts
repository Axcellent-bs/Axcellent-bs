import type { ExecutionRecord } from "./finance";
import { COMM_OUTCOME_KO } from "./labels";
import type { CommLine, CommOutcome } from "./types";

// AI 대화방: 총괄(orchestrator)의 사건 기록, 안전 관문(safety)의 실행 기록, 공통 로그의 부서 간 소통(dashboard-api)을
// "누가 누구에게 무엇을 보냈나" 메시지로 바꾼다. 문장은 기록된 값(skill·상태·숫자·id)으로만 만들고
// 부서가 돌려준 자유 문장(summary·note·untrusted 등)은 화면에 옮기지 않는다. 화면 없는 순수 함수만 둔다.

// ---------------------------------------------------------------- 총괄 JSON (ax-backend orchestrator models.py view())
export type StepStatus = "pending" | "running" | "held" | "completed" | "failed" | "skipped" | "rejected_by_human";

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
  output: Record<string, unknown>;
}

export interface OrchIncident {
  context_id: string;
  kind: string;
  title: string;
  component: string;
  status: "open" | "resolved" | "degraded" | string;
  opened_at: string | null;
  closed_at: string | null;
  steps: OrchStep[];
  events: { at: string | null; message: string }[];
  summary: string[];
  metrics: Record<string, number>;
  pending_holds: { step_id: string; skill: string; label: string }[];
}

/** POST /api/demo/incidents 본문 (main.py DemoRequest). 모두 선택 */
export interface DemoRequest {
  hold_rollback?: boolean; // 롤백이 C 등급 보류가 된다(사람 승인 대기). 총괄 목 모드 옵션 이름
  fail_marketing?: boolean;
  wrong_owner?: boolean;
  infra_card_empty?: boolean;
}

// ---------------------------------------------------------------- 이름표
export const DEPT_KO: Record<string, string> = {
  orchestrator: "중앙 Agent(총괄)", planning: "기획개발", support: "고객지원", marketing: "마케팅", hr: "인사",
  finance: "재무", data: "데이터분석", infra: "인프라", safety: "안전 관문", memory: "회사 기억", "shop-api": "판매 사이트",
};
const NOT_AGENT = new Set(["orchestrator", "safety", "memory", "shop-api"]);
/** 말하는 쪽 이름. 부서는 "{부서} Agent", 총괄·안전 관문 등은 그대로 */
export function speaker(dept: string | null | undefined): string {
  if (!dept) return "담당 미정";
  const ko = DEPT_KO[dept] ?? dept;
  return NOT_AGENT.has(dept) ? ko : `${ko} Agent`;
}

export const INCIDENT_STATUS_KO: Record<string, string> = { open: "진행 중", resolved: "해결", degraded: "일부 미완료" };

// 사건 이름. 총괄은 "payment 장애 감지 (error_rate_5xx)"처럼 코드로 주므로 사람이 읽는 말로 바꾼다
const COMPONENT_KO: Record<string, string> = { payment: "결제", api: "판매 사이트", db: "주문 DB", search: "검색", shipping: "배송" };
const METRIC_KO: Record<string, string> = {
  error_rate_5xx: "서버 오류(5xx) 급증", p95_latency_ms: "응답 지연", order_fail_rate: "주문 실패율 상승", payment_fail_rate: "결제 실패율 상승",
};
/** 총괄 제목 끝의 "(metric)"에서 지표 코드를 꺼낸다 */
function metricOf(inc: OrchIncident): string | null {
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

/** 방 이름: "결제 장애 — 서버 오류(5xx) 급증" */
export function incidentTitle(inc: OrchIncident): string {
  const comp = COMPONENT_KO[inc.component] ?? inc.component;
  const metric = metricOf(inc);
  return metric ? `${comp} 장애 — ${METRIC_KO[metric] ?? metric}` : `${comp} 장애`;
}

// skill·액션 이름표. 화면에는 이 이름만 보이고 코드 이름은 마우스를 올리면(툴팁) 보인다
const ACTION_KO: Record<string, string> = {
  // 총괄 시나리오 A
  diagnose_incident: "원인 진단", anomaly_detection: "이상 탐지", impact_analysis: "영향 분석",
  rollback_deployment: "배포 되돌리기", verify_recovery: "복구 확인", restart_service: "서비스 다시 켜기",
  find_related_prs: "관련 코드 변경 찾기", link_incident: "사건에 코드 변경 연결", verify_rollback_target: "되돌릴 배포 확인",
  draft_notice: "공지 초안 작성", publish_notice: "공지 게시", get_notice: "공지 조회",
  loss_aggregation: "손실 리포트", record_case: "사건 기록", check_plan: "예전 결정과 비교",
  // 돈이 나가는 일·안전 관문
  issue_coupon: "쿠폰 지급", refund_request: "환불", confirm_settlement: "판매자 정산 확정",
  seller_review: "판매자 입점 심사", settlement_hold: "판매자 정산 멈춤", hold_settlement: "판매자 정산 멈춤",
  verify_amount: "금액 다시 계산", review_hold: "보류 건 1차 판단", review_threshold_change: "기준 변경 판단",
  // 고객지원·기획개발·인사
  send_reply: "고객 답변 보내기", request_compensation: "보상 요청", create_claim: "클레임 접수", decide_claim: "클레임 결정",
  get_claim: "클레임 조회", receive_return: "반품 입고", inspect_return: "반품 검수", create_ticket: "변경 요청 접수",
  generate_pr: "코드 변경 만들기", merge_pr: "코드 변경 합치기", confirm_spec: "기획서 확정",
  start_handover: "인수인계 시작", answer_handover_question: "인수인계 질문 답변", decide_candidate_card: "후보 기록 결정",
};
export const actionKo = (a: string) => ACTION_KO[a] ?? a;
/** 코드 이름 → 사람이 읽는 이름. 모르는 이름은 밑줄을 띄어 쓴다 */
export const skillKo = (a: string | null | undefined) => (a ? ACTION_KO[a] ?? a.replace(/_/g, " ") : "");
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
  | { kind: "msg"; id: string; at: string; room: string; from: string; to: string; fromKey: string; central: boolean; text: string; tag?: string; raw?: string; tone: Tone; facts: Fact[] }
  | { kind: "system"; id: string; at: string; room: string; text: string }
  | { kind: "hold"; id: string; at: string; room: string; title: string; detail: string; state: "pending" | "approved" | "rejected" | "timeout"; contextId?: string; stepId?: string; link?: string }
  | { kind: "separator"; id: string; at: string; room: string; text: string; raw?: string; variant: "day" | "topic" };

export const SAFETY_ROOM = "safety";
/** 총괄 사건에 묶이지 않는 공통 로그 소통 (부서끼리 직접 주고받은 A2A) */
export const DIRECT_ROOM = "direct";
export const ALL_ROOM = "all";

// ---------------------------------------------------------------- 안전한 결과 값만 고르기
const FIELD_KO: Record<string, string> = {
  failed_order_count: "실패 주문", affected_customer_count: "영향 고객", damage_amount: "피해 금액", affected_customer_ids: "영향 고객 id",
  suspects: "의심 배포", impact_scope: "영향 범위", status: "판정", rolled_back_to: "되돌린 버전", candidates: "후보 PR", linked_paras: "연결",
  notice_id: "공지 id", eligible_customer_ids: "보상 대상", amount_per_customer: "1인 금액", issued: "지급", total_amount: "총액",
  failed_orders: "실패 주문", affected_customers: "영향 고객", lost_amount: "매출 손실", compensation_coupon_count: "보상 쿠폰",
  compensation_total: "보상 합계", total_loss: "총 손실", recovered: "복구", infra_metrics_ok: "지표 정상", card_id: "기록 id",
  channel_code: "게시 위치", state: "상태", match: "금액 일치", pr_number: "코드 변경 번호", severity: "심각도", grade: "등급",
};
/** 코드 값 → 사람이 읽는 말 */
const VALUE_KO: Record<string, string> = {
  published: "게시됨", draft: "초안", banner: "쇼핑몰 배너", popup: "팝업", email: "메일", resolved: "해결됨", open: "진행 중",
  pass: "통과", fail: "실패", approved: "승인", rejected: "거절", held: "보류", high: "높음", mid: "보통", low: "낮음",
  suspected: "의심됨", confirmed: "확정", unknown: "확인할 수 없음", rolled_back: "되돌림", recovered: "복구됨", degraded: "일부 미완료",
  completed: "완료", failed: "실패", skipped: "건너뜀", critical: "매우 높음",
  // 구성요소
  payment: "결제", api: "판매 사이트", db: "주문 DB", search: "검색", shipping: "배송",
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
    const label = FIELD_KO[key] ?? key;
    if (typeof value === "number" && Number.isFinite(value)) {
      facts.push({ label, value: MONEY.test(key) ? `${fmtNum(value)}원` : fmtNum(value) });
    } else if (typeof value === "boolean") {
      facts.push({ label, value: value ? "예" : "아니오" });
    } else if (typeof value === "string" && SAFE_TOKEN.test(value)) {
      facts.push({ label, value: VALUE_KO[value] ?? value });
    } else if (Array.isArray(value)) {
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

// ---------------------------------------------------------------- 총괄 사건 → 메시지
const CENTRAL = "orchestrator";

function transferFrom(inc: OrchIncident, step: OrchStep): string | null {
  // 엔진은 이관하면 step.dept를 새 부서로 바꾼다. 원래 부서는 이벤트 "label: A → B 이관(...)"에만 남는다.
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
      return { text: `${stepLabel(step.label)} 완료했습니다.${retryTxt}`, tone: "ok" };
    case "failed": {
      if (step.note.startsWith("canceled")) return { text: `시간 안에 끝내지 못했습니다 — 시간 초과${retryTxt}.`, tone: "fail" };
      if (step.note.startsWith("이관")) return { text: `소관이 아니라고 답했고, 이관할 부서를 찾지 못했습니다.`, tone: "fail" };
      return { text: `실패했습니다${retries > 0 ? ` — ${retries}회 재시도 후 중단` : ""}.`, tone: "fail" };
    }
    case "rejected_by_human":
      return { text: step.note === "시간 초과로 자동 거절" ? "결정 기한이 지나 자동 거절되었습니다." : "사람이 거절해 진행하지 않았습니다.", tone: "warn" };
    case "held":
      return { text: "C 등급이라 사람 승인을 기다립니다. 보류 중입니다.", tone: "warn" };
    case "skipped":
      return { text: "건너뛰었습니다.", tone: "muted" };
    default:
      return { text: step.status, tone: "muted" };
  }
}

const SKIP_WHY: [RegExp, string][] = [
  [/^선행 단계 미완료/, "앞 단계가 끝나지 않아 건너뜀"],
  [/Agent Card에 skill 없음/, "맡을 부서를 찾지 못해 건너뜀 (Agent Card에 skill 없음)"],
  [/allowed_requesters/, "총괄이 요청할 수 없는 skill이라 건너뜀"],
  [/^고객 자료 대기/, "고객 자료를 기다려 보류"],
];

export function incidentItems(inc: OrchIncident): ChatItem[] {
  const room = inc.context_id;
  const out: ChatItem[] = [];
  const opened = inc.opened_at ?? inc.events[0]?.at ?? "";
  out.push({ kind: "separator", id: `${room}:sep`, at: opened, room, text: `${incidentTitle(inc)}${opened ? ` · ${clockKo(opened)} 시작` : ""}`, raw: `사건 번호 ${inc.context_id}`, variant: "topic" });
  out.push({ kind: "system", id: `${room}:open`, at: opened, room, text: `판매 사이트에서 ${incidentTitle(inc)} 신호가 와서 사건이 열렸습니다.` });

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
        kind: "hold", id: `${sid}:hold`, at: holdAt, room, contextId: inc.context_id, stepId: step.step_id, state,
        title: stepLabel(step.label), detail: `${speaker(step.dept)} · ${skillKo(step.skill)}`,
      });
      if (pending) continue;
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
    out.push({ kind: "system", id: `${room}:close`, at: inc.closed_at, room, text: `사건 종료 — ${INCIDENT_STATUS_KO[inc.status] ?? inc.status}${parts.length ? ` · ${parts.join(" · ")}` : ""}` });
  }
  return out;
}

function heldAt(inc: OrchIncident, step: OrchStep): string | null {
  return inc.events.find((e) => e.message === `${step.label}: C 등급 보류 — 승인 대기열에 올림`)?.at ?? null;
}

/** 재시도처럼 단계 메시지에 안 드러나는 이벤트만 시스템 줄로. (요청·완료·종료 이벤트는 메시지와 겹친다) */
export function incidentEventItems(inc: OrchIncident): ChatItem[] {
  return inc.events
    .map((e, i) => ({ e, i }))
    .filter(({ e }) => / 재시도 \d+$/.test(e.message) || /: 사람이 승인 — 재개$/.test(e.message) || e.message.startsWith("같은 사건에 합침"))
    .map(({ e, i }) => {
      if (e.message.startsWith("같은 사건에 합침")) {
        return { kind: "system" as const, id: `${inc.context_id}:ev${i}`, at: e.at ?? inc.opened_at ?? "", room: inc.context_id, text: "같은 장애 신호가 다시 들어와 진행 중인 이 사건에 합쳤습니다" };
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
export function executionItems(rec: ExecutionRecord): ChatItem[] {
  const room = SAFETY_ROOM;
  const d = rec.decision;
  const req = rec.request;
  const id = `exe:${rec.execution_id}`;
  const amount = d.computed_amount != null ? ` (금액 ${fmtNum(d.computed_amount)}원)` : "";
  const out: ChatItem[] = [{
    kind: "msg", id: `${id}:req`, at: rec.created_at, room, from: speaker(req.requester), to: speaker("safety"), fromKey: req.requester, central: false,
    text: `${actionKo(req.action)} 실행 요청${amount}`, tag: "안전 관문 기록", raw: rawOf(`action: ${req.action}`, req.context_id), tone: "request", facts: [],
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
  });
  if (d.status === "held") {
    out.push({
      kind: "hold", id: `${id}:hold`, at: rec.created_at, room, state: "pending", link: "/finance",
      title: `${speaker(req.requester)}의 ${actionKo(req.action)}${amount}`, detail: "재무 AI 화면(/finance)에서 결정합니다",
    });
  }
  return out;
}

function verifierName(v: string) {
  return v === "finance" ? "재무 AI" : speaker(v);
}

// ---------------------------------------------------------------- 공통 로그 소통 → 메시지
const COMM_TONE: Record<CommOutcome, Tone> = { completed: "ok", rejected: "fail", held: "warn", needs_human: "warn", in_progress: "request" };

/** 소통 한 줄이 들어갈 방: 총괄 사건과 context_id가 같으면 그 사건 방, 아니면 "부서 간 직통" */
export function commRoom(line: CommLine, incidentIds: ReadonlySet<string>): string {
  return line.context_id && incidentIds.has(line.context_id) ? line.context_id : DIRECT_ROOM;
}

/** 총괄 사건 방에서 총괄이 보내거나 받은 줄인데 같은 skill 단계가 이미 있으면 총괄 기록이 같은 일을 보여 준다 */
function coveredByIncident(line: CommLine, inc: OrchIncident | undefined): boolean {
  if (!inc || !line.skill) return false;
  if (line.from_agent !== CENTRAL && line.to_agent !== CENTRAL) return false;
  return inc.steps.some((s) => s.skill === line.skill);
}

/**
 * 공통 로그 한 줄 → 말풍선. 문장은 skill·결과 값으로만 만든다(payload는 dashboard-api가 이미 뺐다).
 * `execution_id`가 안전 관문 기록과 같으면 `dupOf`에 그 id를 넣는다 — 전체 방에서는 빼고, 사건 방에서는 연결 표시만 단다.
 */
export function commItem(line: CommLine, room: string, safetyIds: ReadonlySet<string>): Extract<ChatItem, { kind: "msg" }> & { dupOf?: string } {
  const skill = line.skill ?? "무슨 일인지 기록 없음";
  const label = COMM_OUTCOME_KO[line.outcome] ?? line.outcome;
  const dupOf = line.execution_id && safetyIds.has(line.execution_id) ? line.execution_id : undefined;
  const tag = ["공통 로그 기록", dupOf ? "안전 관문 방에 같은 건이 있음" : null].filter(Boolean).join(" · ");
  const raw = rawOf(line.skill ? `skill: ${line.skill}` : null, line.execution_id, line.context_id);
  const holdHint = line.outcome === "held" && !dupOf ? " — 안전 관문 보류 큐(/finance)에서 결정" : "";
  return {
    kind: "msg", id: `log:${line.log_id}`, at: line.occurred_at, room,
    from: speaker(line.from_agent), to: line.to_agent ? speaker(line.to_agent) : "받는 쪽 미기록",
    fromKey: line.from_agent, central: line.from_agent === CENTRAL,
    text: `${line.skill ? skillKo(line.skill) : skill}: ${label}${holdHint}`, tag, raw, tone: COMM_TONE[line.outcome] ?? "muted", facts: [], dupOf,
  };
}

/** 소통 줄을 방별 말풍선으로. 총괄 기록과 겹치는 줄은 빼고, 안전 관문과 겹치는 줄은 `dupOf`를 단다 */
export function commItems(comms: CommLine[], incidents: OrchIncident[], executions: ExecutionRecord[]) {
  const byId = new Map(incidents.map((i) => [i.context_id, i]));
  const ids = new Set(byId.keys());
  const safetyIds = new Set(executions.map((e) => e.execution_id));
  return comms
    .map((line) => ({ line, room: commRoom(line, ids) }))
    .filter(({ line, room }) => !coveredByIncident(line, byId.get(room)))
    .map(({ line, room }) => commItem(line, room, safetyIds));
}

// ---------------------------------------------------------------- 합치기
const time = (iso: string) => (iso ? new Date(iso).getTime() : 0);

/** 방 하나(또는 전체)의 타임라인. 시간순, 같은 시각이면 만들어진 순서를 지킨다 */
export function buildTimeline(incidents: OrchIncident[], executions: ExecutionRecord[], comms: CommLine[], room: string): ChatItem[] {
  const items: ChatItem[] = [];
  for (const inc of incidents) {
    if (room !== ALL_ROOM && room !== inc.context_id) continue;
    items.push(...incidentItems(inc), ...incidentEventItems(inc));
  }
  if (room === ALL_ROOM || room === SAFETY_ROOM) {
    const recs = [...executions].sort((a, b) => time(a.created_at) - time(b.created_at));
    for (const rec of recs) items.push(...executionItems(rec));
  }
  for (const c of commItems(comms, incidents, executions)) {
    // 전체 방에서는 안전 관문 기록과 같은 건(execution_id 일치)을 두 번 보이지 않는다
    if (room === ALL_ROOM ? !c.dupOf : room === c.room) {
      const { dupOf: _dup, ...msg } = c;
      items.push(msg);
    }
  }
  const indexed = items.map((it, i) => ({ it, i }));
  indexed.sort((a, b) => time(a.it.at) - time(b.it.at) || a.i - b.i);
  return withDaySeparators(indexed.map((x) => x.it));
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
  return items.filter((i) => i.kind === "msg").length;
}
