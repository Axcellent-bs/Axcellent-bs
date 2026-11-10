import { ApiError } from "./apiError";
import { FINANCE_AGENT_URL, SAFETY_API_URL, SAFETY_HUMAN_TOKEN } from "./env";
import { humanizeIds, incidentNo } from "./format";
import { ExampleMissing, exampleCall, exampleOn } from "./financeExample";

// 재무 화면 → finance(8105) · safety(8001) API. 응답은 ax-backend 규약 {data, meta} / {error: {code, message}}.
// 재무 AI = 고객사의 재무팀 (결산·대사·판매자 정산·팀 예산·쿠폰·환불 장부).
// 형식 기준: ax-backend docs/notes/finance/2026-10-23-finance-redesign.md §11·§12-1. 필드 이름을 바꾸지 않는다.

// ---------------------------------------------------------------- 공통 봉투
export interface FinanceMeta {
  mock: boolean;
  /** §12-1 이벤트 응답에는 없을 수 있다 */
  collected_at?: string;
  source?: "shop-api" | "seed" | string;
  /** §12-1 이벤트 목록: 쿠폰 장부를 어디서 채웠는지 */
  ledger_source?: LedgerSource;
  /** 값을 못 받아 null로 둔 필드 이름 */
  unavailable?: string[];
}
export interface Envelope<T> { data: T; meta: FinanceMeta }

// ---------------------------------------------------------------- 고객지원 비용 기록 (record_support_cost가 장부에 남긴 것)
export interface SupportCostRecord {
  record_id: string;
  inquiry_id: number;
  order_id: string | null;
  resolution: string;
  claim_decision: string | null;
  refund_amount: number;
  coupon_issued: boolean;
  expense_used: boolean;
  /** 장부에서 주문을 못 찾으면 null (추정하지 않는다) */
  shipping_fee: number | null;
  context_id: string;
  recorded_at: string;
  /** 문의를 닫은 뒤 끝난 환불·쿠폰으로 금액이 바뀐 시각 */
  updated_at?: string;
}

// ---------------------------------------------------------------- §11 overview
export type TodoKind = "settlement_batch" | "settlement_deadline" | "closing_confirm" | "closing_drift" | "vat_drift" | "recon_exception" | "team_budget";
/** 할 일에서 사람이 바로 누를 결정 */
export type TodoActionType = "closing_confirm" | "settlement_request" | "review_decision" | "recon_note" | "open_tab" | "team_budget_approve";
export interface TodoAction { type: TodoActionType; period?: string; date?: string; review_id?: string | null; month?: string; signature?: string }
/** 건마다 결정할 때의 한 건 (대사 불일치·감사 의심) */
export interface TodoItem { ref: string; title: string; detail: string; amount: number | null }
export interface FinanceTodo {
  kind: TodoKind;
  title: string;
  count: number | null;
  amount: number | null;
  link: string | null;
  action?: TodoAction;
  items?: TodoItem[];
}
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
/** 판단에 쓴 규칙과 근거 (finance_rules.toml) */
export interface AppliedRule { rule_id: string; title: string; statement: string; basis: string; review: "expert" | "pending"; detail: string | null; rules_version: number }
export interface IncomeEstimate {
  year: number; from: string; to: string;
  operating_result: number | null; income_tax: number | null; local_income_tax: number | null; total: number | null;
  not_included: string[]; basis: string; applied_rules: AppliedRule[];
}
export interface ExportFile { name: string; bytes: number; sha256: string }
export interface ExportPackage {
  package_id: string; month: string; version: number; generated_at: string; files: ExportFile[]; mock: boolean;
}
/** 은행 거래내역 파일을 읽은 기록. `source`: 화면에서 올림(`upload`, `by`가 올린 사람) / 서버 폴더(`folder`) */
export interface BankImport { file: string; at: string; status: "imported" | "failed"; source?: "upload" | "folder"; by?: string; added?: number; rows?: number; withdrawals?: number; skipped?: number; reason?: string }
export interface ExportsView { packages: ExportPackage[]; bank_imports: BankImport[] }

