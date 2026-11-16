"""매출 분석 표준 판매 저장소 (data 자체 SQLite 파일, 커밋하지 않음).

고객사가 바뀌어도 같은 모양이다. 판매 서버(캣북스 shop-api 등)마다 커넥터가 계약 `shop_sales`
형식으로 바꿔 주고, 수집기(`sales_collect`)가 정제(`sales_clean`)한 뒤 여기에 덮어쓴다. 매출 분석
(`sales_analysis`)은 이 저장소만 읽는다(판매 서버를 직접 읽지 않는다).

- 위치: 환경변수 `DATA_SALES_DB`(없으면 `backend/.data/sales.db`)
- 모든 표에 `tenant_id`. 시각은 UTC ISO 문자열(초 단위, `...Z`), 금액은 정수 원
- `sales_orders`: 주문 한 건 한 줄. (tenant_id, order_id)가 기본 키라 같은 주문은 덮어쓴다(upsert)
- `sales_order_lines`·`sales_refunds`: 주문을 덮어쓸 때 그 주문 것을 지우고 다시 넣는다
- `sales_dropped`: 정제에서 버린 것(주문·줄·환불). 주문을 다시 받으면 그 주문 것을 새로 적는다
  (같은 주문을 두 번 받아도 두 번 세지 않는다). 주문째 버린 주문은 `sales_orders`에서도 지운다
- `sync_state`: 고객사·출처마다 커서, 마지막 성공 수집 시각, 마지막 시도·오류.
  고객 내보내기는 `<출처>:customers`로 따로 둔다(커서가 섞이지 않게). 가짜 출처(데모)는 `fake`
- `sales_customers`·`sales_customer_attributes`: 고객 한 명 한 줄(가명 buyer_id, 가입 시각)과 그
  고객의 속성(key → 값 code). 같은 고객을 다시 받으면 덮어쓰고 속성 줄은 지우고 다시 넣는다
- `sales_order_lines.genre_code`(11/16): 예전 DB 파일에는 없으므로 열 때 칸을 더한다(ALTER TABLE)
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderRow
from data_agent.sales_clean import EXCLUDED_STATUSES, PAID, clean

DB_ENV = "DATA_SALES_DB"
DEFAULT_DB = Path(__file__).resolve().parents[4] / ".data" / "sales.db"  # backend/.data

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sales_orders (
    tenant_id       TEXT NOT NULL,
    order_id        TEXT NOT NULL,
    buyer_id        TEXT NOT NULL,      -- 가명 ID
    status          TEXT NOT NULL,
    payment_status  TEXT,               -- succeeded | failed | ... | NULL(결제 시도 없음)
    created_at      TEXT NOT NULL,      -- UTC
    updated_at      TEXT NOT NULL,
    items_amount    INTEGER NOT NULL,
    shipping_fee    INTEGER NOT NULL,
    points_used     INTEGER NOT NULL,
    coupon_discount INTEGER NOT NULL,
    src_source      TEXT,
    src_medium      TEXT,
    src_campaign    TEXT,
    synced_at       TEXT NOT NULL,      -- 이 판을 받은 시각
    PRIMARY KEY (tenant_id, order_id)
);
CREATE INDEX IF NOT EXISTS idx_sales_orders_created ON sales_orders(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sales_orders_buyer ON sales_orders(tenant_id, buyer_id, created_at);
CREATE TABLE IF NOT EXISTS sales_order_lines (
    tenant_id     TEXT NOT NULL,
    order_id      TEXT NOT NULL,
    line_no       INTEGER NOT NULL,     -- 원본 줄 순번 (버린 줄은 빈다)
    product_id    TEXT NOT NULL,
    category_code TEXT,
    title         TEXT,
    qty           INTEGER NOT NULL,
    unit_price    INTEGER NOT NULL,
    coupon_share  INTEGER NOT NULL,
    points_share  INTEGER NOT NULL,
    genre_code    TEXT,                 -- 상품 세부 분류 (11/16, 없으면 NULL)
    PRIMARY KEY (tenant_id, order_id, line_no)
);
CREATE TABLE IF NOT EXISTS sales_refunds (
    tenant_id   TEXT NOT NULL,
    order_id    TEXT NOT NULL,
    refund_no   INTEGER NOT NULL,
    refund_id   TEXT,
    amount      INTEGER NOT NULL,
    refunded_at TEXT NOT NULL,          -- UTC
    PRIMARY KEY (tenant_id, order_id, refund_no)
);
CREATE INDEX IF NOT EXISTS idx_sales_refunds_at ON sales_refunds(tenant_id, refunded_at);
CREATE TABLE IF NOT EXISTS sales_dropped (
    tenant_id   TEXT NOT NULL,
    order_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- order | line | refund
    ref         INTEGER NOT NULL,
    reason      TEXT NOT NULL,          -- 한국어 이유 (sales_clean)
    created_at  TEXT NOT NULL,          -- 주문 접수 시각 (기간별로 세려고)
    PRIMARY KEY (tenant_id, order_id, kind, ref)
);
CREATE TABLE IF NOT EXISTS sync_state (
    tenant_id       TEXT NOT NULL,
    source          TEXT NOT NULL,      -- 커넥터 이름 (예: shop-api)
    cursor          TEXT,
    last_synced_at  TEXT,               -- 끝까지 받은 마지막 시각 (실패한 수집은 바꾸지 않음)
    last_attempt_at TEXT,
    last_error      TEXT,               -- 마지막 시도의 실패 이유 (성공하면 NULL)
    PRIMARY KEY (tenant_id, source)
);
CREATE TABLE IF NOT EXISTS sales_customers (
    tenant_id   TEXT NOT NULL,
    buyer_id    TEXT NOT NULL,          -- 가명 ID
    joined_at   TEXT NOT NULL,          -- UTC
    updated_at  TEXT NOT NULL,
    synced_at   TEXT,                   -- 이 판을 받은 시각
    PRIMARY KEY (tenant_id, buyer_id)
);
CREATE TABLE IF NOT EXISTS sales_customer_attributes (
    tenant_id   TEXT NOT NULL,
    buyer_id    TEXT NOT NULL,
    key         TEXT NOT NULL,          -- 속성 key (카탈로그 [sales] attributes)
    value_code  TEXT NOT NULL,
    PRIMARY KEY (tenant_id, buyer_id, key)
);
"""

