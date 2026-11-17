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
- 인덱스는 모두 `CREATE INDEX IF NOT EXISTS`라 예전 DB 파일도 열 때 붙는다
- 집계 읽기(11/17, 고객·시장 분석): 주문을 파이썬으로 올리지 않고 SQL `GROUP BY`·
  `COUNT(DISTINCT buyer_id)`로 구매자 수만 돌려준다. "결제 완료"는 `sales_clean.counts_as_revenue`와
  같은 조건(payment_status = succeeded, 상태가 EXCLUDED_STATUSES 아님)이다. 고객 속성은 구매자마다
  `keys` 순서의 값 묶음(셀, 없으면 None)으로 센다
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
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
-- 고객·시장 분석 집계(11/17): 기간 안 결제 완료 구매자를 표를 읽지 않고 인덱스만으로 고른다
CREATE INDEX IF NOT EXISTS idx_sales_orders_paid
    ON sales_orders(tenant_id, created_at, payment_status, status, buyer_id, order_id);
-- 주문 id 순으로 덮는 인덱스: 주문 × 줄 조인을 양쪽 다 주문 id 순서로 읽게 한다 (무작위 읽기 없음)
CREATE INDEX IF NOT EXISTS idx_sales_orders_byid ON sales_orders(
    tenant_id, order_id, created_at, payment_status, status, buyer_id, coupon_discount, points_used
);
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
-- 고객·시장 분석(11/17): 대상(장르·분류·상품) 하나의 줄을 바로 찾고, 코드 목록을 빨리 본다.
-- genre_code 인덱스는 예전 파일에 칸을 더한 뒤 만든다 (_AFTER_COLUMNS)
CREATE INDEX IF NOT EXISTS idx_sales_lines_category
    ON sales_order_lines(tenant_id, category_code, order_id);
CREATE INDEX IF NOT EXISTS idx_sales_lines_product
    ON sales_order_lines(tenant_id, product_id, order_id);
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
# 더한 칸을 쓰는 인덱스 (칸을 더한 뒤에 만든다)
_AFTER_COLUMNS = """
CREATE INDEX IF NOT EXISTS idx_sales_lines_genre
    ON sales_order_lines(tenant_id, genre_code, order_id);
"""

# 고객·시장 분석의 대상 종류 → 주문 줄 칸·그 칸의 인덱스 (코드 상수라 SQL에 그대로 넣는다)
ITEM_COLUMNS: dict[str, str] = {
    "genre": "genre_code",
    "category": "category_code",
    "product": "product_id",
}
LINE_INDEX: dict[str, str] = {
    "genre": "idx_sales_lines_genre",
    "category": "idx_sales_lines_category",
    "product": "idx_sales_lines_product",
}

