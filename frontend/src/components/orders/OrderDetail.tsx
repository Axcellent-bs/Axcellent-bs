import { TableView } from "@/components/charts";
import { Chip, Empty, ErrorBox, Panel, RefList, TONE, Waiting, useResource } from "@/components/finance/bits";
import { humanId, shortDateTime, won } from "@/lib/format";
import { support, type OrderDetail as Detail } from "@/lib/support";

// 주문 한 건의 상세: 품목·결제·환불·배송. 판매 서버에 기록된 값 그대로이고, 없는 값은 "—"로 보여 준다. 읽기 전용이다.

export const DASH = "—";

export const STATUS_TONE: Record<string, string> = {
  paid: TONE.ok,
  pending_payment: TONE.warn,
  payment_failed: TONE.bad,
  cancelled: TONE.muted,
};

/** 판매 서버가 쓰는 결제 값의 한글 이름. 모르는 값은 그대로 보여 준다 */
const METHOD_KO: Record<string, string> = { card: "카드", bank_transfer: "계좌이체", points: "적립금" };
const PAY_KO: Record<string, string> = { succeeded: "성공", failed: "실패", pending: "대기", refunded: "환불" };
const label = (map: Record<string, string>, v: string | null | undefined) => (v == null || v === "" ? DASH : map[v] ?? v);

export const money = (n: number | null | undefined) => (n == null ? DASH : won(n));
export const time = (iso: string | null | undefined) => (iso ? shortDateTime(iso) : DASH);
const text = (v: string | number | null | undefined) => (v == null || v === "" ? DASH : String(v));

export function OrderDetail({ orderNo, tick, onClose }: { orderNo: string; tick: number; onClose?: () => void }) {
  const res = useResource(`order-${orderNo}`, () => support.order(orderNo), tick);
  if (!res.env) return res.error ? <ErrorBox message={`주문을 읽지 못했습니다. ${res.error}`} onRetry={res.reload} /> : <Waiting />;
  const o: Detail = res.env.data;
  const facts: [string, string][] = [
    ["주문 시각", time(o.created_at)],
    ["결제 금액", money(o.total_amount)],
    ["환불 금액", money(o.refunded_amount)],
    ["배송비", money(o.shipping_fee)],
    ["적립금 사용", money(o.points_used)],
    ["쿠폰 할인", money(o.coupon_discount)],
    ["배송 예정", time(o.promised_delivery_at)],
    ["출고", time(o.shipped_at)],
    ["배송 완료", time(o.delivered_at)],
  ];
  return (
    <Panel id="order-detail" title={`${humanId(o.order_no)} 상세`} meta={res.env.meta}
      hint="판매 서버에 기록된 값 그대로입니다. 판매 서버에 없는 값은 —로 보입니다."
      tools={
        <div className="flex items-center gap-2">
          <Chip label={o.status_ko} tone={STATUS_TONE[o.status] ?? TONE.muted} />
          {onClose && <button type="button" className="btn-ghost text-xs" onClick={onClose}>닫기</button>}
        </div>
      }>
      {res.error && <div className="mb-3"><ErrorBox message={`새로 받지 못해 이전 값을 보여 줍니다. ${res.error}`} onRetry={res.reload} /></div>}
      <div className="space-y-4">
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
          {facts.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3 border-b border-ink-100 pb-1 sm:block sm:border-0 sm:pb-0">
              <dt className="text-xs text-ink-500">{k}</dt>
              <dd className="font-medium text-ink-900">{v}</dd>
            </div>
          ))}
        </dl>
        <div>
          <p className="mb-2 text-xs font-medium text-ink-700">품목</p>
          {o.items.length === 0 ? <Empty>품목 기록이 없습니다.</Empty> : (
            <TableView columns={["상품", "수량", "단가", "결제 몫"]}
              rows={o.items.map((i) => [text(i.title), text(i.qty), money(i.unit_price), money(i.paid_amount)])} />
          )}
        </div>
        <div>
          <p className="mb-2 text-xs font-medium text-ink-700">결제 내역</p>
          {o.payments.length === 0 ? <Empty>결제 기록이 없습니다.</Empty> : (
            <TableView columns={["시각", "금액", "수단", "결과", "실패 사유"]}
              rows={o.payments.map((p) => [time(p.created_at), money(p.amount), label(METHOD_KO, p.method_code), label(PAY_KO, p.status), text(p.failure_code)])} />
          )}
        </div>
        <div>
          <p className="mb-2 text-xs font-medium text-ink-700">환불 내역</p>
          {o.refunds == null ? <Empty>이 판매 서버는 환불 기록을 따로 주지 않습니다. 환불 금액 합계는 위에 있습니다.</Empty>
            : o.refunds.length === 0 ? <Empty>환불 기록이 없습니다.</Empty> : (
              <TableView columns={["차례", "시각", "금액"]}
                rows={o.refunds.map((r, i) => [`${i + 1}번째`, time(r.created_at), money(r.amount)])} />
            )}
        </div>
        <RefList refs={[o.order_no]} label="주문 번호 보기" />
      </div>
    </Panel>
  );
}
