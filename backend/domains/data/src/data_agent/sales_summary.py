"""구매자 요약 표 (11/18): 처음 보는 조건에서도 고객·시장 분석이 주문 전체를 다시 훑지 않게.

표 두 개 (판매 저장소 안, 계약이 아닌 내부 표)
- `sales_buyer_summary`(tenant_id, buyer_id): 결제 완료 주문의 첫·두 번째·마지막 접수 시각, 결제
  완료 주문 수, 매출, 첫 주문의 유입 출처(src_source)
- `sales_buyer_items`(tenant_id, buyer_id, level, item_code): 그 사람이 산 장르·상품·분류(level
  genre·product·category)마다 첫·마지막 접수 시각, 그 항목이 든 결제 완료 주문 수, 그 항목 매출

정의
- 결제 완료 = `sales_clean.counts_as_revenue`와 같은 조건 (결제 성공, 취소·반품 상태 아님)
- 매출 = 매출 분석 정의 그대로: 줄 순매출(줄 몫 합이 주문 할인과 다르면 `sales_sql.line_nets`의
  배분) − 환불(줄 순매출 비율로 `allocate`). 요약 표는 기간이 없어 그 주문의 환불을 모두 뺀다
- 첫 주문 순서 = (접수 시각, 주문 id). 두 번째 시각은 재구매율(첫 구매 뒤 N일 안 두 번째 결제)용

갱신 (`refresh`)
- 수집기가 주문을 저장·갱신(취소·환불 포함)할 때 그 페이지에 나온 구매자 줄만 지우고 그 사람의
  결제 완료 주문으로 다시 계산한다. `SalesStore.upsert`의 같은 쓰기(저장소 버전을 올리는 거래) 안
- 처음 채우기(`rebuild`): 기존 주문이 있는 저장소. 명령 `python -m data_agent.sales_collect
  --rebuild-summary`, 데이터 AI가 시작할 때 준비되지 않았으면 자동으로 (`ensure`)

준비됨 (`ready`)
- 새 저장소(주문이 하나도 없을 때 연 파일)는 처음부터 수집이 채우므로 준비됨(`*` 줄)
- 예전 파일은 `rebuild`가 끝나야 그 고객사가 준비됨. 준비되지 않으면 분석은 예전 SQL로 센다
- 시험처럼 주문을 SQL로 바로 넣으면 `rebuild`를 불러야 한다
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from data_agent.sales_clean import EXCLUDED_STATUSES, PAID

# sync_state의 "준비됨" 줄: tenant_id = SUMMARY_TENANT, source = 고객사(또는 ALL_TENANTS),
# last_synced_at = 채운 시각. 고객사 줄과 따로 둬 판매 출처(`SalesStore.sync_state`)로 보이지 않게
SUMMARY_TENANT = "*summary*"
ALL_TENANTS = "*"  # 새 저장소: 모든 고객사가 처음부터 준비됨
LEVELS: dict[str, str] = {
    "genre": "genre_code",
    "product": "product_id",
    "category": "category_code",
}
CHUNK = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales_buyer_summary (
    tenant_id        TEXT NOT NULL,
    buyer_id         TEXT NOT NULL,
    first_paid_at    TEXT NOT NULL,
    second_paid_at   TEXT,              -- 두 번째 결제 완료 주문 (없으면 NULL)
    last_paid_at     TEXT NOT NULL,
    paid_orders      INTEGER NOT NULL,
    revenue          INTEGER NOT NULL,
    first_src_source TEXT,
    PRIMARY KEY (tenant_id, buyer_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_buyer_summary_last ON sales_buyer_summary(tenant_id, last_paid_at);
CREATE INDEX IF NOT EXISTS idx_buyer_summary_first ON sales_buyer_summary(tenant_id, first_paid_at);
CREATE TABLE IF NOT EXISTS sales_buyer_items (
    tenant_id     TEXT NOT NULL,
    buyer_id      TEXT NOT NULL,
    level         TEXT NOT NULL,        -- genre · product · category
    item_code     TEXT NOT NULL,
    first_paid_at TEXT NOT NULL,
    last_paid_at  TEXT NOT NULL,
    paid_orders   INTEGER NOT NULL,
    revenue       INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, buyer_id, level, item_code)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_buyer_items_item
    ON sales_buyer_items(tenant_id, level, item_code, last_paid_at);
"""


def _excluded() -> tuple[str, list[str]]:
    excluded = sorted(EXCLUDED_STATUSES)
    return ",".join("?" * len(excluded)), excluded