# 집계 세션 동안의 페이지 캐시 (음수 = KiB). 주문 30만 행 기준 임시 표·정렬이 40MB 안팎
SEGMENT_CACHE_KIB = -131072  # 128MB
REPEAT_DAYS = (30, 60, 90)  # 재구매를 보는 날 수 (첫 구매 뒤, retention 계약 r30·r60·r90)
DEFAULT_CACHE_KIB = -2000  # SQLite 기본값 (약 2MB)

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
            self._conn.executescript(_AFTER_COLUMNS)

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

    # ------------------------------------------------------------------ 집계 읽기 (고객·시장 분석)

    def _rows(self, sql: str, params: dict[str, object]) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    @staticmethod
    def _window(tenant_id: str, start: datetime, end: datetime) -> dict[str, object]:
        params: dict[str, object] = {"t": tenant_id, "s": iso(start), "e": iso(end), "paid": PAID}
        params.update({f"x{i}": s for i, s in enumerate(sorted(EXCLUDED_STATUSES))})
        return params

    @staticmethod
    def _paid_cte() -> str:
        """기간 안 결제 완료 주문 (counts_as_revenue와 같은 조건)."""
        excluded = ", ".join(f":x{i}" for i in range(len(EXCLUDED_STATUSES)))
        return (
            "paid AS (SELECT order_id, buyer_id, created_at, coupon_discount, points_used "
            "FROM sales_orders WHERE tenant_id = :t AND created_at >= :s AND created_at < :e "
            f"AND payment_status = :paid AND LOWER(status) NOT IN ({excluded}))"
        )

    @contextmanager
    def segment_session(
        self, tenant_id: str, start: datetime, end: datetime, keys: Sequence[str]
    ) -> Iterator[SegmentSession]:
        """고객·시장 분석 한 번의 집계 읽기. 연결을 잡고 임시 표(기간 안 결제 완료 주문, 구매자별
        속성)를 만들어 여러 집계에 같이 쓰고, 끝나면 지운다. 숫자만 돌려준다."""
        with self._lock:
            # 임시 표·정렬을 메모리에 (디스크 임시 파일로 넘기지 않게). 끝나면 기본값으로 되돌린다.
            # temp_store를 바꾸면 있던 임시 표가 지워지므로 임시 표를 만들기 전에 바꾼다
            self._conn.execute("PRAGMA temp_store = MEMORY")
            self._conn.execute(f"PRAGMA cache_size = {SEGMENT_CACHE_KIB}")
            session = SegmentSession(self, self._conn, tenant_id, start, end, keys)
            try:
                yield session
            finally:
                session.drop()
                self._conn.commit()
                self._conn.execute("PRAGMA temp_store = DEFAULT")
                self._conn.execute(f"PRAGMA cache_size = {DEFAULT_CACHE_KIB}")

    def order_counts(self, tenant_id: str, start: datetime, end: datetime) -> tuple[int, int]:
        """기간 [start, end)에 접수한 주문 수(상태 무관)와 그중 결제 완료 주문 수."""
        params = self._window(tenant_id, start, end)
        row = self._rows(
            f"WITH {self._paid_cte()} SELECT "
            "(SELECT COUNT(*) FROM sales_orders WHERE tenant_id = :t AND created_at >= :s "
            "AND created_at < :e) AS created, (SELECT COUNT(*) FROM paid) AS paid_n",
            params,
        )[0]
        return int(row["created"]), int(row["paid_n"])

    def product_title(
        self, tenant_id: str, start: datetime, end: datetime, product_id: str
    ) -> str | None:
        """상품 하나의 이름: 기간 안 결제 완료 주문을 (접수 시각, 주문 id, 줄 순번) 순으로 볼 때
        처음 나온 이름 (줄 인덱스로 그 상품 줄만 본다). 없으면 None."""
        excluded = ", ".join(f":x{i}" for i in range(len(EXCLUDED_STATUSES)))
        rows = self._rows(
            "SELECT l.title FROM sales_order_lines l INDEXED BY idx_sales_lines_product "
            "CROSS JOIN sales_orders o INDEXED BY idx_sales_orders_byid ON o.tenant_id = :t "
            "AND o.order_id = l.order_id WHERE l.tenant_id = :t AND l.product_id = :pid "
            "AND l.title IS NOT NULL AND l.title != '' AND o.created_at >= :s "
            f"AND o.created_at < :e AND o.payment_status = :paid AND LOWER(o.status) NOT IN "
            f"({excluded}) ORDER BY o.created_at, o.order_id, l.line_no LIMIT 1",
            {**self._window(tenant_id, start, end), "pid": product_id},
        )
        return rows[0]["title"] if rows else None

    def order_days(self, tenant_id: str, start: datetime, end: datetime) -> set[str]:
        """기간 안 주문(상태 무관)이 있는 한국 날짜 "YYYY-MM-DD"."""
        rows = self._rows(
            "SELECT DISTINCT date(substr(created_at, 1, 19), '+9 hours') AS d FROM sales_orders "
            "WHERE tenant_id = :t AND created_at >= :s AND created_at < :e",
            self._window(tenant_id, start, end),
        )
        return {r["d"] for r in rows}

    def unknown_line_codes(  # noqa: PLR0913 - 기간과 아는 코드 두 목록
        self, tenant_id: str, start: datetime, end: datetime, categories: Iterable[str],
        genres: Iterable[str],
    ) -> tuple[set[str], set[str]]:  # fmt: skip
        """기간 안 주문(상태 무관) 줄에 나온 category_code·genre_code 중 `categories`·`genres`에
        없는 것 (빈 값 제외). 표 전체의 코드 목록(인덱스, 몇 개뿐)을 먼저 보고, 모르는 코드만
        기간 안에 나왔는지 확인한다."""
        out: tuple[set[str], set[str]] = (set(), set())
        params = self._window(tenant_id, start, end)
        for found, column, known in zip(
            out, ("category_code", "genre_code"), (set(categories), set(genres)), strict=True
        ):
            codes = self._rows(
                f"SELECT DISTINCT {column} AS c FROM sales_order_lines WHERE tenant_id = :t "
                f"AND {column} IS NOT NULL AND {column} != ''",
                params,
            )
            for code in sorted({r["c"] for r in codes} - known):
                hit = self._rows(
                    "SELECT 1 FROM sales_order_lines l JOIN sales_orders o ON o.tenant_id = :t "
                    f"AND o.order_id = l.order_id WHERE l.tenant_id = :t AND l.{column} = :code "
                    "AND o.created_at >= :s AND o.created_at < :e LIMIT 1",
                    {**params, "code": code},
                )
                if hit:
                    found.add(code)
        return out

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


