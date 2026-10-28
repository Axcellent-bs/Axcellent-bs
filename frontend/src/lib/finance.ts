import { ApiError } from "./apiError";
import { FINANCE_AGENT_URL, SAFETY_API_URL, SAFETY_HUMAN_TOKEN } from "./env";
import { humanizeIds, incidentNo } from "./format";

// 재무 화면 → finance(8105) · safety(8001) API. 응답은 ax-backend 규약 {data, meta} / {error: {code, message}}.
// 재무 AI = 캣북스의 재무팀 (결산·대사·판매자 정산·예산 한도·사후 감사).
// 형식 기준: ax-backend docs/notes/finance/2026-10-23-finance-redesign.md §11·§12-1. 필드 이름을 바꾸지 않는다.

// ---------------------------------------------------------------- 공통 봉투
export interface FinanceMeta {
  mock: boolean;
  /** §12-1 사건 응답에는 없을 수 있다 */
  collected_at?: string;
  source?: "shop-api" | "seed" | string;
  /** §12-1 사건 목록: 쿠폰 장부를 어디서 채웠는지 */
  ledger_source?: LedgerSource;
  /** 값을 못 받아 null로 둔 필드 이름 */
  unavailable?: string[];
}
export interface Envelope<T> { data: T; meta: FinanceMeta }

// ---------------------------------------------------------------- §11 overview
export type TodoKind = "settlement_batch" | "closing_confirm" | "recon_exception" | "audit_finding" | "policy_alert";
export interface FinanceTodo { kind: TodoKind; title: string; count: number | null; amount: number | null; link: string | null }
export interface FinanceKpis {
  gmv: number | null;
  commission_revenue: number | null;
  refund_total: number | null;
  coupon_cost: number | null;
  pg_fee: number | null;
  net_result: number | null;
}
export interface FinanceOverview { date: string; kpis: FinanceKpis; todo: FinanceTodo[] }

// ---------------------------------------------------------------- ① 결산·리포트
export type ClosingPeriod = "day" | "month";
export interface ClosingLine { code: string; label: string; amount: number | null }
export interface JournalEntry { account: string; debit: number; credit: number; memo: string }
export interface ClosingReport {
  period: ClosingPeriod;
  date: string;
  from: string;
  to: string;
  lines: ClosingLine[];
  journal: JournalEntry[];
  commentary: string;
  commentary_source: "llm" | "template";
  status: "draft" | "confirmed";
  confirmed_by: string | null;
  confirmed_at: string | null;
}

// ---------------------------------------------------------------- ② 매출·PG 대사
export type ReconStatus = "matched" | "mismatch" | "missing_in_pg" | "pending" | "fee_anomaly";
export interface ReconItem {
  order_id: string;
  payment_id: string;
  paid_at: string | null;
  order_amount: number | null;
  pg_amount: number | null;
  fee: number | null;
  expected_fee: number | null;
  pay_out_amount: number | null;
  paid_out_date: string | null;
  status: ReconStatus;
  reason: string | null;
  note: string | null;
}
export interface Reconciliation {
  from: string;
  to: string;
  counts: Partial<Record<ReconStatus, number>>;
  totals: { order_amount: number | null; pg_amount: number | null; pg_fee: number | null; pay_out: number | null };
  items: ReconItem[];
}

// ---------------------------------------------------------------- ③ 판매자 정산
export type StatementStatus = "ready" | "held" | "requested" | "approved" | "rejected";
export type BatchStatus = "none" | "requested" | "approved" | "rejected";
export interface HoldReason { code: string; label: string }
export interface SellerStatement {
  seller_id: string;
  name: string;
  orders: number;
  gross: number | null;
  commission_rate: number | null;
  commission: number | null;
  seller_coupon: number | null;
  refunds: number | null;
  payout: number | null;
  hold_reasons: HoldReason[];
  status: StatementStatus;
  review_id: string | null;
}
export interface SettlementBatch { status: BatchStatus; review_id: string | null; execution_id: string | null; total_payout: number; seller_ids: string[] }
export interface Settlements { period: string; from: string; to: string; pay_date: string | null; statements: SellerStatement[]; batch: SettlementBatch }

// ---------------------------------------------------------------- ⑤ 예산·한도 정책
export type PolicyAlert = "ok" | "warn" | "over";
export interface PolicyRule {
  key: string;
  label: string;
  limit: number | null;
  unit: string;
  used_today: number | null;
  used_month: number | null;
  ratio: number | null;
  alert: PolicyAlert | null;
  source?: string;
}
export interface PolicyProposal { key: string; current: number | null; proposed: number; reason: string; evidence?: Record<string, unknown> }
export interface Policy { rules: PolicyRule[]; proposals: PolicyProposal[]; how_to_change: string }

