import sqlite3
from pathlib import Path

import pytest

from ax_shop_api.db import Db

# 주문 목록·상세가 주문마다 order_id로 찾는 세 표 (`service.py` `_order`)
LOOKUPS = {
    "order_items": "SELECT * FROM order_items WHERE order_id = ?",
    "payments": "SELECT payment_id FROM payments WHERE order_id = ? ORDER BY rowid",
    "refunds": "SELECT COALESCE(SUM(amount), 0) FROM refunds WHERE order_id = ?",
}


def _plan(db: Db, sql: str) -> str:
    with db.tx() as conn:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql, ("ord_0001",)).fetchall()
    return " | ".join(str(row["detail"]) for row in rows)


@pytest.mark.parametrize("table", sorted(LOOKUPS))
def test_order_lookups_use_an_index_not_a_full_scan(table: str) -> None:
    plan = _plan(Db(":memory:"), LOOKUPS[table])
    assert "USING" in plan and "INDEX" in plan, plan
    assert f"SCAN {table}" not in plan, plan


def test_indexes_are_added_to_an_existing_db_file(tmp_path: Path) -> None:
    """옛 DB 파일(인덱스 없음)도 다시 열면 인덱스가 붙고, 주문 데이터는 그대로다."""
    path = str(tmp_path / "shop.db")
    first = Db(path)
    with first.tx() as conn:
        conn.execute("INSERT INTO buyers VALUES ('buy_0001', 't', NULL, '2026-10-30')")
        conn.execute(
            "INSERT INTO orders(order_id, buyer_id, status, total_amount, tenant_id, created_at,"
            " updated_at) VALUES ('ord_0001', 'buy_0001', 'paid', 12000, 't', 'x', 'x')"
        )
        for index in ("idx_order_items_order_id", "idx_payments_order_id", "idx_refunds_order_id"):
            conn.execute(f"DROP INDEX {index}")
    first._conn.close()

    raw = sqlite3.connect(path)
    assert not raw.execute("SELECT name FROM sqlite_master WHERE name LIKE 'idx_%'").fetchall()
    raw.close()

    second = Db(path)
    with second.tx() as conn:
        names = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert {"idx_order_items_order_id", "idx_payments_order_id", "idx_refunds_order_id"} <= names
    assert count == 1
