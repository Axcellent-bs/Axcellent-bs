"""일별 집계 3-1a (설계 docs/superpowers/specs/2026-11-24-daily-aggregate-design.md 3.1·5.4·5.5).

- 다 지난 날의 일별 행을 더하면 지금 매출 분석(`sales_sql.tally`)과 같은 숫자 (가짜 판매 출처 +
  경계 주문: 취소·결제 실패·환불·옛 주문 환불·할인 몫 불일치·줄 없음)
- 어떤 집계 표에도 구매자 id·주문 id 칸이 없다
- 다시 채워도 같은 결과, 회사마다 따로, 13개월 지난 날은 달 행으로 (합은 그대로), 5년 지난 달은 지움
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import pytest

from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRefund, SalesOrderRow
from data_agent import catalog
from data_agent.aggregate import store as agg_store
from data_agent.aggregate.backfill import backfill, months_back, retention_bounds
from data_agent.aggregate.build_day import build_day, day_window
from data_agent.aggregate.store import AggregateStore
from data_agent.sales_collect import collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_quality import kst_day
from data_agent.sales_sql import tally
from data_agent.sales_store import SalesStore

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
    src = FakeSalesSource(load_config(FAKE_DIR / "catbooks.toml"), clock=lambda: NOW)
    assert asyncio.run(collect_once(src, store, source_name="fake", now=NOW)).errors == []
    store.upsert(TENANT, edge_orders(), NOW)
    return store


@pytest.fixture(scope="module")
def sales() -> SalesStore:
    return filled()


@pytest.fixture(scope="module")
def agg(sales: SalesStore) -> AggregateStore:
    a = AggregateStore(":memory:")
    run = backfill(sales, a, TENANT, catalog.load_catalog("catbooks").sales, NOW, run_id="run_t")
    assert run.status == "done" and run.as_of == date(2026, 11, 15)
    return a


def _sum(agg: AggregateStore, start: date, end: date) -> dict:
    out: dict = {"revenue": 0, "orders": 0, "coupon_orders": 0, "cat": Counter(), "prod": Counter(),
                 "prod_orders": Counter(), "chan": Counter(), "chan_orders": Counter()}  # fmt: skip
    for r in agg.sales_rows(TENANT, start, end):
        net = r["revenue_net"] - r["refund_amount"]
        if r["grain"] == "order":
            out["revenue"] += net
            out["orders"] += r["orders"]
            out["coupon_orders"] += r["orders"] if r["coupon_used"] else 0
            out["chan"][(r["src_source"], r["src_medium"])] += net
            out["chan_orders"][(r["src_source"], r["src_medium"])] += r["orders"]
        else:
            out["cat"][r["category_code"]] += net
            if r["product_id"]:
                out["prod"][r["product_id"]] += net
                out["prod_orders"][r["product_id"]] += r["orders"]
    return out


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v}


@pytest.mark.parametrize("days", [1, 7, 30])
def test_daily_rows_add_up_to_tally(sales: SalesStore, agg: AggregateStore, days: int) -> None:
    end_day = kst_day(NOW)  # 오늘은 안 넣음 (다 지난 날만)
    start_day = end_day - timedelta(days=days)
    t, _ = tally(sales, TENANT, day_window(start_day)[0], day_window(end_day)[0])
    got = _sum(agg, start_day, end_day)
    assert got["revenue"] == t.revenue and got["orders"] == t.orders
    assert got["coupon_orders"] == t.coupon_orders
    assert _clean(got["cat"]) == _clean(dict(t.by_category))
    assert _clean(got["prod"]) == _clean(dict(t.by_product))
    # 환불만 있는 상품 줄은 주문 수 0 (그날 접수 주문이 없음) → tally에는 칸이 없다
    assert _clean(got["prod_orders"]) == _clean(dict(t.product_orders))
    chan = {((s or ""), (m or "")): v for (s, m), v in t.by_channel.items()}
    assert _clean(got["chan"]) == _clean(chan)
    chan_orders = {((s or ""), (m or "")): v for (s, m), v in t.channel_orders.items()}
    assert _clean(got["chan_orders"]) == _clean(chan_orders)


def test_edge_orders_are_in_the_rows(agg: AggregateStore) -> None:
    rows = agg.sales_rows(TENANT, date(2026, 1, 1), date(2026, 11, 16))
    assert any(r["src_campaign"] == "winter" and r["coupon_used"] for r in rows)
    # 옛 주문(50일 전 접수)의 환불이 환불 날짜 줄 행에 든다 (주문 수 0)
    assert any(r["grain"] == "line" and r["product_id"] == "p2" and r["refund_amount"] == 4000
               and r["orders"] == 0 for r in rows)  # fmt: skip
    assert not any(r["refund_amount"] == 9999 for r in rows)  # 취소 주문 환불은 빼지 않음
    assert any(r["grain"] == "line" and r["product_id"] == "" for r in rows)  # 줄 없는 주문


def test_order_and_line_grains_have_same_money(agg: AggregateStore) -> None:
    for day in agg.days(TENANT):
        d = date.fromisoformat(day)
        rows = agg.sales_rows(TENANT, d, d + timedelta(days=1))
        for col in ("revenue_net", "refund_amount", "qty"):
            assert sum(r[col] for r in rows if r["grain"] == "order") == sum(
                r[col] for r in rows if r["grain"] == "line"
            )


def test_no_table_has_person_or_order_ids(agg: AggregateStore) -> None:
    banned = {"buyer_id", "order_id", "customer_id", "created_at", "refunded_at"}
    for table in ("agg_sales_daily", "agg_sales_monthly", "agg_quality_daily", "agg_runs"):
        cols = set(agg.columns(table))
        assert cols and not banned & cols, table
        assert "tenant_id" in cols


def test_quality_row_counts_and_current_values_only_on_as_of(agg: AggregateStore) -> None:
    as_of = agg.quality(TENANT, date(2026, 11, 15))
    older = agg.quality(TENANT, date(2026, 11, 1))
    assert as_of is not None and as_of["customers"] is not None
    assert older is not None and older["customers"] is None  # 옛 날짜는 그날 값을 몰라 비움
    assert as_of["paid_orders"] <= as_of["created_orders"]


def test_backfill_again_gives_same_rows(sales: SalesStore) -> None:
    def snapshot(a: AggregateStore) -> list[tuple]:
        skip = {"run_id", "computed_at"}
        return [tuple(v for k, v in dict(r).items() if k not in skip)
                for r in a.sales_rows(TENANT, date(2000, 1, 1), date(2100, 1, 1))]  # fmt: skip

    a = AggregateStore(":memory:")
    cfg = catalog.load_catalog("catbooks").sales
    backfill(sales, a, TENANT, cfg, NOW)
    first = snapshot(a)
    backfill(sales, a, TENANT, cfg, NOW)
    assert snapshot(a) == first and first


def test_companies_are_kept_apart() -> None:
    sales = SalesStore(":memory:")
    when = NOW - timedelta(days=2)
    sales.upsert("co-a", [order("a1", "x", when, [line("pa")])], NOW)
    sales.upsert("co-b", [order("b1", "y", when, [line("pb", price=5000)])], NOW)
    a = AggregateStore(":memory:")
    backfill(sales, a, "co-a", None, NOW)
    rows_a = a.sales_rows("co-a", date(2026, 1, 1), date(2026, 12, 1))
    assert {r["product_id"] for r in rows_a if r["grain"] == "line"} == {"pa"}
    assert a.sales_rows("co-b", date(2026, 1, 1), date(2026, 12, 1)) == []  # 따로 채워야 생긴다
    backfill(sales, a, "co-b", None, NOW)
    assert {r["tenant_id"] for r in a.sales_rows("co-b", date(2026, 1, 1), date(2026, 12, 1))} == {
        "co-b"
    }
    assert a.sales_rows("co-a", date(2026, 1, 1), date(2026, 12, 1)) == rows_a  # co-a는 그대로


def test_retention_rolls_old_days_into_months_and_keeps_totals() -> None:
    sales = SalesStore(":memory:")
    rows = [
        order("old", "x", datetime(2025, 3, 10, 3, tzinfo=UTC), [line("p")]),  # 13개월 넘음 → 달 행
        order("old2", "y", datetime(2025, 3, 20, 3, tzinfo=UTC), [line("p", price=5000)]),
        order("ancient", "z", datetime(2020, 1, 5, 3, tzinfo=UTC), [line("p")]),  # 5년 넘음 → 지움
        order("new", "w", datetime(2026, 11, 2, 3, tzinfo=UTC), [line("p")]),
    ]
    sales.upsert(TENANT, rows, NOW)
    a = AggregateStore(":memory:")
    run = backfill(sales, a, TENANT, None, NOW)
    keep_from, drop_before = retention_bounds(run.as_of)
    assert keep_from == date(2025, 11, 1) and drop_before == date(2021, 12, 1)
    assert min(a.days(TENANT)) >= keep_from.isoformat()
    months = {r["month"]: r for r in a.monthly_rows(TENANT) if r["grain"] == "order"}
    assert months["2025-03"]["orders"] == 2 and months["2025-03"]["revenue_net"] == 15000
    assert "2020-01" not in months
    assert run.rows["rolled_up_daily_rows"] > 0


def test_months_back() -> None:
    assert months_back(date(2026, 11, 15), 12) == date(2025, 11, 1)
    assert months_back(date(2026, 1, 31), 1) == date(2025, 12, 1)


def test_build_day_window_is_korean_midnight() -> None:
    start, end = day_window(date(2026, 11, 15))
    assert start == datetime(2026, 11, 14, 15, tzinfo=UTC) and end - start == timedelta(days=1)


def test_failed_build_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    sales = SalesStore(":memory:")
    sales.upsert(TENANT, [order("a", "x", NOW - timedelta(days=1), [line("p")])], NOW)
    a = AggregateStore(":memory:")

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("secret")

    monkeypatch.setattr("data_agent.aggregate.backfill.build_day", boom)
    with pytest.raises(RuntimeError):
        backfill(sales, a, TENANT, None, NOW, run_id="r_fail")
    run = a.runs(TENANT)[-1]
    assert run["status"] == "failed" and run["reason"] == "RuntimeError"  # 예외 글은 남기지 않음


def test_default_db_path_is_backend_data() -> None:
    assert agg_store.DEFAULT_DB.parts[-3:] == ("backend", ".data", "aggregates.db")
    assert build_day  # 공개 함수
