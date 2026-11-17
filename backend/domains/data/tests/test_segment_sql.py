"""고객·시장 분석을 SQL 집계로 바꾼 뒤에도 숫자가 같은지 (11/17).

예전 경로 = 기간 안 주문을 전부 파이썬으로 읽어(`orders_created`·`customers`) 세던 11/16 코드.
여기서는 그 계산을 `PythonPath`(SalesStore의 집계 읽기를 예전 파이썬 계산으로 바꾼 것)로 옮겨 두고,
같은 가짜 데이터를 넣은 두 저장소에서 segment_profile·segment_favorites·market_coverage·
event_proposal 후보가 똑같이 나오는지 본다.

성능 시험(주문 30만 행)은 느려서 `DATA_PERF=1`일 때만 돈다.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from data_agent import catalog
from data_agent.sales_clean import counts_as_revenue
from data_agent.sales_collect import collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_quality import kst_day, quality
from data_agent.sales_store import SalesStore, StoredOrder, customers_source
from data_agent.skills import event_proposal as ep
from data_agent.skills import segment_analysis as seg

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 예전 경로 (11/16 파이썬 계산)


def item_buyers(paid: Iterable[StoredOrder]) -> dict[tuple[str, str], set[str]]:
    found: dict[tuple[str, str], set[str]] = defaultdict(set)
    for order in paid:
        for line in order.lines:
            if line.genre_code:
                found[("genre", line.genre_code)].add(order.buyer_id)
            if line.category_code:
                found[("category", line.category_code)].add(order.buyer_id)
            if line.product_id:
                found[("product", line.product_id)].add(order.buyer_id)
    return found


def titles_of(paid: Iterable[StoredOrder], *, genre_only: bool = False) -> dict[str, str]:
    titles: dict[str, str] = {}
    for order in paid:
        for line in order.lines:
            if genre_only and not line.genre_code:
                continue
            if line.product_id and line.title and line.product_id not in titles:
                titles[line.product_id] = line.title
    return titles


class OldSession:
    """예전 계산: 기간 안 주문 전부를 파이썬으로 올려 센다 (`SegmentSession`과 같은 질문)."""

    def __init__(self, store: PythonPath, t: str, s: datetime, e: datetime, keys) -> None:  # noqa: ANN001
        self.store, self.t, self.keys = store, t, list(keys)
        self.paid = store.paid_of(t, s, e)
        self.attrs = {c.buyer_id: c.attributes for c in store.customers(t)}

    def _cells(self, buyers: Iterable[str]) -> dict[tuple, int]:
        return dict(Counter(tuple(self.attrs.get(b, {}).get(k) for k in self.keys)
                            for b in buyers))  # fmt: skip

    def buyer_cells(self):  # noqa: ANN201
        return self._cells({o.buyer_id for o in self.paid})

    def active_cells(self, s: datetime, e: datetime):  # noqa: ANN201
        return self._cells({o.buyer_id for o in self.store.paid_of(self.t, s, e)})

    def item_cells(self, kind: str, code: str):  # noqa: ANN201
        return self._cells(item_buyers(self.paid).get((kind, code), set()))

    def item_counts(self, filters: dict[str, str]):  # noqa: ANN201
        overall = {o.buyer_id for o in self.paid}
        attrs = self.attrs
        segment = {b for b in overall
                   if all(attrs.get(b, {}).get(k) == v for k, v in filters.items())}  # fmt: skip
        return [(k, code, len(who), len(who & segment))
                for (k, code), who in item_buyers(self.paid).items()]  # fmt: skip

    def titles(self, *, genre_only: bool = False):  # noqa: ANN201
        return titles_of(self.paid, genre_only=genre_only)

    def genre_cells(self):  # noqa: ANN201
        return {code: self._cells(who)
                for (k, code), who in item_buyers(self.paid).items() if k == "genre"}  # fmt: skip

    def genre_products(self):  # noqa: ANN201
        who: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for order in self.paid:
            for line in order.lines:
                if line.genre_code and line.product_id:
                    who[line.genre_code][line.product_id].add(order.buyer_id)
        return {g: {p: len(b) for p, b in ps.items()} for g, ps in who.items()}

    def genre_nets(self):  # noqa: ANN201
        return {}, self.paid  # 모든 주문을 line_nets 배분으로 (예전 genre_aov)


class PythonPath(SalesStore):
    """집계 읽기를 예전처럼 주문 전체를 파이썬으로 올려 센다 (비교 기준)."""

    def paid_of(self, t: str, s: datetime, e: datetime) -> list[StoredOrder]:
        return [o for o in self.orders_created(t, s, e)
                if counts_as_revenue(o.status, o.payment_status)]  # fmt: skip

    @contextmanager
    def segment_session(self, t, s, e, keys):  # noqa: ANN001, ANN201
        yield OldSession(self, t, s, e, keys)

    def order_counts(self, t, s, e):  # noqa: ANN001, ANN201
        return len(self.orders_created(t, s, e)), len(self.paid_of(t, s, e))

    def order_days(self, t, s, e):  # noqa: ANN001, ANN201
        return {kst_day(o.created_at).isoformat() for o in self.orders_created(t, s, e)}

    def unknown_line_codes(self, t, s, e, categories, genres):  # noqa: ANN001, ANN201, PLR0913
        lines = [ln for o in self.orders_created(t, s, e) for ln in o.lines]
        return ({ln.category_code for ln in lines if ln.category_code} - set(categories),
                {ln.genre_code for ln in lines if ln.genre_code} - set(genres))  # fmt: skip

    def product_title(self, t, s, e, pid):  # noqa: ANN001, ANN201
        return titles_of(o for o in self.paid_of(t, s, e)).get(pid)


# ---------------------------------------------------------------- 같은 데이터


def edge_orders() -> list[SalesOrderRow]:
    """가짜 출처에 없는 경우: 취소·결제 실패, 할인 몫이 안 맞는 주문, 장르·이름 없는 줄."""
    day = NOW - timedelta(days=2)

    def row(n: int, buyer: str, lines: list[SalesOrderLine], **kw) -> SalesOrderRow:  # noqa: ANN003
        amount = sum(ln.qty * ln.unit_price for ln in lines)
        base = {"order_id": f"edge{n}", "buyer_id": buyer, "status": "paid", "created_at": day,
                "updated_at": day, "items_amount": amount, "payment_status": "succeeded",
                "lines": lines}  # fmt: skip
        return SalesOrderRow.model_validate({**base, **kw})

    novel = SalesOrderLine(product_id="edge_p1", category_code="book", title="경계 상품",
                           qty=2, unit_price=12000, genre_code="novel")  # fmt: skip
    essay = SalesOrderLine(product_id="edge_p2", category_code="book", title=None, qty=1,
                           unit_price=9000, genre_code="essay")  # fmt: skip
    plain = SalesOrderLine(product_id="edge_p3", category_code=None, title="분류 없음", qty=1,
                           unit_price=5000)  # fmt: skip
    return [
        row(1, "edge_b1", [novel, essay], coupon_discount=3000),  # 줄 몫 0 ≠ 할인 → 배분
        row(2, "edge_b2", [novel], status="cancelled"),
        row(3, "edge_b3", [essay], payment_status="failed"),
        row(4, "edge_b4", [plain.model_copy(update={"points_share": 500}), essay],
            points_used=500),  # 몫이 맞음
        row(5, "edge_b5", [novel]),  # 고객 기록 없는 구매자
        row(6, "edge_b6", [essay, novel]),  # 카탈로그에 없는 속성 값
    ]


def edge_customers() -> list[CustomerRow]:
    day = NOW - timedelta(days=300)
    return [
        CustomerRow(buyer_id="edge_b1", joined_at=day, updated_at=day,
                    attributes={"age_band": "20s", "gender": "female"}),
        CustomerRow(buyer_id="edge_b4", joined_at=day, updated_at=day, attributes={}),
        CustomerRow(buyer_id="edge_b6", joined_at=day, updated_at=day,
                    attributes={"age_band": "70s", "gender": "female", "occupation": "x"}),
    ]  # fmt: skip


def filled(kind: type[SalesStore]) -> SalesStore:
    store = kind(":memory:")
    src = FakeSalesSource(load_config(FAKE_DIR / "catbooks.toml"), clock=lambda: NOW)
    result = run(collect_once(src, store, source_name="fake", now=NOW))
    assert result.errors == []
    store.upsert(TENANT, edge_orders(), NOW)
    store.upsert_customers(TENANT, edge_customers(), NOW)
    return store


@pytest.fixture(scope="module")
def stores() -> tuple[SalesStore, SalesStore]:
    return filled(SalesStore), filled(PythonPath)


@pytest.fixture(scope="module")
def cat():  # noqa: ANN201
    return catalog.load_catalog("catbooks")


def test_quality_report_same_as_reading_orders(stores, cat) -> None:  # noqa: ANN001
    new, _ = stores
    for period in ("1d", "7d", "30d", "90d", "365d"):
        w = seg.segment_window(period, NOW)
        created, paid = new.order_counts(TENANT, w.start, w.end)
        orders = new.orders_created(TENANT, w.start, w.end)
        old = quality(new, TENANT, w, orders, cat.sales)
        from data_agent.sales_quality import quality_counted

        assert quality_counted(new, TENANT, w, created, cat.sales) == old
        assert created == len(orders)
        assert paid == sum(counts_as_revenue(o.status, o.payment_status) for o in orders)


PROFILES = [
    ("genre", "novel", [], "90d"), ("genre", "essay", ["gender"], "30d"),
    ("category", "book", [], "7d"), ("product", "edge_p1", ["age_band"], "90d"),
    ("genre", "ghost", [], "90d"), ("genre", "novel", [], "1d"),
    ("category", "stationery", [], "90d"),
]  # fmt: skip


@pytest.mark.parametrize(("kind", "code", "keys", "period"), PROFILES)
def test_profile_same_as_python_path(stores, cat, kind, code, keys, period) -> None:  # noqa: ANN001, PLR0913
    new, old = stores
    args = (TENANT, kind, code, keys, period, cat.sales, cat.id, NOW)
    got, want = seg.profile(new, *args), seg.profile(old, *args)
    assert got == want


FAVORITES = [
    ({"age_band": "20s", "gender": "female"}, "90d"), ({"occupation": "employee"}, "30d"),
    ({"age_band": "50s_plus"}, "7d"), ({"age_band": "90s"}, "90d"),
    ({"age_band": "10s", "gender": "male", "occupation": "student"}, "1d"),
]  # fmt: skip


@pytest.mark.parametrize(("filters", "period"), FAVORITES)
def test_favorites_same_as_python_path(stores, cat, filters, period) -> None:  # noqa: ANN001
    new, old = stores
    args = (TENANT, filters, period, 10, cat.sales, cat.id, NOW)
    got, want = seg.favorites(new, *args), seg.favorites(old, *args)
    assert got == want
    if filters == {"age_band": "20s", "gender": "female"}:
        assert got.status == "ok" and got.internal  # 비교가 빈 결과끼리가 아니다


def test_coverage_same_as_python_path(stores, cat) -> None:  # noqa: ANN001
    new, old = stores
    args = (TENANT, ["age_band", "gender"], cat.sales, cat.id, NOW)
    got, want = seg.coverage_buyers(new, *args), seg.coverage_buyers(old, *args)
    assert got == want and got.status == "ok" and any(r.buyers for r in got.rows)


def test_event_proposal_candidates_same_as_python_path(stores, cat) -> None:  # noqa: ANN001
    new, old = stores
    got = ep.read_base(new, TENANT, cat.sales, cat.id, NOW)
    want = ep.read_base(old, TENANT, cat.sales, cat.id, NOW)
    assert got == want and got.candidates  # 후보·객단가·상위 상품까지 같다
    w = seg.segment_window(ep.ANALYSIS_PERIOD, NOW)
    with new.segment_session(TENANT, w.start, w.end, []) as s:
        totals, uneven = s.genre_nets()
    assert any(o.order_id == "edge1" for o in uneven)  # 할인 몫이 안 맞는 주문은 파이썬 배분
    assert ep.genre_aov(totals, uneven) == ep.genre_aov({}, old.paid_of(TENANT, w.start, w.end))


class NoOrderReads(SalesStore):
    """고객 기록 관문에 걸리면 주문 집계·주문 읽기를 부르면 안 된다."""

    def orders_created(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("주문을 파이썬으로 읽으면 안 된다")

    def segment_session(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("관문에 걸렸는데 집계를 부르면 안 된다")

    order_days = unknown_line_codes = segment_session


def test_no_customers_stops_before_reading_orders(cat) -> None:  # noqa: ANN001
    store = NoOrderReads(":memory:")
    store.upsert(TENANT, edge_orders(), NOW)
    store.finish_sync(TENANT, "fake", NOW, None)
    prof = seg.profile(store, TENANT, "genre", "novel", [], "90d", cat.sales, cat.id, NOW)
    fav = seg.favorites(store, TENANT, {"age_band": "20s"}, "90d", 10, cat.sales, cat.id, NOW)
    for out in (prof, fav):
        assert out.status == "insufficient" and seg.NO_CUSTOMERS in out.insufficient_reasons
        assert out.quality.rows_in_period == 6 and out.basis and out.basis.rows_read == 6
    # 고객 내보내기 오류도 같은 관문
    store.upsert_customers(TENANT, edge_customers(), NOW)
    store.finish_sync(TENANT, customers_source("fake"), NOW, "고객 내보내기 실패")
    again = seg.profile(store, TENANT, "genre", "novel", [], "90d", cat.sales, cat.id, NOW)
    assert "데이터 부족: 고객 내보내기 실패" in again.insufficient_reasons


def test_indexes_exist_on_old_db_file(tmp_path) -> None:  # noqa: ANN001
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)  # 인덱스 없이 만든 예전 파일
    conn.execute("CREATE TABLE sales_orders (tenant_id TEXT, order_id TEXT, buyer_id TEXT, "
                 "status TEXT, payment_status TEXT, created_at TEXT, updated_at TEXT, "
                 "items_amount INTEGER, shipping_fee INTEGER, points_used INTEGER, "
                 "coupon_discount INTEGER, src_source TEXT, src_medium TEXT, src_campaign TEXT, "
                 "synced_at TEXT, PRIMARY KEY (tenant_id, order_id))")  # fmt: skip
    conn.commit()
    conn.close()
    SalesStore(path).close()
    names = {r[0] for r in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'")}  # fmt: skip
    assert {"idx_sales_orders_created", "idx_sales_orders_paid"} <= names


# ---------------------------------------------------------------- 성능 (DATA_PERF=1)

PERF_ORDERS = 300_000


def big_store(path: str, orders: int, customers: bool) -> SalesStore:
    """주문 `orders`건(최근 90일, 줄 1~2개)과 고객 6만 명을 SQL로 바로 넣은 저장소."""
    import random

    store = SalesStore(path)
    rng = random.Random(7)
    genres = ["novel", "essay", "business", "it", "comics", "hobby"]
    start = NOW - timedelta(days=89)
    o_rows, l_rows = [], []
    for i in range(orders):
        at = (start + timedelta(seconds=rng.randrange(89 * 86400))).strftime("%Y-%m-%dT%H:%M:%SZ")
        status = "paid" if rng.random() > 0.05 else "cancelled"
        o_rows.append((TENANT, f"o{i}", f"b{rng.randrange(60000)}", status, "succeeded", at, at,
                       20000, 0, 0, 0, None, None, None, at))  # fmt: skip
        for n in range(1 + (i % 2)):
            g = rng.choice(genres)
            l_rows.append((TENANT, f"o{i}", n, f"p{g}{rng.randrange(200)}", "book", f"t{g}", 1,
                           10000, 0, 0, g))  # fmt: skip
    with store._tx() as conn:  # noqa: SLF001 - 성능 시험용 대량 적재
        conn.executemany("INSERT INTO sales_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", o_rows)
        conn.executemany(
            "INSERT INTO sales_order_lines (tenant_id, order_id, line_no, product_id, "
            "category_code, title, qty, unit_price, coupon_share, points_share, genre_code) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", l_rows,
        )  # fmt: skip
        if customers:
            ages = ["10s", "20s", "30s", "40s", "50s_plus"]
            sexes = ["female", "male", "no_answer"]
            jobs = ["student", "employee", "self_employed", "other", "no_answer"]
            stamp = "2025-01-01T00:00:00Z"
            conn.executemany("INSERT INTO sales_customers VALUES (?,?,?,?,?)",
                             [(TENANT, f"b{i}", stamp, stamp, stamp) for i in range(60000)])
            conn.executemany(
                "INSERT INTO sales_customer_attributes VALUES (?,?,?,?)",
                [(TENANT, f"b{i}", k, rng.choice(v)) for i in range(60000)
                 for k, v in (("age_band", ages), ("gender", sexes), ("occupation", jobs))],
            )  # fmt: skip
    store.finish_sync(TENANT, "shop-api", NOW - timedelta(minutes=1), None)
    return store


@pytest.mark.skipif(os.environ.get("DATA_PERF") != "1", reason="DATA_PERF=1일 때만 (느림)")
def test_perf_profile_on_300k_orders(tmp_path, cat) -> None:  # noqa: ANN001
    store = big_store(str(tmp_path / "big.db"), PERF_ORDERS, customers=True)
    started = time.perf_counter()
    out = seg.profile(store, TENANT, "genre", "novel", [], "90d", cat.sales, cat.id, NOW)
    took = time.perf_counter() - started
    print(f"\nsegment_profile 주문 {PERF_ORDERS:,}건: {took:.2f}초")
    assert out.status == "ok" and out.quality.rows_in_period == PERF_ORDERS
    assert took < 3.0
    bare = big_store(str(tmp_path / "bare.db"), PERF_ORDERS, customers=False)
    started = time.perf_counter()
    gated = seg.profile(bare, TENANT, "genre", "novel", [], "90d", cat.sales, cat.id, NOW)
    took = time.perf_counter() - started
    print(f"segment_profile 고객 기록 없음: {took:.2f}초")
    assert gated.status == "insufficient" and took < 1.0