# 예전 DB 파일에 없을 수 있는 칸: (표, 칸, 형식). 열 때 없으면 더한다
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("sales_order_lines", "genre_code", "TEXT"),
)

CUSTOMERS_SUFFIX = ":customers"  # 고객 내보내기의 sync_state 출처 = `<판매 출처>:customers`
FAKE_SOURCE = "fake"  # 가짜 판매 출처(데모 전용, `sales_fake`)의 sync_state 출처 이름


def customers_source(source: str) -> str:
    """판매 기록 출처 이름 → 고객 내보내기 출처 이름 (커서를 따로 둔다)."""
    return f"{source}{CUSTOMERS_SUFFIX}"


def iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


@dataclass(frozen=True)
class StoredLine:
    product_id: str
    category_code: str | None
    title: str | None
    qty: int
    unit_price: int
    coupon_share: int
    points_share: int
    genre_code: str | None = None


@dataclass(frozen=True)
class StoredCustomer:
    buyer_id: str
    joined_at: datetime
    updated_at: datetime
    attributes: dict[str, str] = field(default_factory=dict)  # key → 값 code (없는 key = 모름)


@dataclass(frozen=True)
class StoredRefund:
    amount: int
    refunded_at: datetime


@dataclass
class StoredOrder:
    order_id: str
    buyer_id: str
    status: str
    payment_status: str | None
    created_at: datetime
    items_amount: int
    shipping_fee: int
    points_used: int
    coupon_discount: int
    src_source: str | None
    src_medium: str | None
    lines: list[StoredLine] = field(default_factory=list)
    refunds: list[StoredRefund] = field(default_factory=list)


@dataclass(frozen=True)
class SyncState:
    cursor: str | None
    last_synced_at: datetime | None
    last_attempt_at: datetime | None
    last_error: str | None
    source: str | None = None  # 출처 이름 (예: shop-api, fake)


@dataclass(frozen=True)
class UpsertResult:
    saved: int  # 저장(덮어쓰기 포함)한 주문
    dropped_orders: int  # 주문째 버린 주문
    dropped_parts: int  # 버린 줄·환불