// ---------------------------------------------------------------- ⑦ 내부통제·감사
export type FindingSeverity = "high" | "medium" | "low";
export interface AuditFinding { code: string; severity: FindingSeverity; title: string; detail: string; refs: string[] }
export interface AuditStats {
  refund_count: number | null;
  refund_amount: number | null;
  refund_rate: number | null;
  refund_rate_prev: number | null;
  coupon_count: number | null;
  coupon_amount: number | null;
  gate_held: number | null;
  gate_rejected: number | null;
}
export interface AuditReport { from: string; to: string; stats: AuditStats; findings: AuditFinding[] }

// ---------------------------------------------------------------- §12 시나리오 A: 장애 사건 재무
// ⑨ 쿠폰 장부 기록 → ⑩ 손실 리포트(피해 + 보상) → ⑰ 사건 후 감사. 쿠폰 건별 승인은 안전 장치(A/C)가 한다.
/** 장부 출처: 이벤트 버스(coupon.issued) / 안전 장치 실행 기록(issue_coupon) / 목 시드 */
export type LedgerSource = "events" | "safety_executions" | "seed";

export interface IncidentSummary {
  context_id: string;
  component: string | null;
  opened_at: string | null;
  damage_amount: number | null;
  compensation_total: number;
  coupon_count: number;
  held_count: number;
  checks_ok: boolean | null;
}

export interface ImpactCategory { category: string; orders: number; amount: number }
export interface IncidentImpact {
  failed_orders: number;
  affected_customers: number;
  damage_amount: number;
  by_category: ImpactCategory[];
  affected_customer_ids: string[];
  source: string;
  collected_at: string | null;
}

export type CouponStatus = "executed" | "held" | "rejected" | "failed";
export interface IncidentCoupon {
  buyer_id: string;
  execution_id: string;
  coupon_id: string | null;
  count: number;
  unit_value: number;
  amount: number;
  purpose_code: string;
  reason_code: string | null;
  grade: "A" | "C";
  status: CouponStatus;
  review_id: string | null;
  at: string;
  /** 데이터 영향 고객 목록에 있는지. null = 영향 고객 목록이 없어 확인 불가 */
  in_impact: boolean | null;
}
export interface IncidentCompensation {
  coupons: IncidentCoupon[];
  total_executed: number;
  total_held: number;
  count_executed: number;
  count_held: number;
}

export interface IncidentLimits {
  per_customer: { limit: number | null; max_used: number; buyer_id: string | null };
  per_incident: { limit: number | null; used: number; remaining: number | null };
}

export type IncidentCheckCode = "recipients_in_impact" | "one_coupon_per_buyer" | "per_customer_limit" | "per_incident_limit" | "amount_recheck";
export interface IncidentCheck { code: IncidentCheckCode | string; ok: boolean | null; title: string; detail: string; refs: string[] }


export interface IncidentDetail {
  context_id: string;
  component: string | null;
  window: { from: string; to: string } | null;
  impact: IncidentImpact | null;
  compensation: IncidentCompensation;
  limits: IncidentLimits;
  checks: IncidentCheck[];
  ledger_source: LedgerSource;
}

// ---------------------------------------------------------------- 안전 장치 보류 큐 (safety)
export interface RuleHit { key: string; observed?: number; op?: string; value?: number | string; outside?: string[]; invalid?: string }
// 안전 장치 실행 기록 (AI 대화방이 부서 간 실행 흐름을 그릴 때 읽는다. safety README 형식)
export type Grade = "A" | "B" | "C";
export type ExecStatus = "executed" | "verifying" | "held" | "rejected" | "failed";

export interface ExecuteDecision {
  execution_id: string;
  grade: Grade;
  status: ExecStatus;
  computed_amount: number | null;
  threshold_version: number | null;
  verifier: string | null;
  hold_reason: string | null;
  review_id: string | null;
}

export interface ExecutionRecord {
  execution_id: string;
  created_at: string;
  request: { action: string; requester: string; context_id: string; params: Record<string, unknown>; claimed_grade: string | null };
  decision: ExecuteDecision;
  customer: string | null;
}