// 캠페인 재무: 손익 기록, 예산 판단, 실패 교훈, 총괄에 낸 의견
export type CampaignVerdict = "good" | "neutral" | "bad";
export type BudgetDecision = "approved" | "conditional" | "rejected";
export interface CampaignRecord {
  campaign_id: string; campaign_name: string; channel: string; period_from: string; period_to: string;
  spend_krw: number; coupon_krw: number; gmv_krw: number | null; orders?: number | null; measured_gmv_krw?: number | null; measured_orders?: number | null; effect_generated_at?: string | null; revenue_krw: number | null; profit_krw: number | null;
  roi: number | null; roas: number | null; ltv_profit_krw: number | null; verdict: CampaignVerdict | null; status: "recorded" | "waiting_revenue"; source: string;
  applied_rules: AppliedRule[];
}
export interface BudgetReview {
  review_id: string; campaign_id: string; campaign_name: string; channel: string; requested_krw: number; approved_limit_krw: number;
  decision: BudgetDecision; reasons: string[]; recommendation: string; reviewed_at: string; applied_rules: AppliedRule[];
}
export interface CampaignLesson { lesson_id: string; campaign_name: string; channel: string; profit_krw: number; roi: number | null; status: "open" | "closed"; learned_at: string }
export interface OpinionRow {
  status: "pending" | "delivered"; attempts: number; last_problem?: string | null;
  opinion: { opinion_id: string; kind: string; campaign_name: string | null; verdict: string | null; recommendation: string; submitted_at: string };
}
export interface CampaignView {
  campaigns: CampaignRecord[]; reviews: BudgetReview[]; lessons: CampaignLesson[]; opinions: OpinionRow[];
  budget: { month: string; monthly_krw: number; provisional: boolean; basis: string; committed_krw: number | null };
  running: { campaign_id: string; active_krw: number }[];
  break_even_roas: number | null;
}
export interface FinanceInsight {
  insight_id: string; kind: string; severity: "warn" | "info"; title: string; message: string;
  evidence: { label: string; value: string | number | null }[]; recommendation: string; applied_rules: AppliedRule[];
}

/** 쇼핑몰 장부에 없는 월 손익. 재무 담당이 입력한다 */
export interface OtherPnlInput {
  month: string; salary: number; rent: number; other_sga: number;
  non_operating_income: number; non_operating_expense: number; income_tax: number | null; memo?: string;
}
export interface OtherPnlView { month: string; locked: boolean; entry: (OtherPnlInput & { by: string; at: string }) | null }

export interface ClosingReport {
  applied_rules?: AppliedRule[];
  closable?: boolean;
  snapshot_at?: string | null;
  drift?: { code: string; label: string; confirmed: number; current: number; diff: number }[];
  close_warnings?: { code: string; message: string }[];
  adjustment_proposal?: { journal: { account: string; debit: number; credit: number; memo: string }[]; net: number; signature: string } | null;
  adjustments?: { reason: string; by: string; at: string; posted_month: string }[];
  prior_adjustments?: { for_month: string; reason: string; lines: { code: string; diff: number }[] }[];
  net_after_adjustments?: number | null;
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
  /** 무엇과 맞췄나: 올린 PG 정산 파일(uploaded) · 결제 기록으로 만든 모의 자료(generated) */
  pg_basis?: "uploaded" | "generated";
}
/** 대사 기간의 건이 어느 PG 자료와 맞춰졌는지와 올린 파일이 덮는 판매일 기간 */
export interface PgCoverage { uploaded: number; generated: number; periods: { from: string; to: string }[] }
export interface PgImport {
  file: string | null;
  at: string;
  source: "upload" | "folder" | "clear";
  by?: string;
  status: "imported" | "failed" | "cleared";
  rows?: number;
  period?: { from: string; to: string };
  reason?: string;
}
export interface Reconciliation {
  from: string;
  to: string;
  counts: Partial<Record<ReconStatus, number>>;
  totals: { order_amount: number | null; pg_amount: number | null; pg_fee: number | null; pay_out: number | null };
  items: ReconItem[];
  pg_coverage?: PgCoverage;
  /** 서버가 보낸 건의 상태와 그 상태의 전체 건수. `total`이 items 수보다 크면 잘린 것이다 */
  shown?: { statuses: ReconStatus[]; total: number; limit: number };
}

// ---------------------------------------------------------------- ③ 판매자 정산
export type StatementStatus = "ready" | "held" | "requested" | "approved" | "rejected";
export type BatchStatus = "none" | "requested" | "approved" | "rejected";
export interface HoldReason { code: string; label: string }
/** 이번 기간에 그 판매자가 판 쇼핑몰 상품. 번호는 shop-api 상품 번호 그대로 */
export interface StatementProduct { product_id: string; name: string; qty: number; gross: number }
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
  products?: StatementProduct[];
}
export interface SettlementBatch { status: BatchStatus; review_id: string | null; execution_id: string | null; total_payout: number; seller_ids: string[] }
export interface Settlements { period: string; from: string; to: string; pay_date: string | null; statements: SellerStatement[]; batch: SettlementBatch }