class SalesStore:
    """SQLite 파일(또는 `:memory:`) 저장소. 연결 하나를 잠금으로 보호한다."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """예전 DB 파일에 새 칸을 더한다 (있으면 그대로). 잠금 안에서 부른다."""
        for table, column, kind in _ADDED_COLUMNS:
            have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------ 쓰기 (수집기)

    def upsert(
        self, tenant_id: str, rows: Iterable[SalesOrderRow], synced_at: datetime
    ) -> UpsertResult:
        """정제해서 덮어쓴다. 한 트랜잭션(페이지 하나가 함께 저장되거나 함께 취소된다)."""
        saved = dropped_orders = dropped_parts = 0
        stamp = iso(synced_at)
        with self._tx() as conn:
            for row in rows:
                key = (tenant_id, row.order_id)
                for table in ("sales_order_lines", "sales_refunds", "sales_dropped"):
                    conn.execute(f"DELETE FROM {table} WHERE tenant_id = ? AND order_id = ?", key)
                result = clean(row)
                created = iso(row.created_at)
                conn.executemany(
                    "INSERT INTO sales_dropped (tenant_id, order_id, kind, ref, reason, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    [(*key, d.kind, d.ref, d.reason, created) for d in result.dropped],
                )
                if result.order is None:
                    conn.execute(
                        "DELETE FROM sales_orders WHERE tenant_id = ? AND order_id = ?", key
                    )
                    dropped_orders += 1
                    continue
                dropped_parts += len(result.dropped)
                o = result.order
                conn.execute(
                    "INSERT INTO sales_orders (tenant_id, order_id, buyer_id, status, "
                    "payment_status, created_at, updated_at, items_amount, shipping_fee, "
                    "points_used, coupon_discount, src_source, src_medium, src_campaign, "
                    "synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(tenant_id, order_id) DO UPDATE SET buyer_id = excluded.buyer_id, "
                    "status = excluded.status, payment_status = excluded.payment_status, "
                    "created_at = excluded.created_at, updated_at = excluded.updated_at, "
                    "items_amount = excluded.items_amount, shipping_fee = excluded.shipping_fee, "
                    "points_used = excluded.points_used, "
                    "coupon_discount = excluded.coupon_discount, "
                    "src_source = excluded.src_source, src_medium = excluded.src_medium, "
                    "src_campaign = excluded.src_campaign, synced_at = excluded.synced_at",
                    (
                        *key,
                        o.buyer_id,
                        o.status,
                        o.payment_status,
                        created,
                        iso(o.updated_at),
                        o.items_amount,
                        o.shipping_fee,
                        o.points_used,
                        o.coupon_discount,
                        o.src_source,
                        o.src_medium,
                        o.src_campaign,
                        stamp,
                    ),
                )
                kept = {id(line) for line in result.lines}
                # 칸 이름을 적는다: 예전 DB 파일은 genre_code가 맨 뒤에 더해져 칸 순서가 다르다
                conn.executemany(
                    "INSERT INTO sales_order_lines (tenant_id, order_id, line_no, product_id, "
                    "category_code, title, qty, unit_price, coupon_share, points_share, "
                    "genre_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            *key,
                            i,
                            ln.product_id,
                            ln.category_code,
                            ln.title,
                            ln.qty,
                            ln.unit_price,
                            ln.coupon_share,
                            ln.points_share,
                            ln.genre_code,
                        )
                        for i, ln in enumerate(o.lines)
                        if id(ln) in kept
                    ],
                )
                kept_refunds = {id(r) for r in result.refunds}
                conn.executemany(
                    "INSERT INTO sales_refunds (tenant_id, order_id, refund_no, refund_id, "
                    "amount, refunded_at) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (*key, i, r.refund_id, r.amount, iso(r.refunded_at))
                        for i, r in enumerate(o.refunds)
                        if id(r) in kept_refunds
                    ],
                )
                saved += 1
        return UpsertResult(saved, dropped_orders, dropped_parts)

    def upsert_customers(
        self, tenant_id: str, rows: Iterable[CustomerRow], synced_at: datetime
    ) -> int:
        """고객을 덮어쓴다. 그 고객의 속성 줄은 지우고 받은 것으로 다시 넣는다. 한 트랜잭션."""
        saved = 0
        stamp = iso(synced_at)
        with self._tx() as conn:
            for row in rows:
                key = (tenant_id, row.buyer_id)
                conn.execute(
                    "INSERT INTO sales_customers (tenant_id, buyer_id, joined_at, updated_at, "
                    "synced_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(tenant_id, buyer_id) DO UPDATE "
                    "SET joined_at = excluded.joined_at, updated_at = excluded.updated_at, "
                    "synced_at = excluded.synced_at",
                    (*key, iso(row.joined_at), iso(row.updated_at), stamp),
                )
                conn.execute(
                    "DELETE FROM sales_customer_attributes WHERE tenant_id = ? AND buyer_id = ?",
                    key,
                )
                conn.executemany(
                    "INSERT INTO sales_customer_attributes (tenant_id, buyer_id, key, value_code) "
                    "VALUES (?, ?, ?, ?)",
                    [(*key, k, v) for k, v in row.attributes.items() if k and v],
                )
                saved += 1
        return saved

    def save_cursor(self, tenant_id: str, source: str, cursor: str | None) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO sync_state(tenant_id, source, cursor) VALUES (?, ?, ?) "
                "ON CONFLICT(tenant_id, source) DO UPDATE SET cursor = excluded.cursor",
                (tenant_id, source, cursor),
            )

    def finish_sync(
        self, tenant_id: str, source: str, attempted_at: datetime, error: str | None
    ) -> None:
        """수집 한 번을 마친다. 오류가 없을 때만 마지막 수집 시각을 바꾼다."""
        stamp = iso(attempted_at)
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO sync_state(tenant_id, source, last_synced_at, last_attempt_at, "
                "last_error) VALUES (?, ?, ?, ?, ?) ON CONFLICT(tenant_id, source) DO UPDATE SET "
                "last_attempt_at = excluded.last_attempt_at, last_error = excluded.last_error, "
                "last_synced_at = COALESCE(excluded.last_synced_at, last_synced_at)",
                (tenant_id, source, None if error else stamp, stamp, error),
            )

    # ------------------------------------------------------------------ 읽기 (분석·품질)

    def sync_state(self, tenant_id: str, source: str | None = None) -> SyncState:
        """출처를 주지 않으면 그 고객사에서 가장 최근에 끝까지 받은 판매 기록 출처
        (고객 내보내기 `<출처>:customers`는 빼고 본다. 매출 숫자의 신선도는 판매 기록이 정한다)."""
        sql = "SELECT * FROM sync_state WHERE tenant_id = ?"
        args: list[str] = [tenant_id]
        if source is not None:
            sql += " AND source = ?"
            args.append(source)
        else:
            sql += " AND source NOT LIKE ?"
            args.append(f"%{CUSTOMERS_SUFFIX}")
        sql += " ORDER BY last_synced_at DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, args).fetchone()
        if row is None:
            return SyncState(None, None, None, None)
        return SyncState(
            row["cursor"],
            parse(row["last_synced_at"]) if row["last_synced_at"] else None,
            parse(row["last_attempt_at"]) if row["last_attempt_at"] else None,
            row["last_error"],
            row["source"],
        )

    def customers(self, tenant_id: str) -> list[StoredCustomer]:
        """그 고객사의 고객 전부와 속성 (buyer_id 순서). 속성이 없는 key는 "모름"이다."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sales_customers WHERE tenant_id = ? ORDER BY buyer_id",
                (tenant_id,),
            ).fetchall()
            attrs = self._conn.execute(
                "SELECT buyer_id, key, value_code FROM sales_customer_attributes "
                "WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
        by_buyer: dict[str, dict[str, str]] = {}
        for a in attrs:
            by_buyer.setdefault(a["buyer_id"], {})[a["key"]] = a["value_code"]
        return [
            StoredCustomer(
                buyer_id=r["buyer_id"],
                joined_at=parse(r["joined_at"]),
                updated_at=parse(r["updated_at"]),
                attributes=by_buyer.get(r["buyer_id"], {}),
            )
            for r in rows
        ]

    def attribute_values(self, tenant_id: str) -> dict[str, set[str]]:
        """그 고객사 고객 속성에 나온 key → 값 code 모음 (품질 보고의 매핑 안 된 값용)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT key, value_code FROM sales_customer_attributes "
                "WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
        found: dict[str, set[str]] = {}
        for r in rows:
            found.setdefault(r["key"], set()).add(r["value_code"])
        return found

    def customer_count(self, tenant_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM sales_customers WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
        return int(row[0])

    def orders_created(self, tenant_id: str, start: datetime, end: datetime) -> list[StoredOrder]:
        """접수 시각이 [start, end)인 주문 (상태 무관), 줄·환불 포함."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sales_orders WHERE tenant_id = ? AND created_at >= ? "
                "AND created_at < ? ORDER BY created_at, order_id",
                (tenant_id, iso(start), iso(end)),
            ).fetchall()
            return self._with_details(tenant_id, rows)

    def orders_refunded(self, tenant_id: str, start: datetime, end: datetime) -> list[StoredOrder]:
        """환불 시각이 [start, end)인 환불이 있는 주문 (접수 시각 무관), 줄·환불 포함."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sales_orders o WHERE o.tenant_id = ? AND EXISTS (SELECT 1 FROM "
                "sales_refunds r WHERE r.tenant_id = o.tenant_id AND r.order_id = o.order_id "
                "AND r.refunded_at >= ? AND r.refunded_at < ?) ORDER BY o.created_at, o.order_id",
                (tenant_id, iso(start), iso(end)),
            ).fetchall()
            return self._with_details(tenant_id, rows)

    def buyers_paid_before(
        self, tenant_id: str, buyer_ids: Iterable[str], before: datetime
    ) -> set[str]:
        """`before` 전에 매출에 넣는 주문(결제 완료, 취소 아님)이 있는 구매자."""
        ids = sorted(set(buyer_ids))
        found: set[str] = set()
        excluded = sorted(EXCLUDED_STATUSES)
        with self._lock:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ",".join("?" * len(chunk))
                status_marks = ",".join("?" * len(excluded))
                found.update(
                    r["buyer_id"]
                    for r in self._conn.execute(
                        f"SELECT DISTINCT buyer_id FROM sales_orders WHERE tenant_id = ? "
                        f"AND buyer_id IN ({marks}) AND created_at < ? AND payment_status = ? "
                        f"AND LOWER(status) NOT IN ({status_marks})",
                        (tenant_id, *chunk, iso(before), PAID, *excluded),
                    )
                )
        return found

    def dropped(self, tenant_id: str, start: datetime, end: datetime) -> dict[str, int]:
        """접수 시각이 [start, end)인 주문에서 버린 것: 이유 → 수."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT reason, COUNT(*) AS n FROM sales_dropped WHERE tenant_id = ? "
                "AND created_at >= ? AND created_at < ? GROUP BY reason ORDER BY reason",
                (tenant_id, iso(start), iso(end)),
            ).fetchall()
        return {r["reason"]: r["n"] for r in rows}

    def order_count(self, tenant_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM sales_orders WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
        return int(row[0])

    def _with_details(self, tenant_id: str, rows: list[sqlite3.Row]) -> list[StoredOrder]:
        orders = {
            r["order_id"]: StoredOrder(
                order_id=r["order_id"],
                buyer_id=r["buyer_id"],
                status=r["status"],
                payment_status=r["payment_status"],
                created_at=parse(r["created_at"]),
                items_amount=r["items_amount"],
                shipping_fee=r["shipping_fee"],
                points_used=r["points_used"],
                coupon_discount=r["coupon_discount"],
                src_source=r["src_source"],
                src_medium=r["src_medium"],
            )
            for r in rows
        }
        ids = list(orders)
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            for ln in self._conn.execute(
                f"SELECT * FROM sales_order_lines WHERE tenant_id = ? AND order_id IN ({marks}) "
                "ORDER BY order_id, line_no",
                (tenant_id, *chunk),
            ):
                orders[ln["order_id"]].lines.append(
                    StoredLine(
                        product_id=ln["product_id"],
                        category_code=ln["category_code"],
                        title=ln["title"],
                        qty=ln["qty"],
                        unit_price=ln["unit_price"],
                        coupon_share=ln["coupon_share"],
                        points_share=ln["points_share"],
                        genre_code=ln["genre_code"],
                    )
                )
            for rf in self._conn.execute(
                f"SELECT * FROM sales_refunds WHERE tenant_id = ? AND order_id IN ({marks}) "
                "ORDER BY order_id, refund_no",
                (tenant_id, *chunk),
            ):
                orders[rf["order_id"]].refunds.append(
                    StoredRefund(rf["amount"], parse(rf["refunded_at"]))
                )
        return list(orders.values())
