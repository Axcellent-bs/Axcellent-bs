import { useEffect, useMemo, useState } from "react";
import { TableView } from "@/components/charts";
import { Chip, Empty, ErrorBox, Panel, RefList, TONE, Waiting, useResource } from "@/components/finance/bits";
import { api } from "@/lib/api";
import { companyConnection, type ConnectionState } from "@/lib/companyConnection";
import { humanId, shortDateTime, won } from "@/lib/format";
import { support, type OrderDetail, type OrderRow } from "@/lib/support";

// 주문 내역: 구매자를 고르면 그 구매자의 주문(최신순)과 한 건의 상세를 보여 준다. 읽기 전용이다.
// 판매 서버에 없는 값은 0이나 빈 칸으로 채우지 않고 "—"로 보여 준다. 판매 서버를 못 읽으면 이유를 보여 준다.
// 취소·환불 같은 쓰기는 여기에 없다(기존 클레임 흐름의 안전 관문을 쓴다).

const DASH = "—";
const DAY_MS = 86_400_000;
const PAGE = 100;
const KST = "+09:00";

type Preset = "7" | "30" | "90" | "custom";
const PRESETS: { id: Preset; label: string }[] = [
  { id: "7", label: "최근 7일" },
  { id: "30", label: "최근 30일" },
  { id: "90", label: "최근 90일" },
  { id: "custom", label: "직접 지정" },
];

const SOURCE_KO: Record<string, string> = { connection: "회사 연결", env: "환경 설정", default: "기본 주소" };

const STATUS_TONE: Record<string, string> = {
  paid: TONE.ok,
  pending_payment: TONE.warn,
  payment_failed: TONE.bad,
  cancelled: TONE.muted,
};

/** 판매 서버가 쓰는 결제 값의 한글 이름. 모르는 값은 그대로 보여 준다 */
const METHOD_KO: Record<string, string> = { card: "카드", bank_transfer: "계좌이체", points: "적립금" };
const PAY_KO: Record<string, string> = { succeeded: "성공", failed: "실패", pending: "대기", refunded: "환불" };
const label = (map: Record<string, string>, v: string | null | undefined) => (v == null || v === "" ? DASH : map[v] ?? v);

const money = (n: number | null | undefined) => (n == null ? DASH : won(n));
const time = (iso: string | null | undefined) => (iso ? shortDateTime(iso) : DASH);
const text = (v: string | number | null | undefined) => (v == null || v === "" ? DASH : String(v));
/** 화면의 "yyyy-mm-dd"(한국 날짜)를 판매 서버에 줄 ISO 시각으로 */
const kstStart = (day: string) => new Date(`${day}T00:00:00${KST}`).toISOString();
/** 숫자 사용자 ID: `buy_0007` → 7 */
const userNumber = (buyerId: string) => Number(buyerId.replace(/\D/g, "")) || 0;
const buyerKey = (id: number) => `buy_${String(id).padStart(4, "0")}`;

function ShopSource() {
  const [state, setState] = useState<ConnectionState | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    companyConnection.get().then((s) => alive && setState(s)).catch(() => alive && setFailed(true));
    return () => { alive = false; };
  }, []);
  if (failed) return <p className="text-sm text-ink-500">읽는 판매 서버 주소를 확인하지 못했습니다.</p>;
  if (!state) return <p className="text-sm text-ink-500">읽는 판매 서버 확인 중…</p>;
  return (
    <p className="text-sm text-ink-700">
      읽는 판매 서버: <span className="break-all font-mono text-xs">{state.shop_api_url}</span>
      <span className="ml-2 chip bg-ink-100 text-xs text-ink-700">{SOURCE_KO[state.source] ?? state.source}</span>
    </p>
  );
}

function Detail({ orderNo, tick }: { orderNo: string; tick: number }) {
  const res = useResource(`order-${orderNo}`, () => support.order(orderNo), tick);
  if (!res.env) return res.error ? <ErrorBox message={`주문을 읽지 못했습니다. ${res.error}`} onRetry={res.reload} /> : <Waiting />;
  const o: OrderDetail = res.env.data;
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
      tools={<Chip label={o.status_ko} tone={STATUS_TONE[o.status] ?? TONE.muted} />}>
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
        <RefList refs={[o.order_no]} label="주문 번호 보기" />
      </div>
    </Panel>
  );
}

