export type Level = "error" | "warning";
export interface Finding { code: string; level: Level; message: string }
export interface Validation { passed: boolean; findings: Finding[] }

// ---------------------------------------------------------------- shop
export interface Me { id: number; name: string; email: string; points: number }
export interface Category { key: string; label: string; count: number }

export interface Product {
  id: number; category: string; category_ko: string; title: string; author: string; publisher: string; description: string;
  list_price: number; sale_price: number; discount_pct: number; stock: number; sold_out: boolean; returnable: boolean;
  custom_made: boolean; hue: number; sales: number; rating: number | null; review_count: number; published_at: string;
}

export interface Review {
  id: number; product_id: number; user: string; rating: number; title: string; body: string; helpful: number;
  verified: boolean; created_at: string; status: string;
}
export interface ProductDetail extends Product {
  related: Product[]; reviews: Review[]; rating_distribution: Record<string, number>;
}

export interface CartPrice { items_total: number; shipping_fee: number; total: number; free_shipping_gap: number }

export interface OrderItem {
  id: number; product_id: number; title: string; category: string; unit_price: number; qty: number; paid_amount: number;
  points_share: number; returnable: boolean; refunded_qty: number; remaining_qty: number; reviewed: boolean; can_review: boolean; hue: number;
}

export type OrderStatus = "paid" | "preparing" | "shipped" | "delivered" | "cancelled";
export interface Order {
  id: number; order_no: string; status: OrderStatus; status_ko: string; items_total: number; shipping_fee: number;
  points_used: number; paid_amount: number; refunded_amount: number; payment_method: string; receiver: string; address: string;
  paid_at: string; shipped_at: string | null; delivered_at: string | null; expected_delivery_at: string | null; late_days: number;
  items: OrderItem[]; claims?: Claim[]; open_claims?: number;
}

// ---------------------------------------------------------------- claims
export type ClaimType = "cancel" | "return" | "exchange" | "compensation";
export type ClaimReason = "change_of_mind" | "defect" | "wrong_item" | "damaged" | "late_delivery" | "missing_item" | "other";
export type ClaimStatus = "needs_info" | "needs_review" | "awaiting_return" | "return_received" | "completed" | "rejected" | "withdrawn";
export type Outcome = "auto_rejected" | "needs_info" | "needs_review" | "approved_pending_return" | "auto_approved";

export interface Reason { code: string; level: "block" | "review" | "warn" | "info"; message: string; rule: string | null }
export interface DecisionLine { order_item_id: number; title: string; qty: number; requested_qty: number; unit_price: number; refund_cash: number; points_back: number }

export interface Decision {
  outcome: Outcome; resolution: string; requires_return: boolean; reasons: Reason[]; lines: DecisionLine[];
  refund_cash: number; points_back: number; shipping_refund: number; payable: number;
  deductions: { code: string; amount: number; message: string }[];
  compensation: { kind: string; amount: number; reason: string } | null; claimed_amount: number | null; policy_refs: string[];
}

export interface Claim {
  id: number; order_id: number; type: ClaimType; reason: ClaimReason; status: ClaimStatus; created_at: string; description: string;
  claimed_amount: number | null; unsealed: boolean; evidence_count: number; outcome: Outcome | null; resolution: string | null;
  requires_return: boolean; reasons: Reason[]; lines: DecisionLine[]; deductions: { code: string; amount: number; message: string }[];
  expected_refund: number; points_back: number; compensation: number; payable: number; tracking: string | null;
  return_received_at: string | null; inspection: string | null; refund: number; compensation_paid: number; points_returned: number;
  policy_refs: string[]; exchange_shipment: { tracking: string; shipped_at: string } | null; processed_at: string | null;
  conversation_id: number | null; order_no?: string;
  // agent-only
  customer?: string; abuse?: { score: number; signals: { code: string; points: number; message: string }[] }; decided_by?: string;
  agent_note?: string | null; hold_reason?: string | null; manager_approved?: boolean; inspection_note?: string | null; user_id?: number;
}

export interface ClaimInput {
  order_id: number; type: ClaimType; reason: ClaimReason; items?: { order_item_id: number; qty: number }[];
  claimed_amount?: number | null; unsealed?: boolean; evidence_count?: number; description?: string;
}

export interface AuditEntry { actor: string; action: string; detail: Record<string, unknown>; at: string }
export interface ClaimDetail extends Claim {
  audit: AuditEntry[];
  receipt: {
    order_no: string; status: string; items_total: number; shipping_fee: number; points_used: number; paid_amount: number;
    paid_at: string; delivered_at: string | null; expected_delivery_at: string | null;
    items: { id: number; title: string; qty: number; paid_amount: number; points_share: number; refunded_qty: number }[];
  };
}

