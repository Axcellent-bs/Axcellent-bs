"""한국 날짜 하루의 일별 매출·품질 행 (설계 3.1·3.8·5.2).

지금 매출 분석(`sales_sql.tally`)과 같은 정의를 그대로 쓴다. 그래서 다 지난 날의 일별 행을 더하면
그 기간 `tally`의 매출·주문 수·쿠폰 주문 수·분류·상품·유입별 매출과 같다.

- 결제 완료 = `counts_as_revenue` (결제 성공, 취소·결제 실패·결제 대기·전액 환불 아님)
- 주문 숫자는 **접수 날짜**, 환불은 **환불 날짜**(한국). 접수가 옛날인 주문의 환불도 그 날 행에 든다
- 줄 순매출 = 줄 금액 − 쿠폰·포인트 몫(`line_nets`, 몫 합이 주문 할인과 다르면 줄 금액 비율로 나눔).
  줄 없는 주문은 상품 칸이 빈 한 줄. 배송비는 넣지 않는다
- 환불은 그 주문의 줄 순매출 비율로 나눈다(`allocate`). 주문 수는 바꾸지 않는다
- 두 단: `order` 행(주문 단위, 상품 칸 비움)·`line` 행(상품·분류·장르 단위). 상품끼리 주문 수를
  더하지 않게 나눈다
- 구매자 id·주문 id는 메모리에서만 쓰고 행에 남기지 않는다
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from ax_contracts.data_access.sales_aggregates import QualityDailyRow, SalesDailyRow
from ax_contracts.schemas.data import CatalogSales
from data_agent.sales_clean import counts_as_revenue
from data_agent.sales_quality import KST
from data_agent.sales_sql import allocate, line_nets
from data_agent.sales_store import SalesStore, StoredOrder


def day_window(day: date) -> tuple[datetime, datetime]:
    """한국 날짜 하루 [그날 0시, 다음날 0시)를 UTC로."""
    start = datetime.combine(day, time(0), KST)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


@dataclass
class _Cell:
    orders: set[str] = field(default_factory=set)  # 메모리에서만 (주문 수를 셀 때)
    revenue_net: int = 0
    qty: int = 0
    coupon_discount: int = 0
    points_used: int = 0
    refund_amount: int = 0
    created_orders: int = 0


OrderKey = tuple[str, str, str, bool]  # src_source, src_medium, src_campaign, coupon_used
LineKey = tuple[str, str, str, str, str, str, bool]  # product, category, genre + OrderKey


def _campaigns(store: SalesStore, tenant_id: str, ids: Iterable[str]) -> dict[str, str]:
    """주문 id → 유입 캠페인 (StoredOrder에 없는 칸이라 따로 읽는다)."""
    out: dict[str, str] = {}
    wanted = sorted(set(ids))
    with store.reading() as conn:
        for i in range(0, len(wanted), 500):
            chunk = wanted[i : i + 500]
            marks = ",".join("?" * len(chunk))
            for r in conn.execute(
                f"SELECT order_id, src_campaign FROM sales_orders WHERE tenant_id = ? "
                f"AND order_id IN ({marks})",
                (tenant_id, *chunk),
            ):
                out[r["order_id"]] = r["src_campaign"] or ""
    return out


def _order_key(o: StoredOrder, campaigns: dict[str, str]) -> OrderKey:
    return (
        o.src_source or "",
        o.src_medium or "",
        campaigns.get(o.order_id, ""),
        o.coupon_discount > 0,
    )


def _line_keys(o: StoredOrder, ok: OrderKey) -> list[LineKey]:
    """줄마다 키 (`line_nets`와 같은 순서). 줄 없는 주문은 상품 칸이 빈 한 줄."""
    if not o.lines:
        return [("", "", "", *ok)]
    return [(ln.product_id, ln.category_code or "", ln.genre_code or "", *ok) for ln in o.lines]


def build_day(  # noqa: PLR0913 - 저장소·회사·날짜·카탈로그·계산 번호·시각
    store: SalesStore,
    tenant_id: str,
    day: date,
    cfg: CatalogSales | None,
    run_id: str,
    now: datetime,
    *,
    source_cursor: str | None = None,
    with_current: bool = False,
) -> tuple[list[SalesDailyRow], QualityDailyRow]:
    """그 회사의 한국 날짜 `day` 일별 매출 행과 품질 행.

    `with_current`: 품질 행에 지금 값(고객 수·카탈로그에 없는 속성)을 넣는다. 그날 시점 값은
    알 수 없어 하루 계산의 기준일(어제)에만 넣고, 첫 채우기의 옛 날짜는 비워 둔다.
    """
    start, end = day_window(day)
    created = store.orders_created(tenant_id, start, end)
    refunded = store.orders_refunded(tenant_id, start, end)
    campaigns = _campaigns(store, tenant_id, [o.order_id for o in created + refunded])
    orders: dict[OrderKey, _Cell] = defaultdict(_Cell)
    lines: dict[LineKey, _Cell] = defaultdict(_Cell)

    for o in created:
        ok = _order_key(o, campaigns)
        orders[ok].created_orders += 1
        if not counts_as_revenue(o.status, o.payment_status):
            continue
        cell = orders[ok]
        cell.orders.add(o.order_id)
        cell.coupon_discount += o.coupon_discount
        cell.points_used += o.points_used
        nets = line_nets(o)
        for key, net, qty in zip(
            _line_keys(o, ok), nets, [ln.qty for ln in o.lines] or [0], strict=True
        ):
            cell.revenue_net += net.net
            cell.qty += qty
            lc = lines[key]
            lc.orders.add(o.order_id)
            lc.revenue_net += net.net
            lc.qty += qty

    for o in refunded:
        if not counts_as_revenue(o.status, o.payment_status):
            continue  # 취소 주문의 환불은 빼지 않는다 (주문이 이미 매출에 없다)
        amount = sum(r.amount for r in o.refunds if start <= r.refunded_at < end)
        if not amount:
            continue
        ok = _order_key(o, campaigns)
        orders[ok].refund_amount += amount
        nets = line_nets(o)
        for key, share in zip(
            _line_keys(o, ok), allocate(amount, [n.net for n in nets]), strict=True
        ):
            lines[key].refund_amount += share

    common = {
        "tenant_id": tenant_id,
        "run_id": run_id,
        "computed_at": now,
        "source_cursor": source_cursor,
    }
    rows = [
        SalesDailyRow(
            **common, day=day, grain="order", src_source=k[0], src_medium=k[1], src_campaign=k[2],
            coupon_used=k[3], orders=len(c.orders), revenue_net=c.revenue_net, qty=c.qty,
            coupon_discount=c.coupon_discount, points_used=c.points_used,
            refund_amount=c.refund_amount, created_orders=c.created_orders,
        )
        for k, c in sorted(orders.items())
    ] + [
        SalesDailyRow(
            **common, day=day, grain="line", product_id=k[0], category_code=k[1], genre_code=k[2],
            src_source=k[3], src_medium=k[4], src_campaign=k[5], coupon_used=k[6],
            orders=len(c.orders), revenue_net=c.revenue_net, qty=c.qty,
            refund_amount=c.refund_amount,
        )
        for k, c in sorted(lines.items())
    ]  # fmt: skip
    return rows, _quality(store, tenant_id, day, created, cfg, common, with_current=with_current)


def _quality(  # noqa: PLR0913 - 품질 행에 필요한 것들
    store: SalesStore,
    tenant_id: str,
    day: date,
    created: list[StoredOrder],
    cfg: CatalogSales | None,
    common: dict,
    *,
    with_current: bool,
) -> QualityDailyRow:
    start, end = day_window(day)
    known_cat = set(cfg.categories) if cfg else set()
    known_genre = set(cfg.genres) if cfg else set()
    unknown: Counter[str] = Counter()
    for o in created:
        for ln in o.lines:
            # 분류 표가 없는 회사는 사이트 분류 값을 그대로 쓴다(모르는 값으로 세지 않는다)
            if ln.category_code and known_cat and ln.category_code not in known_cat:
                unknown[f"category:{ln.category_code}"] += 1
            if ln.genre_code and ln.genre_code not in known_genre:
                unknown[f"genre:{ln.genre_code}"] += 1
    unmapped: dict[str, int] = {}
    customers: int | None = None
    if with_current:
        customers = store.customer_count(tenant_id)
        unmapped = _unmapped_attributes(store, tenant_id, cfg)
    return QualityDailyRow(
        **common, day=day, created_orders=len(created),
        paid_orders=sum(1 for o in created if counts_as_revenue(o.status, o.payment_status)),
        dropped_by_reason=store.dropped(tenant_id, start, end), unknown_codes=dict(unknown),
        unmapped_attributes=unmapped, customers=customers,
    )  # fmt: skip


def _unmapped_attributes(
    store: SalesStore, tenant_id: str, cfg: CatalogSales | None
) -> dict[str, int]:
    """카탈로그 속성에 없는 고객 속성 값: "key=값" → 고객 수 (지금 값)."""
    known = {a.key: set(a.values) for a in cfg.attributes} if cfg else {}
    with store.reading() as conn:
        rows = conn.execute(
            "SELECT key, value_code, COUNT(*) AS n FROM sales_customer_attributes "
            "WHERE tenant_id = ? GROUP BY key, value_code",
            (tenant_id,),
        ).fetchall()
    return {
        f"{r['key']}={r['value_code']}": r["n"]
        for r in rows
        if r["value_code"] not in known.get(r["key"], set())
    }
