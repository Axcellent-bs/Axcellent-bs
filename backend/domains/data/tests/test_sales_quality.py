"""매출 분석 기간(한국 시간 자정 경계)과 품질 보고·품질 관문 두 가지."""

from datetime import UTC, datetime, timedelta

from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from ax_contracts.schemas.data import CatalogSales
from data_agent.sales_clean import NON_POSITIVE_QTY
from data_agent.sales_quality import days_in, gate, paid_orders, quality, window
from data_agent.sales_store import SalesStore

TENANT = "shop-demo"
CFG = CatalogSales(categories={"book": "도서"})
# 한국 시간 11/16 12:00 = UTC 11/16 03:00
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)


def row(order_id: str, at: datetime, **over) -> SalesOrderRow:
    base = {
        "order_id": order_id, "buyer_id": "buy_1", "status": "paid", "created_at": at,
        "updated_at": at, "items_amount": 10000, "payment_status": "succeeded",
        "lines": [SalesOrderLine(product_id="p1", category_code="book", qty=1, unit_price=10000)],
    }  # fmt: skip
    base.update(over)
    return SalesOrderRow(**base)


def test_today_window_starts_at_kst_midnight_and_compares_with_yesterday() -> None:
    w = window("today", NOW)
    assert w.start == datetime(2026, 11, 15, 15, 0, tzinfo=UTC)  # 한국 11/16 0시
    assert w.end == NOW
    assert w.previous_start == datetime(2026, 11, 14, 15, 0, tzinfo=UTC)
    assert w.previous_end == NOW - timedelta(days=1)


def test_7d_and_30d_windows() -> None:
    w7 = window("7d", NOW)
    assert w7.start == datetime(2026, 11, 9, 15, 0, tzinfo=UTC)  # 한국 11/10 0시
    assert w7.previous_start == w7.start - timedelta(days=7)
    assert w7.previous_end - w7.previous_start == w7.end - w7.start  # 같은 길이
    w30 = window("30d", NOW)
    assert w30.start == datetime(2026, 10, 17, 15, 0, tzinfo=UTC)  # 한국 10/18 0시
    assert w30.previous_end <= w30.start


def test_window_accepts_kst_now_and_returns_utc() -> None:
    from data_agent.sales_quality import KST

    w = window("today", NOW.astimezone(KST))
    assert w.start.tzinfo == UTC and w.start == datetime(2026, 11, 15, 15, 0, tzinfo=UTC)


def test_midnight_boundary_belongs_to_next_day() -> None:
    w = window("today", NOW)
    store = SalesStore(":memory:")
    store.upsert(
        TENANT,
        [
            row("before", w.start - timedelta(seconds=1)),  # 한국 11/15 23:59:59
            row("at", w.start),  # 한국 11/16 0:00:00
        ],
        NOW,
    )
    ids = [o.order_id for o in store.orders_created(TENANT, w.start, w.end)]
    assert ids == ["at"]


def test_days_in_kst() -> None:
    w = window("7d", NOW)
    days = days_in(w.start, w.end)
    assert [d.isoformat() for d in (days[0], days[-1])] == ["2026-11-10", "2026-11-16"]
    assert len(days) == 7


def test_quality_report() -> None:
    w = window("7d", NOW)
    store = SalesStore(":memory:")
    day = w.start + timedelta(hours=2)
    bad_line = SalesOrderLine(product_id="p2", qty=0, unit_price=1)
    stray = SalesOrderLine(product_id="p3", category_code="toy", qty=1, unit_price=500)
    store.upsert(
        TENANT,
        [
            row("o1", day),
            row("o2", day + timedelta(days=2), lines=[stray, bad_line]),
            row("o3", day, items_amount=-1),  # 주문째 버림
        ],
        NOW,
    )
    store.finish_sync(TENANT, "shop-api", NOW - timedelta(minutes=3), None)
    orders = store.orders_created(TENANT, w.start, w.end)
    report = quality(store, TENANT, w, orders, CFG)
    assert report.rows_in_period == 2
    assert report.empty_days == ["2026-11-11", "2026-11-13", "2026-11-14", "2026-11-15",
                                 "2026-11-16"]  # fmt: skip
    assert report.dropped_rows == 2
    assert report.dropped_reasons == {"금액이 음수": 1, NON_POSITIVE_QTY: 1}
    assert report.last_synced_at == NOW - timedelta(minutes=3)
    assert report.unknown_category_codes == ["toy"]


def test_gate_stale_collection() -> None:
    store = SalesStore(":memory:")
    store.finish_sync(TENANT, "shop-api", NOW - timedelta(minutes=45), None)
    report = quality(store, TENANT, window("7d", NOW), [], CFG)
    reasons = gate(report, paid_count=100, cfg=CFG, now=NOW)
    assert reasons == ["데이터 부족: 마지막 수집이 45분 전 (기준 30분 안)"]


def test_gate_too_few_orders() -> None:
    store = SalesStore(":memory:")
    store.finish_sync(TENANT, "shop-api", NOW - timedelta(minutes=1), None)
    report = quality(store, TENANT, window("today", NOW), [], CFG)
    assert gate(report, paid_count=19, cfg=CFG, now=NOW) == [
        "데이터 부족: 기간 안 결제 완료 주문 19건 (기준 20건 이상)"
    ]
    assert gate(report, paid_count=20, cfg=CFG, now=NOW) == []


def test_gate_never_synced_and_no_config() -> None:
    store = SalesStore(":memory:")
    report = quality(store, TENANT, window("today", NOW), [], None)
    no_config = "데이터 부족: 회사 카탈로그에 매출 분석 설정([sales])이 없음"
    assert gate(report, 50, None, NOW) == [no_config]
    assert gate(report, 50, CFG, NOW) == ["데이터 부족: 판매 기록을 아직 끝까지 받은 적이 없음"]


def test_paid_orders_filter() -> None:
    store = SalesStore(":memory:")
    at = NOW - timedelta(hours=1)
    store.upsert(
        TENANT,
        [row("a", at), row("b", at, status="cancelled"), row("c", at, payment_status="failed")],
        NOW,
    )
    orders = store.orders_created(TENANT, at, NOW)
    assert [o.order_id for o in paid_orders(orders)] == ["a"]


def test_catbooks_catalog_has_sales_section() -> None:
    from data_agent.catalog import load_catalog

    cat = load_catalog("catbooks")
    assert cat.sales is not None
    assert cat.sales.categories == {"book": "도서", "stationery": "문구"}
    assert cat.sales.min_product_orders == 5
    assert cat.sales.max_staleness_minutes == 30 and cat.sales.min_period_orders == 20
    assert "배송비 제외" in cat.sales.revenue_definition
