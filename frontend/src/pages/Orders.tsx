import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { Chip, Empty, ErrorBox, TONE, Waiting, useResource } from "@/components/finance/bits";
import { DASH, OrderDetail, STATUS_TONE, money, time } from "@/components/orders/OrderDetail";
import { PreparingNotice } from "@/components/Preparing";
import { CompanyName } from "@/demo/CompanyName";
import { api } from "@/lib/api";
import { humanId } from "@/lib/format";
import { LIST_UNSUPPORTED, ORDER_TABS, orderPage, tabOf, type OrderListRow, type OrderTab } from "@/lib/orders";

// 주문 관리(고객지원 AI의 서비스 "주문 관리", /orders): 판매자 센터의 주문 조회처럼 전체 주문을 최신순으로 본다.
// 위쪽 탭은 상태(부분·전체 환불 포함)와 건수, 아래는 기간·구매자·주문 번호로 거르기, 한 페이지 20건.
// 보기만 한다. 취소·환불은 클레임 흐름(안전 관문)에서 한다. 판매 서버에 없는 값은 "—"로 보여 준다.

const REFRESH_MS = 30_000;
const PAGE_SIZE = 20;
const DAY_MS = 86_400_000;
const KST = "+09:00";

type Preset = "1" | "7" | "30" | "90" | "custom";
const PRESETS: { id: Preset; label: string }[] = [
  { id: "1", label: "오늘" },
  { id: "7", label: "1주" },
  { id: "30", label: "1개월" },
  { id: "90", label: "3개월" },
  { id: "custom", label: "직접 지정" },
];

/** 화면의 "yyyy-mm-dd"(한국 날짜)를 서버에 줄 ISO 시각으로 */
const kstStart = (day: string) => new Date(`${day}T00:00:00${KST}`).toISOString();
const todayKst = () => new Date(Date.now() + 9 * 3_600_000).toISOString().slice(0, 10);
const buyerKey = (id: number) => `buy_${String(id).padStart(4, "0")}`;

const TAB_TONE: Partial<Record<OrderTab, string>> = { partial_refund: TONE.warn, full_refund: TONE.muted };
const TAB_LABEL = Object.fromEntries(ORDER_TABS.map((t) => [t.id, t.label])) as Record<OrderTab, string>;

function StatusChip({ row }: { row: OrderListRow }) {
  const tab = tabOf(row);
  if (tab === "partial_refund" || tab === "full_refund") return <Chip label={TAB_LABEL[tab]} tone={TAB_TONE[tab] ?? TONE.muted} />;
  return <Chip label={row.status_ko} tone={STATUS_TONE[row.status] ?? TONE.muted} />;
}

/** 1 … 4 5 [6] 7 8 … 20 */
function pageList(current: number, last: number): (number | "gap")[] {
  const out: (number | "gap")[] = [];
  for (let p = 1; p <= last; p++) {
    if (p === 1 || p === last || Math.abs(p - current) <= 2) out.push(p);
    else if (out[out.length - 1] !== "gap") out.push("gap");
  }
  return out;
}

function Pager({ page, last, onPage }: { page: number; last: number; onPage: (p: number) => void }) {
  if (last <= 1) return null;
  const btn = "min-w-[2rem] rounded-lg px-2 py-1 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500";
  return (
    <nav aria-label="페이지" className="flex flex-wrap items-center justify-center gap-1">
      <button type="button" className={`${btn} text-ink-700 hover:bg-ink-100 disabled:opacity-40`} disabled={page <= 1} onClick={() => onPage(page - 1)}>이전</button>
      {pageList(page, last).map((p, i) => p === "gap"
        ? <span key={`gap-${i}`} className="px-1 text-ink-400">…</span>
        : <button key={p} type="button" aria-current={p === page ? "page" : undefined} onClick={() => onPage(p)}
            className={`${btn} ${p === page ? "bg-brand-600 font-semibold text-white" : "text-ink-700 hover:bg-ink-100"}`}>{p}</button>)}
      <button type="button" className={`${btn} text-ink-700 hover:bg-ink-100 disabled:opacity-40`} disabled={page >= last} onClick={() => onPage(page + 1)}>다음</button>
    </nav>
  );
}

