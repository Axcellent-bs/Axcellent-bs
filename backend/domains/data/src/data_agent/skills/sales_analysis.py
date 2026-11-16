"""매출 분석 (`sales_analysis`): 무엇이 팔리고, 누가 사고, 어디서 들어오는지.

표준 판매 저장소(`sales_store`)만 읽는다. 판매 서버를 직접 읽지 않는다. LLM을 쓰지 않고 회사
상태를 바꾸지 않는다(grade read). 숫자는 모두 이 파일의 코드가 계산하고 금액은 정수 원이다.

숫자 정의 (카탈로그 `[sales] revenue_definition`과 같은 뜻)
- 매출 = 결제 완료 주문의 상품 금액 합 − 쿠폰 할인 − 포인트 사용 − 기간 안 환불 (배송비 제외).
  주문은 접수 시각, 환불은 환불 시각으로 기간을 정한다. 취소·결제 실패·결제 대기 주문은 빼고
  그 환불도 빼지 않는다(`sales_clean.counts_as_revenue`)
- 객단가 = 매출 ÷ 결제 완료 주문 수 (반올림)
- 재구매 고객 = 이 기간 시작 전에도 결제 완료 주문이 있는 buyer_id. 비율 = 재구매 고객 ÷ 기간 구매자
- 직전 기간 = 같은 길이로 하루 단위만큼 앞 (`sales_quality.window`)

나누기
- 주문의 할인(쿠폰 + 포인트)은 줄마다 받은 몫(`coupon_share`·`points_share`)이 합과 맞으면 그대로,
  안 맞으면 줄 금액 비율로 나눈다(나머지는 큰 몫부터 1원씩). 환불도 줄 순매출 비율로 나눈다.
  그래서 카테고리·상품 매출의 합이 전체 매출과 같다
- 상품 증감: 두 기간 모두 최소 표본(`min_product_orders`, 상품이 든 결제 완료 주문 수) 이상인
  상품만 비교한다. 아니면 "표본 부족"으로 센다
- 유입 경로: (src_source, src_medium)의 카탈로그 이름. 표시가 없으면 "직접 유입", 카탈로그에 없는
  출처는 "기타 경로"로 묶는다(영문 코드를 화면에 내지 않는다)
- 품질 관문(`sales_quality.gate`)에 걸리면 숫자 칸을 비우고 "데이터 부족: 이유"만 낸다
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    CatalogSales,
    SalesAnalysisOutput,
    SalesBasis,
    SalesCategoryRow,
    SalesChannelRow,
    SalesCustomerMix,
    SalesKpi,
    SalesPeriod,
    SalesProductMove,
)
from data_agent.sales_clean import counts_as_revenue
from data_agent.sales_quality import KST, PERIOD_LABEL, Window, gate, quality, window
from data_agent.sales_store import FAKE_SOURCE, SalesStore, StoredOrder

FAKE_DATA = "가짜 데이터(데모용)"  # 저장소를 가짜 출처(`sales_fake`)가 채웠을 때 근거·요약에 붙인다
TOP_PRODUCTS = 5
NO_CATEGORY = "이름 없는 분류"
NO_PRODUCT = "이름 없는 상품"
DIRECT = "직접 유입"
OTHER_SOURCE = "기타 경로"
QUERIES = ["sales_store.sales_orders", "sales_store.sales_order_lines", "sales_store.sales_refunds"]


# ---------------------------------------------------------------- 나누기 (정수 원)


def allocate(total: int, weights: Sequence[int]) -> list[int]:
    """`total`을 `weights` 비율로 정수로 나눈다. 합은 늘 `total`. 무게가 모두 0이면 첫 칸에."""
    if not weights:
        return []
    base = sum(w for w in weights if w > 0)
    if base <= 0:
        return [total] + [0] * (len(weights) - 1)
    sign = -1 if total < 0 else 1
    amount = abs(total)
    exact = [amount * max(w, 0) / base for w in weights]
    parts = [int(x) for x in exact]
    rest = amount - sum(parts)
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - parts[i]), i))
    for i in order[:rest]:
        parts[i] += 1
    return [sign * p for p in parts]


@dataclass(frozen=True)
class LineNet:
    product_id: str
    category_code: str
    title: str | None
    net: int  # 줄 금액 − 할인 몫


def line_nets(order: StoredOrder) -> list[LineNet]:
    """결제 완료 주문의 줄 순매출. 줄이 없으면 분류 없는 한 줄로 본다."""
    discount = order.coupon_discount + order.points_used
    if not order.lines:
        return [LineNet("", "", None, order.items_amount - discount)]
    gross = [ln.qty * ln.unit_price for ln in order.lines]
    shares = [ln.coupon_share + ln.points_share for ln in order.lines]
    if sum(shares) != discount:
        shares = allocate(discount, gross)
    return [
        LineNet(ln.product_id, ln.category_code or "", ln.title, g - s)
        for ln, g, s in zip(order.lines, gross, shares, strict=True)
    ]


# ---------------------------------------------------------------- 한 기간 집계


@dataclass
class Tally:
    revenue: int = 0
    orders: int = 0
    coupon_orders: int = 0
    buyers: set[str] = field(default_factory=set)
    repeat_buyers: set[str] = field(default_factory=set)
    new_revenue: int = 0
    repeat_revenue: int = 0
    by_category: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_product: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    product_orders: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    product_meta: dict[str, tuple[str | None, str]] = field(default_factory=dict)
    by_channel: dict[tuple[str | None, str | None], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    channel_orders: dict[tuple[str | None, str | None], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    rows: int = 0


def tally(
    store: SalesStore, tenant_id: str, start: datetime, end: datetime
) -> tuple[Tally, list[StoredOrder]]:
    """[start, end) 집계. 돌려주는 두 번째 값은 그 기간에 접수한 주문(상태 무관, 품질 보고용)."""
    created = store.orders_created(tenant_id, start, end)
    refunded = store.orders_refunded(tenant_id, start, end)
    paid = [o for o in created if counts_as_revenue(o.status, o.payment_status)]
    paid_refunded = [o for o in refunded if counts_as_revenue(o.status, o.payment_status)]
    buyers = {o.buyer_id for o in paid} | {o.buyer_id for o in paid_refunded}
    before = store.buyers_paid_before(tenant_id, buyers, start)
    t = Tally(rows=len(created) + len({o.order_id for o in refunded}))
    for order in paid:
        nets = line_nets(order)
        _add(t, order, nets, [n.net for n in nets], before)
        t.orders += 1
        t.coupon_orders += 1 if order.coupon_discount > 0 else 0
        t.buyers.add(order.buyer_id)
        if order.buyer_id in before:
            t.repeat_buyers.add(order.buyer_id)
        for pid in {n.product_id for n in nets if n.product_id}:
            t.product_orders[pid] += 1
        key = (order.src_source, order.src_medium)
        t.channel_orders[key] += 1
    for order in paid_refunded:
        amount = sum(r.amount for r in order.refunds if start <= r.refunded_at < end)
        if amount:
            nets = line_nets(order)
            parts = allocate(-amount, [n.net for n in nets])
            _add(t, order, nets, parts, before)
    return t, created


def _add(
    t: Tally, order: StoredOrder, nets: list[LineNet], amounts: list[int], before: set[str]
) -> None:
    total = sum(amounts)
    t.revenue += total
    if order.buyer_id in before:
        t.repeat_revenue += total
    else:
        t.new_revenue += total
    t.by_channel[(order.src_source, order.src_medium)] += total
    for n, amount in zip(nets, amounts, strict=True):
        t.by_category[n.category_code] += amount
        if n.product_id:
            t.by_product[n.product_id] += amount
            known = t.product_meta.get(n.product_id)
            t.product_meta[n.product_id] = (n.title or (known[0] if known else None),
                                            n.category_code)  # fmt: skip


# ---------------------------------------------------------------- 출력 만들기


def change(value: float | None, previous: float | None) -> float | None:
    """변화율. 직전이 0 이하이거나 없으면 None."""
    if value is None or previous is None or previous <= 0:
        return None
    return round((value - previous) / previous, 4)


def share(part: int, total: int) -> float | None:
    if total <= 0 or part < 0 or part > total:
        return None
    return round(part / total, 4)


def rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole > 0 else None


def kpis(cur: Tally, prev: Tally) -> list[SalesKpi]:
    aov = round(cur.revenue / cur.orders) if cur.orders else None
    prev_aov = round(prev.revenue / prev.orders) if prev.orders else None
    repeat = rate(len(cur.repeat_buyers), len(cur.buyers))
    prev_repeat = rate(len(prev.repeat_buyers), len(prev.buyers))
    repeat_change = (
        round(repeat - prev_repeat, 4) if repeat is not None and prev_repeat is not None else None
    )
    return [
        SalesKpi(key="revenue", label="매출", unit="won", value=cur.revenue,
                 previous=prev.revenue, change=change(cur.revenue, prev.revenue)),
        SalesKpi(key="order_count", label="주문 수", unit="count", value=cur.orders,
                 previous=prev.orders, change=change(cur.orders, prev.orders)),
        SalesKpi(key="aov", label="객단가", unit="won", value=aov, previous=prev_aov,
                 change=change(aov, prev_aov)),
        SalesKpi(key="repeat_rate", label="재구매 고객 비율", unit="rate", value=repeat,
                 previous=prev_repeat, change=repeat_change, change_kind="points"),
    ]  # fmt: skip


def categories(cur: Tally, prev: Tally, cfg: CatalogSales) -> list[SalesCategoryRow]:
    codes = set(cur.by_category) | set(prev.by_category)
    rows = [
        SalesCategoryRow(
            code=code,
            label=cfg.categories.get(code, NO_CATEGORY),
            revenue=cur.by_category.get(code, 0),
            share=share(cur.by_category.get(code, 0), cur.revenue),
            previous_revenue=prev.by_category.get(code, 0),
            change=change(cur.by_category.get(code, 0), prev.by_category.get(code, 0)),
        )
        for code in codes
    ]
    return sorted(rows, key=lambda r: (-r.revenue, r.label, r.code))


def products(
    cur: Tally, prev: Tally, cfg: CatalogSales
) -> tuple[list[SalesProductMove], list[SalesProductMove], int]:
    ids = set(cur.by_product) | set(prev.by_product)
    least = cfg.min_product_orders
    moves: list[SalesProductMove] = []
    low = 0
    for pid in ids:
        orders, prev_orders = cur.product_orders.get(pid, 0), prev.product_orders.get(pid, 0)
        if orders < least or prev_orders < least:
            low += 1
            continue
        title, code = cur.product_meta.get(pid) or prev.product_meta.get(pid) or (None, "")
        moves.append(
            SalesProductMove(
                product_id=pid,
                label=title or NO_PRODUCT,
                category_label=cfg.categories.get(code, NO_CATEGORY),
                revenue=cur.by_product.get(pid, 0),
                previous_revenue=prev.by_product.get(pid, 0),
                change=change(cur.by_product.get(pid, 0), prev.by_product.get(pid, 0)),
                orders=orders,
                previous_orders=prev_orders,
            )
        )
    rated = [(m.change, m) for m in moves if m.change is not None]
    up = [m for c, m in sorted(rated, key=lambda x: (-x[0], x[1].label)) if c > 0]
    down = [m for c, m in sorted(rated, key=lambda x: (x[0], x[1].label)) if c < 0]
    return up[:TOP_PRODUCTS], down[:TOP_PRODUCTS], low


def channel_label(source: str | None, medium: str | None, cfg: CatalogSales) -> str:
    if not source and not medium:
        return DIRECT
    name = cfg.source_labels.get(source or "")
    if name is None:
        return OTHER_SOURCE
    how = cfg.medium_labels.get(medium or "")
    return f"{name} · {how}" if how else name


def channels(cur: Tally, cfg: CatalogSales) -> list[SalesChannelRow]:
    grouped: dict[str, list[tuple[str | None, str | None]]] = defaultdict(list)
    for key in set(cur.by_channel) | set(cur.channel_orders):
        grouped[channel_label(*key, cfg)].append(key)
    rows = []
    for label, keys in grouped.items():
        revenue = sum(cur.by_channel.get(k, 0) for k in keys)
        source, medium = keys[0] if len(keys) == 1 and label != OTHER_SOURCE else (None, None)
        rows.append(
            SalesChannelRow(
                source=source,
                medium=medium,
                label=label,
                revenue=revenue,
                orders=sum(cur.channel_orders.get(k, 0) for k in keys),
                share=share(revenue, cur.revenue),
            )
        )
    return sorted(rows, key=lambda r: (-r.revenue, r.label))


def won(value: int | float | None) -> str:
    return "확인할 수 없음" if value is None else f"{round(value):,}원"


def pct(value: float | None, points: bool = False) -> str:
    if value is None:
        return "비교 없음"
    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.1f}{'%p' if points else '%'}"


def summary(out: SalesAnalysisOutput) -> str:
    """요약 한 문장 (코드 템플릿, 숫자는 출력값만). 가짜 출처면 앞에 표시를 붙인다."""
    text = _summary(out)
    return f"[{out.basis.data_source}] {text}" if out.basis.data_source else text


def _summary(out: SalesAnalysisOutput) -> str:
    label = PERIOD_LABEL[out.period]
    if out.status != "ok":
        return f"{label} 매출 숫자를 내지 않음 — " + "; ".join(out.insufficient_reasons)
    k = {x.key: x for x in out.kpis}
    text = (
        f"{label} 매출 {won(k['revenue'].value)} (직전 대비 {pct(k['revenue'].change)}), "
        f"결제 완료 주문 {k['order_count'].value}건, 객단가 {won(k['aov'].value)}."
    )
    if out.categories and out.categories[0].share is not None:
        top = out.categories[0]
        text += f" 가장 많이 팔린 분류는 {top.label} (매출 비중 {top.share * 100:.1f}%)."
    return text


def _kst(moment: datetime) -> str:
    local = moment.astimezone(KST)
    return f"{local:%Y-%m-%d %H:%M}"


def analyze(
    store: SalesStore,
    tenant_id: str,
    period: SalesPeriod,
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
) -> SalesAnalysisOutput:
    w: Window = window(period, now)
    cur, created = tally(store, tenant_id, w.start, w.end)
    prev, _ = tally(store, tenant_id, w.previous_start, w.previous_end)
    report = quality(store, tenant_id, w, created, cfg)
    fake = store.sync_state(tenant_id).source == FAKE_SOURCE
    basis = SalesBasis(
        period_from=w.start,
        period_to=w.end,
        previous_from=w.previous_start,
        previous_to=w.previous_end,
        rows_read=cur.rows + prev.rows,
        last_synced_at=report.last_synced_at,
        catalog_id=catalog_id,
        catalog_version=cfg.version if cfg else None,
        data_source=FAKE_DATA if fake else None,
    )
    reasons = gate(report, cur.orders, cfg, now)
    common = {
        "period": period,
        "as_of": w.end,
        "definition": cfg.revenue_definition if cfg else "",
        "quality": report,
        "basis": basis,
    }
    if reasons or cfg is None:
        out = SalesAnalysisOutput(**common, status="insufficient", insufficient_reasons=reasons)
    else:
        up, down, low = products(cur, prev, cfg)
        out = SalesAnalysisOutput(
            **common,
            kpis=kpis(cur, prev),
            categories=categories(cur, prev, cfg),
            products_up=up,
            products_down=down,
            products_low_sample=low,
            min_product_orders=cfg.min_product_orders,
            customers=SalesCustomerMix(
                buyers=len(cur.buyers),
                repeat_buyers=len(cur.repeat_buyers),
                new_revenue=cur.new_revenue,
                repeat_revenue=cur.repeat_revenue,
                new_share=share(cur.new_revenue, cur.revenue),
                repeat_share=share(cur.repeat_revenue, cur.revenue),
                coupon_orders=cur.coupon_orders,
                coupon_order_rate=rate(cur.coupon_orders, cur.orders),
            ),
            channels=channels(cur, cfg),
        )
    return out.model_copy(update={"summary": summary(out)})


def evidence(out: SalesAnalysisOutput, now: datetime) -> Evidence:
    b = out.basis
    synced = _kst(b.last_synced_at) if b.last_synced_at else "없음"
    return Evidence(
        queries=list(QUERIES),
        data_summary=(
            f"기간 {_kst(b.period_from)}~{_kst(b.period_to)} (한국 시간), 비교 "
            f"{_kst(b.previous_from)}~{_kst(b.previous_to)}, 읽은 행 {b.rows_read}, "
            f"마지막 수집 {synced}, 카탈로그 {b.catalog_id} 판 {b.catalog_version or '없음'}"
            + (f", 출처 {b.data_source}" if b.data_source else "")
        ),
        collected_at=now,
        ref_ids=[],
    )