// ---------------------------------------------------------------- ③ 정산 기한 · 미지급금 롤포워드
export type DeadlineStatus = "overdue" | "at_risk" | "due_soon" | "ok";
export interface DeadlineSeller {
  seller_id: string;
  name: string;
  orders: number;
  /** 기한이 걸린 판매자 몫(쿠폰·환불 차감 전) */
  amount: number;
  oldest_confirmed: string;
  deadline: string;
  days_left: number;
  /** 보류 사유가 있거나 예정일이 지났으면 null */
  expected_pay_date: string | null;
  status: DeadlineStatus;
  status_label: string;
  hold_reasons: HoldReason[];
}
export interface DeadlineReport {
  today: string;
  deadline_days: number;
  basis: string;
  warn_days: number;
  since: string;
  counts: Record<DeadlineStatus, number>;
  waiting_orders: number;
  sellers: DeadlineSeller[];
}
export interface PayableRow { seller_id: string; name: string; opening: number | null; accrued: number | null; paid: number; closing: number | null; overpaid: boolean }
export interface Payables {
  month: string;
  from: string;
  to: string;
  totals: { opening: number | null; accrued: number | null; paid: number; closing: number | null };
  balanced: boolean | null;
  overpaid: string[];
  sellers: PayableRow[];
  basis: string;
}

// ---------------------------------------------------------------- ② 은행 입금 대조 · ③ 13주 현금 흐름 · ① 월 마감
/** 은행 거래 한 줄. 출금은 음수 금액 */
export interface BankRowIn { date: string; amount: number; memo: string; balance?: number }
export type BankStatus = "matched" | "short" | "over" | "missing" | "unexpected" | "not_covered";
export interface BankDay { date: string; expected: number | null; deposited: number | null; diff: number | null; status: BankStatus; status_label: string }
export interface BankMatch {
  uploaded: boolean;
  covered: { from: string; to: string; rows: number } | null;
  counts: Partial<Record<BankStatus, number>>;
  days: BankDay[];
  pg_memo: string;
}
export type TransferStatus = "paid" | "waiting" | "not_found" | "not_covered";
export interface BankTransfer {
  batch: string; seller_id: string | null; name: string; amount: number; approved_on: string; due_by: string;
  status: TransferStatus; status_label: string; bank_date: string | null; bulk: boolean;
}
export interface BankOutRow { category: string; bank: number; book: number | null; book_source: string | null; diff: number | null; judged: boolean; comparable: boolean }
export interface BankUnexplained { id: string; date: string; amount: number; memo: string; note: string | null; noted_by: string | null }
export interface BankOutflows {
  uploaded: boolean;
  covered: { from: string; to: string }[];
  transfers: BankTransfer[];
  months: { month: string; fully_covered: boolean; rows: BankOutRow[] }[];
  unexplained: BankUnexplained[];
  counts: Partial<Record<TransferStatus, number>> & { unexplained_open?: number };
  transfer_days: number;
  applied_rules: AppliedRule[];
}
export interface CashWeek {
  week_start: string; basis: "scheduled" | "estimated"; inflow: number | null; outflow: number | null;
  fixed?: number | null; net: number | null; cumulative: number | null; balance?: number | null;
}
export interface Cashflow {
  today: string;
  weeks: CashWeek[];
  trailing_weeks: number;
  average: { inflow: number | null; outflow: number | null };
  negative_weeks: string[];
  opening?: { as_of: string; balance: number; ordered: boolean; stale: boolean } | null;
  fixed?: { month: string; monthly: number; weekly: number } | null;
  min_cash_krw?: number;
  short_weeks?: string[];
  note: string;
}
export interface CloseItem { key: string; label: string; status: "done" | "pending" | "unknown"; detail: string; link: string }
export interface CloseChecklist { month: string; items: CloseItem[]; done: number; total: number; ready: boolean; closed: boolean }

// ---------------------------------------------------------------- ④ 팀 예산 (인사 ↔ 재무)
export interface TeamBudgetRow { team: string; headcount: number; budget: number; prev_headcount: number | null; prev_budget: number | null; change: number | null }
export interface TeamBudgetApproval { month: string; per_head_monthly: number; total: number; signature: string; by: string; at: string; note: string | null; rows: { team: string; headcount: number; budget: number }[] }
export interface TeamBudget {
  month: string;
  prev_month: string;
  per_head_monthly: number;
  basis: string | null;
  /** 인사 AI가 가상 명부(예시) 인원을 줬으면 true. 계산만 보여 주고 확정하지 않는다 (옛 응답은 없음) */
  example?: boolean;
  /** pending 확정 전 · approved 확정됨 · changed 확정 뒤 인원·단가가 바뀜 · unavailable 인원을 못 받음 */
  status: "pending" | "approved" | "changed" | "unavailable";
  rows: TeamBudgetRow[];
  total: number | null;
  prev_total: number | null;
  change_total: number | null;
  headcount_total: number | null;
  signature: string | null;
  approved: TeamBudgetApproval | null;
  /** 팀별 실제 지출 자료가 아직 없어 늘 null (화면은 "준비 중") */
  actual_spend: null;
}

