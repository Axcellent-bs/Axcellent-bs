"""원본 판매 기록을 데이터 AI 메모리에만 (3단계 3-3 A안, DATA_SALES_STORE=memory).

- 스위치 읽기, 메모리 저장소로 처음부터 받은 것이 파일 저장소와 같음(주문 수·매출 집계)
- 메모리 모드에서는 sales.db·잠금 파일이 생기지 않고, 수집기 명령은 안내만 하고 끝난다
- 처음 받기가 끝나면 한 번 바꿔 끼우고, 하루 집계 기록의 입력 이름은 company_server
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from data_agent import sales_collect, sales_memory
from data_agent.aggregate.store import DB_ENV as AGG_DB_ENV
from data_agent.aggregate.store import AggregateStore
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_sql import tally
from data_agent.sales_store import DB_ENV as SALES_DB_ENV
from data_agent.sales_store import SalesStore

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00


def _source() -> FakeSalesSource:
    return FakeSalesSource(load_config(FAKE_DIR / "catbooks.toml"), clock=lambda: NOW)


def test_memory_mode_switch() -> None:
    assert sales_memory.memory_mode({"DATA_SALES_STORE": "memory"})
    assert sales_memory.memory_mode({"DATA_SALES_STORE": " Memory "})
    assert not sales_memory.memory_mode({})
    assert not sales_memory.memory_mode({"DATA_SALES_STORE": "file"})


def test_load_full_matches_file_store_and_writes_no_file(tmp_path: Path) -> None:
    mem = asyncio.run(sales_memory.load_full(_source(), TENANT, "fake", NOW))
    file_store = SalesStore(tmp_path / "sales.db")
    first = sales_collect.collect_once(
        _source(), file_store, tenant_id=TENANT, source_name="fake", now=NOW
    )
    assert asyncio.run(first).errors == []
    try:
        assert mem.memory
        assert mem.order_span(TENANT)[0] == file_store.order_span(TENANT)[0] > 0
        start, end = NOW - timedelta(days=30), NOW
        got, _ = tally(mem, TENANT, start, end)
        want, _ = tally(file_store, TENANT, start, end)
        assert (got.revenue, got.orders, got.buyers) == (want.revenue, want.orders, want.buyers)
        assert mem.customer_count(TENANT) == file_store.customer_count(TENANT)
    finally:
        mem.close()
        file_store.close()
    # 메모리 쪽은 파일을 만들지 않는다(파일 저장소 sales.db와 그 -wal·-shm만 있다)
    assert all(p.name.startswith("sales.db") for p in tmp_path.iterdir())


def test_run_forever_swaps_once_and_records_company_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sales_db = tmp_path / "sales.db"
    agg_db = tmp_path / "aggregates.db"
    env = {"DATA_SALES_STORE": "memory", SALES_DB_ENV: str(sales_db), AGG_DB_ENV: str(agg_db),
           "DATA_AGGREGATE_DAILY": "1", "DATA_SALES_TENANT_ID": TENANT,
           "DATA_TENANT_CATALOG": "catbooks"}  # fmt: skip
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    swapped: list[SalesStore] = []
    asyncio.run(sales_memory.run_forever(
        env, swapped.append, every=60, source_factory=lambda _e: _source(),
        clock=lambda: NOW, rounds=2,
    ))  # fmt: skip
    assert len(swapped) == 1 and swapped[0].memory  # 처음 받기 뒤 한 번만 바꿔 끼운다
    assert not sales_db.exists()  # 원본은 디스크에 없다
    agg = AggregateStore(agg_db)
    try:
        runs = agg.runs(TENANT)
        assert runs and runs[-1]["status"] == "done"
        assert all(r["input"] == "company_server" for r in runs)
    finally:
        agg.close()
        swapped[0].close()


def test_collector_command_only_notices_in_memory_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sales_db = tmp_path / "sales.db"
    monkeypatch.setenv("DATA_SALES_STORE", "memory")
    monkeypatch.setenv(SALES_DB_ENV, str(sales_db))
    for argv in ([], ["--every", "60"], ["--reset"], ["--rebuild-summary"]):
        assert sales_collect.main(argv) == 0
    assert "메모리에만" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []  # sales.db도 잠금 파일도 만들지 않는다
