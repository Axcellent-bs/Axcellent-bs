"""일별 집계 하루 계산·병행 비교·수집기 연결 (3-1b, 설계 5.1·5.3·6절).

- 하루 한 번만 (같은 기준일로 끝났으면 다시 안 돈다)
- 지난 계산 뒤 새로 받은 주문이 건드린 옛 날짜(환불 날짜 포함)를 다시 만든다
- 원본(tally)과 집계를 7·30일로 비교해 agg_compare에 남긴다 (가짜 판매 데이터에서 다름 0)
- 실패하면 기록만 남기고 그날 스냅샷은 안 생긴다
- 수집기는 DATA_AGGREGATE_DAILY=1이고 한국 00:30 뒤일 때만 계산한다
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRefund, SalesOrderRow
from data_agent import catalog
from data_agent.aggregate import daily
from data_agent.aggregate.backfill import backfill
from data_agent.aggregate.store import AggregateStore
from data_agent.sales_collect import aggregate_daily, collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_store import SalesStore

# 같은 가짜 판매 + 경계 주문 (test_aggregate_daily와 같은 데이터)
TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00 → 기준일 11/15


def line(pid: str, cat: str | None = "book", price: int = 10000, coupon: int = 0) -> SalesOrderLine:
    return SalesOrderLine(product_id=pid, category_code=cat, title="책", qty=1, unit_price=price,
                          coupon_share=coupon, points_share=0)  # fmt: skip


def order(n: str, buyer: str, when: datetime, lines: list[SalesOrderLine], **kw) -> SalesOrderRow:  # noqa: ANN003
    base = {"order_id": n, "buyer_id": buyer, "status": "paid", "created_at": when,
            "updated_at": when, "items_amount": sum(ln.qty * ln.unit_price for ln in lines),
            "payment_status": "succeeded", "lines": lines}  # fmt: skip
    return SalesOrderRow.model_validate({**base, **kw})


def edge_orders() -> list[SalesOrderRow]:
    days = [NOW - timedelta(days=d, hours=2) for d in range(1, 40)]
    return [
        order("e1", "eb1", days[2], [line("p1"), line("p2", price=30000)], coupon_discount=3001,
              src_campaign="winter"),  # 할인 몫 불일치
        order("e2", "eb2", days[0], [], items_amount=7000, points_used=500),  # 줄 없음
        order("e3", "eb3", days[3], [line("p1")], status="cancelled"),
        order("e4", "eb4", days[1], [line("p1")], payment_status="failed"),
        order("e6", "eb6", days[3], [line("p1", coupon=1000), line("p3", price=25000)],
              coupon_discount=1000, refunds=[SalesOrderRefund(amount=7777, refunded_at=days[1])]),
        order("e7", "eb7", NOW - timedelta(days=50), [line("p2")],  # 옛 주문을 기간 안에 환불
              refunds=[SalesOrderRefund(amount=4000, refunded_at=days[0])]),
        order("e8", "eb8", days[4], [line("p3")], status="cancelled",  # 취소 주문 환불은 빼지 않음
              refunds=[SalesOrderRefund(amount=9999, refunded_at=days[1])]),
        order("e11", "eb10", days[1], [line("p3", cat=None)], src_source="naver", src_medium="cpc"),
    ]  # fmt: skip


def filled() -> SalesStore:
    store = SalesStore(":memory:")
    # 시험이 보는 기간은 길어야 30일 → 데모 설정의 90일 대신 40일 (11/25 CI 시간 줄이기)
    cfg = load_config(FAKE_DIR / "catbooks.toml").model_copy(update={"period_days": 40})
    src = FakeSalesSource(cfg, clock=lambda: NOW)
    assert asyncio.run(collect_once(src, store, source_name="fake", now=NOW)).errors == []
    store.upsert(TENANT, edge_orders(), NOW)
    return store


AS_OF = date(2026, 11, 15)


@pytest.fixture
def cfg():  # noqa: ANN201
    return catalog.load_catalog("catbooks").sales


def test_runs_once_per_day_and_compare_is_all_same(cfg) -> None:  # noqa: ANN001
    sales, agg = filled(), AggregateStore(":memory:")
    run = daily.run_daily(sales, agg, TENANT, cfg, NOW, run_id="d1")
    assert run is not None and run.status == "done" and run.as_of == AS_OF
    assert run.rows["compare_different"] == 0 and run.rows["compare_same"] == 4
    assert (
        daily.run_daily(sales, agg, TENANT, cfg, NOW + timedelta(hours=3)) is None
    )  # 같은 날 두 번째
    rows = agg.compare_rows(TENANT, AS_OF)
    assert {r["target"] for r in rows} == {"sales_analysis:7d", "sales_analysis:30d",
                                           "customer_mix:7d", "customer_mix:30d"}  # fmt: skip
    assert all(r["same"] == 1 and r["diffs"] == "[]" for r in rows)
    assert agg.buyer_rows(TENANT, AS_OF) and agg.more_rows("agg_pairs", TENANT, AS_OF)


def test_old_day_changed_after_last_run_is_rebuilt(cfg) -> None:  # noqa: ANN001
    sales, agg = filled(), AggregateStore(":memory:")
    backfill(sales, agg, TENANT, cfg, NOW, run_id="b1")
    old_day = datetime(2026, 11, 3, 3, tzinfo=UTC)  # 한국 11/3
    later = NOW + timedelta(days=1)
    refund = SalesOrderRefund(amount=345, refunded_at=later - timedelta(hours=20))  # 한국 11/16
    late = order("late", "lb", old_day, [line("late_p", price=12345)], refunds=[refund])
    sales.upsert(TENANT, [late], later)
    since = daily.last_success(agg, TENANT)[1]
    assert date(2026, 11, 3) in daily.changed_days(sales, TENANT, since, date(2026, 11, 16))
    run = daily.run_daily(sales, agg, TENANT, cfg, later, run_id="d2")
    assert run is not None and run.status == "done" and run.rows["compare_different"] == 0
    day3 = agg.sales_rows(TENANT, date(2026, 11, 3), date(2026, 11, 4))
    assert any(r["product_id"] == "late_p" and r["revenue_net"] == 12345 for r in day3)
    day16 = agg.sales_rows(TENANT, date(2026, 11, 16), date(2026, 11, 17))
    assert any(r["product_id"] == "late_p" and r["refund_amount"] == 345 for r in day16)


def test_expand_old_rebuilds_whole_month() -> None:
    days = daily.expand_old(
        [date(2025, 3, 10), date(2026, 11, 1)], date(2025, 11, 1), date(2025, 3, 5)
    )
    assert date(2025, 3, 5) in days and date(2025, 3, 31) in days and date(2025, 3, 4) not in days
    assert date(2026, 11, 1) in days


def test_failure_keeps_last_success_and_makes_no_snapshot(cfg, monkeypatch) -> None:  # noqa: ANN001
    sales, agg = filled(), AggregateStore(":memory:")
    daily.run_daily(sales, agg, TENANT, cfg, NOW, run_id="ok")

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(daily, "snapshot_day", boom)
    run = daily.run_daily(sales, agg, TENANT, cfg, NOW + timedelta(days=1), run_id="bad")
    assert run is not None and run.status == "failed" and run.reason == "RuntimeError"
    assert agg.buyer_rows(TENANT, date(2026, 11, 16)) == []  # 실패한 기준일 스냅샷 없음
    assert agg.buyer_rows(TENANT, AS_OF)  # 마지막 성공 기준일은 그대로
    assert daily.last_success(agg, TENANT)[0] == AS_OF


def test_collector_hook_only_when_enabled_and_after_0030(tmp_path) -> None:  # noqa: ANN001
    sales = filled()
    db = tmp_path / "agg.db"
    env = {"DATA_AGGREGATES_DB": str(db), "DATA_TENANT_CATALOG": "catbooks"}
    aggregate_daily(sales, TENANT, env, NOW)  # 켜지 않음
    assert not db.exists()
    on = {**env, "DATA_AGGREGATE_DAILY": "1"}
    aggregate_daily(sales, TENANT, on, datetime(2026, 11, 15, 15, 20, tzinfo=UTC))  # 한국 00:20
    assert not db.exists()
    aggregate_daily(sales, TENANT, on, NOW)  # 한국 12:00
    a = AggregateStore(db)
    try:
        assert [r["status"] for r in a.runs(TENANT)] == ["done"]
    finally:
        a.close()