// ---------------------------------------------------------------- ⑤ 세무 지원
export interface VatRow { code: string; label: string; supply: number; vat: number }
export interface VatPack {
  applied_rules?: AppliedRule[];
  closable?: boolean;
  filing_status?: "draft" | "confirmed";
  confirmed_by?: string;
  confirmed_at?: string;
  drift?: { code: string; label: string; confirmed: number; current: number; diff: number }[];
  quarter: string;
  from: string;
  to: string;
  filing: { kind: string; due: string };
  sales: VatRow[];
  purchases: VatRow[];
  output_vat: number | null;
  input_vat: number | null;
  payable: number | null;
  coupon: { company_coupon: number | null; commission_before_coupon: number | null; commission_after_coupon: number | null; coupon_over_commission: number | null; reduces_base: boolean };
  seller_sales: { exempt: number; taxable: number } | null;
  settings: { commission_vat: string; pg_fee_vat: string; shipping_supplier: string };
  checks: string[];
  basis: string;
}
export interface SellerSalesRow { seller_id: string; name: string; business_no: string | null; month: string; total: number; by_method: Record<string, number>; by_tax: Record<string, number>; orders: number }
export interface SellerReport { applied_rules?: AppliedRule[]; quarter: string; from: string; to: string; due: string; rows: SellerSalesRow[]; missing: string[]; basis: string; unavailable: boolean }

// ---------------------------------------------------------------- ① 월간 변동 분석
export interface VarianceLine {
  code: string;
  label: string;
  current: number | null;
  previous: number | null;
  diff: number | null;
  pct: number | null;
  last_year: number | null;
  yoy_diff: number | null;
  yoy_pct: number | null;
  flag: boolean;
  note: string;
}
export interface VarianceCategory { category: string; gmv: number; gmv_prev: number; commission: number; commission_prev: number; commission_diff: number }
export interface Variance {
  month: string;
  lines: VarianceLine[];
  drivers: { commission_change: number; volume_effect: number; rate_mix_effect: number } | null;
  categories: VarianceCategory[];
  summary: string[];
  thresholds: { flag_ratio: number; flag_min_amount: number };
}

// ---------------------------------------------------------------- 재무제표 (손익계산서, 당기·전기)
export type StatementPeriod = "month" | "quarter" | "year";
/** 값이 null이면 장부에 없거나 못 받은 자료 (지어내지 않는다) */
export interface StatementRow { code: string; label: string; level: number; total: boolean; current: number | null; previous: number | null; note: string }
export interface StatementNote { code: string; label: string; current: number | null; previous: number | null; note: string }
export interface StatementSpan { name: string; from: string; to: string }
export interface IncomeStatement {
  applied_rules?: AppliedRule[];
  title: string;
  company: string | null;
  period: StatementPeriod;
  current: StatementSpan;
  previous: StatementSpan;
  unit: string;
  rows: StatementRow[];
  notes: StatementNote[];
  basis: string;
}

// ---------------------------------------------------------------- §12 시나리오 A: 장애 이벤트 재무
// 쿠폰·환불은 끝난 금액만 장부에 적는다. 쿠폰 건별 승인은 안전 장치(A/C)가 한다.
/** 장부 출처: 이벤트 버스(coupon.issued) / 안전 장치 실행 기록(issue_coupon) / 목 시드 */
export type LedgerSource = "events" | "safety_executions" | "seed";

export interface IncidentSummary {
  context_id: string;
  component: string | null;
  opened_at: string | null;
  damage_amount: number | null;
  compensation_total: number;
  coupon_count: number;
  refund_total: number | null;
  money_out_total: number | null;
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

export interface IncidentCoupon {
  buyer_id: string | null;
  execution_id: string | null;
  coupon_id: string | null;
  count: number;
  unit_value: number;
  amount: number;
  purpose_code: string;
  reason_code: string | null;
  at: string;
}
export interface IncidentCompensation {
  coupons: IncidentCoupon[];
  total_executed: number;
  count_executed: number;
}

export interface IncidentRefunds { total: number; count: number }

export interface IncidentDetail {
  context_id: string;
  component: string | null;
  window: { from: string; to: string } | null;
  /** 쇼핑몰이 정상 복귀를 알린 시각. 아직 모르면 null */
  recovered_at?: string | null;
  impact: IncidentImpact | null;
  compensation: IncidentCompensation;
  /** 이벤트 구간 안에 영향 고객에게 나간 환불. 구간·영향 고객을 모르면 null (0원과 다르다) */
  refunds: IncidentRefunds | null;
  /** 보상 + 환불. 둘 중 하나라도 모르면 null */
  money_out_total: number | null;
  ledger_source: LedgerSource;
}

// ---------------------------------------------------------------- 안전 장치 보류 큐 (safety)
export interface RuleHit { key: string; product?: string; version?: string; kev_cves?: string[]; observed?: number; op?: string; value?: number | string; outside?: string[]; named_hit?: string[]; invalid?: string }
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
  report: { totals?: Record<string, number | null>; calculation?: string; computed_amount?: number | null; hold_reason?: string; rules?: RuleHit[]; claimed?: Record<string, number>; reason?: string; summary?: string };
}