def mark_new_store(conn: sqlite3.Connection) -> None:
    """주문이 하나도 없는 저장소를 열 때: 모든 고객사가 처음부터 준비됨 (수집이 채운다)."""
    if conn.execute("SELECT 1 FROM sales_orders LIMIT 1").fetchone() is None:
        conn.execute(
            "INSERT OR IGNORE INTO sync_state (tenant_id, source, last_synced_at) VALUES (?, ?, ?)",
            (SUMMARY_TENANT, ALL_TENANTS, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")),
        )
        conn.commit()


def ready(conn: sqlite3.Connection, tenant_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sync_state WHERE tenant_id = ? AND source IN (?, ?) LIMIT 1",
        (SUMMARY_TENANT, ALL_TENANTS, tenant_id),
    ).fetchone()
    return row is not None


@dataclass
class _Buyer:
    first: tuple[str, str] | None = None  # (접수 시각, 주문 id)
    second: tuple[str, str] | None = None
    last: str = ""
    orders: int = 0
    revenue: int = 0
    src: str | None = None


@dataclass
class _Item:
    first: str = ""
    last: str = ""
    orders: int = 0
    revenue: int = 0


def _buyer_rows(conn: sqlite3.Connection, tenant_id: str, ids: Sequence[str]) -> list[sqlite3.Row]:
    marks = ",".join("?" * len(ids))
    ex_marks, excluded = _excluded()
    return conn.execute(
        f"SELECT * FROM sales_orders WHERE tenant_id = ? AND buyer_id IN ({marks}) "
        f"AND payment_status = ? AND LOWER(status) NOT IN ({ex_marks}) "
        "ORDER BY buyer_id, created_at, order_id",
        (tenant_id, *ids, PAID, *excluded),
    ).fetchall()


def refresh(
    store: object, conn: sqlite3.Connection, tenant_id: str, buyer_ids: Iterable[str]
) -> int:
    """구매자 줄만 다시 계산한다 (쓰기 거래 안에서 부른다). 다시 쓴 구매자 수(결제 완료가 있는)."""
    from data_agent.sales_sql import allocate, line_nets  # sales_sql → sales_store 순환을 피한다

    ids = sorted({b for b in buyer_ids if b is not None})
    written = 0
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        marks = ",".join("?" * len(chunk))
        for table in ("sales_buyer_summary", "sales_buyer_items"):
            conn.execute(
                f"DELETE FROM {table} WHERE tenant_id = ? AND buyer_id IN ({marks})",
                (tenant_id, *chunk),
            )
        rows = _buyer_rows(conn, tenant_id, chunk)
        if not rows:
            continue
        orders = store._with_details(tenant_id, rows, conn)  # type: ignore[attr-defined]  # noqa: SLF001
        buyers: dict[str, _Buyer] = defaultdict(_Buyer)
        items: dict[tuple[str, str, str], _Item] = defaultdict(_Item)
        created = {r["order_id"]: r["created_at"] for r in rows}
        for order in sorted(orders, key=lambda o: (o.buyer_id, created[o.order_id], o.order_id)):
            at = created[order.order_id]
            b = buyers[order.buyer_id]
            key = (at, order.order_id)
            if b.first is None:
                b.first, b.src = key, order.src_source
            elif b.second is None:
                b.second = key
            b.last = max(b.last, at)
            b.orders += 1
            nets = line_nets(order)
            amounts = [n.net for n in nets]
            for refund in order.refunds:
                for i, part in enumerate(allocate(-refund.amount, [n.net for n in nets])):
                    amounts[i] += part
            b.revenue += sum(amounts)
            seen: set[tuple[str, str, str]] = set()
            lines = order.lines or [None]
            for line, amount in zip(lines, amounts, strict=True):
                if line is None:
                    continue
                for level, column in LEVELS.items():
                    code = getattr(line, column)
                    if not code:
                        continue
                    k = (order.buyer_id, level, code)
                    it = items[k]
                    it.revenue += amount
                    if k not in seen:
                        seen.add(k)
                        it.orders += 1
                        it.first = at if not it.first else min(it.first, at)
                        it.last = max(it.last, at)
        conn.executemany(
            "INSERT INTO sales_buyer_summary (tenant_id, buyer_id, first_paid_at, second_paid_at, "
            "last_paid_at, paid_orders, revenue, first_src_source) VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    tenant_id,
                    bid,
                    b.first[0] if b.first else "",
                    b.second[0] if b.second else None,
                    b.last,
                    b.orders,
                    b.revenue,
                    b.src,
                )
                for bid, b in buyers.items()
            ],  # fmt: skip
        )
        conn.executemany(
            "INSERT INTO sales_buyer_items (tenant_id, buyer_id, level, item_code, first_paid_at, "
            "last_paid_at, paid_orders, revenue) VALUES (?,?,?,?,?,?,?,?)",
            [(tenant_id, *k, it.first, it.last, it.orders, it.revenue) for k, it in items.items()],
        )
        written += len(buyers)
    return written


@dataclass(frozen=True)
class RebuildResult:
    buyers: int
    items: int


def rebuild(store: object, conn: sqlite3.Connection, tenant_id: str) -> RebuildResult:
    """고객사 하나의 요약 표를 처음부터 채운다 (쓰기 거래 안에서). 끝나면 준비됨으로 남긴다."""
    for table in ("sales_buyer_summary", "sales_buyer_items"):
        conn.execute(f"DELETE FROM {table} WHERE tenant_id = ?", (tenant_id,))
    ex_marks, excluded = _excluded()
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT buyer_id FROM sales_orders WHERE tenant_id = ? AND payment_status = ? "
            f"AND LOWER(status) NOT IN ({ex_marks}) ORDER BY buyer_id",
            (tenant_id, PAID, *excluded),
        )
    ]
    refresh(store, conn, tenant_id, ids)
    conn.execute(
        "INSERT INTO sync_state (tenant_id, source, last_synced_at) VALUES (?, ?, ?) "
        "ON CONFLICT(tenant_id, source) DO UPDATE SET last_synced_at = excluded.last_synced_at",
        (SUMMARY_TENANT, tenant_id, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    return counts(conn, tenant_id)


def counts(conn: sqlite3.Connection, tenant_id: str) -> RebuildResult:
    b = conn.execute(
        "SELECT COUNT(*) FROM sales_buyer_summary WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()[0]
    i = conn.execute(
        "SELECT COUNT(*) FROM sales_buyer_items WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()[0]
    return RebuildResult(int(b), int(i))
