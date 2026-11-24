"""기간 스냅샷 2 (3-1a 뒷부분 2, 설계 3.6~3.8·5.4): 함께 산 상품·즐겨 사는 것·채널 가치.

같은 기간이면 지금 화면 기능(basket_pairs.basket·segment_analysis.favorites·channel_value)과 같은
숫자를 저장하는지, 숨김(k)과 id 없음, 지난 13개월 월말 기준일을 본다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRefund, SalesOrderRow
from data_agent import catalog
from data_agent.aggregate.backfill import backfill, month_ends
from data_agent.aggregate.build_day import day_window
from data_agent.aggregate.store import AggregateStore
from data_agent.sales_collect import collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_store import SalesStore
from data_agent.skills import basket_pairs, channel_value
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


AS_OF = date(2026, 11, 15)
SKILL_NOW = day_window(AS_OF)[1] - timedelta(seconds=1)  # 화면 기간 = 기준일까지 꽉 찬 N일


@pytest.fixture(scope="module")
def sales() -> SalesStore:
    return filled()


@pytest.fixture(scope="module")
def cfg():  # noqa: ANN201
    return catalog.load_catalog("catbooks").sales


@pytest.fixture(scope="module")
def agg(sales: SalesStore, cfg) -> AggregateStore:  # noqa: ANN001
    a = AggregateStore(":memory:")
    run = backfill(sales, a, TENANT, cfg, NOW, run_id="run_m")
    assert run.status == "done" and run.rows["agg_pairs"] > 0
    return a


@pytest.mark.parametrize("period", ["30d", "90d"])
@pytest.mark.parametrize("level", ["genre", "product"])
def test_pairs_match_basket_skill(agg, sales, cfg, period: str, level: str) -> None:  # noqa: ANN001
    out = basket_pairs.basket(sales, TENANT, level, period, {}, 20, cfg, "catbooks", SKILL_NOW)
    assert out.status == "ok"
    want = {(p.a_code, p.b_code, p.together_buyers, p.a_buyers, p.b_buyers)
            for p in out.pairs if not p.suppressed}  # fmt: skip
    rows = [r for r in agg.more_rows("agg_pairs", TENANT, AS_OF)
            if r["period"] == period and r["level"] == level and r["attr_cell"] == ""]  # fmt: skip
    got = {
        (r["code_a"], r["code_b"], r["together_buyers"], r["a_buyers"], r["b_buyers"]) for r in rows
    }
    assert got == want
    slots = sum(1 for p in out.pairs if p.suppressed)
    assert all(r["hidden_slots"] == slots for r in rows)


def test_favorites_match_skill(agg, sales, cfg) -> None:  # noqa: ANN001
    a = cfg.attributes[0]
    checked = 0
    for v in a.values:
        f = {a.key: v}
        out = seg.favorites(sales, TENANT, f, "90d", 20, cfg, "catbooks", SKILL_NOW)
        rows = [r for r in agg.more_rows("agg_segment_profile", TENANT, AS_OF)
                if r["period"] == "90d" and r["attr_cell"] == f"{a.key}={v}"
                and (r["item_code"] != "" or r["buyers_hidden"])]  # 묶음 크기 줄 빼고  # fmt: skip
        if out.status != "ok" or out.segment_suppressed:
            assert all(r["buyers_hidden"] for r in rows)
            continue
        want = {(i.kind, i.code, None if i.suppressed else i.buyers) for i in out.internal}
        got = {(r["item_level"], r["item_code"], r["buyers"]) for r in rows}
        assert got == want
        checked += 1
    assert checked  # 실제로 비교한 묶음이 있다


def test_channel_value_matches_skill(cfg) -> None:  # noqa: ANN001
    """첫 구매가 90일보다 오래된 새 고객(경로마다 6명)으로 화면 기능의 묶기·숨김과 비교."""
    sales = SalesStore(":memory:")
    first = NOW - timedelta(days=200)
    rows_in = []
    for i in range(18):  # 네이버·모르는 출처(기타 경로)·직접 6명씩
        src = "naver" if i < 6 else ("ghost" if i < 12 else None)
        rows_in.append(order(f"f{i}", f"c{i}", first, [line("p", price=10000)], src_source=src))
        if i % 2 == 0:  # 재구매 (90일 안)
            rows_in.append(order(f"s{i}", f"c{i}", first + timedelta(days=10), [line("p")]))
    sales.upsert(TENANT, rows_in, NOW)
    from data_agent.aggregate.build_more import build_more

    _, _, stored = build_more(sales, TENANT, AS_OF, cfg, "r", NOW)
    # 화면(channel_value 기능)이 쓰는 같은 세션 숫자 → build_rows (작은 데이터라 화면 관문은 건너뜀)
    end = day_window(AS_OF)[1]
    with sales.segment_session(TENANT, datetime(2000, 1, 1, tzinfo=UTC), end, []) as s:
        raw, refunds, _ = s.channel_value(
            end - timedelta(days=365), end, cfg.value_horizon_days, end
        )
    want = channel_value.build_rows(raw, refunds, cfg)
    rows = {r.source_label: r for r in stored if r.period == "365d"}
    assert want and set(rows) == {r.label for r in want}
    assert any(not x.suppressed for x in want)
    for x in want:
        r = rows[x.label]
        if x.suppressed:
            assert r.new_buyers.hidden and r.horizon_net is None
        else:
            assert r.new_buyers.value == x.new_customers
            assert max(0, round(r.horizon_net / r.new_buyers.value)) == x.revenue_per_customer


def test_month_end_snapshots_are_filled(agg) -> None:  # noqa: ANN001
    days = agg.snapshot_days(TENANT)
    assert "2026-11-15" in days and "2026-10-31" in days
    assert all(d in days for d in [x.isoformat() for x in month_ends(AS_OF, 13)] if d >= min(days))
    assert month_ends(AS_OF, 2) == [date(2026, 9, 30), date(2026, 10, 31)]


def test_new_tables_have_no_ids_and_keep_tenant(agg) -> None:  # noqa: ANN001
    for table in ("agg_pairs", "agg_segment_profile", "agg_channel_value"):
        cols = set(agg.columns(table))
        assert cols and not {"buyer_id", "order_id", "customer_id"} & cols and "tenant_id" in cols
    assert agg.more_rows("agg_pairs", "other-co", AS_OF) == []