// ---------------------------------------------------------------- 유지되는 기능
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

/** 사람 결정 버튼을 켤지. 예시 데이터 모드는 서버에 보내지 않으므로 늘 켠다 (전환하면 화면을 다시 읽는다) */
export const exampleMode = exampleOn();
export const humanTokenSet = SAFETY_HUMAN_TOKEN.length > 0 || exampleMode;
/** 재무 화면 밖(고객지원·마케팅)의 보류 결정용. 재무 화면의 예시 데이터 전환과 상관없이 늘 실제 서버를 본다 */
export const liveHumanTokenSet = SAFETY_HUMAN_TOKEN.length > 0;

/** `raw`: 파일을 그대로 보낸다(JSON으로 바꾸지 않는다) */
interface CallOptions { method?: "GET" | "POST"; body?: unknown; raw?: Blob; human?: boolean; live?: boolean }

async function call(base: string, server: string, path: string, opts: CallOptions = {}): Promise<unknown> {
  if (exampleMode && !opts.live) {
    try {
      return await exampleCall(base === SAFETY_API_URL ? "safety" : "finance", path, opts.method ?? "GET", opts.body);
    } catch (e) {
      throw new FinanceApiError(e instanceof ExampleMissing ? e.message : "예시 데이터를 읽지 못했습니다.", 404, "example_missing");
    }
  }
  const headers: Record<string, string> = {};
  if (opts.raw !== undefined) headers["Content-Type"] = "application/octet-stream";
  else if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.human && SAFETY_HUMAN_TOKEN) headers.Authorization = `Bearer ${SAFETY_HUMAN_TOKEN}`;
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.raw ?? (opts.body === undefined ? undefined : JSON.stringify(opts.body)),
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

