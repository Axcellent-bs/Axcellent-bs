"""`sales_collect --reset` (11/17): 저장소를 옆으로 옮기고 처음부터 다시 받기.

목 표본(`MockSalesSource`, `DATA_MOCK=1`)은 시험에서만 쓴다.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from data_agent import collector_lock, sales_cache, sales_collect
from data_agent.sales_collect import mock_rows
from data_agent.sales_store import SalesStore

NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
TENANT = "shop-demo"


def _old_store(db: Path, env: dict[str, str]) -> str:
    """전환 전 옛 주문과 끝까지 간 커서가 있는 저장소. 저장소 생성 id를 돌려준다."""
    store = SalesStore(db)
    old = [r.model_copy(update={"order_id": f"old_{i}"}) for i, r in enumerate(mock_rows(5))]
    store.upsert(TENANT, old, NOW)
    store.save_cursor(TENANT, sales_collect.source_name(env), "mock:30")
    store_id = store.store_id
    store.close()
    return store_id


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    values = {"DATA_SALES_DB": str(tmp_path / "sales.db"), "DATA_MOCK": "1"}
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    return values


def test_store_id_is_made_once_per_file(tmp_path: Path) -> None:
    a = SalesStore(tmp_path / "s.db")
    first = a.store_id
    a.close()
    b = SalesStore(tmp_path / "s.db")
    assert b.store_id == first  # 다시 열면 그대로
    assert b.sync_state(TENANT).source is None  # 판매 기록 출처로 보이지 않는다
    b.close()
    assert SalesStore(tmp_path / "other.db").store_id != first


def test_reset_backs_up_and_refills_from_the_start(
    env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = Path(env["DATA_SALES_DB"])
    old_id = _old_store(db, env)
    # 커서가 끝이라 그냥 수집하면 아무것도 들어오지 않는다 (리셋이 필요한 상황)
    assert sales_collect.main(["--reset"]) == 0
    out = capsys.readouterr().out

    backups = sorted(tmp_path.glob("sales.db.bak-*"))
    assert backups and backups[0].name.startswith("sales.db.bak-")
    store = SalesStore(db)
    try:
        since = datetime(2020, 1, 1, tzinfo=UTC)
        ids = {o.order_id for o in store.orders_created(TENANT, since, NOW)}
        assert ids == {r.order_id for r in mock_rows()}  # 옛 주문은 없고 목 표본만
        assert store.store_id != old_id
        assert f"저장소 생성 id {store.store_id}" in out
    finally:
        store.close()
    old = SalesStore(backups[0])
    try:
        assert old.order_count(TENANT) == 5 and old.store_id == old_id  # 백업은 그대로
    finally:
        old.close()
    assert f"전체 주문 {len(mock_rows())}건" in out
    assert "판매 서버에 고객 내보내기 없음" in out  # 목 출처는 고객 내보내기가 없다
    assert "어제까지 7일(" in out and "결제 완료" in out
    assert "유입 출처별 주문 (전체): " in out and "naver" in out and "없음" in out


def test_reset_without_db_file_just_collects(
    env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sales_collect.main(["--reset"]) == 0
    assert not list(tmp_path.glob("*.bak-*"))
    assert "백업 없음" in capsys.readouterr().out


def test_reset_refuses_when_collector_holds_the_lock(
    env: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = Path(env["DATA_SALES_DB"])
    _old_store(db, env)
    before = db.read_bytes()
    lock = collector_lock.acquire(sales_collect.lock_path(env), "test")
    assert lock is not None
    try:
        assert sales_collect.main(["--reset"]) == 1
    finally:
        lock.close()
    assert db.read_bytes() == before and not list(tmp_path.glob("*.bak-*"))
    assert "pkill -f data_agent.sales_collect" in capsys.readouterr().out


def test_reset_with_every_is_refused(
    env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert sales_collect.main(["--reset", "--every", "60"]) == 2
    assert "--reset은 한 번만 실행" in capsys.readouterr().out


def test_cache_key_differs_after_reset(env: dict[str, str]) -> None:
    """리셋한 새 저장소가 옛 저장소와 같은 버전 번호를 써도 옛 결과를 꺼내지 않는다."""
    db = Path(env["DATA_SALES_DB"])
    _old_store(db, env)
    cache = sales_cache.ResultCache()
    old = SalesStore(db)
    token, version = old.cache_token, old.data_version(TENANT)
    got_old = sales_cache.cached(
        "sales_analysis", old, TENANT, {"period": "7d"}, "catbooks", "1", NOW,
        lambda: "옛 결과", cache,
    )  # fmt: skip
    old.close()
    assert sales_collect.main(["--reset"]) == 0
    new = SalesStore(db)
    new.cache_token = token  # 같은 서버 프로세스에서 같은 저장소 객체라고 쳐도
    try:
        assert new.data_version(TENANT) == version  # 버전 번호가 같아도
        got_new = sales_cache.cached(
            "sales_analysis", new, TENANT, {"period": "7d"}, "catbooks", "1", NOW,
            lambda: "새 결과", cache,
        )  # fmt: skip
    finally:
        new.close()
    assert got_old.value == "옛 결과"
    assert got_new.value == "새 결과" and not got_new.hit


# ---------------------------------------------------------------- 구매자 요약 표 (11/18)


def _summary_rows(store: SalesStore) -> tuple[list, list]:
    with store.reading() as conn:
        return (
            [tuple(r) for r in conn.execute("SELECT * FROM sales_buyer_summary ORDER BY 1, 2")],
            [tuple(r) for r in conn.execute("SELECT * FROM sales_buyer_items ORDER BY 1, 2, 3, 4")],
        )


def test_reset_fills_the_summary_tables_in_the_new_store(
    env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    db = Path(env["DATA_SALES_DB"])
    _old_store(db, env)
    assert sales_collect.main(["--reset"]) == 0
    out = capsys.readouterr().out
    store = SalesStore(db)
    try:
        assert store.summary_ready(TENANT)
        counts = store.summary_counts(TENANT)
        assert counts.buyers > 0 and counts.items > 0
        assert f"[sales] 요약 표: 구매자 {counts.buyers:,}명, 항목 {counts.items:,}줄" in out
        assert "(수집하며 채움)" in out
        collected = _summary_rows(store)
        store.rebuild_summary(TENANT)
        assert _summary_rows(store) == collected  # 수집하며 채운 것 = 처음부터 채운 것
        old_ids = {r[1] for r in collected[0]}
        assert not any(b.startswith("old_") for b in old_ids)  # 옛 저장소 구매자는 없다
    finally:
        store.close()


def test_empty_summary_is_filled_automatically_right_after_reset(
    env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """리셋 직후 요약 표가 비어 있어도(준비됨 줄 없는 파일로 바꿔 끼운 경우) 데이터 AI가 채운다."""
    import asyncio
    import sqlite3

    from data_agent import sales_reads, sales_summary

    db = Path(env["DATA_SALES_DB"])
    assert sales_collect.main(["--reset"]) == 0
    capsys.readouterr()
    store = SalesStore(db)
    want = _summary_rows(store)
    store.close()
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM sales_buyer_summary")
    conn.execute("DELETE FROM sales_buyer_items")
    conn.execute("DELETE FROM sync_state WHERE tenant_id = ?", (sales_summary.SUMMARY_TENANT,))
    conn.commit()
    conn.close()
    store = SalesStore(db)
    try:
        assert not store.summary_ready(TENANT)
        assert asyncio.run(sales_reads.ensure_summary(store, TENANT)) is True
        assert store.summary_ready(TENANT) and _summary_rows(store) == want
    finally:
        store.close()


def test_rebuild_summary_command(env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    db = Path(env["DATA_SALES_DB"])
    _old_store(db, env)
    assert sales_collect.main(["--rebuild-summary"]) == 0
    out = capsys.readouterr().out
    assert "[sales] 요약 표: 구매자 " in out and "(처음부터 다시 채움)" in out
    assert sales_collect.main(["--rebuild-summary", "--reset"]) == 2
