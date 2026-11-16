"""매출 분석 표준 판매 저장소: 정제 규칙, upsert(같은 주문 두 번), 버린 행 세기, 기간 조회."""

from datetime import UTC, datetime, timedelta

from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRefund, SalesOrderRow
from data_agent.sales_clean import NEGATIVE, NON_POSITIVE_QTY, clean, counts_as_revenue
from data_agent.sales_store import SalesStore

T0 = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)
TENANT = "shop-demo"


def row(order_id: str = "ord_1", **over) -> SalesOrderRow:
    base = {
        "order_id": order_id,
        "buyer_id": "buy_1",
        "status": "paid",
        "created_at": T0,
        "updated_at": T0,
        "items_amount": 36000,
        "coupon_discount": 2000,
        "points_used": 1000,
        "payment_status": "succeeded",
        "lines": [
            SalesOrderLine(
                product_id="prd_1",
                category_code="book",
                qty=2,
                unit_price=18000,
                coupon_share=2000,
                points_share=1000,
            ),
        ],
        "refunds": [],
    }
    base.update(over)
    return SalesOrderRow(**base)


def test_clean_drops_negative_order_whole() -> None:
    result = clean(row(shipping_fee=-1))
    assert result.order is None and [d.reason for d in result.dropped] == [NEGATIVE]


def test_clean_drops_bad_lines_and_refunds_only() -> None:
    good = SalesOrderLine(product_id="prd_2", qty=1, unit_price=5000)
    zero = SalesOrderLine(product_id="prd_3", qty=0, unit_price=5000)
    neg = SalesOrderLine(product_id="prd_4", qty=1, unit_price=-5000)
    refunds = [
        SalesOrderRefund(amount=-10, refunded_at=T0),
        SalesOrderRefund(amount=1000, refunded_at=T0),
    ]
    result = clean(row(lines=[good, zero, neg], refunds=refunds))
    assert result.order is not None
    assert [ln.product_id for ln in result.lines] == ["prd_2"]
    assert [r.amount for r in result.refunds] == [1000]
    assert sorted((d.kind, d.reason) for d in result.dropped) == [
        ("line", NEGATIVE), ("line", NON_POSITIVE_QTY), ("refund", NEGATIVE)
    ]  # fmt: skip


def test_counts_as_revenue_excludes_cancelled_failed_pending() -> None:
    assert counts_as_revenue("paid", "succeeded")
    assert counts_as_revenue("delivered", "succeeded")
    assert not counts_as_revenue("cancelled", "succeeded")
    assert not counts_as_revenue("payment_failed", "failed")
    assert not counts_as_revenue("pending_payment", None)
    assert not counts_as_revenue("paid", "failed")


def test_upsert_same_order_twice_overwrites() -> None:
    store = SalesStore(":memory:")
    store.upsert(TENANT, [row()], T0)
    store.upsert(TENANT, [row(status="cancelled", updated_at=T0 + timedelta(hours=1))], T0)
    orders = store.orders_created(TENANT, T0, T0 + timedelta(days=1))
    assert len(orders) == 1 and orders[0].status == "cancelled"
    assert len(orders[0].lines) == 1  # 줄도 두 번 쌓이지 않는다
    assert store.order_count(TENANT) == 1


def test_dropped_counted_once_per_order_even_if_received_twice() -> None:
    store = SalesStore(":memory:")
    bad = row(lines=[SalesOrderLine(product_id="p", qty=0, unit_price=1)])
    store.upsert(TENANT, [bad, row("ord_2", items_amount=-5)], T0)
    store.upsert(TENANT, [bad, row("ord_2", items_amount=-5)], T0)
    assert store.dropped(TENANT, T0, T0 + timedelta(days=1)) == {
        NEGATIVE: 1, NON_POSITIVE_QTY: 1
    }  # fmt: skip
    assert store.order_count(TENANT) == 1  # 음수 주문은 저장하지 않는다


def test_order_becoming_invalid_is_removed() -> None:
    store = SalesStore(":memory:")
    store.upsert(TENANT, [row()], T0)
    store.upsert(TENANT, [row(coupon_discount=-1)], T0)
    assert store.order_count(TENANT) == 0


def test_tenants_are_separate() -> None:
    store = SalesStore(":memory:")
    store.upsert("a", [row()], T0)
    store.upsert("b", [row(status="cancelled")], T0)
    assert store.orders_created("a", T0, T0 + timedelta(hours=1))[0].status == "paid"
    assert store.orders_created("b", T0, T0 + timedelta(hours=1))[0].status == "cancelled"


def test_refund_lookup_by_refund_time() -> None:
    store = SalesStore(":memory:")
    later = T0 + timedelta(days=8)
    store.upsert(TENANT, [row(refunds=[SalesOrderRefund(amount=3000, refunded_at=later)])], T0)
    assert store.orders_refunded(TENANT, T0, T0 + timedelta(days=1)) == []
    found = store.orders_refunded(TENANT, later, later + timedelta(hours=1))
    assert [r.amount for r in found[0].refunds] == [3000]


def test_buyers_paid_before_ignores_cancelled() -> None:
    store = SalesStore(":memory:")
    store.upsert(
        TENANT,
        [
            row("o1", buyer_id="a"),
            row("o2", buyer_id="b", status="cancelled"),
            row("o3", buyer_id="c", created_at=T0 + timedelta(days=3)),
        ],
        T0,
    )
    assert store.buyers_paid_before(TENANT, ["a", "b", "c"], T0 + timedelta(days=1)) == {"a"}


def test_sync_state_keeps_last_success_on_failure() -> None:
    store = SalesStore(":memory:")
    store.save_cursor(TENANT, "shop-api", "c1")
    store.finish_sync(TENANT, "shop-api", T0, None)
    store.finish_sync(TENANT, "shop-api", T0 + timedelta(minutes=5), "판매 기록 [UNAVAILABLE]")
    state = store.sync_state(TENANT, "shop-api")
    assert state.cursor == "c1" and state.last_synced_at == T0
    assert state.last_error == "판매 기록 [UNAVAILABLE]"
    assert state.last_attempt_at == T0 + timedelta(minutes=5)


def test_file_db_persists(tmp_path) -> None:
    path = tmp_path / "sub" / "sales.db"
    store = SalesStore(path)
    store.upsert(TENANT, [row()], T0)
    store.close()
    assert SalesStore(path).order_count(TENANT) == 1