export function OrderHistory({ tick, buyer, onBuyer }: { tick: number; buyer: string | null; onBuyer: (id: string) => void }) {
  const users = useResource("order-buyers", async () => ({ data: await api.users(), meta: { mock: false } }), 0);
  const [preset, setPreset] = useState<Preset>("30");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [shown, setShown] = useState(PAGE);
  const [picked, setPicked] = useState<string | null>(null);

  // 주문을 고르면 아래 상세가 보이도록 옮긴다 (목록이 길어도 찾지 않게)
  useEffect(() => {
    if (picked) document.getElementById("order-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [picked]);

  const list = users.env?.data ?? [];
  const current = buyer ?? (list[0] ? buyerKey(list[0].id) : null);
  // 새로고침(tick)마다 "최근 N일"의 기준 시각을 다시 잡는다
  const period = useMemo(() => {
    if (preset === "custom") {
      return { since: from ? kstStart(from) : undefined, until: to ? new Date(new Date(kstStart(to)).getTime() + DAY_MS).toISOString() : undefined };
    }
    return { since: new Date(Date.now() - Number(preset) * DAY_MS).toISOString(), until: undefined };
  }, [preset, from, to, tick]);

  const orders = useResource(
    `orders-${current}-${period.since ?? ""}-${period.until ?? ""}`,
    () => (current ? support.orders(userNumber(current), period.since, period.until) : Promise.resolve({ data: [] as OrderRow[], meta: { mock: false } })),
    tick,
  );
  useEffect(() => { setShown(PAGE); setPicked(null); }, [current, period.since, period.until]);

  if (!users.env) return users.error ? <ErrorBox message={`구매자 목록을 받지 못했습니다. ${users.error}`} onRetry={users.reload} /> : <Waiting />;

  const rows: OrderRow[] = orders.env?.data ?? [];
  const open = rows.find((o) => o.order_no === picked)?.order_no ?? picked;

  return (
    <div className="space-y-5">
      <Panel id="order-history" title="주문 내역" meta={orders.env?.meta}
        hint="구매자를 고르면 그 구매자의 주문을 최신순으로 보여 줍니다. 보기만 하며, 취소·환불은 클레임 흐름에서 합니다."
        tools={<button type="button" className="btn-ghost text-xs" onClick={() => void orders.reload()}>다시 읽기</button>}>
        <div className="space-y-4">
          <ShopSource />
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs font-medium text-ink-700">
              구매자
              <select className="field mt-1 block min-w-[12rem]" value={current ?? ""} onChange={(e) => onBuyer(e.target.value)}>
                {list.map((u) => <option key={u.id} value={buyerKey(u.id)}>{u.name} ({buyerKey(u.id)})</option>)}
              </select>
            </label>
            <div role="group" aria-label="조회 기간" className="flex gap-1 rounded-full bg-surface p-1 ring-1 ring-ink-100">
              {PRESETS.map((p) => (
                <button key={p.id} type="button" aria-pressed={preset === p.id} onClick={() => setPreset(p.id)}
                  className={`rounded-full px-3 py-1 text-xs font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${preset === p.id ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>{p.label}</button>
              ))}
            </div>
            {preset === "custom" && (
              <div className="flex flex-wrap items-end gap-2">
                <label className="text-xs font-medium text-ink-700">시작일<input type="date" className="field mt-1 block" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} /></label>
                <label className="text-xs font-medium text-ink-700">끝일<input type="date" className="field mt-1 block" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} /></label>
              </div>
            )}
          </div>

          {!orders.env ? (
            orders.error ? <ErrorBox message={`판매 서버에서 주문을 읽지 못했습니다. ${orders.error}`} onRetry={orders.reload} /> : <Waiting />
          ) : (
            <>
              {orders.error && <ErrorBox message={`새로 받지 못해 이전 값을 보여 줍니다. ${orders.error}`} onRetry={orders.reload} />}
              {rows.length === 0 ? <Empty>이 기간에 주문이 없습니다.</Empty> : (
                <>
                  <p className="text-xs text-ink-500">{rows.length}건{rows.length > shown ? ` 중 ${shown}건 표시` : ""}, 최신순</p>
                  <div className="max-h-[26rem] overflow-auto rounded-xl border border-ink-100">
                    <table className="w-full min-w-[40rem] text-left text-sm">
                      <thead className="sticky top-0 bg-surface text-xs text-ink-500">
                        <tr><th className="px-3 py-2 font-medium">주문 시각</th><th className="pr-3 font-medium">주문</th><th className="pr-3 font-medium">상태</th><th className="pr-3 text-right font-medium">결제 금액</th><th className="pr-3 text-right font-medium">환불</th></tr>
                      </thead>
                      <tbody>
                        {rows.slice(0, shown).map((o) => (
                          <tr key={o.order_no} className={`border-t border-ink-100 ${open === o.order_no ? "bg-brand-50" : ""}`}>
                            <td className="px-3 py-2 whitespace-nowrap text-ink-700">{time(o.created_at)}</td>
                            <td className="pr-3">
                              <button type="button" aria-pressed={open === o.order_no} onClick={() => setPicked(o.order_no)}
                                className="text-left font-medium text-brand-700 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
                                {humanId(o.order_no)} · {o.title}
                              </button>
                            </td>
                            <td className="pr-3"><Chip label={o.status_ko} tone={STATUS_TONE[o.status] ?? TONE.muted} /></td>
                            <td className="pr-3 text-right whitespace-nowrap">{money(o.total_amount)}</td>
                            <td className="pr-3 text-right whitespace-nowrap">{money(o.refunded_amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {rows.length > shown && <button type="button" className="btn-ghost text-xs" onClick={() => setShown((n) => n + PAGE)}>{PAGE}건 더 보기</button>}
                </>
              )}
            </>
          )}
        </div>
      </Panel>
      {open && <Detail orderNo={open} tick={tick} />}
    </div>
  );
}
