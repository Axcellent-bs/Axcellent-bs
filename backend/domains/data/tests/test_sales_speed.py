"""매출 분석·고객·시장 분석 속도 (11/18): 저장소 버전·결과 캐시·WAL 읽기 연결·미리 계산.

성능 시험(주문 30만 건)은 느려서 `DATA_PERF=1`일 때만 돈다:
`DATA_PERF=1 uv run pytest -s domains/data/tests/test_sales_speed.py -k perf`
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from data_agent import catalog, handlers, sales_reads
from data_agent.sales_cache import CACHE, ResultCache, cached
from data_agent.sales_store import VERSION_SOURCE, SalesStore

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)


def order(n: int, when: datetime | None = None) -> SalesOrderRow:
    at = when or NOW - timedelta(hours=1)
    return SalesOrderRow(
        order_id=f"o{n}", buyer_id=f"b{n % 7}", status="paid", created_at=at, updated_at=at,
        items_amount=10000, payment_status="succeeded",
        lines=[SalesOrderLine(product_id="p1", category_code="book", title="책", qty=1,
                              unit_price=10000, genre_code="novel")],
    )  # fmt: skip


# ---------------------------------------------------------------- 저장소 버전


def test_version_rises_only_when_rows_are_saved() -> None:
    store = SalesStore(":memory:")
    assert store.data_version(TENANT) == 0
    store.upsert(TENANT, [order(1)], NOW)
    assert store.data_version(TENANT) == 1
    store.upsert(TENANT, [], NOW)  # 새 행 없음
    store.finish_sync(TENANT, "shop-api", NOW, None)  # 수집 상태만
    store.save_cursor(TENANT, "shop-api", "c1")
    assert store.data_version(TENANT) == 1
    store.upsert_customers(TENANT, [CustomerRow(buyer_id="b1", joined_at=NOW, updated_at=NOW)], NOW)
    assert store.data_version(TENANT) == 2
    assert store.data_version("other") == 0
    # 버전 줄은 판매 기록 출처가 아니다 (가장 최근 수집 출처를 고를 때 섞이지 않는다)
    assert store.sync_state(TENANT).source == "shop-api"
    assert store.sync_state(TENANT, VERSION_SOURCE).last_synced_at is None


# ---------------------------------------------------------------- 결과 캐시


def test_cache_hits_until_version_or_input_changes() -> None:
    store = SalesStore(":memory:")
    cache = ResultCache()
    calls: list[str] = []

    def compute(tag: str):  # noqa: ANN202
        def run() -> str:
            calls.append(tag)
            return f"v{len(calls)}"
        return run

    def call(inputs: dict[str, Any], tag: str = "x"):  # noqa: ANN202
        return cached("s", store, TENANT, inputs, "cat", "1", NOW, compute(tag), cache)

    first = call({"period": "7d", "top": 5})
    again = call({"top": 5, "period": "7d"})  # 키 순서가 달라도 같은 입력
    assert (first.hit, again.hit) == (False, True) and again.value == first.value
    assert again.computed_at == first.computed_at
    assert not call({"period": "30d", "top": 5}).hit  # 다른 입력
    store.upsert(TENANT, [order(1)], NOW)  # 새 행 → 버전이 오른다
    # 이전 결과 먼저 (11/17): 예전 결과(v1)를 stale로 바로 주고 새 계산은 뒤에서 한 번
    stale = call({"period": "7d", "top": 5})
    assert stale.stale and stale.value == first.value and stale.computed_at == first.computed_at
    cache.wait()
    fresh = call({"period": "7d", "top": 5})
    assert fresh.hit and not fresh.stale and fresh.value == "v3"
    assert len(calls) == 3
    waited = cached("s", store, TENANT, {"period": "30d", "top": 5}, "cat", "1", NOW,
                    compute("w"), cache, stale_ok=False)  # fmt: skip
    assert not waited.stale and waited.value == "v4"  # 미리 계산은 기다려 새로 계산
    assert len(calls) == 4
    other = SalesStore(":memory:")  # 다른 저장소는 섞이지 않는다
    assert not cached("s", other, TENANT, {"period": "7d", "top": 5}, "cat", "1", NOW,
                      compute("o"), cache).hit  # fmt: skip


def test_cache_is_lru_and_old_entries_are_recomputed() -> None:
    cache = ResultCache(size=2, max_age=timedelta(minutes=30))
    now = datetime.now(UTC)
    for key in ("a", "b", "c"):
        cache.get_or_compute(key, lambda k=key: k, now)
    assert len(cache) == 2
    assert not cache.get_or_compute("a", lambda: "a2", now).hit  # 가장 오래된 a가 빠졌다
    later = now + timedelta(minutes=31)
    assert not cache.get_or_compute("c", lambda: "c2", later).hit  # 30분이 지나 다시 계산


def test_same_key_at_once_is_computed_once() -> None:
    cache = ResultCache()
    calls: list[int] = []
    gate = threading.Event()

    def slow() -> int:
        calls.append(1)
        gate.wait(2)
        return 42

    with ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(cache.get_or_compute, "k", slow, NOW) for _ in range(4)]
        time.sleep(0.2)
        gate.set()
        results = [f.result() for f in futures]
    assert calls == [1] and {r.value for r in results} == {42}
    assert sum(not r.hit for r in results) == 1


def test_stale_result_first_and_refresh_starts_once() -> None:
    """같은 묶음의 예전 결과가 있으면 바로 주고, 새 계산은 동시에 여러 번 불러도 뒤에서 한 번."""
    cache = ResultCache()
    family = ("s", "t", "{}")
    old = cache.get_or_compute((family, 1), lambda: "옛", NOW, family)
    calls: list[int] = []
    gate = threading.Event()

    def slow() -> str:
        calls.append(1)
        gate.wait(5)
        return "새"

    started = time.perf_counter()
    got = [cache.get_or_compute((family, 2), slow, NOW, family) for _ in range(3)]
    took = time.perf_counter() - started
    assert took < 0.5  # 새 계산을 기다리지 않았다
    assert all(g.stale and g.value == "옛" and g.computed_at == old.computed_at for g in got)
    gate.set()
    cache.wait()
    assert calls == [1]  # 같은 키 계산은 한 번만
    fresh = cache.get_or_compute((family, 2), slow, NOW, family)
    assert fresh.value == "새" and fresh.hit and not fresh.stale
    assert calls == [1]


def test_stale_is_not_taken_from_another_store_id() -> None:
    """저장소 생성 id가 다르면(리셋한 새 저장소) 예전 결과로 쓰지 않고 기다려 새로 계산한다."""
    cache = ResultCache()
    a, b = SalesStore(":memory:"), SalesStore(":memory:")
    b.cache_token = a.cache_token  # 같은 서버 프로세스·같은 저장소 객체 자리라고 쳐도
    assert a.store_id != b.store_id
    cached("s", a, TENANT, {}, "cat", "1", NOW, lambda: "옛", cache)
    b.upsert(TENANT, [order(1)], NOW)  # 버전도 다르다
    got = cached("s", b, TENANT, {}, "cat", "1", NOW, lambda: "새", cache)
    assert got.value == "새" and not got.stale and not got.hit


def test_handler_marks_stale_in_basis_then_refreshes() -> None:
    CACHE.clear()
    store = SalesStore(":memory:")
    now = datetime.now(UTC)
    store.upsert(TENANT, [order(i, now - timedelta(hours=1)) for i in range(30)], now)
    store.finish_sync(TENANT, "shop-api", now, None)
    catalog.use_catalog(catalog.load_catalog("catbooks"))
    handlers.use_sales_store(store)

    def call() -> Any:
        return asyncio.run(handlers.sales_analysis(_request("sales_analysis", period="today")))

    try:
        first = call()
        store.upsert(TENANT, [order(99, now - timedelta(minutes=30))], now)  # 새 판매 기록
        second = call()
        CACHE.wait()
        third = call()
    finally:
        handlers.use_sales_store(None)
        catalog.use_catalog(None)
    b1, b2, b3 = (r.output["basis"] for r in (first, second, third))
    assert b1["stale"] is False and b1["computed_at"]
    assert b2["stale"] is True and b2["computed_at"] == b1["computed_at"]  # 예전 결과·그 계산 시각
    assert second.output["kpis"] == first.output["kpis"]
    assert "갱신 중" in second.evidence.data_summary
    assert b3["stale"] is False  # 뒤에서 새로 계산한 결과
    rows = {k["key"]: k["value"] for k in third.output["kpis"]}
    assert rows["order_count"] == {k["key"]: k["value"] for k in first.output["kpis"]}[
        "order_count"
    ] + 1


def _request(skill: str, **inp: Any) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_req_dashboard", token="mock:dashboard-api",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return AgentRequest(meta=meta, skill=skill, input=inp)


def test_handlers_stamp_calculation_time_and_cache_hit() -> None:
    CACHE.clear()
    store = SalesStore(":memory:")
    now = datetime.now(UTC)
    store.upsert(TENANT, [order(i, now - timedelta(hours=1)) for i in range(30)], now)
    store.finish_sync(TENANT, "shop-api", now, None)
    catalog.use_catalog(catalog.load_catalog("catbooks"))
    handlers.use_sales_store(store)
    try:
        first = asyncio.run(handlers.sales_analysis(_request("sales_analysis", period="today")))
        second = asyncio.run(handlers.sales_analysis(_request("sales_analysis", period="today")))
    finally:
        handlers.use_sales_store(None)
        catalog.use_catalog(None)
    assert first.status == TaskStatus.COMPLETED and first.evidence and second.evidence
    assert "계산 " in first.evidence.data_summary and "캐시" not in first.evidence.data_summary
    assert "(캐시에서 꺼냄)" in second.evidence.data_summary
    assert second.evidence.collected_at == first.evidence.collected_at  # 계산 시각
    assert first.output == second.output


def test_warm_fills_the_cache_for_screen_defaults() -> None:
    CACHE.clear()
    store = SalesStore(":memory:")
    store.upsert(TENANT, [order(i) for i in range(30)], NOW)
    store.finish_sync(TENANT, "shop-api", NOW, None)
    cat = catalog.load_catalog("catbooks")
    done = sales_reads.warm(store, TENANT, cat, NOW)
    assert done == ["sales_analysis today", "sales_analysis 7d", "sales_analysis 30d",
                    "segment_options", "segment_profile", "market_coverage", "event_proposal"]
    assert sales_reads.sales_analysis(store, TENANT, "7d", cat, NOW).hit
    first = next(iter(cat.sales.genres))
    assert sales_reads.segment_profile(store, TENANT, "genre", first, [], "90d", cat, NOW).hit
    keys = list(cat.sales.population[0].attribute_keys)
    assert sales_reads.coverage_buyers(store, TENANT, keys, cat, NOW).hit
    assert sales_reads.event_base(store, TENANT, cat, NOW).hit


def test_warm_failure_is_logged_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **kw: Any) -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(sales_reads, "analyze", boom)
    CACHE.clear()
    done = sales_reads.warm(SalesStore(":memory:"), TENANT, catalog.load_catalog("catbooks"), NOW)
    assert "sales_analysis 7d" not in done and "segment_options" in done


# ---------------------------------------------------------------- WAL · 읽기 전용 연결


def test_file_store_uses_wal_and_old_files_are_switched(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    sqlite3.connect(path).execute("PRAGMA journal_mode = DELETE").close()
    store = SalesStore(path)
    mode = sqlite3.connect(path).execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    with store.reading() as conn:
        with pytest.raises(sqlite3.OperationalError):  # 읽기 연결로는 쓰지 못한다
            conn.execute("INSERT INTO sync_state (tenant_id, source) VALUES ('t', 's')")
    store.close()


def test_reads_are_not_blocked_while_the_collector_writes(tmp_path: Path) -> None:
    """쓰기(수집기)가 거래를 잡고 있는 동안에도 다른 스레드의 읽기는 기다리지 않고 바로 끝난다."""
    store = SalesStore(tmp_path / "sales.db")
    store.upsert(TENANT, [order(i) for i in range(5)], NOW)
    holding = threading.Event()
    release = threading.Event()

    def collector() -> None:
        with store._tx() as conn:  # noqa: SLF001 - 수집기가 페이지를 쓰는 중인 상태를 흉내
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE sales_orders SET status = 'paid' WHERE order_id = 'o1'")
            holding.set()
            release.wait(5)

    writer = threading.Thread(target=collector)
    writer.start()
    assert holding.wait(5)
    started = time.perf_counter()
    count = store.order_count(TENANT)
    took = time.perf_counter() - started
    release.set()
    writer.join(5)
    assert count == 5 and took < 0.5  # 잠금을 기다리지 않았다 (기다렸다면 5초 가까이)
    store.close()


# ---------------------------------------------------------------- 성능 (DATA_PERF=1)

PERF = pytest.mark.skipif(os.environ.get("DATA_PERF") != "1", reason="DATA_PERF=1일 때만 (느림)")


@pytest.fixture(scope="module")
def big(tmp_path_factory: pytest.TempPathFactory) -> SalesStore:
    import importlib.util  # noqa: PLC0415 - 성능 시험에서만

    spec = importlib.util.spec_from_file_location(
        "segment_sql_perf", Path(__file__).with_name("test_segment_sql.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # 주문 30만 건 가짜 저장소 만들기를 같이 쓴다
    return module.big_store(str(tmp_path_factory.mktemp("perf") / "big.db"), 300_000,
                            customers=True)  # fmt: skip


def _time(fn: Any) -> float:
    started = time.perf_counter()
    fn()
    return time.perf_counter() - started


@PERF
def test_perf_sales_analysis_first_and_cached(big: SalesStore) -> None:
    cat = catalog.load_catalog("catbooks")
    CACHE.clear()
    lines, firsts, hits = [], [], []
    for period in ("today", "7d", "30d"):
        first = _time(lambda p=period: sales_reads.sales_analysis(big, TENANT, p, cat, NOW))
        hit = _time(lambda p=period: sales_reads.sales_analysis(big, TENANT, p, cat, NOW))
        lines.append(f"sales_analysis {period}: 첫 계산 {first:.2f}초, 캐시 {hit:.3f}초")
        firsts.append(first)
        hits.append(hit)
    print("\n" + "\n".join(lines))
    assert max(hits) < 0.3
    assert max(firsts) < 3.0


@PERF
def test_perf_previous_result_first(big: SalesStore) -> None:
    """판매 기록이 바뀐 뒤 예전 결과가 있으면 0.3초 안에 준다 (새 계산은 뒤에서)."""
    cat = catalog.load_catalog("catbooks")
    CACHE.clear()
    lines, quick = [], []
    for period in ("today", "7d", "30d"):
        sales_reads.sales_analysis(big, TENANT, period, cat, NOW)
    big.upsert(TENANT, [order(900_001, NOW - timedelta(hours=2))], NOW)  # 저장소 버전이 오른다
    for period in ("today", "7d", "30d"):
        started = time.perf_counter()
        got = sales_reads.sales_analysis(big, TENANT, period, cat, NOW)
        took = time.perf_counter() - started
        assert got.stale
        lines.append(f"sales_analysis {period}: 예전 결과 먼저 {took:.3f}초")
        quick.append(took)
    started = time.perf_counter()
    CACHE.wait(120)
    lines.append(f"뒤 갱신 3개 끝까지 {time.perf_counter() - started:.2f}초")
    assert not sales_reads.sales_analysis(big, TENANT, "30d", cat, NOW).stale
    print("\n" + "\n".join(lines))
    assert max(quick) < 0.3


@PERF
def test_perf_four_cells_at_once(big: SalesStore) -> None:
    """고객·시장 분석 칸 4개를 동시에 불러도 가장 늦은 것이 따로 부른 것의 1.5배 안."""
    cat = catalog.load_catalog("catbooks")
    keys = list(cat.sales.population[0].attribute_keys)
    first = next(iter(cat.sales.genres))
    cells = {
        "segment_profile": lambda: sales_reads.segment_profile(big, TENANT, "genre", first, [],
                                                               "90d", cat, NOW),
        "segment_favorites": lambda: sales_reads.segment_favorites(
            big, TENANT, {"age_band": "20s"}, "90d", 10, cat, NOW),
        "market_coverage": lambda: sales_reads.coverage_buyers(big, TENANT, keys, cat, NOW),
        "event_proposal": lambda: sales_reads.event_base(big, TENANT, cat, NOW),
    }  # fmt: skip
    alone = {}
    for name, fn in cells.items():
        CACHE.clear()
        alone[name] = _time(fn)
    CACHE.clear()
    with ThreadPoolExecutor(4) as pool:
        futures = {name: pool.submit(_time, fn) for name, fn in cells.items()}
        together = {name: f.result() for name, f in futures.items()}
    slowest = max(together, key=lambda n: together[n])
    print("\n" + "\n".join(f"{n}: 따로 {alone[n]:.2f}초, 동시 {together[n]:.2f}초" for n in cells))
    assert together[slowest] <= 1.5 * alone[slowest]
