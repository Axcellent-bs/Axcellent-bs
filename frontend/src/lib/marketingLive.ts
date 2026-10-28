import { DASHBOARD_API_URL, RELAY_API_URL } from "./env";
import { finance, type ExecutionRecord, type Review } from "./finance";
import { shortDateTime } from "./format";

// 마케팅 AI 화면의 실제 데이터.
// - 쇼핑몰(shop-api) GET /api/shop/banners: 지금 캣북스에 걸린 공지 배너. 고객 화면용이라 토큰이 없다.
// - 안전 장치(safety) GET /api/safety/executions: 마케팅 AI가 요청한 공지 게시·쿠폰 발급 기록(등급·금액).
// - 안전 장치 GET /api/safety/reviews: 사람 결정을 기다리는 보류 건.
// - dashboard-api GET /api/dashboard/incidents: 사건 이름(구성요소·시작 시각)을 붙이는 데만 쓴다.
// 쇼핑몰 쿠폰 목록(GET /api/shop/coupons)은 마케팅·고객지원·재무·총괄 토큰만 받아 화면에서 부르지 않는다.
// 공지 본문은 배너가 게시 중일 때만 쇼핑몰에서 받을 수 있다(끝난 공지 본문은 마케팅 AI의 get_notice만 준다).

export interface ShopBanner {
  notice_id: string;
  text: string;
  status: string;
  valid_from: string | null;
  valid_to: string | null;
  context_id: string | null;
  created_at: string;
  ended_at: string | null;
}

interface IncidentLite { context_id: string; component?: string | null; opened_at?: string | null; closed_at?: string | null }

/** 공지 게시 기록 한 건 (안전 장치 실행 기록에서) */
export interface NoticeRun {
  execution_id: string;
  at: string;
  context_id: string;
  channel: string | null;
  grade: string;
  status: string;
  hold_reason: string | null;
  /** 같은 사건의 배너가 지금 게시 중이면 그 배너 */
  banner: ShopBanner | null;
}

/** 쿠폰 발급 한 건 */
export interface CouponRun {
  execution_id: string;
  at: string;
  context_id: string;
  purpose: string | null;
  buyer_id: string | null;
  count: number;
  unit_value: number | null;
  total: number;
  grade: string;
  status: string;
  hold_reason: string | null;
}

/** 사건·목적별 쿠폰 묶음 */
export interface CouponGroup {
  key: string;
  context_id: string;
  purpose: string | null;
  issued: number;
  issued_total: number;
  held: number;
  held_total: number;
  failed: number;
  customers: number;
  last_at: string;
}

export interface MarketingLive {
  banners: ShopBanner[] | null;
  notices: NoticeRun[] | null;
  coupons: CouponRun[] | null;
  groups: CouponGroup[];
  holds: Review[] | null;
  incidents: Map<string, IncidentLite>;
  errors: string[];
  loaded_at: string;
}

// ---------------------------------------------------------------- 쉬운 말
export const PURPOSE_LIVE_KO: Record<string, string> = { compensation: "보상", campaign: "캠페인" };
export const CHANNEL_LIVE_KO: Record<string, string> = { banner: "쇼핑몰 배너", email: "메일", sns: "SNS" };
const COMPONENT_KO: Record<string, string> = { payment: "결제", api: "판매 사이트", db: "주문 DB", search: "검색", shipping: "배송" };
const HOLD_KO: Record<string, string> = {
  over_threshold: "금액 기준을 넘음", amount_mismatch: "적어 낸 금액과 계산이 다름", unregistered_action: "등록되지 않은 일",
  verify_rejected: "재무 AI가 거절함", verify_timeout: "확인하는 쪽이 응답하지 않음", schema_invalid: "요청 내용이 잘못됨",
  review_timeout: "기한 안에 결정이 없어 자동 취소", limit_exceeded: "기준 이상",
};
export const holdKo = (code: string | null | undefined) => (code ? HOLD_KO[code] ?? "기타 사유" : "");
const STATUS_KO: Record<string, string> = { executed: "실행됨", verifying: "확인 중", held: "사람 결정 대기", rejected: "거절됨", failed: "실패" };
export const statusKo = (s: string) => STATUS_KO[s] ?? "확인할 수 없음";
const ACTION_KO: Record<string, string> = { issue_coupon: "쿠폰 발급", publish_notice: "공지 게시" };
export const actionKo = (a: string) => ACTION_KO[a] ?? "기타 요청";

/** 사건 이름: "결제 장애 · 10. 28. 오후 04:32". 모르는 사건이면 "장애 사건", 사건 밖이면 "사건 밖 요청" */
export function incidentName(contextId: string | null, incidents: Map<string, IncidentLite>): string {
  if (!contextId || !contextId.startsWith("ctx_inc_")) return "사건 밖 요청";
  const inc = incidents.get(contextId);
  if (!inc) return "장애 사건";
  const comp = inc.component ? COMPONENT_KO[inc.component] ?? "서비스" : "서비스";
  return inc.opened_at ? `${comp} 장애 · ${shortDateTime(inc.opened_at)}` : `${comp} 장애`;
}

/** 사건이 끝났는지 (끝난 시각이 있으면) */
export const incidentClosed = (contextId: string, incidents: Map<string, IncidentLite>) => Boolean(incidents.get(contextId)?.closed_at);

