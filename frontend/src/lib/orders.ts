import { ApiError } from "./apiError";
import { DASHBOARD_API_URL } from "./env";
import type { FinanceMeta } from "./finance";
import type { OrderRow } from "./support";

// 주문 관리(/orders): 판매 서버의 주문을 최신순·페이지로 본다. 읽기 전용이다.
// dashboard-api `GET /api/dashboard/orders/page`가 판매 서버에 원래 있는 주문 목록을 받아 나눠 준다(판매 서버는 고치지 않는다).
// 판매 서버가 주지 않은 값은 null이고 화면에서 "—"로 보여 준다. 값은 만들지 않는다.

/** 위쪽 상태 탭. 환불 두 가지는 판매 서버 상태가 아니라 환불 금액으로 나눈다 */
export type OrderTab = "all" | "pending_payment" | "paid" | "partial_refund" | "full_refund" | "cancelled" | "payment_failed";
export const ORDER_TABS: { id: OrderTab; label: string }[] = [
  { id: "all", label: "전체" },
  { id: "pending_payment", label: "결제 대기" },
  { id: "paid", label: "결제 완료" },
  { id: "partial_refund", label: "부분 환불" },
  { id: "full_refund", label: "전체 환불" },
  { id: "cancelled", label: "취소" },
  { id: "payment_failed", label: "결제 실패" },
];

export interface OrderListRow extends OrderRow {
  buyer_id: string | null;
  shipped_at: string | null;
  delivered_at: string | null;
}

export interface OrderPage {
  rows: OrderListRow[];
  /** 고른 탭·기간·검색에 맞는 전체 건수 */
  total: number;
  /** 탭별 건수 (기간·검색은 같이 적용). 서버가 주지 않으면 null */
  counts: Partial<Record<OrderTab, number>> | null;
  meta: FinanceMeta;
}

export interface OrderQuery {
  tab: OrderTab;
  since?: string;
  until?: string;
  /** 주문 번호(일부도 됨) */
  q?: string;
  buyerId?: string;
  limit: number;
  offset: number;
}

/** 판매 서버 상태와 환불 금액으로 탭을 고른다 (dashboard-api `order_tab`과 같은 규칙) */
export function tabOf(o: Pick<OrderRow, "status" | "total_amount" | "refunded_amount">): OrderTab {
  const refunded = o.refunded_amount ?? 0;
  if (refunded > 0 && o.total_amount != null) return refunded >= o.total_amount ? "full_refund" : "partial_refund";
  return (ORDER_TABS.some((t) => t.id === o.status) ? o.status : "all") as OrderTab;
}

const qs = (p: Record<string, string | number | undefined>) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v !== undefined && v !== "") q.set(k, String(v));
  return q.toString();
};

export async function orderPage(query: OrderQuery): Promise<OrderPage> {
  const path = `/api/dashboard/orders/page?${qs({
    status: query.tab === "all" ? undefined : query.tab,
    since: query.since,
    until: query.until,
    q: query.q,
    buyer_id: query.buyerId,
    limit: query.limit,
    offset: query.offset,
  })}`;
  let res: Response;
  try {
    res = await fetch(`${DASHBOARD_API_URL}${path}`, { cache: "no-store" });
  } catch {
    throw new ApiError("업무 서버(dashboard-api)에 연결하지 못했습니다. 서버가 켜져 있는지 확인하세요.", 0);
  }
  const body = (await res.json().catch(() => null)) as {
    data?: OrderListRow[];
    meta?: FinanceMeta & { total?: number; counts?: Partial<Record<OrderTab, number>> };
    error?: { message?: string };
  } | null;
  if (!res.ok) throw new ApiError(body?.error?.message ?? `요청에 실패했습니다 (${res.status}).`, res.status);
  if (!body?.data) throw new ApiError("서버 응답이 비어 있습니다.", res.status);
  const meta = { mock: false, ...(body.meta ?? {}) };
  return { rows: body.data, total: meta.total ?? body.data.length, counts: meta.counts ?? null, meta };
}
