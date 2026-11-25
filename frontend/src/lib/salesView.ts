// 매출 분석 탭(components/data/SalesAnalysis.tsx)이 그리는 값. 서버가 준 숫자를 글자로만 바꾼다(새 지표를 계산하지 않는다).
// 순수 함수라 node --test로 시험한다 (salesView.test.ts). 코드값(category code · source · medium · product_id)은 글자로 내보내지 않는다.
import type { ChannelValueOutput, SalesAnalysisOutput, SalesPeriod } from "./data.ts";

export type Tone = "up" | "down" | "flat";

export const SALES_PERIODS: { id: SalesPeriod; label: string }[] = [
  { id: "today", label: "오늘" },
  { id: "7d", label: "최근 7일" },
  { id: "30d", label: "최근 30일" },
];

export function compareLabel(period: SalesPeriod): string {
  return period === "today" ? "비교: 직전 하루" : period === "7d" ? "비교: 직전 7일" : "비교: 직전 30일";
}

const withComma = (n: number) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

export const won = (n: number) => `${withComma(n)}원`;
export const countText = (n: number) => `${withComma(n)}건`;

/** 0..1 → 소수 한 자리까지 퍼센트 ("12.3%", "40%") */
export function rate(v: number): string {
  const p = Math.round(v * 1000) / 10;
  return `${Number.isInteger(p) ? p : p.toFixed(1)}%`;
}

/** 변화 한 줄. ratio → "▲ 6.2%", points → "▲ 1.8%p", 없으면 "비교 없음" */
/** 늘면 나쁜 KPI (취소 주문·환불액) */
const RISE_IS_BAD = new Set<string>(["cancel_count", "refund_amount"]);
const flip = (t: Tone): Tone => (t === "up" ? "down" : t === "down" ? "up" : t);

export function changeText(change: number | null, kind: "ratio" | "points"): { text: string; tone: Tone } {
  if (change == null || !Number.isFinite(change)) return { text: "비교 없음", tone: "flat" };
  const unit = kind === "points" ? "%p" : "%";
  const p = Math.round(Math.abs(change) * 1000) / 10;
  const body = `${Number.isInteger(p) ? p : p.toFixed(1)}${unit}`;
  if (p === 0) return { text: `― ${body}`, tone: "flat" };
  return change > 0 ? { text: `▲ ${body}`, tone: "up" } : { text: `▼ ${body}`, tone: "down" };
}