export interface Review {
  review_id: string;
  execution_id: string;
  context_id: string;
  action: string;
  requester: string;
  created_at: string;
  expires_at: string;
  status: "pending" | "approved" | "rejected" | "expired";
  decided_by: string | null;
  decided_at: string | null;
  note: string | null;
  report: { totals?: Record<string, number | null>; calculation?: string; computed_amount?: number | null; hold_reason?: string; rules?: RuleHit[]; claimed?: Record<string, number>; reason?: string };
}

// ---------------------------------------------------------------- 유지되는 기능
export interface MarketOp { skill: "seller_review" | "settlement_hold"; seller_id: string; decision?: string; status?: string; risk_score?: number | null; reason_code: string; review_id: string | null; at: string }
export interface SelfTestCheck { name: string; ok: boolean; detail: string; level: "fail" | "warn" }
export interface SelfTest {
  result: "pass" | "warn" | "fail";
  summary: string;
  checks: SelfTestCheck[];
  passed: number;
  total: number;
  duration_ms: number;
  ran_at: string;
  /** 자동(10분마다) / 사람이 직접. 예전 서버에는 없다 */
  trigger?: "auto" | "manual";
}
/** GET /self-test/last 의 meta. 새 필드는 예전 서버에 없을 수 있다 */
export interface SelfTestLastMeta {
  /** 다음 자동 테스트 시각(ISO). 자동 테스트가 꺼져 있으면 null */
  next_run_at?: string | null;
  /** 자동 테스트 간격(초) */
  interval_seconds?: number;
  ttl_hours?: number;
}

// ---------------------------------------------------------------- 호출
/** 실패 정보. `status` 0은 서버에 닿지 못함(꺼져 있음·주소 틀림·CORS) */
export class FinanceApiError extends ApiError {
  constructor(message: string, status: number, public code: string | null = null) {
    super(message, status);
  }
}

/** "재무 AI 서버(8105)" 처럼 주소에서 포트를 꺼내 쉬운 이름을 만든다 */
export function serverName(base: string, name: string): string {
  try {
    const u = new URL(base);
    const port = u.port || (u.protocol === "https:" ? "443" : "80");
    return `${name}(${port})`;
  } catch {
    return name;
  }
}
export const FINANCE_SERVER = serverName(FINANCE_AGENT_URL, "재무 AI 서버");
export const SAFETY_SERVER = serverName(SAFETY_API_URL, "안전 장치 서버");

export const humanTokenSet = SAFETY_HUMAN_TOKEN.length > 0;

interface CallOptions { method?: "GET" | "POST"; body?: unknown; human?: boolean }

async function call(base: string, server: string, path: string, opts: CallOptions = {}): Promise<unknown> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.human && humanTokenSet) headers.Authorization = `Bearer ${SAFETY_HUMAN_TOKEN}`;
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      cache: "no-store",
    });
  } catch {
    throw new FinanceApiError(`${server}에 연결하지 못했습니다. 서버가 켜져 있는지 확인하세요.`, 0, "unreachable");
  }
  const body = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null;
  if (!res.ok) {
    const code = body?.error?.code ?? null;
    const message = body?.error?.message
      ?? (res.status === 404 ? `${server}에 아직 이 기능이 없습니다 (404).`
        : res.status === 401 || res.status === 403 ? "사람 권한이 없어 처리하지 못했습니다. VITE_SAFETY_HUMAN_TOKEN을 확인하세요."
          : `요청에 실패했습니다 (${res.status}).`);
    throw new FinanceApiError(message, res.status, code);
  }
  return body;
}

const fin = <T>(path: string, opts?: CallOptions) => call(FINANCE_AGENT_URL, FINANCE_SERVER, path, opts) as Promise<T>;
const saf = <T>(path: string, opts?: CallOptions) => call(SAFETY_API_URL, SAFETY_SERVER, path, opts) as Promise<T>;
const data = async <T>(p: Promise<{ data: T }>) => (await p).data;

