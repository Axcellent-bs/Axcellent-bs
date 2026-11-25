"""매출 분석(`sales_analysis`) 한 기간 집계를 SQL로 (11/18).

예전에는 기간 안 주문 전부(줄·환불 포함)를 파이썬으로 올려 `Tally`를 채웠다. 이제 저장소 읽기 연결의
분석 문맥(`SalesStore.analysis`: 임시 표·메모리)에서 GROUP BY로 바로 센다. 숫자 정의는 그대로다.

- 결제 완료 = `sales_clean.counts_as_revenue`와 같은 조건(payment_status = succeeded, 상태가
  EXCLUDED_STATUSES 아님). 주문은 접수 시각, 환불은 환불 시각으로 기간을 정한다
- 줄 순매출 = 줄 금액 − 줄 할인 몫. 줄 몫 합이 주문 할인(쿠폰 + 포인트)과 같은 주문("고른 주문")은
  SQL로 더한다
- 파이썬이 예전 계산(`sales_analysis.line_nets`·`allocate`) 그대로 더하는 것은 두 가지뿐이다
  - 줄 몫 합이 주문 할인과 다르거나 줄이 없는 결제 완료 주문 (드물다)
  - 기간 안 환불이 있는 결제 완료 주문 (환불을 줄 순매출 비율로 나눈다)
- 주문 수·쿠폰 주문·유입 경로별 주문·상품이 든 주문 수·구매자·재구매 고객은 모든 결제 완료 주문을
  SQL로 센다. 재구매 고객 = 기간 시작 전에도 결제 완료 주문이 있는 구매자
- 상품 이름·분류(product_meta)는 예전처럼 "주문을 (접수 시각, 주문 id, 줄 순번) 순으로 볼 때 마지막
  분류와 마지막으로 비지 않은 이름"이고, 그 위에 기간 안 환불 주문을 같은 순서로 다시 얹는다
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from data_agent import sales_summary
from data_agent.sales_clean import EXCLUDED_STATUSES, PAID, counts_as_revenue
from data_agent.sales_store import SalesStore, StoredOrder, iso

_EXCLUDED = ", ".join(f":x{i}" for i in range(len(EXCLUDED_STATUSES)))
# 취소로 세는 주문 상태 (결제 실패·결제 대기는 취소가 아니다). 철자가 다른 회사도 받는다
CANCELLED_STATUSES = ("cancelled", "canceled", "refunded_full")
PAID_WHERE = (
    "o.tenant_id = :t AND o.created_at >= :s AND o.created_at < :e AND o.payment_status = :paid "
    f"AND LOWER(o.status) NOT IN ({_EXCLUDED})"
)

Key = tuple[str | None, str | None]  # (src_source, src_medium)


# ---------------------------------------------------------------- 나누기 (정수 원, 예전 그대로)


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


@dataclass
class Tally:
    """한 기간 집계 (예전 `sales_analysis.Tally`와 같은 칸, 구매자는 수만)."""

    revenue: int = 0
    orders: int = 0
    coupon_orders: int = 0
    buyers: int = 0
    repeat_buyers: int = 0
    new_revenue: int = 0
    repeat_revenue: int = 0
    by_category: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_product: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    product_orders: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    product_meta: dict[str, tuple[str | None, str]] = field(default_factory=dict)
    by_channel: dict[Key, int] = field(default_factory=lambda: defaultdict(int))
    channel_orders: dict[Key, int] = field(default_factory=lambda: defaultdict(int))
    rows: int = 0
    # SQL 집계(`tally`)는 상품 이름·분류를 미리 다 세지 않는다. 필요한 상품만 `product_names`로
    # 찾는다: 기간과, 그 위에 다시 얹을 기간 안 환불 주문의 줄 (상품 id, 이름, 분류, 순서대로)
    window: tuple[datetime, datetime] | None = None
    refund_lines: list[tuple[str, str | None, str]] = field(default_factory=list)
    # 취소·환불 (11/25, 회사 분리 최소안): 기간에 접수한 주문 중 취소된 주문 수,
    # 기간에 일어난 환불 합.
    # 원본에서 세지 못한 집계(예: 일별 집계 행만)면 None ("확인할 수 없음", 0으로 지어내지 않는다)
    cancelled: int | None = None
    refund_amount: int | None = None


def _params(tenant_id: str, start: datetime, end: datetime) -> dict[str, object]:
    params: dict[str, object] = {"t": tenant_id, "s": iso(start), "e": iso(end), "paid": PAID}
    params.update({f"x{i}": s for i, s in enumerate(sorted(EXCLUDED_STATUSES))})
    return params


def _drop(conn: sqlite3.Connection) -> None:
    for name in ("sa_lines", "sa_buyers", "sa_odd"):
        conn.execute(f"DROP TABLE IF EXISTS temp.{name}")


def _build(conn: sqlite3.Connection, p: dict[str, object], summary: bool = False) -> None:
    """임시 표: 결제 완료 주문 줄(주문 id 순으로 한 번 조인), 구매자(재구매 여부), 고르지 않은
    주문. `summary`면 재구매 여부를 구매자 요약 표의 첫 결제 시각(< 기간 시작)으로 본다."""
    _drop(conn)
    conn.execute(
        "CREATE TEMP TABLE sa_lines AS SELECT o.order_id, l.line_no, o.created_at, o.buyer_id, "
        "o.src_source AS src, o.src_medium AS med, l.qty * l.unit_price AS gross, "
        "l.coupon_share + l.points_share AS share, COALESCE(l.category_code, '') AS cat, "
        "l.product_id AS pid, l.title FROM sales_orders o INDEXED BY idx_sales_orders_sales "
        "CROSS JOIN sales_order_lines l ON l.tenant_id = :t AND l.order_id = o.order_id "
        f"WHERE {PAID_WHERE}",
        p,
    )
    conn.execute(
        "CREATE TEMP TABLE sa_buyers (buyer_id TEXT PRIMARY KEY, rep INTEGER NOT NULL) "
        "WITHOUT ROWID"
    )
    if summary:  # 기간 시작 전 결제 완료 주문이 있다 = 첫 결제 < 기간 시작 (요약 표)
        conn.execute(
            "INSERT INTO sa_buyers SELECT b.buyer_id, COALESCE((SELECT s.first_paid_at < :s "
            "FROM sales_buyer_summary s WHERE s.tenant_id = :t AND s.buyer_id = b.buyer_id), 0) "
            f"FROM (SELECT DISTINCT o.buyer_id FROM sales_orders o WHERE {PAID_WHERE} "
            "ORDER BY o.buyer_id) b",
            p,
        )
    else:
        conn.execute(
            "INSERT INTO sa_buyers SELECT b.buyer_id, EXISTS (SELECT 1 FROM sales_orders o "
            "INDEXED BY idx_sales_orders_buyer_paid WHERE o.tenant_id = :t "
            "AND o.buyer_id = b.buyer_id AND o.created_at < :s AND o.payment_status = :paid "
            f"AND LOWER(o.status) NOT IN ({_EXCLUDED})) FROM (SELECT DISTINCT o.buyer_id "
            f"FROM sales_orders o WHERE {PAID_WHERE} ORDER BY o.buyer_id) b",
            p,
        )
    # 고르지 않은 주문: 줄 몫 합이 주문 할인과 다르거나 줄이 없다 (주문 id 순으로 한 번 훑는다)
    conn.execute("CREATE TEMP TABLE sa_odd (order_id TEXT PRIMARY KEY) WITHOUT ROWID")
    conn.execute(
        "INSERT INTO sa_odd SELECT o.order_id FROM sales_orders o "
        "INDEXED BY idx_sales_orders_sales LEFT JOIN sales_order_lines l ON l.tenant_id = :t "
        "AND l.order_id = o.order_id "
        f"WHERE {PAID_WHERE} GROUP BY o.order_id HAVING COUNT(l.line_no) = 0 "
        "OR SUM(l.coupon_share + l.points_share) != MAX(o.coupon_discount + o.points_used)",
        p,
    )


EVEN = "x.order_id NOT IN (SELECT order_id FROM sa_odd)"


def _sql_part(conn: sqlite3.Connection, p: dict[str, object], t: Tally) -> None:
    """고른 주문의 줄 순매출과 모든 결제 완료 주문의 수."""
    net = "SUM(x.gross - x.share)"
    for r in conn.execute(f"SELECT x.cat, {net} AS n FROM sa_lines x WHERE {EVEN} GROUP BY x.cat"):
        t.by_category[r["cat"]] += int(r["n"])
    for r in conn.execute(
        f"SELECT x.pid, {net} AS n FROM sa_lines x WHERE {EVEN} AND x.pid != '' GROUP BY x.pid"
    ):
        t.by_product[r["pid"]] += int(r["n"])
    for r in conn.execute(
        f"SELECT x.src, x.med, {net} AS n FROM sa_lines x WHERE {EVEN} GROUP BY x.src, x.med"
    ):
        t.by_channel[(r["src"], r["med"])] += int(r["n"])
    for r in conn.execute(
        f"SELECT b.rep, {net} AS n FROM sa_lines x CROSS JOIN sa_buyers b "
        f"ON b.buyer_id = x.buyer_id WHERE {EVEN} GROUP BY b.rep"
    ):
        if r["rep"]:
            t.repeat_revenue += int(r["n"])
        else:
            t.new_revenue += int(r["n"])
    t.revenue = t.new_revenue + t.repeat_revenue
    for r in conn.execute(
        "SELECT x.pid, COUNT(DISTINCT x.order_id) AS n FROM sa_lines x WHERE x.pid != '' "
        "GROUP BY x.pid"
    ):
        t.product_orders[r["pid"]] = int(r["n"])
    for r in conn.execute(
        "SELECT o.src_source AS src, o.src_medium AS med, COUNT(*) AS n, "
        "SUM(CASE WHEN o.coupon_discount > 0 THEN 1 ELSE 0 END) AS c FROM sales_orders o "
        f"INDEXED BY idx_sales_orders_sales WHERE {PAID_WHERE} GROUP BY o.src_source, o.src_medium",
        p,
    ):
        t.channel_orders[(r["src"], r["med"])] = int(r["n"])
        t.orders += int(r["n"])
        t.coupon_orders += int(r["c"])
    row = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(rep), 0) AS r FROM sa_buyers").fetchone()
    t.buyers, t.repeat_buyers = int(row["n"]), int(row["r"])


def product_names(
    store: SalesStore, tenant_id: str, t: Tally, pids: set[str]
) -> dict[str, tuple[str | None, str]]:
    """상품 id → (이름, 분류 code): 예전 계산 그대로. 기간 안 결제 완료 줄을 (접수 시각, 주문 id,
    줄 순번) 순으로 보며 "비지 않은 이름이면 바꾸고 분류는 늘 바꾼다", 그 위에 기간 안 환불 주문의
    줄을 같은 규칙으로 얹는다. 필요한 상품만 상품 id 인덱스로 찾는다."""
    if not pids or t.window is None:
        return {}
    out: dict[str, tuple[str | None, str]] = {}

    def put(pid: str, title: str | None, cat: str) -> None:
        known = out.get(pid)
        out[pid] = (title or (known[0] if known else None), cat)

    p = _params(tenant_id, *t.window)
    ids = sorted(pids)
    with store.reading() as conn:
        for at in range(0, len(ids), 500):
            chunk = ids[at : at + 500]
            marks = ", ".join(f":p{i}" for i in range(len(chunk)))
            rows = conn.execute(
                "SELECT l.product_id AS pid, l.title, COALESCE(l.category_code, '') AS cat "
                "FROM sales_order_lines l INDEXED BY idx_sales_lines_product CROSS JOIN "
                "sales_orders o INDEXED BY idx_sales_orders_sales ON o.tenant_id = :t "
                f"AND o.order_id = l.order_id WHERE l.tenant_id = :t AND l.product_id IN ({marks}) "
                f"AND {PAID_WHERE} ORDER BY o.created_at, o.order_id, l.line_no",
                {**p, **{f"p{i}": pid for i, pid in enumerate(chunk)}},
            ).fetchall()
            for r in rows:
                put(r["pid"], r["title"], r["cat"])
    for pid, title, cat in t.refund_lines:
        if pid in pids:
            put(pid, title, cat)
    return out


def add_amounts(  # noqa: PLR0913 - 예전 `_add` 그대로
    t: Tally, order: StoredOrder, nets: list[LineNet], amounts: list[int], before: set[str],
    *, meta: bool = True,
) -> None:  # fmt: skip
    """한 주문의 줄별 금액을 더한다 (예전 `sales_analysis._add`). `meta`면 상품 이름·분류도
    얹는다."""
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
            if meta:
                known = t.product_meta.get(n.product_id)
                t.product_meta[n.product_id] = (n.title or (known[0] if known else None),
                                                n.category_code)  # fmt: skip


def _odd_orders(store: SalesStore, conn: sqlite3.Connection, tenant_id: str) -> list[StoredOrder]:
    rows = conn.execute(
        "SELECT o.* FROM sa_odd d CROSS JOIN sales_orders o ON o.tenant_id = ? "
        "AND o.order_id = d.order_id ORDER BY o.created_at, o.order_id",
        (tenant_id,),
    ).fetchall()
    return store._with_details(tenant_id, rows, conn)  # noqa: SLF001 - 같은 분석 연결


def cancel_refund(conn: sqlite3.Connection, p: dict[str, object]) -> tuple[int, int]:
    """(기간에 접수해 취소된 주문 수, 기간에 일어난 환불 합).

    환불은 주문 상태와 상관없이 환불 시각으로 센다.
    """
    marks = ", ".join(f":c{i}" for i in range(len(CANCELLED_STATUSES)))
    q = {**p, **{f"c{i}": v for i, v in enumerate(CANCELLED_STATUSES)}}
    cancelled = conn.execute(
        "SELECT COUNT(*) FROM sales_orders WHERE tenant_id = :t AND created_at >= :s "
        f"AND created_at < :e AND LOWER(status) IN ({marks})",
        q,
    ).fetchone()[0]
    refund = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM sales_refunds WHERE tenant_id = :t "
        "AND refunded_at >= :s AND refunded_at < :e",
        q,
    ).fetchone()[0]
    return int(cancelled), int(refund)


def tally(store: SalesStore, tenant_id: str, start: datetime, end: datetime) -> tuple[Tally, int]:
    """[start, end) 집계와 그 기간에 접수한 주문 수(상태 무관, 품질 보고용)."""
    p = _params(tenant_id, start, end)
    t = Tally(window=(start, end))
    with store.analysis() as conn:
        try:
            _build(conn, p, store.use_summary and sales_summary.ready(conn, tenant_id))
            _sql_part(conn, p, t)
            odd = _odd_orders(store, conn, tenant_id)
            created = int(conn.execute(
                "SELECT COUNT(*) FROM sales_orders WHERE tenant_id = :t AND created_at >= :s "
                "AND created_at < :e", p,
            ).fetchone()[0])  # fmt: skip
            t.cancelled, t.refund_amount = cancel_refund(conn, p)
        finally:
            _drop(conn)
    refunded_any = store.orders_refunded(tenant_id, start, end)  # 기간 안 환불이 있는 주문 (드묾)
    t.rows = created + len(refunded_any)
    refunded = [o for o in refunded_any if counts_as_revenue(o.status, o.payment_status)]
    before = store.buyers_paid_before(
        tenant_id, {o.buyer_id for o in odd} | {o.buyer_id for o in refunded}, start
    )
    for order in odd:  # 고르지 않은 주문: 예전 배분 그대로 (수는 SQL이 이미 셌다)
        nets = line_nets(order)
        add_amounts(t, order, nets, [n.net for n in nets], before, meta=False)
    for order in refunded:  # 기간 안 환불: 줄 순매출 비율로 나눈다 (상품 이름·분류는 나중에 얹는다)
        amount = sum(r.amount for r in order.refunds if start <= r.refunded_at < end)
        if amount:
            nets = line_nets(order)
            add_amounts(t, order, nets, allocate(-amount, [n.net for n in nets]), before,
                        meta=False)  # fmt: skip
            t.refund_lines += [(n.product_id, n.title, n.category_code) for n in nets
                               if n.product_id]  # fmt: skip
    return t, created