export interface QueueEntry {
  position: number; claim_id: number; order_no: string; customer: string; type: ClaimType; reason: ClaimReason; received_at: string;
  inspection: string | null; amount: number; compensation: number; ready: boolean; blockers: { code: string; message: string }[];
  items: { title: string; qty: number }[];
}
export interface QueueView { queue: QueueEntry[]; awaiting_return: Claim[] }
export interface QueueRun {
  results: { claim_id: number; result: "paid" | "held" | "skipped"; blockers?: { code: string; message: string }[]; refund?: number; compensation?: number }[];
  paid: number; held: number; queue: QueueEntry[];
}
export interface ClaimStats {
  total: number; by_status: Record<string, number>; rule_hits: Record<string, number>;
  agent_reviews: number; agent_overrides: number; overridden_rules: Record<string, number>;
}

// ---------------------------------------------------------------- support
export interface InquiryItem { id: number; subject: string; status: string; order_id: number | null; updated_at: string; answered: boolean; preview: string }
export interface InquiryMessage { id: number; role: "customer" | "agent"; text: string; created_at: string }
export interface Inquiry extends InquiryItem { messages: InquiryMessage[] }

export interface Source { id: string; doc_id: string; category: string; title: string; section: string; text: string; score: number; from_decision?: boolean }
export interface Draft { style: "empathetic" | "concise" | "step_by_step"; reply: string; source_ids: string[]; validation: Validation }
export interface Classification {
  intent_ko: string; category: string; sentiment: "calm" | "frustrated" | "angry"; urgency: "low" | "medium" | "high";
  search_query: string; pii_masked: string[];
}
export interface Analysis {
  classification?: Classification; sources?: Source[]; drafts?: Draft[]; timings_ms?: Record<string, number>; validation?: Validation;
  facts?: unknown;
}
export interface Message { id: number; role: "customer" | "agent"; text: string; analysis: Analysis | null; created_at: string }
export interface Summary {
  summary_ko: string; issue_ko: string; actions_ko: string[]; next_action_ko: string; category: string; resolved: boolean;
  escalate: boolean; escalate_reason_ko: string; tags: string[];
}
export interface ConversationListItem {
  id: number; customer_name: string; subject: string; category: string | null; sentiment: string | null;
  urgency: "low" | "medium" | "high" | null; status: "open" | "resolved" | "escalated"; order_id: number | null;
  updated_at: string; preview: string; awaiting_reply: boolean;
}
export interface Conversation extends ConversationListItem { messages: Message[]; summary: Summary | null; order_no: string | null; claims: Claim[] }

export interface Kpi {
  conversations: number; by_status: Record<string, number>; agent_replies: number; draft_adoption_rate: number | null;
  accepted_as_is: number; edited: number; rejected: number; avg_edit_ratio: number | null; avg_first_draft_ms: number | null;
  drafts_generated: number; drafts_blocked_by_guard: number;
}

export interface AdminReview extends Review { product: string; flags: { code: string; message: string; info?: boolean }[] }

export interface Dashboard {
  days: number; timezone: string;
  daily: { date: string; inquiries: number; claims: number; auto: number; agent: number; paid: number }[];
  totals: {
    inquiries: number; open_waiting: number; high_waiting: number; claims: number; auto_rate: number | null; claims_rejected: number;
    needs_review: number; awaiting_return: number; queue_waiting: number; queue_ready: number; paid_cash: number; paid_points: number;
    agent_reviews: number; agent_overrides: number; flagged_reviews: number;
  };
  by_category: Record<string, number>; claim_reasons: Record<string, number>; guard_findings: Record<string, number>;
  queue: QueueEntry[];
  inquiry_queue: { id: number; customer_name: string; subject: string; category: string | null; urgency: string | null; sentiment: string | null; preview: string; waiting_minutes: number }[];
}

export interface Health { status: string; llm_provider: string; kb_chunks: number; demo_tools: boolean }

// ---------------------------------------------------------------- 공통 로그 소통 (dashboard-api GET /api/dashboard/communications)
/** 결과 값 5가지. 소통 탭과 AI 대화방이 같은 값·같은 이름표(labels.ts COMM_OUTCOME_KO)를 쓴다 */
export type CommOutcome = "completed" | "rejected" | "held" | "needs_human" | "in_progress";
/** 소통 한 줄 (communications.py CommunicationLine). 코드가 만든 필드만 있고 payload·고객 원문은 없다 */
export interface CommLine {
  log_id: string; occurred_at: string; from_agent: string; to_agent: string | null; skill: string | null;
  outcome: CommOutcome; context_id: string | null; execution_id: string | null;
}
export interface CommMeta { total: number; truncated: boolean }
