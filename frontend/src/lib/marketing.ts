// 마케팅 AI 화면의 시연용 가상 데이터. 실제 API를 부르지 않는다.
// 용어와 범위는 ax-backend docs/notes/marketing/agent-design.md, agents/marketing/agent-card.json,
// docs/notes/marketing/instagram-policy-checklist.md 를 따른다. 설계 노트에 없는 기능은 '예정'·'확인 필요'로만 표시한다.

export type NoticeChannel = "banner" | "email" | "sns";
export type NoticeKind = "outage" | "recovery" | "maintenance";

// 공지 사실 필드만 담는다. 장애 원인 칸은 만들지 않는다.
export interface NoticeFacts {
  kind: NoticeKind;
  component: string;
  starts_at: string;
  ends_at: string | null;
  promises: string[];
}
export interface NoticeDraft { draft_id: string; facts: NoticeFacts; text: string; created_at: string; status: "draft" | "rejected"; reject_reason?: string }
export interface PublishedNotice { notice_id: string; draft_id: string; facts: NoticeFacts; text: string; channels: NoticeChannel[]; published_at: string; incident_started_at: string | null }

export type CouponPurpose = "campaign" | "compensation";
export interface CouponRecord {
  record_id: string;
  context_id: string;
  sequence: number;
  purpose_code: CouponPurpose;
  count: number;
  unit_price: number;
  requested_by: "support";
  at: string;
  // A: 마케팅이 바로 발급. C: 기준 이상·금액 불일치로 안전 장치 보류 큐에서 사람 결정을 기다림
  grade: "A" | "C";
  hold_reason?: "limit_exceeded" | "amount_mismatch";
}
export const couponTotal = (c: Pick<CouponRecord, "count" | "unit_price">) => c.count * c.unit_price; // 총액 = 건수 x 단가

export interface MarketingActivity { at: string; skill: string; grade: "read" | "A" | "C"; summary: string }

const iso = (minAgo: number) => new Date(Date.now() - minAgo * 60000).toISOString();

export const DRAFTS: NoticeDraft[] = [
  {
    draft_id: "drf_0004",
    facts: { kind: "recovery", component: "결제", starts_at: iso(95), ends_at: iso(20), promises: ["결제 실패 건은 자동 취소됩니다"] },
    text: "결제 기능이 정상으로 돌아왔습니다. 결제가 실패한 주문은 자동 취소됩니다.",
    created_at: iso(18), status: "draft",
  },
  {
    draft_id: "drf_0003",
    facts: { kind: "maintenance", component: "검색", starts_at: iso(-1500), ends_at: iso(-1380), promises: [] },
    text: "검색 기능 점검이 예정되어 있습니다. 점검 중에는 검색을 이용하기 어려울 수 있습니다.",
    created_at: iso(300), status: "rejected", reject_reason: "게시 채널이 지정되지 않아 거절됨",
  },
];

export const NOTICES: PublishedNotice[] = [
  {
    notice_id: "ntc_0012",
    draft_id: "drf_0002",
    facts: { kind: "outage", component: "결제", starts_at: iso(95), ends_at: null, promises: ["결제 실패 건은 자동 취소됩니다"] },
    text: "현재 결제 기능에 문제가 있습니다. 결제가 실패한 주문은 자동 취소됩니다. 정상화되면 다시 알려드리겠습니다.",
    channels: ["banner", "email"], published_at: iso(88), incident_started_at: iso(95),
  },
  {
    notice_id: "ntc_0011",
    draft_id: "drf_0001",
    facts: { kind: "maintenance", component: "주문 조회", starts_at: iso(-600), ends_at: iso(-480), promises: [] },
    text: "주문 조회 점검이 예정되어 있습니다.",
    channels: ["banner"], published_at: iso(1200), incident_started_at: null,
  },
];

// C 건은 10분이 지나면 자동 거절되므로, 대기로 보이는 건은 10분 안쪽으로 둔다.
export const COUPONS: CouponRecord[] = [
  { record_id: "cpn_0007", context_id: "ctx_inc_0100", sequence: 1, purpose_code: "compensation", count: 12, unit_price: 1000, requested_by: "support", at: iso(14), grade: "A" },
  { record_id: "cpn_0008", context_id: "ctx_inc_0100", sequence: 2, purpose_code: "compensation", count: 30, unit_price: 1000, requested_by: "support", at: iso(13), grade: "A" },
  { record_id: "cpn_0009", context_id: "ctx_inc_0100", sequence: 3, purpose_code: "compensation", count: 400, unit_price: 5000, requested_by: "support", at: iso(9), grade: "C", hold_reason: "limit_exceeded" },
  { record_id: "cpn_0010", context_id: "ctx_inc_0100", sequence: 4, purpose_code: "compensation", count: 8, unit_price: 1000, requested_by: "support", at: iso(6), grade: "C", hold_reason: "amount_mismatch" },
];

export const ACTIVITY: MarketingActivity[] = [
  { at: iso(6), skill: "issue_coupon", grade: "C", summary: "cpn_0010 금액 불일치: 사람 확인 대기로 보류" },
  { at: iso(9), skill: "issue_coupon", grade: "C", summary: "cpn_0009 기준 이상: 사람 확인 대기로 보류" },
  { at: iso(13), skill: "issue_coupon", grade: "A", summary: "cpn_0008 발급 (순번 2, 30건 x 1,000원)" },
  { at: iso(14), skill: "issue_coupon", grade: "A", summary: "cpn_0007 발급 (순번 1, 12건 x 1,000원)" },
  { at: iso(18), skill: "draft_notice", grade: "read", summary: "결제 정상화 공지 초안 drf_0004 작성 (사실 필드만 사용)" },
  { at: iso(88), skill: "publish_notice", grade: "A", summary: "ntc_0012 게시 (배너, 메일)" },
  { at: iso(89), skill: "draft_notice", grade: "read", summary: "결제 장애 공지 초안 drf_0002 작성 (원인은 적지 않음)" },
  { at: iso(300), skill: "publish_notice", grade: "A", summary: "drf_0003 게시 거절: 채널 미지정" },
];

export const KIND_KO: Record<NoticeKind, string> = { outage: "장애", recovery: "정상화", maintenance: "점검" };
export const CHANNEL_KO: Record<NoticeChannel, string> = { banner: "배너", email: "메일", sns: "SNS" };
export const HOLD_KO = { limit_exceeded: "기준 이상", amount_mismatch: "금액 불일치" } as const;
export const SKILL_KO: Record<string, string> = { draft_notice: "공지 초안 작성", publish_notice: "공지 게시", issue_coupon: "쿠폰 발급" };
export const PURPOSE_KO: Record<CouponPurpose, string> = { campaign: "캠페인", compensation: "보상" };