Cells = dict[tuple, int]  # 속성 값 셀(keys 순서, 없으면 None) → 구매자 수
_NON_EMPTY = "{c} IS NOT NULL AND {c} != ''"


class SegmentSession:
    """`SalesStore.segment_session` 안에서만 쓴다 (연결 잠금을 잡은 채).

    결제 완료 주문은 `idx_sales_orders_paid`(덮는 인덱스)로 바로 읽는다. 임시 표
    - `seg_attrs`(buyer_id 키): 구매자별 속성 값 v0..vn (`keys` 순서, 없으면 NULL)
    - `seg_lines`(처음 필요할 때 한 번): 기간 안 결제 완료 주문의 줄 (주문 × 줄 조인을 한 번만)

    `COUNT(DISTINCT …)`를 여러 개 겹치지 않게, 먼저 (code, 구매자)를 한 번 걸러 놓고 센다.
    """

    def __init__(  # noqa: PLR0913 - 저장소·연결·기간·속성
        self, store: SalesStore, conn: sqlite3.Connection, tenant_id: str, start: datetime,
        end: datetime, keys: Sequence[str],
    ) -> None:  # fmt: skip
        self.store, self.conn, self.tenant_id, self.keys = store, conn, tenant_id, list(keys)
        self.params = SalesStore._window(tenant_id, start, end)  # noqa: SLF001
        self._lines = False
        self._buyers = False
        excluded = ", ".join(f":x{i}" for i in range(len(EXCLUDED_STATUSES)))
        self.paid_where = (
            "o.tenant_id = :t AND o.created_at >= :s AND o.created_at < :e "
            f"AND o.payment_status = :paid AND LOWER(o.status) NOT IN ({excluded})"
        )
        self.drop()
        cols = "".join(f", v{i} TEXT" for i in range(len(self.keys)))
        conn.execute(f"CREATE TEMP TABLE seg_attrs (buyer_id TEXT PRIMARY KEY{cols}) WITHOUT ROWID")
        if self.keys:
            pivot = ", ".join(
                f"MAX(CASE WHEN key = :k{i} THEN value_code END)" for i in range(len(self.keys))
            )
            marks = ", ".join(f":k{i}" for i in range(len(self.keys)))
            conn.execute(
                f"INSERT INTO seg_attrs SELECT buyer_id, {pivot} FROM sales_customer_attributes "
                f"WHERE tenant_id = :t AND key IN ({marks}) GROUP BY buyer_id",
                {"t": tenant_id, **{f"k{i}": k for i, k in enumerate(self.keys)}},
            )

    def drop(self) -> None:
        for name in ("seg_attrs", "seg_lines", "seg_buyers"):
            self.conn.execute(f"DROP TABLE IF EXISTS temp.{name}")

    def _all(self, sql: str, params: dict[str, object] | None = None) -> list[sqlite3.Row]:
        return self.conn.execute(sql, {**self.params, **(params or {})}).fetchall()

    def _cells_of(self, buyers: str, params: dict[str, object] | None = None) -> Cells:
        """`buyers`(buyer_id 한 칸, 중복 없는 SELECT) 구매자를 속성 값 셀로 센다."""
        if not self.keys:
            n = int(self._all(f"SELECT COUNT(*) AS n FROM ({buyers})", params)[0]["n"])
            return {(): n} if n else {}
        cols = ", ".join(f"a.v{i}" for i in range(len(self.keys)))
        rows = self._all(
            f"SELECT {cols}, COUNT(*) AS n FROM ({buyers}) b "
            f"LEFT JOIN seg_attrs a ON a.buyer_id = b.buyer_id GROUP BY {cols}",
            params,
        )
        return {tuple(r[f"v{i}"] for i in range(len(self.keys))): int(r["n"]) for r in rows}

    def lines(self) -> None:
        """결제 완료 주문 줄 임시 표 (처음 한 번만 조인)."""
        if self._lines:
            return
        # 주문 id 순으로 읽어 넣는다. (장르, 구매자) 인덱스는 장르별 구매자 집계용
        self.conn.execute(
            "CREATE TEMP TABLE seg_lines AS SELECT o.order_id, o.buyer_id, o.created_at, "
            "o.coupon_discount + o.points_used AS disc, l.line_no, l.genre_code, "
            "l.category_code, l.product_id, l.title, l.qty * l.unit_price AS gross, "
            "l.coupon_share + l.points_share AS share FROM sales_orders o "
            "INDEXED BY idx_sales_orders_byid CROSS JOIN sales_order_lines l "
            f"ON l.tenant_id = :t AND l.order_id = o.order_id WHERE {self.paid_where}",
            self.params,
        )
        self.conn.execute("CREATE INDEX temp.seg_lines_genre ON seg_lines(genre_code, buyer_id)")
        self._lines = True

    # ---- 집계 (숫자만)

    def buyer_cells(self) -> Cells:
        """기간 안 결제 완료 구매자 수: 셀 → 명."""
        return self._cells_of(
            f"SELECT DISTINCT o.buyer_id FROM sales_orders o WHERE {self.paid_where}"
        )

    def active_cells(self, start: datetime, end: datetime) -> Cells:
        """다른 기간 [start, end)의 결제 완료 구매자 수 (예: 대상 고객 = 최근 365일): 셀 → 명."""
        return self._cells_of(
            f"SELECT DISTINCT o.buyer_id FROM sales_orders o WHERE {self.paid_where}",
            {"s": iso(start), "e": iso(end)},
        )

    def item_cells(self, kind: str, code: str) -> Cells:
        """그 대상(장르·분류·상품)이 든 결제 완료 주문의 구매자 수: 셀 → 명 (줄 인덱스로 찾는다)."""
        column = ITEM_COLUMNS[kind]
        return self._cells_of(
            f"SELECT DISTINCT o.buyer_id FROM sales_order_lines l INDEXED BY {LINE_INDEX[kind]} "
            "CROSS JOIN sales_orders o INDEXED BY idx_sales_orders_byid "
            "ON o.tenant_id = :t AND o.order_id = l.order_id "
            f"WHERE l.tenant_id = :t AND l.{column} = :code AND {self.paid_where}",
            {"code": code},
        )

    def item_counts(self, filters: dict[str, str]) -> list[tuple[str, str, int, int]]:
        """(종류, code, 산 구매자 수, 그중 속성 조건이 모두 맞는 구매자 수). 종류는 genre·
        category·product, 빈 code는 세지 않는다. `filters`의 key는 세션 `keys`에 있어야 한다."""
        self.lines()
        match = " AND ".join(
            f"a.v{self.keys.index(k)} = :f{i}" for i, k in enumerate(filters)
        ) or "1"  # fmt: skip
        params = {f"f{i}": v for i, v in enumerate(filters.values())}
        segment = f"SELECT a.buyer_id FROM seg_attrs a WHERE {match}"
        out: list[tuple[str, str, int, int]] = []
        for kind, column in ITEM_COLUMNS.items():
            rows = self._all(
                f"SELECT {column} AS code, COUNT(DISTINCT buyer_id) AS total, "
                f"COUNT(DISTINCT CASE WHEN buyer_id IN ({segment}) THEN buyer_id END) AS n "
                f"FROM seg_lines WHERE {_NON_EMPTY.format(c=column)} GROUP BY {column}",
                params,
            )
            out += [(kind, r["code"], int(r["total"]), int(r["n"])) for r in rows]
        return out

    def titles(self, *, genre_only: bool = False) -> dict[str, str]:
        """상품 id → 이름: 결제 완료 주문을 (접수 시각, 주문 id, 줄 순번) 순으로 볼 때 처음 나온
        이름. `genre_only`면 genre_code가 있는 줄만 본다."""
        self.lines()
        where = f"AND {_NON_EMPTY.format(c='genre_code')} " if genre_only else ""
        rows = self._all(
            "SELECT product_id, title FROM (SELECT product_id, title, ROW_NUMBER() OVER "
            "(PARTITION BY product_id ORDER BY created_at, order_id, line_no) AS rn "
            f"FROM seg_lines WHERE product_id != '' AND {_NON_EMPTY.format(c='title')} "
            f"{where}) WHERE rn = 1"
        )
        return {r["product_id"]: r["title"] for r in rows}

    def genre_cells(self) -> dict[str, Cells]:
        """장르 → (그 장르가 든 결제 완료 주문의 구매자 수: 셀 → 명)."""
        self.lines()
        cols = "".join(f", a.v{i}" for i in range(len(self.keys)))
        rows = self._all(
            f"SELECT x.g{cols}, COUNT(*) AS n FROM (SELECT DISTINCT genre_code AS g, buyer_id "
            f"FROM seg_lines WHERE {_NON_EMPTY.format(c='genre_code')}) x "
            f"LEFT JOIN seg_attrs a ON a.buyer_id = x.buyer_id GROUP BY x.g{cols}"
        )
        out: dict[str, Cells] = {}
        for r in rows:
            cell = tuple(r[f"v{i}"] for i in range(len(self.keys)))
            out.setdefault(r["g"], {})[cell] = int(r["n"])
        return out

    def genre_products(self) -> dict[str, dict[str, int]]:
        """장르 → 상품 id → 산 구매자 수 (genre_code·product_id가 있는 줄)."""
        self.lines()
        # (장르, 구매자) 인덱스를 타면 줄마다 키로 다시 찾아 느리다 → 표를 한 번 읽는다(+genre_code)
        rows = self._all(
            "SELECT genre_code AS g, product_id AS pid, COUNT(DISTINCT buyer_id) AS n "
            "FROM seg_lines WHERE +genre_code IS NOT NULL AND +genre_code != '' "
            "AND product_id != '' GROUP BY genre_code, product_id"
        )
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["g"], {})[r["pid"]] = int(r["n"])
        return out

    def genre_nets(self) -> tuple[dict[str, tuple[int, int]], list[StoredOrder]]:
        """장르 → (그 장르 줄 순매출 합, 그 장르가 든 주문 수). 줄 할인 몫 합이 주문 할인(쿠폰 +
        포인트)과 같은 주문만 더한다. 다른 주문은 줄을 붙여 돌려준다(호출자가
        `sales_analysis.line_nets`의 배분으로 더한다)."""
        self.lines()
        uneven_ids = (
            "SELECT order_id FROM seg_lines GROUP BY order_id HAVING SUM(share) != MAX(disc)"
        )
        rows = self._all(
            "SELECT genre_code AS g, SUM(gross - share) AS net, COUNT(DISTINCT order_id) AS orders "
            "FROM seg_lines WHERE +genre_code IS NOT NULL AND +genre_code != '' "
            f"AND order_id NOT IN ({uneven_ids}) GROUP BY genre_code"
        )
        odd = self._all(
            f"SELECT o.* FROM sales_orders o WHERE o.tenant_id = :t AND o.order_id IN "
            f"({uneven_ids}) ORDER BY o.created_at, o.order_id"
        )
        uneven = self.store._with_details(self.tenant_id, odd)  # noqa: SLF001 - 잠금 안
        return {r["g"]: (int(r["net"]), int(r["orders"])) for r in rows}, uneven

    # ---- 구매자별 기록 (재구매·이탈, 11/17)

    def buyers(self) -> None:
        """구매자별 결제 완료 주문의 첫·마지막 접수 시각과 주문 수 임시 표.

        세션 기간 안의 주문만 보고, 처음 한 번만 만든다."""
        if self._buyers:
            return
        self.conn.execute(
            "CREATE TEMP TABLE seg_buyers (buyer_id TEXT PRIMARY KEY, first_at TEXT NOT NULL, "
            "last_at TEXT NOT NULL, orders INTEGER NOT NULL) WITHOUT ROWID"
        )
        self.conn.execute(
            "INSERT INTO seg_buyers SELECT o.buyer_id, MIN(o.created_at), MAX(o.created_at), "
            f"COUNT(*) FROM sales_orders o WHERE {self.paid_where} GROUP BY o.buyer_id",
            self.params,
        )
        self._buyers = True

    def _cell_cols(self, alias: str = "a") -> str:
        return "".join(f", {alias}.v{i}" for i in range(len(self.keys)))

    def _cell(self, row: sqlite3.Row) -> tuple:
        return tuple(row[f"v{i}"] for i in range(len(self.keys)))

    def cohort_counts(
        self, first_from: datetime, first_to: datetime
    ) -> list[tuple[str, tuple, int, int, int, int]]:
        """첫 결제 완료 주문이 [first_from, first_to)인 구매자: (첫 구매 달 KST "YYYY-MM", 셀,
        사람 수, 30·60·90일 안 결제 완료 주문이 2건 이상인 사람 수). N일 안 = 첫 구매 시각 이상,
        첫 구매 시각 + N일 미만."""
        self.buyers()

        def until(days: int) -> str:
            return f"strftime('%Y-%m-%dT%H:%M:%SZ', substr(b.first_at, 1, 19), '+{days} days')"

        rows = self._all(
            "WITH c AS (SELECT b.buyer_id, "
            + ", ".join(
                f"SUM(CASE WHEN o.created_at < {until(d)} THEN 1 ELSE 0 END) AS c{d}"
                for d in REPEAT_DAYS
            )
            + " FROM seg_buyers b CROSS JOIN sales_orders o INDEXED BY idx_sales_orders_buyer "
            "ON o.tenant_id = :t AND o.buyer_id = b.buyer_id AND o.created_at >= b.first_at "
            f"AND o.created_at < {until(max(REPEAT_DAYS))} WHERE b.first_at >= :cf "
            f"AND b.first_at < :ct AND {self.paid_where} GROUP BY b.buyer_id) "
            "SELECT strftime('%Y-%m', substr(b.first_at, 1, 19), '+9 hours') AS m"
            f"{self._cell_cols()}, COUNT(*) AS n, "
            + ", ".join(
                f"SUM(CASE WHEN c.c{d} >= 2 THEN 1 ELSE 0 END) AS r{d}" for d in REPEAT_DAYS
            )
            + " FROM c CROSS JOIN seg_buyers b ON b.buyer_id = c.buyer_id "
            f"LEFT JOIN seg_attrs a ON a.buyer_id = c.buyer_id GROUP BY m{self._cell_cols()}",
            {"cf": iso(first_from), "ct": iso(first_to)},
        )
        return [
            (r["m"], self._cell(r), int(r["n"]), int(r["r30"]), int(r["r60"]), int(r["r90"]))
            for r in rows
        ]

    def regular_counts(
        self, min_orders: int, last_before: datetime
    ) -> list[tuple[tuple, int, int]]:
        """단골(결제 완료 주문 `min_orders`건 이상)을 셀로: (셀, 단골 수, 마지막 주문이
        `last_before` 전인 단골 수)."""
        self.buyers()
        rows = self._all(
            f"SELECT 1 AS one{self._cell_cols()}, COUNT(*) AS n, "
            "SUM(CASE WHEN b.last_at < :cut THEN 1 ELSE 0 END) AS risk FROM seg_buyers b "
            "LEFT JOIN seg_attrs a ON a.buyer_id = b.buyer_id WHERE b.orders >= :min "
            f"GROUP BY one{self._cell_cols()}",
            {"min": min_orders, "cut": iso(last_before)},
        )
        return [(self._cell(r), int(r["n"]), int(r["risk"])) for r in rows]

    def regular_genres(self, min_orders: int) -> list[tuple[tuple, str, int]]:
        """단골이 산 장르: (셀, genre_code, 그 장르를 산 단골 수). 세션 기간 안 결제 완료 줄."""
        self.buyers()
        self.lines()
        rows = self._all(
            f"SELECT x.genre_code AS g{self._cell_cols()}, COUNT(DISTINCT x.buyer_id) AS n "
            "FROM seg_lines x CROSS JOIN seg_buyers b ON b.buyer_id = x.buyer_id "
            "LEFT JOIN seg_attrs a ON a.buyer_id = x.buyer_id WHERE b.orders >= :min "
            "AND +x.genre_code IS NOT NULL AND +x.genre_code != '' "
            f"GROUP BY x.genre_code{self._cell_cols()}",
            {"min": min_orders},
        )
        return [(self._cell(r), r["g"], int(r["n"])) for r in rows]