/** 쿼리 문자열. 값이 비어 있으면 빼서 서버 기본값(오늘·최근 7일·지난주)을 쓰게 한다 */
export function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const finance = {
  safetyHealth: () => saf<{ status: string }>("/health"),
  /** 안전 장치 실행 기록 (AI 대화방에서 씀) */
  executions: () => data(saf<{ data: ExecutionRecord[] }>("/api/safety/executions")),
  financeHealth: () => fin<{ status: string }>("/health"),

  // 재무팀 5개 모듈
  overview: () => fin<Envelope<FinanceOverview>>("/api/finance/overview"),
  closing: (period: ClosingPeriod, date?: string | null) => fin<Envelope<ClosingReport>>(`/api/finance/closing${query({ period, date })}`),
  /** 결산 확정. 사람 토큰만 받는다 */
  confirmClosing: (period: ClosingPeriod, date: string) =>
    fin<Envelope<ClosingReport>>("/api/finance/closing/confirm", { method: "POST", body: { period, date }, human: true }),
  reconciliation: (date?: string | null) => fin<Envelope<Reconciliation>>(`/api/finance/reconciliation${query({ date })}`),
  /** 대사 불일치 건 처리 메모. 사람 토큰만 */
  reconNote: (paymentId: string, note: string) =>
    fin<Envelope<ReconItem>>(`/api/finance/reconciliation/${encodeURIComponent(paymentId)}/note`, { method: "POST", body: { note }, human: true }),
  settlements: (period?: string | null) => fin<Envelope<Settlements>>(`/api/finance/settlements${query({ period })}`),
  /** 보류 없는 판매자를 묶어 지급 승인 요청 → 안전 장치 보류 큐(C). 돈은 움직이지 않는다 */
  requestSettlementBatch: (period: string) =>
    fin<Envelope<Settlements | SettlementBatch>>("/api/finance/settlements/batch", { method: "POST", body: { period }, human: true }),
  policy: () => fin<Envelope<Policy>>("/api/finance/policy"),
  audit: (days = 7) => fin<Envelope<AuditReport>>(`/api/finance/audit${query({ days })}`),

  // §12-1 장애 사건 재무 (시나리오 A)
  incidents: () => fin<Envelope<IncidentSummary[]>>("/api/finance/incidents"),
  /** 없는 사건이면 404 {"error": {"code": "not_found"}} */
  incident: (contextId: string) => fin<Envelope<IncidentDetail>>(`/api/finance/incidents/${encodeURIComponent(contextId)}`),

  // 유지
  marketOps: () => data(fin<{ data: MarketOp[] }>("/api/finance/market-ops")),
  /** 재무 AI 테스트를 지금 한 번 더 한다(서버는 10분마다 자동으로 한다). 가짜 자료로 점검해 상태를 바꾸지 않음 */
  selfTest: () => data(fin<{ data: SelfTest }>("/api/finance/self-test")),
  /** 가장 최근 테스트 결과(자동·직접) + 다음 자동 테스트 시각 */
  lastSelfTest: () => fin<{ data: SelfTest | null; meta?: SelfTestLastMeta }>("/api/finance/self-test/last"),
  // 안전 장치 보류 큐 (정산 묶음 지급 승인 등 C 등급)
  reviews: () => data(saf<{ data: Review[] }>("/api/safety/reviews")),
  /** C 보류 결정. 사람 토큰만 받는다 (에이전트 토큰은 safety가 거부). */
  decide: (reviewId: string, decision: "approved" | "rejected", note?: string) =>
    data(saf<{ data: Review }>(`/api/safety/reviews/${encodeURIComponent(reviewId)}/decision`, { method: "POST", body: { decision, note: note || null }, human: true })),
};

// ---------------------------------------------------------------- 표시 도우미 (쉬운 말)
export const UNKNOWN = "확인할 수 없음";

/** 1250000 → "1,250,000원". 값이 없으면 "확인할 수 없음" (추정값을 만들지 않는다) */
export function krw(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return UNKNOWN;
  return `${Math.round(n).toLocaleString("ko-KR")}원`;
}

/** 손익 줄처럼 부호가 뜻이 있는 금액: -42000 → "−42,000원", 42000 → "42,000원" */
export function signedKrw(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return UNKNOWN;
  return n < 0 ? `−${krw(-n)}` : krw(n);
}

/** 0.052 → "5.2%". 값이 없으면 "확인할 수 없음" */
export function percent(r: number | null | undefined, digits = 1): string {
  if (r == null || !Number.isFinite(r)) return UNKNOWN;
  const v = r * 100;
  return `${Number.isInteger(v) ? v.toFixed(0) : v.toFixed(digits)}%`;
}

/** 단위가 있는 값: 원이면 금액, 아니면 "3건"·"70점" */
export function withUnit(n: number | null | undefined, unit: string): string {
  if (unit === "원") return krw(n);
  if (n == null || !Number.isFinite(n)) return UNKNOWN;
  return `${n.toLocaleString("ko-KR")}${unit}`;
}