// ---------------------------------------------------------------- 불러오기
async function getJson<T>(url: string, server: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store" });
  } catch {
    throw new Error(`${server}에 연결하지 못했습니다.`);
  }
  if (!res.ok) throw new Error(`${server} 요청에 실패했습니다 (${res.status}).`);
  const body = (await res.json()) as { data?: T } | T;
  return (body && typeof body === "object" && "data" in body ? body.data : body) as T;
}

/** 마케팅 AI가 보낸 요청인지. 서비스로 나뉜 뒤에는 `coupon`, 예전 이름은 `marketing` */
const fromMarketing = (requester: string) => requester === "coupon" || requester === "marketing";
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

function toCoupon(e: ExecutionRecord): CouponRun {
  const p = e.request.params;
  const count = num(p.count) ?? 1;
  const unit = num(p.unit_value);
  // 총액은 안전 장치가 계산한 값(건수 x 단가)을 먼저 쓴다
  const total = e.decision.computed_amount ?? num(p.total_value) ?? (unit === null ? 0 : count * unit);
  return {
    execution_id: e.execution_id,
    at: e.created_at,
    context_id: e.request.context_id,
    purpose: str(p.purpose_code),
    buyer_id: str(p.buyer_id) ?? e.customer,
    count,
    unit_value: unit,
    total,
    grade: e.decision.grade,
    status: e.decision.status,
    hold_reason: e.decision.hold_reason,
  };
}

function groupCoupons(coupons: CouponRun[]): CouponGroup[] {
  const groups = new Map<string, CouponGroup & { buyers: Set<string> }>();
  for (const c of coupons) {
    const key = `${c.context_id}|${c.purpose ?? ""}`;
    const g = groups.get(key) ?? {
      key, context_id: c.context_id, purpose: c.purpose, issued: 0, issued_total: 0, held: 0, held_total: 0, failed: 0,
      customers: 0, last_at: c.at, buyers: new Set<string>(),
    };
    if (c.status === "executed") { g.issued += c.count; g.issued_total += c.total; }
    else if (c.status === "held" || c.status === "verifying") { g.held += c.count; g.held_total += c.total; }
    else g.failed += 1;
    if (c.buyer_id) g.buyers.add(c.buyer_id);
    g.customers = g.buyers.size;
    if (c.at > g.last_at) g.last_at = c.at;
    groups.set(key, g);
  }
  return [...groups.values()]
    .map(({ buyers: _buyers, ...g }) => g)
    .sort((a, b) => b.last_at.localeCompare(a.last_at));
}

export async function loadMarketingLive(): Promise<MarketingLive> {
  const [banners, executions, reviews, incidents] = await Promise.allSettled([
    getJson<ShopBanner[]>(`${RELAY_API_URL}/api/shop/banners`, "캣북스 쇼핑몰 서버"),
    finance.executions(),
    finance.reviews(),
    getJson<IncidentLite[]>(`${DASHBOARD_API_URL}/api/dashboard/incidents`, "화면 중계 서버"),
  ]);
  const errors: string[] = [];
  const why = (r: PromiseRejectedResult) => (r.reason instanceof Error ? r.reason.message : "알 수 없는 오류");
  if (banners.status === "rejected") errors.push(`게시 중 공지를 읽지 못했습니다. ${why(banners)}`);
  if (executions.status === "rejected") errors.push(`공지·쿠폰 처리 기록을 읽지 못했습니다. ${why(executions)}`);
  if (reviews.status === "rejected") errors.push(`보류 건을 읽지 못했습니다. ${why(reviews)}`);
  // 사건 이름은 못 읽어도 화면은 돈다 ("장애 사건"으로 보인다)

  const bannerList = banners.status === "fulfilled" && Array.isArray(banners.value) ? banners.value : null;
  const incidentMap = new Map<string, IncidentLite>();
  if (incidents.status === "fulfilled" && Array.isArray(incidents.value)) for (const i of incidents.value) incidentMap.set(i.context_id, i);

  let notices: NoticeRun[] | null = null;
  let coupons: CouponRun[] | null = null;
  if (executions.status === "fulfilled") {
    const mine = executions.value.filter((e) => fromMarketing(e.request.requester));
    notices = mine
      .filter((e) => e.request.action === "publish_notice")
      .map((e) => ({
        execution_id: e.execution_id,
        at: e.created_at,
        context_id: e.request.context_id,
        channel: str(e.request.params.channel_code),
        grade: e.decision.grade,
        status: e.decision.status,
        hold_reason: e.decision.hold_reason,
        banner: bannerList?.find((b) => b.context_id === e.request.context_id) ?? null,
      }))
      .sort((a, b) => b.at.localeCompare(a.at));
    coupons = mine.filter((e) => e.request.action === "issue_coupon").map(toCoupon).sort((a, b) => b.at.localeCompare(a.at));
  }

  const holds = reviews.status === "fulfilled"
    ? reviews.value.filter((r) => r.status === "pending" && fromMarketing(r.requester) && (r.action === "issue_coupon" || r.action === "publish_notice"))
    : null;

  return {
    banners: bannerList,
    notices,
    coupons,
    groups: groupCoupons(coupons ?? []),
    holds,
    incidents: incidentMap,
    errors,
    loaded_at: new Date().toISOString(),
  };
}
