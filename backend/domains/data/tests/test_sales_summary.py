"""구매자 요약 표 (11/18): 분석이 요약 표로 세도 예전 SQL과 결과가 같은지.

같은 저장소에서 `use_summary`를 켜고 끄며 skill마다 견준다. 자료 = 가짜 캣북스 출처 + 경계 주문
(취소·결제 실패, 할인 몫 불일치, 고객 기록 없는 구매자, 카탈로그에 없는 속성 값, 환불, 결제
완료였다가 취소로 바뀐 주문, 구매자가 바뀐 주문). 성능(주문 30만 건)은 `DATA_PERF=1`일 때만.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRefund, SalesOrderRow
from data_agent import catalog, sales_reads, sales_summary
from data_agent.sales_collect import collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_sql import tally
from data_agent.sales_store import SalesStore
from data_agent.skills import basket_pairs as bp
from data_agent.skills import channel_value as cv
from data_agent.skills import event_proposal as ep
from data_agent.skills import retention as rt
from data_agent.skills import segment_analysis as seg
from data_agent.skills.sales_analysis import analyze

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00
FAR = datetime(2100, 1, 1, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


NOVEL = SalesOrderLine(product_id="sx_p1", category_code="book", title="경계 소설", qty=2,
                       unit_price=12000, genre_code="novel")  # fmt: skip
ESSAY = SalesOrderLine(product_id="sx_p2", category_code="book", title=None, qty=1,
                       unit_price=9000, genre_code="essay")  # fmt: skip
PLAIN = SalesOrderLine(product_id="sx_p3", category_code=None, title="분류 없음", qty=1,
                       unit_price=5000)  # fmt: skip


def order(n: str, buyer: str, lines: list[SalesOrderLine], days: float = 2, **kw) -> SalesOrderRow:  # noqa: ANN003
    at = NOW - timedelta(days=days)
    base = {"order_id": f"sx{n}", "buyer_id": buyer, "status": "paid", "created_at": at,
            "updated_at": at, "items_amount": sum(ln.qty * ln.unit_price for ln in lines),
            "payment_status": "succeeded", "lines": lines}  # fmt: skip
    return SalesOrderRow.model_validate({**base, **kw})


def edge_orders() -> list[SalesOrderRow]:
    refund = SalesOrderRefund(refund_id="r1", amount=4000, refunded_at=NOW - timedelta(days=1))
    return [
        order("1", "sx_b1", [NOVEL, ESSAY], coupon_discount=3000),  # 줄 몫 0 ≠ 할인 → 배분
        order("2", "sx_b2", [NOVEL], status="cancelled"),
        order("3", "sx_b3", [ESSAY], payment_status="failed"),
        order("4", "sx_b4", [PLAIN.model_copy(update={"points_share": 500}), ESSAY],
              points_used=500),  # fmt: skip
        order("5", "sx_b5", [NOVEL]),  # 고객 기록 없는 구매자
        order("6", "sx_b6", [ESSAY, NOVEL]),  # 카탈로그에 없는 속성 값
        order("7", "sx_b1", [NOVEL, ESSAY], days=40, refunds=[refund]),  # 환불
        order("8", "sx_b1", [], days=100),  # 줄 없는 주문
        order("9", "sx_b7", [ESSAY], days=5),  # 나중에 취소로 바뀐다
        order("10", "sx_b7", [NOVEL], days=70),
        order("11", "sx_m1", [NOVEL], days=3),  # 나중에 구매자가 sx_m2로 바뀐다
    ]


def later_changes() -> list[SalesOrderRow]:
    return [
        order("9", "sx_b7", [ESSAY], days=5, status="cancelled"),
        order("11", "sx_m2", [NOVEL], days=3),
    ]


def edge_customers() -> list[CustomerRow]:
    day = NOW - timedelta(days=300)
    attrs = {
        "sx_b1": {"age_band": "20s", "gender": "female"}, "sx_b4": {},
        "sx_b6": {"age_band": "70s", "gender": "female", "occupation": "x"},
        "sx_b7": {"age_band": "30s", "gender": "male"}, "sx_m2": {"age_band": "20s"},
    }  # fmt: skip
    return [CustomerRow(buyer_id=b, joined_at=day, updated_at=day, attributes=a)
            for b, a in attrs.items()]  # fmt: skip


def filled(path: str | Path = ":memory:") -> SalesStore:
    store = SalesStore(path)
    src = FakeSalesSource(load_config(FAKE_DIR / "catbooks.toml"), clock=lambda: NOW)
    assert run(collect_once(src, store, source_name="fake", now=NOW)).errors == []
    store.upsert(TENANT, edge_orders(), NOW)
    store.upsert(TENANT, later_changes(), NOW)  # 증분: 그 구매자 줄만 다시 센다
    store.upsert_customers(TENANT, edge_customers(), NOW)
    return store


@pytest.fixture(scope="module")
def store() -> SalesStore:
    return filled()


@pytest.fixture(scope="module")
def cat():  # noqa: ANN201
    return catalog.load_catalog("catbooks")


def same(store: SalesStore, compute):  # noqa: ANN001, ANN201
    """요약 표로 센 값 == 예전 SQL로 센 값."""
    store.use_summary = True
    got = compute()
    store.use_summary = False
    try:
        want = compute()
    finally:
        store.use_summary = True
    assert got == want
    return got


def dump(store: SalesStore) -> tuple[list, list]:
    with store.reading() as conn:
        return (
            [tuple(r) for r in conn.execute("SELECT * FROM sales_buyer_summary ORDER BY 1, 2")],
            [tuple(r) for r in conn.execute("SELECT * FROM sales_buyer_items ORDER BY 1, 2, 3, 4")],
        )


# ---------------------------------------------------------------- 요약 표 자체


def test_summary_is_used_and_incremental_equals_rebuild(store: SalesStore) -> None:
    assert store.summary_ready(TENANT)
    w = seg.segment_window("30d", NOW)
    with store.segment_session(TENANT, w.start, w.end, []) as s:
        assert s.sum_window and not s.sum_all  # 30일 창은 전체 기록이 아니다
    with store.segment_session(TENANT, rt.HISTORY_FROM, NOW, []) as s:
        assert s.sum_window and s.sum_all
    incremental = dump(store)
    other = filled()
    other.rebuild_summary(TENANT)
    assert dump(other) == incremental  # 수집하며 채운 것 = 처음부터 채운 것
    summary, items = incremental
    buyers = {r[1]: r for r in summary}
    assert "sx_b2" not in buyers and "sx_b3" not in buyers  # 취소·결제 실패만 있는 구매자
    assert "sx_m1" not in buyers and "sx_m2" in buyers  # 구매자가 바뀐 주문
    b7 = buyers["sx_b7"]
    assert b7[5] == 1 and b7[3] is None  # 취소로 바뀐 주문은 빠지고 결제 1건만
    assert any(r[1] == "sx_b1" and r[2] == "category" and r[3] == "book" for r in items)


def test_revenue_follows_the_sales_definition(store: SalesStore) -> None:
    """요약 표 매출 합 = 매출 분석(전체 기간) 매출 (할인 배분·환불·취소 반영)."""
    t, _ = tally(store, TENANT, rt.HISTORY_FROM, FAR)
    with store.reading() as conn:
        total = conn.execute(
            "SELECT SUM(revenue) FROM sales_buyer_summary WHERE tenant_id = ?", (TENANT,)
        ).fetchone()[0]
        b1 = conn.execute(
            "SELECT revenue, paid_orders FROM sales_buyer_summary WHERE buyer_id = 'sx_b1'"
        ).fetchone()
    assert total == t.revenue
    # sx_b1: 33,000 − 3,000 할인 + 33,000 − 4,000 환불 + 0 (줄 없는 주문)
    assert tuple(b1) == (33000 - 3000 + 33000 - 4000, 3)


# ---------------------------------------------------------------- skill마다 예전 SQL과 같다

PROFILES = [("genre", "novel", [], "90d"), ("genre", "essay", ["gender"], "30d"),
            ("category", "book", [], "7d"), ("product", "sx_p1", ["age_band"], "90d"),
            ("genre", "novel", [], "1d"), ("category", "stationery", [], "90d")]  # fmt: skip


@pytest.mark.parametrize(("kind", "code", "keys", "period"), PROFILES)
def test_profile(store, cat, kind, code, keys, period) -> None:  # noqa: ANN001, PLR0913
    out = same(store, lambda: seg.profile(store, TENANT, kind, code, keys, period, cat.sales,
                                          cat.id, NOW))  # fmt: skip
    assert out.status == "ok"


FAVORITES = [({"age_band": "20s", "gender": "female"}, "90d"), ({"occupation": "employee"}, "30d"),
             ({"age_band": "40s", "gender": "male", "occupation": "student"}, "30d"),
             ({"age_band": "50s_plus"}, "7d")]  # fmt: skip


@pytest.mark.parametrize(("filters", "period"), FAVORITES)
def test_favorites(store, cat, filters, period) -> None:  # noqa: ANN001
    same(store, lambda: seg.favorites(store, TENANT, filters, period, 10, cat.sales, cat.id, NOW))


def test_coverage(store, cat) -> None:  # noqa: ANN001
    out = same(store, lambda: seg.coverage_buyers(store, TENANT, ["age_band", "gender"],
                                                  cat.sales, cat.id, NOW))  # fmt: skip
    assert any(r.buyers for r in out.rows)


@pytest.mark.parametrize(
    ("level", "filters", "period"),
    [("genre", {}, "90d"), ("product", {}, "90d"), ("genre", {"age_band": "20s"}, "30d"),
     ("product", {"gender": "female"}, "7d")],
)  # fmt: skip
def test_basket_pairs(store, cat, level, filters, period) -> None:  # noqa: ANN001
    same(store, lambda: bp.basket(store, TENANT, level, period, filters, 20, cat.sales, cat.id,
                                  NOW))  # fmt: skip


@pytest.mark.parametrize(("cohorts", "filters"), [(3, {}), (6, {"age_band": "20s"}), (1, {})])
def test_retention(store, cat, cohorts, filters) -> None:  # noqa: ANN001
    same(store, lambda: rt.retention(store, TENANT, cohorts, filters, cat.sales, cat.id, NOW))


@pytest.mark.parametrize("period", ["365d", "90d", "30d"])
def test_channel_value(store, cat, period) -> None:  # noqa: ANN001
    same(store, lambda: cv.channel_value(store, TENANT, period, cat.sales, cat.id, NOW))


@pytest.mark.parametrize("period", ["today", "7d", "30d"])
def test_sales_analysis_new_vs_repeat(store, cat, period) -> None:  # noqa: ANN001
    out = same(store, lambda: analyze(store, TENANT, period, cat.sales, cat.id, NOW))
    assert out.customers is not None and out.customers.repeat_buyers > 0


def test_event_proposal_candidates(store, cat) -> None:  # noqa: ANN001
    base = same(store, lambda: ep.read_base(store, TENANT, cat.sales, cat.id, NOW))
    assert base.candidates


# ---------------------------------------------------------------- 요약 표를 쓰지 않는 경우


def test_payment_after_the_window_end_falls_back(cat) -> None:  # noqa: ANN001
    """기간 끝(지금) 뒤 결제가 있으면 "마지막 결제 ≥ 시작"이 기간 조건과 달라 예전 SQL로 센다."""
    store = filled()
    store.upsert(TENANT, [order("f1", "sx_b5", [ESSAY], days=-1)], NOW)  # 하루 뒤 접수
    w = seg.segment_window("30d", NOW)
    with store.segment_session(TENANT, w.start, w.end, []) as s:
        assert not s.sum_window and not s.sum_all
    same(store, lambda: seg.profile(store, TENANT, "genre", "essay", [], "30d", cat.sales,
                                    cat.id, NOW))  # fmt: skip


def _forget_summary(path: Path) -> None:
    """요약 표가 없던 예전 파일처럼: 표를 비우고 준비됨 줄을 지운다."""
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM sales_buyer_summary")
    conn.execute("DELETE FROM sales_buyer_items")
    conn.execute("DELETE FROM sync_state WHERE tenant_id = ?", (sales_summary.SUMMARY_TENANT,))
    conn.commit()
    conn.close()


def test_old_file_is_not_ready_until_filled(tmp_path: Path, cat) -> None:  # noqa: ANN001
    path = tmp_path / "sales.db"
    store = filled(path)
    want = dump(store)
    store.close()
    _forget_summary(path)
    old = SalesStore(path)  # 주문이 있는 파일이라 새 저장소로 보지 않는다
    try:
        assert not old.summary_ready(TENANT)
        w = seg.segment_window("90d", NOW)
        with old.segment_session(TENANT, w.start, w.end, []) as s:
            assert not s.sum_window  # 준비 전에는 예전 SQL
        sql = seg.profile(old, TENANT, "genre", "novel", [], "90d", cat.sales, cat.id, NOW)
        assert run(sales_reads.ensure_summary(old, TENANT)) is True  # 데이터 AI 시작 때와 같다
        assert old.summary_ready(TENANT) and dump(old) == want
        assert run(sales_reads.ensure_summary(old, TENANT)) is False  # 이미 준비됨
        assert seg.profile(old, TENANT, "genre", "novel", [], "90d", cat.sales, cat.id, NOW) == sql
        assert old.sync_state(TENANT).source == "fake"  # 준비됨 줄이 판매 출처로 보이지 않는다
    finally:
        old.close()


# ---------------------------------------------------------------- 성능 (DATA_PERF=1)

# 처음 보는 조건 7가지 (화면 칸 4개: 물어보기·누가 사나·함께 사는 것·고객 구성 + 재구매·경로·매출)
CONDITIONS = [
    {"filters": {"age_band": "40s", "gender": "male", "occupation": "student"}, "genre": "it",
     "period": "30d"},
    {"filters": {"age_band": "20s", "gender": "female"}, "genre": "essay", "period": "7d"},
    {"filters": {"occupation": "employee"}, "genre": "business", "period": "90d"},
    {"filters": {"age_band": "50s_plus"}, "genre": "hobby", "period": "1d"},
    {"filters": {"gender": "male"}, "genre": "comics", "period": "30d"},
    {"filters": {"age_band": "10s", "occupation": "student"}, "genre": "novel", "period": "90d"},
    {"filters": {"age_band": "30s", "gender": "no_answer"}, "genre": "it", "period": "7d"},
]  # fmt: skip


def skills(store: SalesStore, cat, c: dict) -> dict:  # noqa: ANN001
    s, period = cat.sales, c["period"]
    sales_period = {"1d": "today", "7d": "7d"}.get(period, "30d")
    return {
        "segment_favorites": lambda: seg.favorites(store, TENANT, c["filters"], period, 10, s,
                                                   cat.id, NOW),
        "segment_profile": lambda: seg.profile(store, TENANT, "genre", c["genre"], [], period, s,
                                               cat.id, NOW),
        "basket_pairs": lambda: bp.basket(store, TENANT, "genre", period, c["filters"], 10, s,
                                          cat.id, NOW),
        "market_coverage": lambda: seg.coverage_buyers(store, TENANT, ["age_band", "gender"], s,
                                                       cat.id, NOW),
        "retention": lambda: rt.retention(store, TENANT, 3, c["filters"], s, cat.id, NOW),
        "channel_value": lambda: cv.channel_value(store, TENANT, "365d", s, cat.id, NOW),
        "sales_analysis": lambda: analyze(store, TENANT, sales_period, s, cat.id, NOW),
    }  # fmt: skip


@pytest.mark.skipif(os.environ.get("DATA_PERF") != "1", reason="DATA_PERF=1일 때만 (느림)")
def test_perf_first_time_conditions_on_300k_orders(tmp_path, cat) -> None:  # noqa: ANN001
    """캐시 없이(skill 함수를 바로) 처음 보는 조건 7가지. 표는 PR 본문에 옮긴다."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))  # 같은 폴더의 30만 건 저장소 도우미
    from test_segment_sql import PERF_ORDERS, big_store

    store = big_store(str(tmp_path / "big.db"), PERF_ORDERS, customers=True)
    started = time.perf_counter()
    filled_ = store.rebuild_summary(TENANT)
    print(f"\n요약 표 처음 채우기: {time.perf_counter() - started:.2f}초 "
          f"(구매자 {filled_.buyers:,}명, 항목 {filled_.items:,}줄)")  # fmt: skip
    worst: dict[str, float] = {}
    for i, c in enumerate(CONDITIONS, 1):
        row = []
        for name, fn in skills(store, cat, c).items():
            t0 = time.perf_counter()
            out = fn()
            took = time.perf_counter() - t0
            worst[name] = max(worst.get(name, 0.0), took)
            row.append(f"{name} {took:.2f}")
            status = out[0].status if isinstance(out, tuple) else out.status
            assert status == "ok", (name, c)
        # 화면 칸 4개 동시 호출 (물어보기·누가 사나·함께 사는 것·고객 구성)
        four = [skills(store, cat, c)[k] for k in ("segment_favorites", "segment_profile",
                                                    "basket_pairs", "market_coverage")]  # fmt: skip
        t0 = time.perf_counter()
        with ThreadPoolExecutor(4) as pool:
            for f in [pool.submit(fn) for fn in four]:
                f.result()
        both = time.perf_counter() - t0
        worst["four_at_once"] = max(worst.get("four_at_once", 0.0), both)
        print(f"조건 {i}: " + ", ".join(row) + f", 칸 4개 동시 {both:.2f}")
    print("가장 늦은 시간: " + ", ".join(f"{k} {v:.2f}" for k, v in worst.items()))