/** 환불률 변화를 쉬운 말로: 5.2% vs 3.1% → "지난주보다 2.1%p 높음" */
export function rateChange(now: number | null | undefined, prev: number | null | undefined): string {
  if (now == null || prev == null) return "지난주와 비교할 수 없음";
  const d = Math.round((now - prev) * 1000) / 10;
  if (d === 0) return "지난주와 같음";
  return `지난주보다 ${Math.abs(d).toFixed(1)}%p ${d > 0 ? "높음" : "낮음"}`;
}

/** 사용률 막대 폭 (0~100). 값이 없으면 0 */
export const barWidth = (ratio: number | null | undefined) => (ratio == null || !Number.isFinite(ratio) ? 0 : Math.max(0, Math.min(100, ratio * 100)));

/** 화면에 기본으로 보여 줄 대사 상태: 일치(matched)는 숨긴다 */
export const RECON_ORDER: ReconStatus[] = ["mismatch", "missing_in_pg", "fee_anomaly", "pending", "matched"];
export const DEFAULT_RECON_FILTER: ReconStatus[] = RECON_ORDER.filter((s) => s !== "matched");

/** 지급 준비된(보류 없는) 판매자 정산금 합계. 값이 없는 판매자가 있으면 null */
export function readyPayout(statements: SellerStatement[]): number | null {
  const ready = statements.filter((s) => s.status === "ready");
  if (ready.some((s) => s.payout == null)) return null;
  return ready.reduce((n, s) => n + (s.payout ?? 0), 0);
}

/** 정산 묶음 요청 응답은 정산 전체이거나 batch만일 수 있어 batch를 꺼낸다 */
export function batchOf(res: Settlements | SettlementBatch): SettlementBatch {
  return "statements" in res ? res.batch : res;
}