export default function OrdersPage() {
  const [params, setParams] = useSearchParams();
  const buyer = params.get("buyer") ?? "";
  const tab = (ORDER_TABS.find((t) => t.id === params.get("tab"))?.id ?? "all") as OrderTab;
  const pageNo = Math.max(1, Number(params.get("page")) || 1);
  const q = params.get("q") ?? "";

  /** 주소에 남겨 새로고침해도 같은 것을 본다. 거르기를 바꾸면 1페이지로 */
  const setQuery = useCallback((next: Record<string, string>, keepPage = false) => {
    setParams((cur) => {
      const p = new URLSearchParams(cur);
      for (const [k, v] of Object.entries(next)) if (v) p.set(k, v); else p.delete(k);
      if (!keepPage) p.delete("page");
      return p;
    }, { replace: true });
  }, [setParams]);

  const [preset, setPreset] = useState<Preset>("30");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [search, setSearch] = useState(q);
  const [picked, setPicked] = useState<string | null>(null);

  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((n) => n + 1), []);
  useEffect(() => {
    const t = setInterval(bump, REFRESH_MS);
    return () => clearInterval(t);
  }, [bump]);

  // 새로고침(tick)마다 "최근 N일"의 기준 시각을 다시 잡는다. "오늘"은 한국 날짜 0시부터
  const period = useMemo(() => {
    if (preset === "custom") {
      return { since: from ? kstStart(from) : undefined, until: to ? new Date(new Date(kstStart(to)).getTime() + DAY_MS).toISOString() : undefined };
    }
    if (preset === "1") return { since: kstStart(todayKst()), until: undefined };
    return { since: new Date(Date.now() - Number(preset) * DAY_MS).toISOString(), until: undefined };
  }, [preset, from, to, tick]);

  const users = useResource("order-buyers", async () => ({ data: await api.users(), meta: { mock: false } }), 0);
  const query = { tab, since: period.since, until: period.until, q: q || undefined, buyerId: buyer || undefined, limit: PAGE_SIZE, offset: (pageNo - 1) * PAGE_SIZE };
  const list = useResource(`orders-page-${JSON.stringify(query)}`, async () => {
    const page = await orderPage(query);
    return { data: page, meta: page.meta };
  }, tick);
  const unsupported = list.error !== null && !list.env && list.error === LIST_UNSUPPORTED;

  useEffect(() => { setPicked(null); }, [tab, buyer, q, period.since, period.until, pageNo]);
  useEffect(() => {
    if (picked) document.getElementById("order-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [picked]);

  const page = list.env?.data;
  const last = page ? Math.max(1, Math.ceil(page.total / PAGE_SIZE)) : 1;

  // 구매자 찾기: 이름 일부·구매자 번호(buy_0007)·숫자(7) 중 무엇이든. 구매자가 많아도 목록을 넘기지 않게 검색으로 고른다
  const people = useMemo(() => (users.env?.data ?? []).map((u) => ({ id: buyerKey(u.id), name: u.name })), [users.env]);
  const label = (id: string) => {
    const p = people.find((x) => x.id === id);
    return p ? `${p.name} (${p.id})` : id;
  };
  const [buyerText, setBuyerText] = useState(buyer ? label(buyer) : "");
  const [buyerError, setBuyerError] = useState<string | null>(null);
  useEffect(() => { if (buyer) setBuyerText(label(buyer)); }, [buyer, people]); // eslint-disable-line react-hooks/exhaustive-deps
  const needle = buyerText.trim().toLowerCase();
  const suggestions = needle ? people.filter((p) => p.name.toLowerCase().includes(needle) || p.id.includes(needle)).slice(0, 30) : [];

  const resolveBuyer = (raw: string): string | { error: string } => {
    const text = raw.trim();
    if (!text) return "";
    const byId = /buy_\d+/i.exec(text)?.[0].toLowerCase();
    if (byId) return byId;
    if (/^\d+$/.test(text)) return buyerKey(Number(text));
    const lower = text.toLowerCase();
    const exact = people.filter((p) => p.name.toLowerCase() === lower);
    if (exact.length === 1) return exact[0].id;
    const partial = people.filter((p) => p.name.toLowerCase().includes(lower));
    if (partial.length === 1) return partial[0].id;
    if (!partial.length) return { error: `"${text}"에 맞는 구매자가 없어요.` };
    return { error: `"${text}"에 맞는 구매자가 ${partial.length}명이에요. 아래 목록에서 한 명을 고르거나 구매자 번호로 찾아 주세요.` };
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const found = resolveBuyer(buyerText);
    if (typeof found !== "string") return setBuyerError(found.error);
    setBuyerError(null);
    setQuery({ q: search.trim(), buyer: found });
  };
  const clearSearch = () => {
    setBuyerText("");
    setSearch("");
    setBuyerError(null);
    setQuery({ q: "", buyer: "" });
  };

  return (
    <div className="w-full space-y-6 px-4 pb-12 pt-2 sm:px-8">
      <header className="flex flex-wrap items-end gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight">주문 관리</h1>
          <p className="mt-1 text-sm text-ink-500">
            <CompanyName />의 판매 서버에 있는 주문을 최신순으로 봅니다. 취소와 부분·전체 환불도 위 탭으로 따로 볼 수 있습니다.
            보기만 하며, 취소·환불 처리는 클레임에서 합니다.
          </p>
        </div>
        <button type="button" className="btn-ghost text-xs sm:ml-auto" onClick={bump}>새로고침</button>
      </header>

      <div role="tablist" aria-label="주문 상태" className="flex w-full flex-wrap gap-1 rounded-2xl bg-surface p-1 ring-1 ring-ink-100">
        {ORDER_TABS.map((t) => {
          const count = page?.counts?.[t.id];
          return (
            <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => setQuery({ tab: t.id === "all" ? "" : t.id })}
              className={`flex-1 whitespace-nowrap rounded-xl px-3 py-2 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${tab === t.id ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>
              {t.label}
              <span className={`ml-1.5 text-xs font-medium ${tab === t.id ? "text-white/80" : "text-ink-500"}`}>{count == null ? "" : count.toLocaleString("ko-KR")}</span>
            </button>
          );
        })}
      </div>

      <section className="card space-y-4 p-4 sm:p-5" aria-label="주문 목록">
        <div className="flex flex-wrap items-end gap-3">
          <div role="group" aria-label="조회 기간" className="flex gap-1 rounded-full bg-surface p-1 ring-1 ring-ink-100">
            {PRESETS.map((p) => (
              <button key={p.id} type="button" aria-pressed={preset === p.id} onClick={() => { setPreset(p.id); setQuery({}); }}
                className={`rounded-full px-3 py-1 text-xs font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 ${preset === p.id ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>{p.label}</button>
            ))}
          </div>
          {preset === "custom" && (
            <div className="flex flex-wrap items-end gap-2">
              <label className="text-xs font-medium text-ink-700">시작일<input type="date" className="field mt-1 block" value={from} max={to || undefined} onChange={(e) => { setFrom(e.target.value); setQuery({}); }} /></label>
              <label className="text-xs font-medium text-ink-700">끝일<input type="date" className="field mt-1 block" value={to} min={from || undefined} onChange={(e) => { setTo(e.target.value); setQuery({}); }} /></label>
            </div>
          )}
          <form onSubmit={submit} className="flex flex-wrap items-end gap-2" role="search">
            <label className="text-xs font-medium text-ink-700">
              구매자
              <input type="search" className="field mt-1 block w-56" value={buyerText} list="order-buyer-suggest" autoComplete="off"
                placeholder="이름이나 구매자 번호" onChange={(e) => { setBuyerText(e.target.value); setBuyerError(null); }} />
              <datalist id="order-buyer-suggest">
                {suggestions.map((p) => <option key={p.id} value={`${p.name} (${p.id})`} />)}
              </datalist>
            </label>
            <label className="text-xs font-medium text-ink-700">
              주문 번호
              <input type="search" className="field mt-1 block w-44" value={search} placeholder="예: ord_2156" onChange={(e) => setSearch(e.target.value)} />
            </label>
            <button type="submit" className="btn-primary text-xs">찾기</button>
            {(buyer || q) && <button type="button" className="btn-quiet text-xs" onClick={clearSearch}>검색 지우기</button>}
          </form>
        </div>

        {buyerError && <p role="alert" className="text-sm text-red-700">{buyerError}</p>}
        {buyer && !buyerError && <p className="text-xs text-ink-500">구매자 <span className="font-medium text-ink-900">{label(buyer)}</span>의 주문만 보고 있어요.</p>}
        {users.error && <ErrorBox message={`구매자 이름을 받지 못했습니다. 구매자 번호로는 찾을 수 있어요. ${users.error}`} onRetry={users.reload} />}

        {unsupported ? (
          <PreparingNotice what="전체 주문" source="판매 서버의 주문 페이지 목록" />
        ) : !page ? (
          list.error ? <ErrorBox message={`판매 서버에서 주문을 읽지 못했습니다. ${list.error}`} onRetry={list.reload} /> : <Waiting />
        ) : (
          <>
            {list.error && <ErrorBox message={`새로 받지 못해 이전 값을 보여 줍니다. ${list.error}`} onRetry={list.reload} />}
            {page.buyerOnly && (
              <p role="status" className="rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-800">
                전체 주문 목록은 아직 준비 중이라, 고른 구매자의 주문만 보여 줍니다. 배송 시각은 상세에서 볼 수 있습니다.
              </p>
            )}
            <p className="text-xs text-ink-500">
              {page.total.toLocaleString("ko-KR")}건, 최신순{page.total > 0 ? ` · ${pageNo} / ${last} 페이지` : ""}
            </p>
            {page.rows.length === 0 ? <Empty>조건에 맞는 주문이 없습니다.</Empty> : (
              <div className="overflow-x-auto rounded-xl border border-ink-100">
                <table className="w-full min-w-[56rem] text-left text-sm">
                  <thead className="bg-surface text-xs text-ink-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">주문 번호</th>
                      <th className="pr-3 font-medium">주문 일시</th>
                      <th className="pr-3 font-medium">구매자</th>
                      <th className="pr-3 font-medium">상품</th>
                      <th className="pr-3 text-right font-medium">결제 금액</th>
                      <th className="pr-3 text-right font-medium">환불 금액</th>
                      <th className="pr-3 font-medium">상태</th>
                      <th className="pr-3 font-medium">배송</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.rows.map((o) => (
                      <tr key={o.order_no} className={`border-t border-ink-100 ${picked === o.order_no ? "bg-brand-50" : ""}`}>
                        <td className="px-3 py-2 whitespace-nowrap">
                          <button type="button" aria-pressed={picked === o.order_no} onClick={() => setPicked(o.order_no)} title={o.order_no}
                            className="font-medium text-brand-700 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
                            {humanId(o.order_no)}
                          </button>
                        </td>
                        <td className="pr-3 whitespace-nowrap text-ink-700">{time(o.created_at)}</td>
                        <td className="pr-3 whitespace-nowrap text-ink-700" title={o.buyer_id ?? undefined}>{o.buyer_id ? humanId(o.buyer_id) : DASH}</td>
                        <td className="max-w-[16rem] truncate pr-3 text-ink-900" title={o.title}>{o.title}</td>
                        <td className="pr-3 text-right whitespace-nowrap">{money(o.total_amount)}</td>
                        <td className="pr-3 text-right whitespace-nowrap">{o.refunded_amount ? money(o.refunded_amount) : DASH}</td>
                        <td className="pr-3"><StatusChip row={o} /></td>
                        <td className="pr-3 whitespace-nowrap text-xs text-ink-700">
                          {o.delivered_at ? `도착 ${time(o.delivered_at)}` : o.shipped_at ? `발송 ${time(o.shipped_at)}` : DASH}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <Pager page={pageNo} last={last} onPage={(p) => setQuery({ page: p > 1 ? String(p) : "" }, true)} />
          </>
        )}
      </section>

      {picked && <OrderDetail orderNo={picked} tick={tick} onClose={() => setPicked(null)} />}
    </div>
  );
}