/** 파일 응답을 받아 브라우저 내려받기로 넘긴다. 이름은 서버가 준 것(Content-Disposition), 없으면 `fallback` */
async function download(path: string, fallback: string): Promise<void> {
  if (exampleMode) throw new FinanceApiError("예시 데이터에서는 내려받지 않습니다. '실제 데이터'로 바꿔서 내려받으세요.", 0, "example_missing");
  let res: Response;
  try {
    res = await fetch(`${FINANCE_AGENT_URL}${path}`, { cache: "no-store" });
  } catch {
    throw new FinanceApiError(`${FINANCE_SERVER}에 연결하지 못했습니다. 서버가 켜져 있는지 확인하세요.`, 0, "unreachable");
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null;
    throw new FinanceApiError(body?.error?.message ?? `내려받지 못했습니다 (${res.status}).`, res.status, body?.error?.code ?? null);
  }
  const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? fallback;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
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
  // 일치·정산 전까지 다 받으면 십수만 건이라 화면에서 고른 상태만 받는다
  reconciliation: (date?: string | null, statuses?: ReconStatus[]) =>
    fin<Envelope<Reconciliation>>(`/api/finance/reconciliation${query({ date, status: statuses?.length ? statuses.join(",") : null })}`),
  /** 대사 불일치 건 처리 메모. 사람 토큰만 */
  reconNote: (paymentId: string, note: string) =>
    fin<Envelope<ReconItem>>(`/api/finance/reconciliation/${encodeURIComponent(paymentId)}/note`, { method: "POST", body: { note }, human: true }),
  settlements: (period?: string | null) => fin<Envelope<Settlements>>(`/api/finance/settlements${query({ period })}`),
  /** 보류 없는 판매자를 묶어 지급 승인 요청 → 안전 장치 보류 큐(C). 돈은 움직이지 않는다 */
  requestSettlementBatch: (period: string) =>
    fin<Envelope<Settlements | SettlementBatch>>("/api/finance/settlements/batch", { method: "POST", body: { period }, human: true }),
  /** 판매 대금 지급 기한 점검 */
  deadlines: () => fin<Envelope<DeadlineReport>>("/api/finance/settlements/deadlines"),
  /** 판매자 미지급금 롤포워드 (그 달). 날짜가 없으면 이번 달 */
  payables: (date?: string | null) => fin<Envelope<Payables>>(`/api/finance/payables${query({ date })}`),
  /** 은행 입금 대조 (올린 거래내역 기간만) */
  bank: () => fin<Envelope<BankMatch>>("/api/finance/bank"),
  /** 은행 입금 내역 올리기. 사람 토큰만 */
  uploadBank: (rows: BankRowIn[], hasWithdrawals = false) => fin<Envelope<BankMatch> & { meta: FinanceMeta & { added?: number; withdrawals?: number } }>("/api/finance/bank/statement", { method: "POST", body: { rows, has_withdrawals: hasWithdrawals }, human: true }),
  // 은행 화면에서 받은 파일(CSV·xlsx)을 그대로 보낸다. 글자 인코딩·안내 줄·칸 이름은 재무 AI 서버가 맞춘다
  uploadBankFile: (file: File) =>
    fin<Envelope<BankMatch> & { meta: FinanceMeta & { added: number; withdrawals: number; has_withdrawals: boolean; import: BankImport } }>(
      `/api/finance/bank/file${query({ name: file.name })}`, { method: "POST", raw: file, human: true }),
  // PG 관리자 화면에서 받은 정산 내역 CSV를 그대로 보낸다. 그 판매일 기간은 이 파일과 대사한다
  uploadPgFile: (file: File) =>
    fin<Envelope<PgImport>>(`/api/finance/pg/file${query({ name: file.name })}`, { method: "POST", raw: file, human: true }),
  pgImports: () => fin<Envelope<{ imports: PgImport[]; periods: { from: string; to: string }[]; rows: number; inbox_enabled: boolean }>>("/api/finance/pg/imports"),
  clearPg: (reason: string) => fin<Envelope<PgImport>>("/api/finance/pg/clear", { method: "POST", body: { reason }, human: true }),
  bankOutflows: () => fin<Envelope<BankOutflows>>("/api/finance/bank/outflows"),
  noteBankOutflow: (rowId: string, note: string) =>
    fin<Envelope<BankOutflows>>(`/api/finance/bank/outflows/${encodeURIComponent(rowId)}/note`, { method: "POST", body: { note }, human: true }),
  /** 13주 현금 흐름 */
  cashflow: () => fin<Envelope<Cashflow>>("/api/finance/cashflow"),
  /** 월 마감 체크리스트 */
  closeChecklist: (date?: string | null) => fin<Envelope<CloseChecklist>>(`/api/finance/close-checklist${query({ date })}`),
  /** 부가세 신고 보조 집계 (분기) */
  vat: (date?: string | null) => fin<Envelope<VatPack>>(`/api/finance/tax/vat${query({ date })}`),
  /** 판매자별 판매 명세 (부가세법 제75조 초안) */
  sellerReport: (date?: string | null) => fin<Envelope<SellerReport>>(`/api/finance/tax/seller-report${query({ date })}`),
  downloadSellerReport: (format: "csv" | "xlsx", date?: string | null) =>
    download(`/api/finance/tax/seller-report${query({ date, format })}`, `seller-sales.${format}`),
  /** 캠페인 손익·예산 판단·교훈·의견 */
  campaigns: () => fin<Envelope<CampaignView>>("/api/finance/campaigns"),
  /** 사람이 놓치기 쉬운 재무 패턴과 근거 */
  insights: () => fin<Envelope<FinanceInsight[]>>("/api/finance/insights"),
  confirmVat: (date: string) => fin<Envelope<VatPack>>("/api/finance/tax/vat/confirm", { method: "POST", body: { date }, human: true }),
  adjustClosing: (date: string, reason: string, signature: string) =>
    fin<Envelope<ClosingReport>>("/api/finance/closing/adjust", { method: "POST", body: { date, reason, signature }, human: true }),
  otherPnl: (month: string) => fin<Envelope<OtherPnlView>>(`/api/finance/other-pnl${query({ month })}`),
  saveOtherPnl: (body: OtherPnlInput) => fin<Envelope<OtherPnlView>>("/api/finance/other-pnl", { method: "POST", body, human: true }),
  /** 법인세 참고 추정. 신고에 쓰지 않는다 */
  incomeEstimate: () => fin<Envelope<IncomeEstimate>>("/api/finance/tax/income-estimate"),
  /** 자동으로 만든 파일과 은행 폴더 기록 */
  exports: () => fin<Envelope<ExportsView>>("/api/finance/exports"),
  runExports: () => fin<Envelope<unknown>>("/api/finance/exports/run", { method: "POST", body: {}, human: true }),
  downloadBundle: (packageId: string) =>
    download(`/api/finance/exports/${encodeURIComponent(packageId)}/bundle`, `${packageId}.zip`),
  downloadExport: (packageId: string, name: string) =>
    download(`/api/finance/exports/${encodeURIComponent(packageId)}/files/${encodeURIComponent(name)}`, name),
  /** 월간 변동 분석 (지난달·작년 같은 달 대비) */
  variance: (date?: string | null) => fin<Envelope<Variance>>(`/api/finance/variance${query({ date })}`),
  /** 팀 예산 (인사 팀 인원 × 인당 월 단가). month 생략 = 이번 달 */
  teamBudget: (month?: string | null) => fin<Envelope<TeamBudget>>(`/api/finance/team-budget${query({ month })}`),
  /** 팀 예산 확정. 사람 토큰만. 보던 숫자(signature)가 지금과 같아야 한다 */
  approveTeamBudget: (month: string, signature: string, note?: string) =>
    fin<Envelope<TeamBudget>>(`/api/finance/team-budget/${encodeURIComponent(month)}/approve`, { method: "POST", body: { signature, note: note || null }, human: true }),
  statements: (period: StatementPeriod, date?: string | null) =>
    fin<Envelope<IncomeStatement>>(`/api/finance/statements${query({ period, date })}`),
  /** 손익계산서 내려받기 (CSV 또는 엑셀) */
  downloadStatement: (period: StatementPeriod, date: string | null | undefined, format: "csv" | "xlsx") =>
    download(`/api/finance/statements${query({ period, date, format })}`, `income-statement-${period}.${format}`),

  // §12-1 장애 이벤트 재무 (시나리오 A)
  incidents: () => fin<Envelope<IncidentSummary[]>>("/api/finance/incidents"),
  /** 없는 이벤트면 404 {"error": {"code": "not_found"}} */
  incident: (contextId: string) => fin<Envelope<IncidentDetail>>(`/api/finance/incidents/${encodeURIComponent(contextId)}`),

  /** 고객지원이 닫은 문의의 비용 기록. 최근 것이 먼저 */
  supportCosts: () => fin<Envelope<SupportCostRecord[]>>("/api/finance/support-costs"),

  // 유지
  /** 재무 AI 테스트를 지금 한 번 더 한다(서버는 10분마다 자동으로 한다). 가짜 자료로 점검해 상태를 바꾸지 않음 */
  selfTest: () => data(fin<{ data: SelfTest }>("/api/finance/self-test")),
  /** 가장 최근 테스트 결과(자동·직접) + 다음 자동 테스트 시각 */
  lastSelfTest: () => fin<{ data: SelfTest | null; meta?: SelfTestLastMeta }>("/api/finance/self-test/last"),
  // 안전 장치 보류 큐 (정산 묶음 지급 승인 등 C 등급)
  reviews: (live = false) => data(saf<{ data: Review[] }>("/api/safety/reviews", { live })),
  /** C 보류 결정. 사람 토큰만 받는다 (에이전트 토큰은 safety가 거부). */
  decide: (reviewId: string, decision: "approved" | "rejected", note?: string, live = false) =>
    data(saf<{ data: Review }>(`/api/safety/reviews/${encodeURIComponent(reviewId)}/decision`, { method: "POST", body: { decision, note: note || null }, human: true, live })),
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

/** 사용률 막대 폭 (0~100). 값이 없으면 0 */
export const barWidth = (ratio: number | null | undefined) => (ratio == null || !Number.isFinite(ratio) ? 0 : Math.max(0, Math.min(100, ratio * 100)));

/** 분류 코드 → 이름 */
export const CATEGORY_KO: Record<string, string> = { book: "도서", books: "도서", stationery: "문구", ebook: "전자책", ebooks: "전자책", goods: "굿즈" };

/** 화면에 기본으로 보여 줄 대사 상태: 사람이 볼 불일치만. 정산 전·일치는 누르면 보인다 */
export const RECON_ORDER: ReconStatus[] = ["mismatch", "missing_in_pg", "fee_anomaly", "pending", "matched"];
export const DEFAULT_RECON_FILTER: ReconStatus[] = RECON_ORDER.filter((s) => s !== "matched" && s !== "pending");

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
export type FinanceTab = "closing" | "reconciliation" | "settlements" | "policy" | "tax" | "campaigns";
export const FINANCE_TABS: FinanceTab[] = ["closing", "reconciliation", "settlements", "policy", "tax", "campaigns"];
const TODO_TAB: Record<TodoKind, FinanceTab> = {
  closing_drift: "closing",
  vat_drift: "tax",
  settlement_batch: "settlements", settlement_deadline: "settlements", closing_confirm: "closing", recon_exception: "reconciliation", team_budget: "policy",
};
export function todoTab(t: Pick<FinanceTodo, "kind" | "link">): FinanceTab {
  const link = (t.link ?? "").replace(/^#/, "");
  if ((FINANCE_TABS as string[]).includes(link)) return link as FinanceTab;
  return TODO_TAB[t.kind] ?? "closing";
}

// ---------------------------------------------------------------- 장애 이벤트 표시 도우미
export const COMPONENT_KO: Record<string, string> = { payment: "결제", order: "주문", shop: "쇼핑몰", search: "검색", delivery: "배송" };
export const componentKo = (c: string | null | undefined) => (c ? COMPONENT_KO[c] ?? "서비스" : null);

/**
 * 이벤트 이름. 시연 이벤트 "ctx_inc_scn_a_0001" + "payment" → "장애 #0001 · 결제".
 * 실제 이벤트 번호(ctx_inc_ + 16진수)는 사람에게 의미가 없어 "결제 장애 · 오후 4:38"처럼 시각으로 부른다.
 */
export function incidentName(contextId: string, component?: string | null, openedAt?: string | null): string {
  const where = componentKo(component);
  const no = incidentNo(contextId);
  if (/^[0-9a-f]{8,}$/i.test(no)) {
    const at = openedAt ? new Date(openedAt).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" }) : null;
    return `${where ?? "서비스"} 장애${at ? ` · ${at}` : ""}`;
  }
  return `장애 #${no}${where ? ` · ${where}` : ""}`;
}

/** 최근 이벤트가 먼저. 시각이 없는 이벤트는 뒤로 */
export function sortIncidents(list: IncidentSummary[]): IncidentSummary[] {
  return [...list].sort((a, b) => {
    if (a.opened_at && b.opened_at) return b.opened_at.localeCompare(a.opened_at);
    if (a.opened_at) return -1;
    if (b.opened_at) return 1;
    return b.context_id.localeCompare(a.context_id);
  });
}

export const LEDGER_SOURCE_KO: Record<LedgerSource, { label: string; detail: string }> = {
  events: { label: "이벤트 알림", detail: "이 이벤트의 안전 장치 실행 기록이 없어(예: 재시작 뒤), 마케팅 AI가 쿠폰을 줄 때 보낸 알림으로 장부를 채웠습니다." },
  safety_executions: { label: "안전 장치 실행 기록", detail: "안전 장치가 남긴 쿠폰 지급 실행 기록을 원본으로 씁니다. 같은 쿠폰의 알림은 금액에 더하지 않습니다." },
  seed: { label: "예시 데이터", detail: "연습용 예시 자료입니다. 실제 쿠폰 지급 기록이 아닙니다." },
};
export const ledgerSourceKo = (s: LedgerSource | string | null | undefined) =>
  s && s in LEDGER_SOURCE_KO ? LEDGER_SOURCE_KO[s as LedgerSource] : { label: s ? "기타 자료" : UNKNOWN, detail: "" };

// ---------------------------------------------------------------- 코드 → 쉬운 말 (화면에 영어 코드를 그대로 보이지 않는다)
/** 모르는 코드일 때 쓰는 말 */
export const OTHER_CHECK = "기타 점검";

/** 쿠폰을 준 이유·목적 */
export const PURPOSE_KO: Record<string, string> = {
  payment_failure: "결제 실패 보상", compensation: "장애 보상", campaign: "마케팅 쿠폰", seller_promo: "판매자 할인 쿠폰",
};
export const purposeKo = (...codes: (string | null | undefined)[]) => {
  for (const c of codes) if (c && PURPOSE_KO[c]) return PURPOSE_KO[c];
  return "보상";
};

/** 마케팅 화면에서 결정하는 보류 건 */
export const MARKETING_HOLD_ACTIONS = ["issue_coupon", "publish_notice", "publish_sns", "generate_media", "start_campaign"] as const;
/** 재무 화면에서 결정하지 않는 보류 건. 환불은 고객지원 화면, 쿠폰·공지·SNS·영상은 마케팅 화면에서 결정한다 */
export const NOT_FINANCE_ACTIONS = new Set<string>(["refund_request", ...MARKETING_HOLD_ACTIONS]);

export const ACTION_KO: Record<string, string> = {
  issue_coupon: "쿠폰 지급", refund_request: "환불", confirm_settlement: "판매자 정산 지급",
  publish_notice: "공지 올리기", publish_sns: "SNS 게시", generate_media: "이미지·영상 만들기", start_campaign: "캠페인 집행",
  rollback_deployment: "배포 되돌리기", restart_service: "서비스 다시 켜기", generate_pr: "코드 변경 만들기",
};

/** "2026-W43" → "2026년 43주차". 모양이 다르면 그대로 */
export function periodKo(p: string): string {
  const m = /^(\d{4})-W(\d{1,2})$/.exec(p);
  return m ? `${m[1]}년 ${Number(m[2])}주차` : p;
}

/**
 * 서버가 보낸 문장을 화면용으로: 기록 번호 → 쉬운 말, 남아 있는 개발자 말·코드 → 한국어.
 * (예전 서버가 보낸 "출처 seed", "data 영향 고객" 같은 문장도 고친다)
 */
export function plainText(text: string | null | undefined, sellers?: Record<string, string>): string {
  if (!text) return "";
  return humanizeIds(text, sellers)
    .replace(/(안전|실행) 관문/g, "안전 장치")
    .replace(/\bdata 영향/g, "데이터 AI 영향")
    .replace(/출처:? (events|safety_executions|seed)\b/g, (_, src: string) => `출처: ${ledgerSourceKo(src).label}`)
    .replace(/\b(issue_coupon|refund_request|confirm_settlement)\b/g, (c: string) => ACTION_KO[c] ?? c)
    .replace(/\b(damage_amount|compensation_total)\b/g, (c: string) => (c === "damage_amount" ? "피해 금액" : "보상 합계"));
}