/** 오늘 날짜(한국 시간) YYYY-MM-DD */
export function todayKst(now = new Date()): string {
  return new Date(now.getTime() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

/** todo.link 또는 kind → 화면 탭 id */
export type FinanceTab = "closing" | "reconciliation" | "settlements" | "policy" | "audit";
export const FINANCE_TABS: FinanceTab[] = ["closing", "reconciliation", "settlements", "policy", "audit"];
const TODO_TAB: Record<TodoKind, FinanceTab> = {
  settlement_batch: "settlements", closing_confirm: "closing", recon_exception: "reconciliation", audit_finding: "audit", policy_alert: "policy",
};
export function todoTab(t: Pick<FinanceTodo, "kind" | "link">): FinanceTab {
  const link = (t.link ?? "").replace(/^#/, "");
  if ((FINANCE_TABS as string[]).includes(link)) return link as FinanceTab;
  return TODO_TAB[t.kind] ?? "closing";
}

// ---------------------------------------------------------------- 장애 사건 표시 도우미
export const COMPONENT_KO: Record<string, string> = { payment: "결제", order: "주문", shop: "쇼핑몰", search: "검색", delivery: "배송" };
export const componentKo = (c: string | null | undefined) => (c ? COMPONENT_KO[c] ?? "서비스" : null);

/** "ctx_inc_scn_a_0001" + "payment" → "장애 #0001 · 결제". 장애가 난 곳을 모르면 "장애 #0001" */
export function incidentName(contextId: string, component?: string | null): string {
  const where = componentKo(component);
  return `장애 #${incidentNo(contextId)}${where ? ` · ${where}` : ""}`;
}

/** 최근 사건이 먼저. 시각이 없는 사건은 뒤로 */
export function sortIncidents(list: IncidentSummary[]): IncidentSummary[] {
  return [...list].sort((a, b) => {
    if (a.opened_at && b.opened_at) return b.opened_at.localeCompare(a.opened_at);
    if (a.opened_at) return -1;
    if (b.opened_at) return 1;
    return b.context_id.localeCompare(a.context_id);
  });
}

export const LEDGER_SOURCE_KO: Record<LedgerSource, { label: string; detail: string }> = {
  events: { label: "이벤트 알림", detail: "마케팅 AI가 쿠폰을 줄 때 보낸 알림을 받아 장부에 적었습니다." },
  safety_executions: { label: "안전 장치 실행 기록", detail: "알림을 받을 수 없어, 안전 장치가 남긴 쿠폰 지급 실행 기록을 읽어 장부를 채웠습니다." },
  seed: { label: "예시 데이터", detail: "연습용 예시 자료입니다. 실제 쿠폰 지급 기록이 아닙니다." },
};
export const ledgerSourceKo = (s: LedgerSource | string | null | undefined) =>
  s && s in LEDGER_SOURCE_KO ? LEDGER_SOURCE_KO[s as LedgerSource] : { label: s ? "기타 자료" : UNKNOWN, detail: "" };

// ---------------------------------------------------------------- 코드 → 쉬운 말 (화면에 영어 코드를 그대로 보이지 않는다)
/** 모르는 코드일 때 쓰는 말 */
export const OTHER_CHECK = "기타 점검";

/** ⑦ 사후 감사 의심 건 종류 */
export const FINDING_KO: Record<string, string> = {
  split_under_limit: "한도 바로 아래로 쪼개기",
  repeat_refund: "같은 고객 반복 환불",
  duplicate_coupon: "같은 고객 쿠폰 중복",
  refund_rate_jump: "환불률 급변",
  amount_recheck_failed: "금액 재계산 불일치",
  gate_hold_trend: "안전 장치 보류·거절 증가",
  compensation_outside_impact: "영향 고객 밖 보상",
};
export const findingKo = (code: string) => FINDING_KO[code] ?? OTHER_CHECK;

/** ⑰ 사건 후 감사 점검 항목 */
export const CHECK_KO: Record<IncidentCheckCode, string> = {
  recipients_in_impact: "보상 받은 고객이 모두 영향 고객인지",
  one_coupon_per_buyer: "고객당 쿠폰 1건",
  per_customer_limit: "고객당 보상 한도",
  per_incident_limit: "사건당 보상 한도",
  amount_recheck: "보상 금액 다시 계산",
};
export const checkKo = (code: string, title?: string | null) =>
  CHECK_KO[code as IncidentCheckCode] ?? (title && !/^[a-z0-9_]+$/.test(title) ? title : OTHER_CHECK);

/** 쿠폰을 준 이유·목적 */
export const PURPOSE_KO: Record<string, string> = {
  payment_failure: "결제 실패 보상", compensation: "장애 보상", campaign: "마케팅 쿠폰", seller_promo: "판매자 할인 쿠폰",
};
export const purposeKo = (...codes: (string | null | undefined)[]) => {
  for (const c of codes) if (c && PURPOSE_KO[c]) return PURPOSE_KO[c];
  return "보상";
};

export const ACTION_KO: Record<string, string> = {
  issue_coupon: "쿠폰 지급", refund_request: "환불", confirm_settlement: "판매자 정산 지급",
  seller_review: "판매자 입점 승인", settlement_hold: "판매자 정산 멈춤", publish_notice: "공지 올리기",
  rollback_deployment: "배포 되돌리기", restart_service: "서비스 다시 켜기", generate_pr: "코드 변경 만들기",
};

/** 설정 파일 이름 → 쉬운 말 */
const SOURCE_FILE_KO: Record<string, string> = { "thresholds.toml": "안전 장치 기준표", "finance_policy.toml": "재무 정책표" };
export const sourceFileKo = (s: string | null | undefined) => (s ? SOURCE_FILE_KO[s] ?? s : "");

/** "2026-W43" → "2026년 43주차". 모양이 다르면 그대로 */
export function periodKo(p: string): string {
  const m = /^(\d{4})-W(\d{1,2})$/.exec(p);
  return m ? `${m[1]}년 ${Number(m[2])}주차` : p;
}

/**
 * 서버가 보낸 문장을 화면용으로: 기록 번호 → 쉬운 말, 남아 있는 개발자 말·코드 → 한국어.
 * (예전 서버가 보낸 "출처 seed", "data 영향 고객", "의심: per_incident_limit" 같은 문장도 고친다)
 */
export function plainText(text: string | null | undefined, sellers?: Record<string, string>): string {
  if (!text) return "";
  return humanizeIds(text, sellers)
    .replace(/(안전|실행) 관문/g, "안전 장치")
    .replace(/\bdata 영향/g, "데이터 AI 영향")
    .replace(/출처:? (events|safety_executions|seed)\b/g, (_, src: string) => `출처: ${ledgerSourceKo(src).label}`)
    .replace(/\b(issue_coupon|refund_request|confirm_settlement)\b/g, (c: string) => ACTION_KO[c] ?? c)
    .replace(/\b(damage_amount|compensation_total)\b/g, (c: string) => (c === "damage_amount" ? "피해 금액" : "보상 합계"))
    .replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (c: string) => CHECK_KO[c as IncidentCheckCode] ?? FINDING_KO[c] ?? c);
}
