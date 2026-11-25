"""매출 분석 집계 읽기 (3-2 ①, 설계 1.2·2.4·6.2).

- `DATA_SALES_READ=aggregate`면 다 지난 날은 일별 집계, 오늘 0시~지금과 비교 기간 끝날만 원본으로
  센다. 화면에 나가는 매출 분석 출력이 원본 경로와 같다(읽은 행 수만 다름)
- 오늘 하루(꽉 찬 날 없음)·집계가 그 날들을 덮지 않음(계산 전, 기준일이 모자람)·스위치 꺼짐은
  원본 경로 그대로
- 집계 파일을 못 읽어도 원본으로 계산한다(화면은 그대로)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRefund, SalesOrderRow
from data_agent import catalog
from data_agent.aggregate import daily, read
from data_agent.aggregate.store import DB_ENV, AggregateStore
from data_agent.sales_collect import collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_sql import tally
from data_agent.sales_store import SalesStore
from data_agent.skills.sales_analysis import analyze

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
    """경계 주문: 할인 몫 불일치·줄 없음·취소·실패·옛 주문 환불·유입 경로·오늘 접수·오늘 환불."""
    days = [NOW - timedelta(days=d, hours=2) for d in range(1, 40)]
    today = NOW - timedelta(hours=1)  # 한국 11/16 11:00 (덜 끝난 날)
    return [
        order("e1", "eb1", days[2], [line("p1"), line("p2", price=30000)], coupon_discount=3001,
              src_campaign="winter"),
        order("e2", "eb2", days[0], [], items_amount=7000, points_used=500),
        order("e3", "eb3", days[3], [line("p1")], status="cancelled"),
        order("e4", "eb4", days[1], [line("p1")], payment_status="failed"),
        order("e6", "eb6", days[3], [line("p1", coupon=1000), line("p3", price=25000)],
              coupon_discount=1000, refunds=[SalesOrderRefund(amount=7777, refunded_at=days[1])]),
        order("e7", "eb7", NOW - timedelta(days=50), [line("p2")],
              refunds=[SalesOrderRefund(amount=4000, refunded_at=days[0])]),
        order("e8", "eb8", days[4], [line("p3")], status="cancelled",
              refunds=[SalesOrderRefund(amount=9999, refunded_at=days[1])]),
        order("e11", "eb10", days[1], [line("p3", cat=None)], src_source="naver", src_medium="cpc"),
        order("t1", "eb1", today, [line("p1", price=15000)], src_source="naver", src_medium="cpc"),
        order("t2", "tb2", days[5], [line("p2")],  # 옛 주문을 오늘 환불
              refunds=[SalesOrderRefund(amount=2500, refunded_at=today)]),
    ]  # fmt: skip


def filled() -> SalesStore:
    store = SalesStore(":memory:")
    # 시험이 보는 기간은 길어야 30일 → 데모 설정의 90일 대신 40일 (11/25 CI 시간 줄이기)
    cfg = load_config(FAKE_DIR / "catbooks.toml").model_copy(update={"period_days": 40})
    src = FakeSalesSource(cfg, clock=lambda: NOW)
    assert asyncio.run(collect_once(src, store, source_name="fake", now=NOW)).errors == []
    store.upsert(TENANT, edge_orders(), NOW)
    return store


@pytest.fixture
def cat():  # noqa: ANN201
    return catalog.load_catalog("catbooks")


@pytest.fixture
def world(tmp_path, cat, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    """원본 + 그날 하루 계산을 마친 집계 파일. 스위치는 시험마다 켠다."""
    path = tmp_path / "aggregates.db"
    monkeypatch.setenv(DB_ENV, str(path))
    monkeypatch.delenv(read.READ_ENV, raising=False)
    sales, agg = filled(), AggregateStore(path)
    run = daily.run_daily(sales, agg, TENANT, cat.sales, NOW, run_id="d1")
    assert run is not None and run.status == "done"
    agg.close()
    return sales


def _out(sales: SalesStore, cat, period: str, now: datetime = NOW) -> dict:  # noqa: ANN001
    out = analyze(sales, TENANT, period, cat.sales, cat.id, now).model_dump(mode="json")
    out["basis"].pop("rows_read")  # 읽은 행 수는 경로마다 다르다(원본 주문 수 ↔ 집계 행 수)
    return out


@pytest.mark.parametrize("period", ["7d", "30d"])
def test_sales_analysis_is_the_same_from_aggregates(world, cat, period, monkeypatch) -> None:  # noqa: ANN001
    want = _out(world, cat, period)
    used: list[bool] = []
    real = read.mixed_tally
    monkeypatch.setattr(
        read, "mixed_tally", lambda *a: used.append((r := real(*a)) is not None) or r
    )
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    got = _out(world, cat, period)
    assert want["status"] == "ok" and want["kpis"]  # 비교할 숫자가 있다
    assert used == [True, True]  # 이번 기간·비교 기간 둘 다 집계 경로
    assert got == want


def test_mixed_tally_reads_daily_rows_for_full_days_and_source_for_today(world) -> None:  # noqa: ANN001
    start = datetime(2026, 11, 9, 15, tzinfo=UTC)  # 한국 11/10 0시 (7일)
    want, want_created = tally(world, TENANT, start, NOW)
    agg = AggregateStore(read.db_path())
    got = read.mixed_tally(world, agg, TENANT, start, NOW)
    assert got is not None
    t, created = got
    assert created == want_created
    for name in ("revenue", "orders", "coupon_orders", "buyers", "repeat_buyers",
                 "new_revenue", "repeat_revenue"):  # fmt: skip
        assert getattr(t, name) == getattr(want, name), name
    for name in ("by_category", "by_product", "product_orders", "by_channel", "channel_orders"):
        assert {k: v for k, v in getattr(t, name).items() if v} == {
            k: v for k, v in getattr(want, name).items() if v
        }, name
    assert ("naver", "cpc") in t.channel_orders  # 빈 유입은 None, 있는 유입은 그대로
    agg.close()


def test_today_and_uncovered_days_stay_on_source(world, cat, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    agg = AggregateStore(read.db_path())
    today = datetime(2026, 11, 15, 15, tzinfo=UTC)  # 한국 11/16 0시
    assert read.mixed_tally(world, agg, TENANT, today, NOW) is None  # 꽉 찬 날 없음
    later = NOW + timedelta(days=2)  # 11/17까지 덮어야 하는데 계산은 11/15까지
    assert read.mixed_tally(world, agg, TENANT, today - timedelta(days=6), later) is None
    assert read.mixed_tally(world, agg, "other-co", today - timedelta(days=6), NOW) is None
    agg.close()
    monkeypatch.delenv(read.READ_ENV)
    want = _out(world, cat, "today")
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    assert _out(world, cat, "today") == want


def test_unreadable_aggregate_file_falls_back_to_source(world, cat, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    want = _out(world, cat, "7d")
    broken = tmp_path / "broken"
    broken.mkdir()  # 폴더라 DB로 열 수 없다
    monkeypatch.setenv(DB_ENV, str(broken))
    monkeypatch.setenv(read.READ_ENV, read.READ_AGGREGATE)
    assert _out(world, cat, "7d") == want


def test_switch_is_off_unless_aggregate() -> None:
    assert not read.enabled({})
    assert not read.enabled({read.READ_ENV: "1"})
    assert read.enabled({read.READ_ENV: " aggregate "})