/** UTC ISO → 한국 시간 "11.16 12:00". 읽을 수 없으면 null */
export function koreaTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  const d = new Date(t + 9 * 3600_000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getUTCMonth() + 1)}.${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

/** 카드 아래 출처 줄: "계산 HH:MM (판매 기록 HH:MM까지)". 계산 시각 = basis.computed_at 또는 근거 collected_at
 *  (캐시에서 나와도 처음 계산한 시각), 판매 기록 = basis.last_synced_at(마지막 수집). 한국 시간.
 *  stale(예전 결과를 먼저 받음, 새 계산이 뒤에서 도는 중)이면 "계산 HH:MM · 갱신 중". 계산 시각이 없으면 null */
export function calcLine(computedAt: string | null | undefined, syncedAt: string | null | undefined, stale = false): string | null {
  const at = koreaTime(computedAt);
  if (!at) return null;
  const synced = koreaTime(syncedAt);
  const hm = (s: string) => s.slice(-5); // "11.16 12:00" → "12:00"
  const head = `계산 ${hm(at)}${stale ? " · 갱신 중" : ""}`;
  return synced ? `${head} (판매 기록 ${hm(synced)}까지)` : head;
}

/** 예전 결과를 먼저 받았으면 이만큼 뒤에 한 번 다시 부른다 */
export const STALE_RELOAD_MS = 30_000;

/** 응답 → 출처 줄 재료 (basis 계산 시각이 먼저, 없으면 근거 collected_at) */
export function calcOfResult(r: { evidence?: { collected_at?: string | null } | null; output: { basis?: { computed_at?: string | null; last_synced_at?: string | null; stale?: boolean } | null } } | null | undefined):
  { line: string | null; stale: boolean; stamp: string | null } {
  if (!r) return { line: null, stale: false, stamp: null };
  const b = r.output.basis;
  const at = b?.computed_at ?? r.evidence?.collected_at ?? null;
  const stale = b?.stale === true;
  return { line: calcLine(at, b?.last_synced_at, stale), stale, stamp: at };
}

export interface KpiView { label: string; value: string; change: string; tone: Tone }
export interface CategoryView { label: string; revenue: string; share: string; sharePct: number; change: string; tone: Tone }
export interface ProductView { label: string; category: string | null; revenue: string; orders: string; change: string; tone: Tone }
export interface ChannelView { label: string; revenue: string; orders: string; share: string; sharePct: number }
export interface StatView { label: string; value: string }

export interface SalesGateView {
  kind: "gate";
  reasons: string[];
  facts: string[];
}
export interface SalesOkView {
  kind: "ok";
  summary: string;
  asOf: string;
  compare: string;
  definition: string;
  kpis: KpiView[];
  categories: CategoryView[];
  productsUp: ProductView[];
  productsDown: ProductView[];
  lowSample: string | null;
  customers: StatView[] | null;
  channels: ChannelView[];
  quality: string[];
}
export type SalesView = SalesGateView | SalesOkView;

const HANGUL = /[가-힣]/;

/** 버린 행 이유는 한글 설명일 때만 보인다 (영어 코드는 내보내지 않는다) */
function droppedText(q: SalesAnalysisOutput["quality"]): string {
  if (q.dropped_rows <= 0) return "버린 행 없음";
  const why = Object.entries(q.dropped_reasons ?? {})
    .filter(([k, n]) => HANGUL.test(k) && n > 0)
    .map(([k, n]) => `${k} ${n}개`);
  return `버린 행 ${q.dropped_rows}개${why.length ? ` (${why.join(", ")})` : ""}`;
}

function product(p: SalesAnalysisOutput["products_up"][number]): ProductView {
  const c = changeText(p.change, "ratio");
  return { label: p.label, category: p.category_label, revenue: won(p.revenue), orders: countText(p.orders), change: c.text, tone: c.tone };
}

const pct = (v: number | null) => (v == null ? 0 : Math.max(0, Math.min(100, v * 100)));

export function salesView(out: SalesAnalysisOutput): SalesView {
  const q = out.quality;
  const synced = koreaTime(q.last_synced_at ?? out.basis?.last_synced_at);
  const syncedText = synced ? `마지막 수집 ${synced}` : "마지막 수집 기록 없음";

  if (out.status === "insufficient") {
    return {
      kind: "gate",
      reasons: [...out.insufficient_reasons],
      facts: [syncedText, `기간 안 주문 행 ${q.rows_in_period}개`, droppedText(q), `주문 없는 날 ${q.empty_days.length}일`],
    };
  }

  const b = out.basis;
  const from = koreaTime(b.previous_from);
  const to = koreaTime(b.previous_to);
  const quality = [syncedText, droppedText(q), `주문 없는 날 ${q.empty_days.length}일`];
  if (q.unknown_category_codes.length > 0) quality.push(`카탈로그에 이름이 없는 분류 ${q.unknown_category_codes.length}개`);

  const cu = out.customers;
  return {
    kind: "ok",
    summary: out.summary,
    asOf: `기준 ${koreaTime(out.as_of) ?? "시각 모름"}`,
    compare: from && to ? `비교 기간 ${from} ~ ${to}` : "비교 기간 모름",
    definition: out.definition,
    kpis: out.kpis.map((k) => {
      const raw = changeText(k.change, k.change_kind);
      // 취소·환불은 늘면 나쁜 쪽이라 색을 뒤집는다 (화살표 방향은 그대로)
      const c = RISE_IS_BAD.has(k.key) ? { ...raw, tone: flip(raw.tone) } : raw;
      const value = k.value == null ? "집계 없음" : k.unit === "won" ? won(k.value) : k.unit === "rate" ? rate(k.value) : countText(k.value);
      return { label: k.label, value, change: c.text, tone: c.tone };
    }),
    categories: out.categories.map((c) => {
      const ch = changeText(c.change, "ratio");
      return { label: c.label, revenue: won(c.revenue), share: c.share == null ? "-" : rate(c.share), sharePct: pct(c.share), change: ch.text, tone: ch.tone };
    }),
    productsUp: out.products_up.map(product),
    productsDown: out.products_down.map(product),
    lowSample: out.products_low_sample > 0
      ? `표본 부족 ${out.products_low_sample}개${out.min_product_orders != null ? ` (두 기간 중 한쪽이라도 주문 ${out.min_product_orders}건 미만이라 비교에서 뺐어요)` : ""}`
      : null,
    customers: cu == null ? null : [
      { label: "신규 고객 매출", value: cu.new_share == null ? "집계 없음" : rate(cu.new_share) },
      { label: "재구매 고객 매출", value: cu.repeat_share == null ? "집계 없음" : rate(cu.repeat_share) },
      { label: "쿠폰 사용 주문", value: cu.coupon_order_rate == null ? "집계 없음" : rate(cu.coupon_order_rate) },
    ],
    channels: out.channels.map((c) => ({
      label: c.label, revenue: won(c.revenue), orders: countText(c.orders), share: c.share == null ? "-" : rate(c.share), sharePct: pct(c.share),
    })),
    quality,
  };
}

// ---- 유입 경로별 고객 가치 (channel_value, 11/17). 매출 분석 탭 "유입 경로별 매출" 아래 카드

export const SUPPRESSED = "표본 부족";

export interface ChannelValueRowView {
  label: string; customers: string; revenue: string; repeat: string; orders: string; cost: string;
  best: boolean; suppressed: boolean;
}
export type ChannelValueView =
  | { kind: "gate"; reasons: string[] }
  | { kind: "ok"; hint: string; columns: string[]; rows: ChannelValueRowView[]; empty: string | null; note: string; summary: string };

export function channelValueView(out: ChannelValueOutput): ChannelValueView {
  if (out.status === "insufficient") {
    return { kind: "gate", reasons: out.insufficient_reasons.length ? [...out.insufficient_reasons] : ["데이터가 부족해 계산하지 않았어요"] };
  }
  const days = out.horizon_days;
  const firstShown = out.rows.findIndex((r) => !r.suppressed);
  const rows = out.rows.map((r, i): ChannelValueRowView => {
    // 비용은 재무가 캠페인 비용을 읽어 줄 수 있게 되기 전까지 비어 있다 (서버 cost_note 그대로)
    const cost = r.cost_krw != null ? won(r.cost_krw) : out.cost_note || "-";
    if (r.suppressed || r.new_customers == null) {
      return { label: r.label, customers: SUPPRESSED, revenue: "-", repeat: "-", orders: "-", cost, best: false, suppressed: true };
    }
    return {
      label: r.label,
      customers: `${withComma(r.new_customers)}명`,
      revenue: r.revenue_per_customer != null ? won(r.revenue_per_customer) : "-",
      repeat: r.repeat_rate != null ? rate(r.repeat_rate) : "-",
      orders: r.orders_per_customer != null ? `${Math.round(r.orders_per_customer * 10) / 10}건` : "-",
      cost,
      best: i === firstShown && out.rows.filter((x) => !x.suppressed).length > 1,
      suppressed: false,
    };
  });
  return {
    kind: "ok",
    hint: `첫 구매를 어느 경로로 했는지로 고객을 묶고, 그 뒤 ${days}일 동안 1인당 얼마를 썼는지 봅니다. 최근 1년 안에 첫 구매하고 ${days}일이 지난 고객만 셉니다.`,
    columns: ["첫 구매 경로", "새 고객", `${days}일 1인 매출`, "재구매율", "1인 주문", "광고·쿠폰 비용"],
    rows,
    empty: rows.length ? null : `최근 1년 안에 첫 구매하고 ${days}일이 지난 고객이 아직 없어요`,
    note: "비용 칸은 재무가 캠페인 비용을 읽어 줄 수 있게 되면 채워 '비용 대비 고객 가치'로 바꿉니다.",
    summary: out.summary,
  };
}
