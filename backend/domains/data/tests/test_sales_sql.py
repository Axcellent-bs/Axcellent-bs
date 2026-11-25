"""매출 분석을 SQL 집계(`sales_sql.tally`)로 바꾼 뒤에도 출력이 같은지 (11/18).

예전 경로 = 기간 안 주문을 전부 파이썬으로 읽어(`orders_created`·`orders_refunded`) 세던 11/17 코드.
그 계산을 여기 `old_tally`·`old_analyze`로 그대로 옮겨 두고, 같은 저장소에서 두 경로의 출력을
비교한다. 가짜 판매 출처(캣북스) 주문에 경계 주문(취소·결제 실패·환불·할인 몫 불일치·줄 없음·
이름 바뀜)을 더한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRefund, SalesOrderRow
from ax_contracts.schemas.data import SalesAnalysisOutput, SalesBasis, SalesCustomerMix
from data_agent import catalog
from data_agent.sales_clean import counts_as_revenue
from data_agent.sales_collect import collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_quality import gate, quality, window
from data_agent.sales_sql import (
    CANCELLED_STATUSES,
    Tally,
    add_amounts,
    allocate,
    line_nets,
    product_names,
)
from data_agent.sales_store import FAKE_SOURCE, SalesStore, StoredOrder
from data_agent.skills import sales_analysis as sa

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00


# ---------------------------------------------------------------- 예전 경로 (11/17 코드 그대로)


def old_tally(
    store: SalesStore, tenant_id: str, start: datetime, end: datetime
) -> tuple[Tally, list[StoredOrder]]:
    created = store.orders_created(tenant_id, start, end)
    refunded = store.orders_refunded(tenant_id, start, end)
    paid = [o for o in created if counts_as_revenue(o.status, o.payment_status)]
    paid_refunded = [o for o in refunded if counts_as_revenue(o.status, o.payment_status)]
    buyers = {o.buyer_id for o in paid} | {o.buyer_id for o in paid_refunded}
    before = store.buyers_paid_before(tenant_id, buyers, start)
    t = Tally(rows=len(created) + len({o.order_id for o in refunded}))
    who: set[str] = set()
    repeat: set[str] = set()
    for order in paid:
        nets = line_nets(order)
        add_amounts(t, order, nets, [n.net for n in nets], before)
        t.orders += 1
        t.coupon_orders += 1 if order.coupon_discount > 0 else 0
        who.add(order.buyer_id)
        if order.buyer_id in before:
            repeat.add(order.buyer_id)
        for pid in {n.product_id for n in nets if n.product_id}:
            t.product_orders[pid] += 1
        t.channel_orders[(order.src_source, order.src_medium)] += 1
    for order in paid_refunded:
        amount = sum(r.amount for r in order.refunds if start <= r.refunded_at < end)
        if amount:
            nets = line_nets(order)
            add_amounts(t, order, nets, allocate(-amount, [n.net for n in nets]), before)
    t.buyers, t.repeat_buyers = len(who), len(repeat)
    # 취소·환불 (11/25): 파이썬으로 따로 센 기준값 — SQL(`cancel_refund`)과 같아야 한다
    t.cancelled = sum(1 for o in created if o.status.lower() in CANCELLED_STATUSES)
    t.refund_amount = sum(
        r.amount for o in refunded for r in o.refunds if start <= r.refunded_at < end
    )
    return t, created


def old_analyze(store: SalesStore, period, cfg, catalog_id: str, now: datetime):  # noqa: ANN001, ANN201
    w = window(period, now)
    cur, created = old_tally(store, TENANT, w.start, w.end)
    prev, _ = old_tally(store, TENANT, w.previous_start, w.previous_end)
    report = quality(store, TENANT, w, created, cfg)
    fake = store.sync_state(TENANT).source == FAKE_SOURCE
    basis = SalesBasis(
        period_from=w.start, period_to=w.end, previous_from=w.previous_start,
        previous_to=w.previous_end, rows_read=cur.rows + prev.rows,
        last_synced_at=report.last_synced_at, catalog_id=catalog_id,
        catalog_version=cfg.version if cfg else None, data_source=sa.FAKE_DATA if fake else None,
    )  # fmt: skip
    reasons = gate(report, cur.orders, cfg, now)
    common = {"period": period, "as_of": w.end, "definition": cfg.revenue_definition if cfg else "",
              "quality": report, "basis": basis}  # fmt: skip
    if reasons or cfg is None:
        out = SalesAnalysisOutput(**common, status="insufficient", insufficient_reasons=reasons)
    else:
        up, down, low = sa.products(cur, prev, cfg)
        out = SalesAnalysisOutput(
            **common, kpis=sa.kpis(cur, prev), categories=sa.categories(cur, prev, cfg),
            products_up=up, products_down=down, products_low_sample=low,
            min_product_orders=cfg.min_product_orders,
            customers=SalesCustomerMix(
                buyers=cur.buyers, repeat_buyers=cur.repeat_buyers, new_revenue=cur.new_revenue,
                repeat_revenue=cur.repeat_revenue, new_share=sa.share(cur.new_revenue, cur.revenue),
                repeat_share=sa.share(cur.repeat_revenue, cur.revenue),
                coupon_orders=cur.coupon_orders,
                coupon_order_rate=sa.rate(cur.coupon_orders, cur.orders),
            ),
            channels=sa.channels(cur, cfg),
        )  # fmt: skip
    return out.model_copy(update={"summary": sa.summary(out)})


# ---------------------------------------------------------------- 같은 데이터


def line(pid: str, cat: str | None = "book", qty: int = 1, price: int = 10000, coupon: int = 0,
         points: int = 0, title: str | None = "책") -> SalesOrderLine:  # fmt: skip
    return SalesOrderLine(product_id=pid, category_code=cat, title=title, qty=qty, unit_price=price,
                          coupon_share=coupon, points_share=points)  # fmt: skip


def edge(n: str, buyer: str, when: datetime, lines: Sequence[SalesOrderLine],
         **kw) -> SalesOrderRow:  # noqa: ANN003  # fmt: skip
    amount = sum(ln.qty * ln.unit_price for ln in lines)
    base = {"order_id": n, "buyer_id": buyer, "status": "paid", "created_at": when,
            "updated_at": when, "items_amount": amount, "payment_status": "succeeded",
            "lines": list(lines)}  # fmt: skip
    return SalesOrderRow.model_validate({**base, **kw})


def edge_orders() -> list[SalesOrderRow]:
    today = NOW - timedelta(hours=1)
    days = [NOW - timedelta(days=d, hours=2) for d in range(1, 40)]
    p1, p2, p3 = "edge_p1", "edge_p2", "edge_p3"
    rows = [
        # 할인 몫 합이 주문 할인과 다름 → 줄 금액 비율로 나눈다
        edge("e1", "eb1", today, [line(p1), line(p2, price=30000)], coupon_discount=3001),
        # 줄이 없는 결제 완료 주문 → 분류 없는 한 줄
        edge("e2", "eb2", days[0], [], items_amount=7000, points_used=500),
        # 취소·결제 실패 (매출 아님), 결제 대기
        edge("e3", "eb3", today, [line(p1)], status="cancelled"),
        edge("e4", "eb4", days[1], [line(p1)], payment_status="failed"),
        edge("e5", "eb5", days[2], [line(p2)], status="pending_payment", payment_status=None),
        # 기간 안 환불 (결제 완료) — 줄이 둘이라 순매출 비율로 나눈다
        edge("e6", "eb6", days[3], [line(p1, coupon=1000), line(p3, price=25000)],
             coupon_discount=1000,
             refunds=[SalesOrderRefund(amount=7777, refunded_at=today)]),
        # 이전 달 주문을 이번 기간에 환불 (환불만 있는 구매자)
        edge("e7", "eb7", NOW - timedelta(days=50), [line(p2)],
             refunds=[SalesOrderRefund(amount=4000, refunded_at=days[0])]),
        # 취소 주문의 환불은 빼지 않는다
        edge("e8", "eb8", days[4], [line(p3)], status="cancelled",
             refunds=[SalesOrderRefund(amount=9999, refunded_at=days[1])]),
        # 같은 상품 이름이 바뀌고(마지막 이름), 이름이 빈 줄은 앞 이름을 남긴다
        edge("e9", "eb9", days[5], [line(p3, title="옛 이름", cat="stationery")]),
        edge("e10", "eb9", days[2], [line(p3, title=None, cat="book")]),
        edge("e11", "eb10", days[1], [line(p3, title="새 이름")], src_source="naver",
             src_medium="cpc"),
        # 직전 기간에 산 구매자 (재구매), 모르는 출처
        edge("e12", "eb1", days[30], [line(p1)], src_source="ghost"),
        edge("e13", "eb11", days[20], [line(p2, cat=None)], src_source="ghost", src_medium="x"),
    ]  # fmt: skip
    # 상품 비교 표본: 두 기간 모두 5번 이상 (늘어난 상품·줄어든 상품이 나오게)
    for i in range(6):
        rows.append(edge(f"up{i}", f"ub{i}", days[i % 6], [line("edge_up", price=20000)]))
        earlier = NOW - timedelta(days=35 + i)  # 30일 기간의 직전 기간
        rows.append(edge(f"upp{i}", f"ub{i}", earlier, [line("edge_up", price=10000)]))
        rows.append(edge(f"dn{i}", f"db{i}", days[i % 6], [line("edge_dn", price=5000)]))
        rows.append(edge(f"dnp{i}", f"db{i}", earlier, [line("edge_dn", price=9000)]))
    return rows


def filled() -> SalesStore:
    store = SalesStore(":memory:")
    src = FakeSalesSource(load_config(FAKE_DIR / "catbooks.toml"), clock=lambda: NOW)
    result = asyncio.run(collect_once(src, store, source_name="fake", now=NOW))
    assert result.errors == []
    store.upsert(TENANT, edge_orders(), NOW)
    return store


@pytest.fixture(scope="module")
def store() -> SalesStore:
    return filled()


@pytest.mark.parametrize("period", ["today", "7d", "30d"])
def test_sales_analysis_same_as_reading_all_orders(store: SalesStore, period: str) -> None:
    cat = catalog.load_catalog("catbooks")
    got = sa.analyze(store, TENANT, period, cat.sales, cat.id, NOW)
    want = old_analyze(store, period, cat.sales, cat.id, NOW)
    assert got == want
    assert got.status == "ok" and got.kpis[0].value  # 빈 결과끼리 비교가 아니다


def test_edge_orders_are_really_in_the_comparison(store: SalesStore) -> None:
    """경계 주문이 실제로 숫자에 들어가는지 (한 번만 확인)."""
    cat = catalog.load_catalog("catbooks")
    out = sa.analyze(store, TENANT, "30d", cat.sales, cat.id, NOW)
    labels = {c.label for c in out.channels}
    assert "기타 경로" in labels
    w = window("30d", NOW)
    t, _ = sa.tally(store, TENANT, w.start, w.end)
    prev, _ = sa.tally(store, TENANT, w.previous_start, w.previous_end)
    # 증감 비교 대상 (두 기간 모두 최소 표본 이상)
    assert t.product_orders["edge_up"] >= 5 and prev.product_orders["edge_up"] >= 5
    # 결제 완료 줄의 마지막 이름은 "새 이름"(e11)이지만, 예전 계산처럼 기간 안 환불 주문(e6, "책")을
    # 마지막에 다시 얹는다
    assert product_names(store, TENANT, t, {"edge_p3"}) == {"edge_p3": ("책", "book")}
    assert t.by_category[""] != 0  # 줄 없는 주문·분류 없는 줄


def test_cancel_and_refund_kpis_count_only_this_company(tmp_path) -> None:
    """취소 주문 수·환불액은 그 회사 주문만 센다(다른 회사 주문이 섞이지 않는다)."""
    from data_agent.sales_sql import tally

    store = SalesStore(str(tmp_path / "sales.db"))
    t0 = NOW - timedelta(days=1)

    def row(oid: str, status: str, refund: int = 0) -> SalesOrderRow:
        return SalesOrderRow(
            order_id=oid, buyer_id="b1", status=status, payment_status="succeeded",
            created_at=t0, updated_at=t0, items_amount=10000, shipping_fee=0, points_used=0,
            coupon_discount=0,
            lines=[SalesOrderLine(product_id="p1", qty=1, unit_price=10000, category_code="c")],
            refunds=[SalesOrderRefund(amount=refund, refunded_at=t0)] if refund else [],
        )  # fmt: skip

    store.upsert(
        TENANT, [row("a1", "paid"), row("a2", "cancelled", 10000), row("a3", "CANCELED")], NOW
    )
    store.upsert("nubi-tour", [row("n1", "cancelled", 5000), row("n2", "cancelled")], NOW)
    mine, _ = tally(store, TENANT, NOW - timedelta(days=7), NOW)
    other, _ = tally(store, "nubi-tour", NOW - timedelta(days=7), NOW)
    assert (mine.cancelled, mine.refund_amount) == (2, 10000)
    assert (other.cancelled, other.refund_amount) == (2, 5000)
