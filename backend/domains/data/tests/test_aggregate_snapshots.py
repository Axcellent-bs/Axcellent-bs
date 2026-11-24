"""기간 스냅샷 3-1a 뒷부분 1 (설계 2.2·3.3~3.5): 구매자 수·코호트·떠날 위험.

- 같은 기간이면 지금 분석 함수(매출 분석 tally, retention의 코호트·떠날 위험)와 같은 숫자
- 사람 수 칸은 저장할 때 카탈로그 k(min_group_size)로 숨긴다: 숨긴 칸은 숫자 NULL
- 어떤 스냅샷 표에도 구매자 id가 없다, 회사마다 따로, 보관(최근 35일 + 달 마지막 기준일)
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import pytest

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRefund, SalesOrderRow
from data_agent import catalog
from data_agent.aggregate.backfill import backfill
from data_agent.aggregate.build_day import day_window
from data_agent.aggregate.build_snapshots import OTHER_PRODUCTS, UNKNOWN, build_snapshots, windows
from data_agent.aggregate.store import AggregateStore
from data_agent.sales_collect import collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_sql import tally
from data_agent.sales_store import SalesStore
from data_agent.skills import retention
from data_agent.skills import segment_analysis as seg

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
    src = FakeSalesSource(load_config(FAKE_DIR / "catbooks.toml"), clock=lambda: NOW)
    assert asyncio.run(collect_once(src, store, source_name="fake", now=NOW)).errors == []
    store.upsert(TENANT, edge_orders(), NOW)
    return store


AS_OF = date(2026, 11, 15)  # NOW(한국 11/16 12:00)의 어제


@pytest.fixture(scope="module")
def sales() -> SalesStore:
    return filled()


@pytest.fixture(scope="module")
def cfg():  # noqa: ANN201
    return catalog.load_catalog("catbooks").sales


@pytest.fixture(scope="module")
def agg(sales: SalesStore, cfg) -> AggregateStore:  # noqa: ANN001
    a = AggregateStore(":memory:")
    run = backfill(sales, a, TENANT, cfg, NOW, run_id="run_s")
    assert run.status == "done" and run.rows["agg_buyers_period"] > 0
    return a


def test_windows_end_at_as_of_midnight_and_cover_fixed_periods() -> None:
    ws = {w.period: w for w in windows(AS_OF)}
    end = day_window(AS_OF)[1]
    assert set(ws) == {"1d", "7d", "30d", "90d", "365d", "this_month", "last_month"}
    assert ws["30d"].end == end and ws["30d"].start == end - timedelta(days=30)
    assert ws["this_month"].start == datetime(2026, 10, 31, 15, tzinfo=UTC)  # 한국 11/1 0시
    assert ws["last_month"].start == datetime(2026, 9, 30, 15, tzinfo=UTC)
    assert ws["last_month"].end == ws["this_month"].start


@pytest.mark.parametrize("period", ["1d", "7d", "30d", "90d", "365d", "this_month", "last_month"])
def test_all_buyers_match_tally(agg: AggregateStore, sales: SalesStore, cfg, period: str) -> None:  # noqa: ANN001
    w = next(x for x in windows(AS_OF) if x.period == period)
    t, _ = tally(sales, TENANT, w.start, w.end)
    rows = agg.buyer_rows(TENANT, AS_OF)
    row = next(r for r in rows if r["period"] == period and r["dim_kind"] == "all")
    k = cfg.min_group_size
    if row["buyers_hidden"]:
        assert 0 < t.buyers < k and row["buyers"] is None
    else:
        assert row["buyers"] == t.buyers
    if not row["new_hidden"]:
        assert row["new_buyers"] == t.buyers - t.repeat_buyers
    if not row["repeat_hidden"]:
        assert row["repeat_buyers"] == t.repeat_buyers
    assert row["new_revenue"] == t.new_revenue and row["repeat_revenue"] == t.repeat_revenue


def test_attribute_cells_follow_profile_hiding(agg: AggregateStore, cfg) -> None:  # noqa: ANN001
    k = cfg.min_group_size
    rows = [r for r in agg.buyer_rows(TENANT, AS_OF) if r["dim_kind"] == "attr"]
    assert rows
    for r in rows:
        if r["buyers_hidden"]:
            assert r["buyers"] is None
        elif r["attr_cell"].endswith("="):  # 모름: 0이거나 k 이상만 숫자로
            assert r["buyers"] == 0 or r["buyers"] >= k
        else:
            assert r["buyers"] >= k  # 값 칸은 0도 숨긴다
    keys = {r["attr_cell"].split("=")[0] for r in rows}
    assert keys == {a.key for a in cfg.attributes}


def test_items_keep_small_products_as_one_hidden_row(agg: AggregateStore, cfg) -> None:  # noqa: ANN001
    k = cfg.min_group_size
    rows = [r for r in agg.buyer_rows(TENANT, AS_OF) if r["period"] == "365d"]
    products = [r for r in rows if r["dim_kind"] == "product"]
    shown = [r for r in products if r["dim_code"] != OTHER_PRODUCTS]
    assert shown and all(r["buyers"] >= k and r["title"] for r in shown)
    others = [r for r in products if r["dim_code"] == OTHER_PRODUCTS]
    assert len(others) <= 1 and all(r["buyers"] is None and r["buyers_hidden"] for r in others)
    for r in rows:
        if r["dim_kind"] in ("category", "genre") and not r["buyers_hidden"]:
            assert r["buyers"] >= k


def test_cohorts_match_retention(agg: AggregateStore, sales: SalesStore, cfg) -> None:  # noqa: ANN001
    end = day_window(AS_OF)[1]
    months = retention.cohort_months(6, end)
    keys = [a.key for a in cfg.attributes]
    with sales.segment_session(TENANT, retention.HISTORY_FROM, end, keys) as s:
        counts = s.cohort_counts(months[0].start, months[-1].end)
    expected = retention.cohort_rows(months, counts, keys, {}, cfg.min_group_size, end)
    stored = {r["cohort_month"]: r for r in agg.cohort_rows(TENANT, AS_OF) if r["attr_cell"] == ""}
    assert any(not c.suppressed for c in expected)  # 비교할 달이 실제로 있다
    assert set(stored) == {m.label for m in months}
    for c in expected:
        row = stored[c.cohort_month]
        if c.suppressed:
            assert row["first_hidden"] and row["first_buyers"] is None
            continue
        assert row["first_buyers"] == c.customers
        repeat = json.loads(row["repeat"])
        for days in ("30", "60", "90"):
            rate = getattr(c, f"r{days}")
            cell = repeat[days]
            if rate is None:
                assert cell is None or cell["hidden"]
            else:
                assert seg.ratio(cell["value"], c.customers) == rate


def test_at_risk_matches_retention(agg: AggregateStore, sales: SalesStore, cfg) -> None:  # noqa: ANN001
    end = day_window(AS_OF)[1]
    keys = [a.key for a in cfg.attributes]
    with sales.segment_session(TENANT, retention.HISTORY_FROM, end, keys) as s:
        regular = s.regular_counts(cfg.repeat_min_orders, end - timedelta(days=cfg.churn_days))
        genres = s.regular_genres(cfg.repeat_min_orders)
    shown = retention.at_risk_rows(cfg, {}, regular, genres)  # 화면은 상위 5개
    assert shown  # 비교할 묶음이 실제로 있다
    stored = {r["attr_cell"]: r for r in agg.at_risk_rows(TENANT, AS_OF)}
    assert len(stored) == len(retention.combos(cfg, {}))
    for x in shown:
        cell = ";".join(f"{k}={v}" for k, v in x.filters.items())
        row = stored[cell]
        assert (row["regulars"], row["at_risk"]) == (x.regulars, x.at_risk)
        assert row["top_genre"] == x.top_item_label
    for row in stored.values():
        assert row["churn_days"] == cfg.churn_days
        if row["regulars_hidden"]:
            assert row["regulars"] is None and row["at_risk"] is None and row["top_genre"] is None


def test_no_snapshot_table_has_buyer_ids(agg: AggregateStore) -> None:
    for table in ("agg_buyers_period", "agg_cohorts", "agg_at_risk"):
        cols = set(agg.columns(table))
        assert cols and not {"buyer_id", "order_id", "customer_id"} & cols and "tenant_id" in cols


def test_small_company_is_hidden_when_saved() -> None:
    """구매자 3명(k=5 미만)뿐인 회사: 전체·속성·코호트 숫자가 저장 때부터 숨겨진다."""
    s = SalesStore(":memory:")
    when = NOW - timedelta(days=3)
    s.upsert(TENANT, [order(f"o{i}", f"b{i}", when, [line("p")]) for i in range(3)], NOW)
    s.upsert_customers(TENANT, [
        CustomerRow(buyer_id=f"b{i}", joined_at=when, updated_at=when,
                    attributes={"gender": "female"})
        for i in range(3)
    ], NOW)  # fmt: skip
    cfg = catalog.load_catalog("catbooks").sales
    buyers, cohorts, at_risk = build_snapshots(s, TENANT, AS_OF, cfg, "r", NOW)
    all7 = next(r for r in buyers if r.period == "7d" and r.dim_kind == "all")
    assert all7.buyers.hidden and all7.buyers.value is None
    assert all7.new_buyers.hidden and all7.repeat_buyers.hidden
    female = next(r for r in buyers if r.period == "7d" and r.attr_cell == {"gender": "female"})
    assert female.buyers.hidden
    assert all(c.first_buyers.hidden or c.first_buyers.value == 0 for c in cohorts)
    assert all(r.regulars.hidden for r in at_risk)
    # k 미만 상품은 이름 없이 한 줄로
    assert not any(r.dim_kind == "product" and r.dim_code == "p" for r in buyers)


def test_unknown_attribute_cell_uses_empty_value() -> None:
    assert UNKNOWN == "" and OTHER_PRODUCTS == ""


def test_prune_keeps_recent_days_and_month_ends(sales: SalesStore, cfg) -> None:  # noqa: ANN001
    a = AggregateStore(":memory:")
    for d in (date(2026, 8, 30), date(2026, 8, 31), date(2026, 10, 1), date(2026, 11, 15)):
        b, c, r = build_snapshots(sales, TENANT, d, cfg, "r", NOW)
        a.replace_snapshots(TENANT, d, b, c, r)
    a.prune_snapshots(TENANT, date(2026, 11, 15))
    assert a.snapshot_days(TENANT) == ["2026-08-31", "2026-10-01", "2026-11-15"]  # 8/30만 지움


def test_companies_snapshots_are_kept_apart(sales: SalesStore, cfg) -> None:  # noqa: ANN001
    a = AggregateStore(":memory:")
    b, c, r = build_snapshots(sales, TENANT, AS_OF, cfg, "r", NOW)
    a.replace_snapshots(TENANT, AS_OF, b, c, r)
    assert a.buyer_rows("other-co", AS_OF) == []
    a.replace_snapshots("other-co", AS_OF, [], [], [])
    assert len(a.buyer_rows(TENANT, AS_OF)) == len(b)  # 다른 회사 바꿔 넣기가 지우지 않음
