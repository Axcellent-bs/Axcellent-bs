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

import logging
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderRow
from data_agent import sales_summary
from data_agent.sales_clean import EXCLUDED_STATUSES, PAID, clean

logger = logging.getLogger(__name__)

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
-- 매출 분석 집계(11/18, `sales_sql`): 주문 id 순 + 유입 경로·금액까지 덮어 표를 읽지 않는다
CREATE INDEX IF NOT EXISTS idx_sales_orders_sales ON sales_orders(
    tenant_id, order_id, created_at, payment_status, status, buyer_id, coupon_discount,
    points_used, items_amount, src_source, src_medium
);
-- 재구매 고객(기간 시작 전 결제 완료 주문) 확인을 표를 읽지 않고
CREATE INDEX IF NOT EXISTS idx_sales_orders_buyer_paid
    ON sales_orders(tenant_id, buyer_id, created_at, payment_status, status);
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
    # 저장소 버전 (11/18): 고객사마다 `VERSION_SOURCE` 줄에 둔다. 새 행을 저장할 때마다 +1
    ("sync_state", "data_version", "INTEGER NOT NULL DEFAULT 0"),
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
VERSION_SOURCE = "*version*"  # sync_state의 저장소 버전 줄 (판매 기록 출처가 아니다)
# sync_state의 저장소 생성 id 줄 (11/17): 새 파일을 만들 때 한 번만 uuid를 `cursor` 칸에 넣는다.
# 리셋한 새 저장소가 옛 저장소와 같은 버전 번호를 다시 써도 결과 캐시가 섞이지 않게 한다
STORE_ID_SOURCE = "*store*"
STORE_ID_TENANT = "*"
_META_SOURCES = (VERSION_SOURCE, STORE_ID_SOURCE)
WRITE_TIMEOUT = 5.0  # 연결이 잠금을 기다리는 초
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
    """SQLite 파일(또는 `:memory:`) 저장소.

    연결 (11/18)
    - 쓰기(수집기): 쓰기 연결 하나를 잠금으로 보호한다 (`_tx`)
    - 읽기(분석): 파일이면 스레드마다 따로 여는 **읽기 전용 연결**(읽기 전용 URI + `query_only`)을
      잠금 없이 쓴다 (`reading`). 파일은 WAL이라 수집이 쓰는 동안에도 읽기가 막히지 않는다
    - `:memory:`(시험)는 연결끼리 자료를 나눌 수 없어 예전처럼 쓰기 연결을 잠금으로 같이 쓴다
    """

    # 분석이 구매자 요약 표(`sales_summary`)를 쓸지 (비교 시험은 False로 예전 SQL과 견준다)
    use_summary: bool = True

    def __init__(self, path: str | Path) -> None:
        self.memory = str(path) == ":memory:"
        if not self.memory:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.cache_token = uuid.uuid4().hex  # 결과 캐시가 저장소를 가리는 값 (`sales_cache`)
        self._lock = threading.RLock()  # 쓰기 연결 (읽기 안에서 같은 스레드가 다시 잡을 수 있게)
        self._local = threading.local()  # 스레드별 읽기 전용 연결
        self._readers: list[sqlite3.Connection] = []
        self._readers_lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=WRITE_TIMEOUT)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._use_wal()
            self._conn.executescript(_SCHEMA)
            self._add_missing_columns()
            self._conn.executescript(_AFTER_COLUMNS)
            self.store_id = self._ensure_store_id()
            # 구매자 요약 표 (11/18, `sales_summary`). 주문이 없는 새 저장소면 처음부터 준비됨
            self._conn.executescript(sales_summary.SCHEMA)
            sales_summary.mark_new_store(self._conn)

    def _ensure_store_id(self) -> str:
        """저장소 생성 id: 없으면(새 파일·예전 파일) 한 번만 만들어 남기고, 있으면 그대로 읽는다."""
        self._conn.execute(
            "INSERT OR IGNORE INTO sync_state (tenant_id, source, cursor) VALUES (?, ?, ?)",
            (STORE_ID_TENANT, STORE_ID_SOURCE, uuid.uuid4().hex),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT cursor FROM sync_state WHERE tenant_id = ? AND source = ?",
            (STORE_ID_TENANT, STORE_ID_SOURCE),
        ).fetchone()
        return str(row["cursor"])

    def _use_wal(self) -> None:
        """파일 저장소는 WAL로 (예전 파일도 열 때 바뀐다). 다른 연결이 잡고 있어 못 바꾸면
        그대로."""
        if self.memory:
            return
        try:
            self._conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError as error:  # 다른 프로세스가 쓰는 중: 다음에 열 때 바꾼다
            logger.warning("판매 저장소를 WAL로 바꾸지 못함: %s", error)

    def _reader(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        uri = Path(self.path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=WRITE_TIMEOUT)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA temp_store = MEMORY")  # 분석 임시 표·정렬은 메모리에
        conn.execute("PRAGMA query_only = 1")
        self._local.conn = conn
        with self._readers_lock:
            self._readers.append(conn)
        return conn

    @contextmanager
    def reading(self) -> Iterator[sqlite3.Connection]:
        """읽기 연결. 파일이면 이 스레드의 읽기 전용 연결(잠금 없음), `:memory:`면 쓰기 연결+잠금.

        끝날 때 열린 거래를 닫는다 (읽기 연결이 옛 자료를 붙잡고 있지 않게)."""
        if self.memory:
            with self._lock:
                yield self._conn
                if self._conn.in_transaction:
                    self._conn.commit()
            return
        conn = self._reader()
        try:
            yield conn
        finally:
            if conn.in_transaction:
                conn.commit()

    @contextmanager
    def analysis(self) -> Iterator[sqlite3.Connection]:
        """분석 문맥: 읽기 연결에서 임시 표를 쓸 수 있게(`query_only` 잠시 해제, 본 DB는 여전히 읽기
        전용 URI) 하고 페이지 캐시를 키운다. 끝나면 되돌린다. 고객·시장 분석·매출 분석이 쓴다."""
        with self.reading() as conn:
            if self.memory:
                conn.execute("PRAGMA temp_store = MEMORY")
            else:
                conn.execute("PRAGMA query_only = 0")
            conn.execute(f"PRAGMA cache_size = {SEGMENT_CACHE_KIB}")
            try:
                yield conn
            finally:
                if conn.in_transaction:
                    conn.commit()
                conn.execute(f"PRAGMA cache_size = {DEFAULT_CACHE_KIB}")
                if self.memory:
                    conn.execute("PRAGMA temp_store = DEFAULT")
                else:
                    conn.execute("PRAGMA query_only = 1")

    def data_version(self, tenant_id: str) -> int:
        """저장소 버전: 수집이 새 행(주문·고객)을 저장할 때마다 오른다 (결과 캐시 키)."""
        with self.reading() as conn:
            row = conn.execute(
                "SELECT data_version FROM sync_state WHERE tenant_id = ? AND source = ?",
                (tenant_id, VERSION_SOURCE),
            ).fetchone()
        return int(row["data_version"] or 0) if row else 0

    def _bump_version(self, conn: sqlite3.Connection, tenant_id: str) -> None:
        conn.execute(
            "INSERT INTO sync_state (tenant_id, source, data_version) VALUES (?, ?, 1) "
            "ON CONFLICT(tenant_id, source) DO UPDATE SET data_version = data_version + 1",
            (tenant_id, VERSION_SOURCE),
        )

    def _add_missing_columns(self) -> None:
        """예전 DB 파일에 새 칸을 더한다 (있으면 그대로). 잠금 안에서 부른다."""
        for table, column, kind in _ADDED_COLUMNS:
            have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        self._conn.commit()

    def close(self) -> None:
        with self._readers_lock:
            readers, self._readers = self._readers, []
        for conn in readers:
            conn.close()
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
        touched: set[str] = set()  # 요약 표를 다시 셀 구매자 (주문의 예전·새 buyer_id)
        with self._tx() as conn:
            for row in rows:
                key = (tenant_id, row.order_id)
                before = conn.execute(
                    "SELECT buyer_id FROM sales_orders WHERE tenant_id = ? AND order_id = ?", key
                ).fetchone()
                if before is not None:
                    touched.add(before["buyer_id"])
                touched.add(row.buyer_id)
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
            if saved or dropped_orders or dropped_parts:
                # 그 구매자 줄만 다시 센다 (전체 재계산 아님). 버전을 올리는 같은 거래 안
                sales_summary.refresh(self, conn, tenant_id, touched)
                self._bump_version(conn, tenant_id)  # 결과 캐시를 버리게 (`sales_cache`)
        return UpsertResult(saved, dropped_orders, dropped_parts)

    # ------------------------------------------------------------------ 구매자 요약 표 (11/18)

    def summary_ready(self, tenant_id: str) -> bool:
        """요약 표가 이 고객사 주문과 맞는지 (아니면 분석은 예전 SQL로 센다)."""
        with self.reading() as conn:
            return sales_summary.ready(conn, tenant_id)

    def rebuild_summary(self, tenant_id: str) -> sales_summary.RebuildResult:
        """요약 표를 처음부터 채운다 (예전 파일·SQL로 바로 넣은 주문). 저장소 버전도 올린다."""
        with self._tx() as conn:
            result = sales_summary.rebuild(self, conn, tenant_id)
            self._bump_version(conn, tenant_id)
        return result

    def ensure_summary(self, tenant_id: str) -> sales_summary.RebuildResult | None:
        """준비되지 않았으면 채운다 (데이터 AI 시작·리셋 직후). 이미 준비됐으면 None."""
        if self.summary_ready(tenant_id):
            return None
        return self.rebuild_summary(tenant_id)

    def summary_counts(self, tenant_id: str) -> sales_summary.RebuildResult:
        with self.reading() as conn:
            return sales_summary.counts(conn, tenant_id)

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
            if saved:
                self._bump_version(conn, tenant_id)
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
            sql += " AND source NOT LIKE ? AND source NOT IN (?, ?)"
            args += [f"%{CUSTOMERS_SUFFIX}", *_META_SOURCES]
        sql += " ORDER BY last_synced_at DESC LIMIT 1"
        with self.reading() as conn:
            row = conn.execute(sql, args).fetchone()
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
        with self.reading() as conn:
            rows = conn.execute(
                "SELECT * FROM sales_customers WHERE tenant_id = ? ORDER BY buyer_id",
                (tenant_id,),
            ).fetchall()
            attrs = conn.execute(
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
        with self.reading() as conn:
            rows = conn.execute(
                "SELECT DISTINCT key, value_code FROM sales_customer_attributes "
                "WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
        found: dict[str, set[str]] = {}
        for r in rows:
            found.setdefault(r["key"], set()).add(r["value_code"])
        return found

    def customer_count(self, tenant_id: str) -> int:
        with self.reading() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM sales_customers WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
        return int(row[0])

    def order_span(self, tenant_id: str) -> tuple[int, datetime | None, datetime | None]:
        """저장된 주문 전부(상태 무관)의 수와 접수 시각 최소·최대 (`sales_collect --reset` 요약)."""
        with self.reading() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n, MIN(created_at) AS lo, MAX(created_at) AS hi "
                "FROM sales_orders WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        lo, hi = row["lo"], row["hi"]
        return int(row["n"]), (parse(lo) if lo else None), (parse(hi) if hi else None)

    def orders_by_source(self, tenant_id: str) -> dict[str | None, int]:
        """저장된 주문 전부(상태 무관)의 유입 출처(src_source)별 수. 출처 없음은 None."""
        with self.reading() as conn:
            rows = conn.execute(
                "SELECT src_source AS s, COUNT(*) AS n FROM sales_orders WHERE tenant_id = ? "
                "GROUP BY src_source ORDER BY n DESC, s",
                (tenant_id,),
            ).fetchall()
        return {r["s"]: int(r["n"]) for r in rows}

    def orders_created(self, tenant_id: str, start: datetime, end: datetime) -> list[StoredOrder]:
        """접수 시각이 [start, end)인 주문 (상태 무관), 줄·환불 포함."""
        with self.reading() as conn:
            rows = conn.execute(
                "SELECT * FROM sales_orders WHERE tenant_id = ? AND created_at >= ? "
                "AND created_at < ? ORDER BY created_at, order_id",
                (tenant_id, iso(start), iso(end)),
            ).fetchall()
            return self._with_details(tenant_id, rows, conn)

    def orders_refunded(self, tenant_id: str, start: datetime, end: datetime) -> list[StoredOrder]:
        """환불 시각이 [start, end)인 환불이 있는 주문 (접수 시각 무관), 줄·환불 포함."""
        with self.reading() as conn:
            # 환불 시각 인덱스로 그 기간 환불의 주문만 찾는다 (주문 전체를 훑지 않게, 11/18)
            rows = conn.execute(
                "SELECT o.* FROM (SELECT DISTINCT r.order_id FROM sales_refunds r "
                "WHERE r.tenant_id = ? AND r.refunded_at >= ? AND r.refunded_at < ?) x "
                "CROSS JOIN sales_orders o ON o.tenant_id = ? AND o.order_id = x.order_id "
                "ORDER BY o.created_at, o.order_id",
                (tenant_id, iso(start), iso(end), tenant_id),
            ).fetchall()
            return self._with_details(tenant_id, rows, conn)

    def buyers_paid_before(
        self, tenant_id: str, buyer_ids: Iterable[str], before: datetime
    ) -> set[str]:
        """`before` 전에 매출에 넣는 주문(결제 완료, 취소 아님)이 있는 구매자."""
        ids = sorted(set(buyer_ids))
        found: set[str] = set()
        excluded = sorted(EXCLUDED_STATUSES)
        with self.reading() as conn:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ",".join("?" * len(chunk))
                status_marks = ",".join("?" * len(excluded))
                found.update(
                    r["buyer_id"]
                    for r in conn.execute(
                        f"SELECT DISTINCT buyer_id FROM sales_orders WHERE tenant_id = ? "
                        f"AND buyer_id IN ({marks}) AND created_at < ? AND payment_status = ? "
                        f"AND LOWER(status) NOT IN ({status_marks})",
                        (tenant_id, *chunk, iso(before), PAID, *excluded),
                    )
                )
        return found

    def dropped(self, tenant_id: str, start: datetime, end: datetime) -> dict[str, int]:
        """접수 시각이 [start, end)인 주문에서 버린 것: 이유 → 수."""
        with self.reading() as conn:
            rows = conn.execute(
                "SELECT reason, COUNT(*) AS n FROM sales_dropped WHERE tenant_id = ? "
                "AND created_at >= ? AND created_at < ? GROUP BY reason ORDER BY reason",
                (tenant_id, iso(start), iso(end)),
            ).fetchall()
        return {r["reason"]: r["n"] for r in rows}

    def order_count(self, tenant_id: str) -> int:
        with self.reading() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM sales_orders WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------ 집계 읽기 (고객·시장 분석)

    def _rows(self, sql: str, params: dict[str, object]) -> list[sqlite3.Row]:
        with self.reading() as conn:
            return conn.execute(sql, params).fetchall()

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
        """고객·시장 분석 한 번의 집계 읽기. 이 스레드의 읽기 연결(`analysis` 문맥: 임시 표·
        메모리)에 임시 표(구매자별 속성 등)를 만들어 여러 집계에 같이 쓰고, 끝나면 지운다. 숫자만
        돌려준다. 다른 분석·수집과 연결을 나눠 쓰지 않아 줄을 서지 않는다."""
        with self.analysis() as conn:
            session = SegmentSession(self, conn, tenant_id, start, end, keys)
            try:
                yield session
            finally:
                session.drop()

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
        with self.reading() as conn:
            return self.product_title_in(conn, self._window(tenant_id, start, end), product_id)

    @staticmethod
    def product_title_in(
        conn: sqlite3.Connection, window: dict[str, object], product_id: str
    ) -> str | None:
        """`product_title`의 본문 (잠금을 잡은 연결, `_window` 값으로)."""
        excluded = ", ".join(f":x{i}" for i in range(len(EXCLUDED_STATUSES)))
        rows = conn.execute(
            "SELECT l.title FROM sales_order_lines l INDEXED BY idx_sales_lines_product "
            "CROSS JOIN sales_orders o INDEXED BY idx_sales_orders_byid ON o.tenant_id = :t "
            "AND o.order_id = l.order_id WHERE l.tenant_id = :t AND l.product_id = :pid "
            "AND l.title IS NOT NULL AND l.title != '' AND o.created_at >= :s "
            f"AND o.created_at < :e AND o.payment_status = :paid AND LOWER(o.status) NOT IN "
            f"({excluded}) ORDER BY o.created_at, o.order_id, l.line_no LIMIT 1",
            {**window, "pid": product_id},
        ).fetchall()
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

    def _with_details(
        self, tenant_id: str, rows: list[sqlite3.Row], conn: sqlite3.Connection
    ) -> list[StoredOrder]:
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
            for ln in conn.execute(
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
            for rf in conn.execute(
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
        self.start, self.end = start, end
        self.sum_window, self.sum_all = self._summary_use(start, end)
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

    def _summary_use(self, start: datetime, end: datetime) -> tuple[bool, bool]:
        """구매자 요약 표(`sales_summary`)로 셀 수 있는지: (기간 [start, end)로, 전체 기록으로).

        - 기간: "기간 안에 결제 완료 주문이 있다" = 마지막 결제 ≥ start. 기간 끝 뒤 결제가 없을 때만
          같다 (기간 끝 = 지금이면 늘 같다) → 요약 표의 가장 늦은 마지막 결제가 end 전이어야 한다
        - 전체 기록(첫 구매·결제 수·두 번째 결제): 위 조건 + 가장 이른 첫 결제가 start 이후
        - 요약 표가 준비되지 않았거나 `SalesStore.use_summary`가 False면 둘 다 False (예전 SQL)
        """
        if not self.store.use_summary or not sales_summary.ready(self.conn, self.tenant_id):
            return False, False
        row = self.conn.execute(
            "SELECT MAX(last_paid_at) AS hi, MIN(first_paid_at) AS lo FROM sales_buyer_summary "
            "WHERE tenant_id = ?",
            (self.tenant_id,),
        ).fetchone()
        window = row["hi"] is None or row["hi"] < iso(end)
        return window, window and (row["lo"] is None or row["lo"] >= iso(start))

    _ITEMS = (
        "SELECT i.item_code AS code, i.buyer_id FROM sales_buyer_items i WHERE i.tenant_id = :t "
        "AND i.level = :lv AND i.last_paid_at >= :s"
    )

    def drop(self) -> None:
        for name in ("seg_attrs", "seg_lines", "seg_buyers", "seg_first", "seg_pi"):
            self.conn.execute(f"DROP TABLE IF EXISTS temp.{name}")
        self._pi: tuple[str, tuple[tuple[str, str], ...]] | None = None
        self._pi_filtered = False  # seg_pi에서 산 사람이 적은 code를 뺐는지 (pair_counts)

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
        if self.sum_window:  # 요약 표: 마지막 결제가 기간 시작 이후
            return self._cells_of(
                "SELECT buyer_id FROM sales_buyer_summary WHERE tenant_id = :t "
                "AND last_paid_at >= :s"
            )
        return self._cells_of(
            f"SELECT DISTINCT o.buyer_id FROM sales_orders o WHERE {self.paid_where}"
        )

    def active_cells(self, start: datetime, end: datetime) -> Cells:
        """다른 기간 [start, end)의 결제 완료 구매자 수 (예: 대상 고객 = 최근 365일): 셀 → 명."""
        if self.sum_window and end >= self.end:  # 끝이 세션 끝 이후면 요약 표 조건이 같다
            return self._cells_of(
                "SELECT buyer_id FROM sales_buyer_summary WHERE tenant_id = :t "
                "AND last_paid_at >= :s",
                {"s": iso(start)},
            )
        return self._cells_of(
            f"SELECT DISTINCT o.buyer_id FROM sales_orders o WHERE {self.paid_where}",
            {"s": iso(start), "e": iso(end)},
        )

    def item_cells(self, kind: str, code: str) -> Cells:
        """그 대상(장르·분류·상품)이 든 결제 완료 주문의 구매자 수: 셀 → 명 (줄 인덱스로 찾는다)."""
        if self.sum_window:
            return self._cells_of(
                "SELECT i.buyer_id FROM sales_buyer_items i WHERE i.tenant_id = :t "
                "AND i.level = :lv AND i.item_code = :code AND i.last_paid_at >= :s",
                {"lv": kind, "code": code},
            )
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
        match = " AND ".join(
            f"a.v{self.keys.index(k)} = :f{i}" for i, k in enumerate(filters)
        ) or "1"  # fmt: skip
        params = {f"f{i}": v for i, v in enumerate(filters.values())}
        segment = f"SELECT a.buyer_id FROM seg_attrs a WHERE {match}"
        out: list[tuple[str, str, int, int]] = []
        if self.sum_window:  # 요약 표: (사람, 항목)당 한 줄이라 COUNT(*) = 구매자 수
            for kind in ITEM_COLUMNS:
                rows = self._all(
                    "SELECT i.item_code AS code, COUNT(*) AS total, "
                    f"SUM(CASE WHEN i.buyer_id IN ({segment}) THEN 1 ELSE 0 END) AS n "
                    "FROM sales_buyer_items i WHERE i.tenant_id = :t AND i.level = :lv "
                    "AND i.last_paid_at >= :s GROUP BY i.item_code",
                    {**params, "lv": kind},
                )
                out += [(kind, r["code"], int(r["total"]), int(r["n"])) for r in rows]
            return out
        self.lines()
        for kind, column in ITEM_COLUMNS.items():
            rows = self._all(
                f"SELECT {column} AS code, COUNT(DISTINCT buyer_id) AS total, "
                f"COUNT(DISTINCT CASE WHEN buyer_id IN ({segment}) THEN buyer_id END) AS n "
                f"FROM seg_lines WHERE {_NON_EMPTY.format(c=column)} GROUP BY {column}",
                params,
            )
            out += [(kind, r["code"], int(r["total"]), int(r["n"])) for r in rows]
        return out

    def titles(
        self, *, genre_only: bool = False, ids: Iterable[str] | None = None
    ) -> dict[str, str]:
        """상품 id → 이름: 결제 완료 주문을 (접수 시각, 주문 id, 줄 순번) 순으로 볼 때 처음 나온
        이름. `genre_only`면 genre_code가 있는 줄만 본다. `ids`를 주면 그 상품만 (주문 × 줄 조인을
        새로 하지 않고 상품 인덱스로 찾는다)."""
        if ids is not None:
            found: dict[str, str] = {}
            for pid in sorted(set(ids)):
                title = self.store.product_title_in(self.conn, self.params, pid)
                if title is not None:
                    found[pid] = title
            return found
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
        cols = "".join(f", a.v{i}" for i in range(len(self.keys)))
        if self.sum_window:
            rows = self._all(
                f"SELECT x.code AS g{cols}, COUNT(*) AS n FROM ({self._ITEMS}) x "
                f"LEFT JOIN seg_attrs a ON a.buyer_id = x.buyer_id GROUP BY x.code{cols}",
                {"lv": "genre"},
            )
        else:
            self.lines()
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

    # ---- 함께 사는 쌍 (basket_pairs, 11/17). 단위 = 같은 사람이 기간 안에 둘 다 산 것

    def _person_items(self, level: str, filters: dict[str, str]) -> None:
        """임시 표 `seg_pi`(buyer_id, code): 기간 안 결제 완료 주문에서 그 사람이 산 장르(또는
        상품) 구분 표. `filters`가 있으면 속성 조건이 모두 맞는 사람만. 같은 조건이면 다시 쓴다."""
        want = (level, tuple(sorted(filters.items())))
        if self._pi == want:
            return
        self.conn.execute("DROP TABLE IF EXISTS temp.seg_pi")
        column = ITEM_COLUMNS[level]
        match = " AND ".join(
            f"a.v{self.keys.index(k)} = :f{i}" for i, k in enumerate(filters)
        )  # fmt: skip
        segment = f"SELECT a.buyer_id FROM seg_attrs a WHERE {match}"
        who = f" AND o.buyer_id IN ({segment})" if match else ""
        self.conn.execute(
            "CREATE TEMP TABLE seg_pi (code TEXT, buyer_id TEXT, PRIMARY KEY (code, buyer_id)) "
            "WITHOUT ROWID"
        )
        if self.sum_window:  # 요약 표 (사람, 항목)를 항목 인덱스 순으로 그대로 옮긴다
            self.conn.execute(
                f"INSERT INTO seg_pi {self._ITEMS}"
                + (f" AND i.buyer_id IN ({segment})" if match else ""),
                {
                    **self.params,
                    "lv": level,
                    **{f"f{i}": v for i, v in enumerate(filters.values())},
                },  # fmt: skip
            )
            self.conn.execute("CREATE INDEX temp.seg_pi_buyer ON seg_pi(buyer_id, code)")
            self._pi = want
            self._pi_filtered = False
            return
        # 줄 인덱스(code, 주문 id)를 덮어 읽고 주문은 주문 id로 찾는다 (줄 표 본문을 읽지 않는다).
        # code 순으로 읽혀 (code, 사람) 키에 거의 차례로 들어간다
        self.conn.execute(
            f"INSERT OR IGNORE INTO seg_pi SELECT l.{column}, o.buyer_id FROM sales_order_lines l "
            f"INDEXED BY {LINE_INDEX[level]} CROSS JOIN sales_orders o "
            "INDEXED BY idx_sales_orders_byid ON o.tenant_id = :t AND o.order_id = l.order_id "
            f"WHERE l.tenant_id = :t AND l.{column} IS NOT NULL AND l.{column} != '' "
            f"AND {self.paid_where}{who}",
            {**self.params, **{f"f{i}": v for i, v in enumerate(filters.values())}},
        )
        self.conn.execute("CREATE INDEX temp.seg_pi_buyer ON seg_pi(buyer_id, code)")
        self._pi = want
        self._pi_filtered = False

    def pair_counts(
        self, level: str, filters: dict[str, str], least: int = 1
    ) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
        """(code → 산 사람 수, (a, b) → 둘 다 산 사람 수). 쌍은 a < b(code 순)이고 둘 다 산 사람이
        `least`명 이상인 것만. `seg_pi`가 (사람, code)당 한 줄이라 COUNT(*) = COUNT(DISTINCT
        buyer_id). 쌍은 a마다 따로 센다(a의 구매자만 (code, 사람) 키로 읽고 그 사람의 다른 code는
        (사람, code) 인덱스로 찾아 묶음이 작다).
        `filters`의 key는 세션 `keys`에 있어야 한다."""
        if self._pi_filtered:  # 거른 표로는 거르기 전 `items`를 셀 수 없다 → 다시 만든다
            self._pi = None
        self._person_items(level, filters)
        items = {
            r["code"]: int(r["n"])
            for r in self._all("SELECT code, COUNT(*) AS n FROM seg_pi GROUP BY code")
        }
        together: dict[tuple[str, str], int] = {}
        # 거르기: 산 사람이 `least`명 미만인 code는 어떤 쌍도 `least`명 이상이 될 수 없어 쌍을
        # 세기 전에 표에서 뺀다 (결과는 같다). lift 분모인 `items`는 거르기 전 값 그대로.
        # 걸러진 표는 숨긴 쌍을 읽기 전에 다시 만든다 (`hidden_pairs`)
        if least > 1 and any(n < least for n in items.values()):
            self.conn.execute(
                "DELETE FROM seg_pi WHERE code IN "
                "(SELECT code FROM seg_pi GROUP BY code HAVING COUNT(*) < :least)",
                {"least": least},
            )
            self._pi_filtered = True
        for a in sorted(c for c, n in items.items() if n >= least):
            for r in self._all(
                "SELECT y.code AS b, COUNT(*) AS n FROM seg_pi x CROSS JOIN seg_pi y "
                "INDEXED BY seg_pi_buyer ON y.buyer_id = x.buyer_id AND y.code > :pa "
                "WHERE x.code = :pa "
                "GROUP BY y.code HAVING COUNT(*) >= :least",
                {"pa": a, "least": least},
            ):
                together[(a, r["b"])] = int(r["n"])
        return items, together

    def hidden_pairs(
        self, level: str, filters: dict[str, str], least: int, limit: int
    ) -> list[tuple[str, str]]:
        """둘 다 산 사람이 1명 이상 `least`명 미만인 쌍 (a < b, code 순) 최대 `limit`개. 숫자는
        돌려주지 않는다 (화면에 숨김으로만 보인다)."""
        if self._pi_filtered:  # 거른 표에는 산 사람이 적은 code의 쌍이 없다 → 다시 만든다
            self._pi = None
        self._person_items(level, filters)
        rows = self._all(
            "SELECT x.code AS a, y.code AS b FROM seg_pi x "
            "JOIN seg_pi y ON y.buyer_id = x.buyer_id AND y.code > x.code "
            "GROUP BY x.code, y.code HAVING COUNT(*) < :least ORDER BY x.code, y.code LIMIT :lim",
            {"least": least, "lim": limit},
        )
        return [(r["a"], r["b"]) for r in rows]

    def together_cells(self, level: str, a: str, b: str) -> Cells:
        """`a`와 `b`를 둘 다 산 사람(조건 없음)을 속성 값 셀로 센다. 거른 표를 그대로 써도 된다
        (`pair_counts`가 돌려준 쌍의 code는 산 사람이 기준 이상이라 빠지지 않았다)."""
        self._person_items(level, {})
        return self._cells_of(
            "SELECT x.buyer_id FROM seg_pi x JOIN seg_pi y ON y.buyer_id = x.buyer_id "
            "AND y.code = :pb WHERE x.code = :pa",
            {"pa": a, "pb": b},
        )

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
        uneven = self.store._with_details(self.tenant_id, odd, self.conn)  # noqa: SLF001
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
        if self.sum_all:  # 세션이 전체 기록을 보면 요약 표 그대로
            self.conn.execute(
                "INSERT INTO seg_buyers SELECT buyer_id, first_paid_at, last_paid_at, paid_orders "
                "FROM sales_buyer_summary WHERE tenant_id = :t",
                self.params,
            )
            self._buyers = True
            return
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

        if self.sum_all:
            # 요약 표: N일 안 결제 2건 이상 = 두 번째 결제가 첫 구매 + N일 전 (주문을 훑지 않음)
            rows = self._all(
                "SELECT strftime('%Y-%m', substr(b.first_at, 1, 19), '+9 hours') AS m"
                f"{self._cell_cols()}, COUNT(*) AS n, "
                + ", ".join(
                    f"SUM(CASE WHEN s.second_paid_at IS NOT NULL AND s.second_paid_at < {until(d)} "
                    f"THEN 1 ELSE 0 END) AS r{d}"
                    for d in REPEAT_DAYS
                )
                + " FROM seg_buyers b CROSS JOIN sales_buyer_summary s ON s.tenant_id = :t "
                "AND s.buyer_id = b.buyer_id LEFT JOIN seg_attrs a ON a.buyer_id = b.buyer_id "
                f"WHERE b.first_at >= :cf AND b.first_at < :ct GROUP BY m{self._cell_cols()}",
                {"cf": iso(first_from), "ct": iso(first_to)},
            )
            return [
                (r["m"], self._cell(r), int(r["n"]), int(r["r30"]), int(r["r60"]), int(r["r90"]))
                for r in rows
            ]
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
        if self.sum_all:
            rows = self._all(
                f"SELECT x.code AS g{self._cell_cols()}, COUNT(*) AS n FROM ({self._ITEMS}) x "
                "CROSS JOIN seg_buyers b ON b.buyer_id = x.buyer_id "
                "LEFT JOIN seg_attrs a ON a.buyer_id = x.buyer_id WHERE b.orders >= :min "
                f"GROUP BY x.code{self._cell_cols()}",
                {"min": min_orders, "lv": "genre"},
            )
            return [(self._cell(r), r["g"], int(r["n"])) for r in rows]
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

    # ---- 첫 구매 경로별 고객 가치 (channel_value, 11/17)

    def first_purchases(self) -> None:
        """구매자별 첫 결제 완료 주문(접수 시각, 같으면 주문 id 순)의 시각과 유입 출처 임시 표."""
        self.conn.execute("DROP TABLE IF EXISTS temp.seg_first")
        self.conn.execute(
            "CREATE TEMP TABLE seg_first (buyer_id TEXT PRIMARY KEY, first_at TEXT NOT NULL, "
            "src TEXT) WITHOUT ROWID"
        )
        if self.sum_all:  # 요약 표의 첫 결제 시각·유입 출처 (첫 순서 = 접수 시각, 주문 id)
            self.conn.execute(
                "INSERT INTO seg_first SELECT buyer_id, first_paid_at, first_src_source "
                "FROM sales_buyer_summary WHERE tenant_id = :t ORDER BY buyer_id",
                self.params,
            )
            return
        self.conn.execute(
            "INSERT INTO seg_first SELECT buyer_id, created_at, src_source FROM (SELECT "
            "o.buyer_id, o.created_at, o.src_source, ROW_NUMBER() OVER (PARTITION BY o.buyer_id "
            "ORDER BY o.created_at, o.order_id) AS rn FROM sales_orders o "
            f"WHERE {self.paid_where}) "
            "WHERE rn = 1 ORDER BY buyer_id",
            self.params,
        )

    def channel_value(
        self, first_from: datetime, first_to: datetime, horizon_days: int, now: datetime
    ) -> tuple[list[tuple[str | None, int, int, int, int]], dict[str | None, int], int]:
        """첫 결제 완료 주문이 [first_from, first_to)인 구매자 중 첫 구매 + `horizon_days`일이
        `now` 이전인 사람만, 첫 구매 출처(src_source)별로:
        ([(출처, 사람 수, 기간 안 주문 순매출 합, 기간 안 주문 수 합, 주문 2건 이상인 사람 수)],
        {출처: 기간 안 환불 합}, 기간이 아직 안 지나 뺀 사람 수).

        기간 = 첫 구매 시각 이상 ~ + horizon_days일 미만. 순매출 = 상품 금액 − 쿠폰 − 포인트(결제
        완료 주문, 접수 시각 기준), 환불 = 그 사람의 결제 완료 주문 환불 중 환불 시각이 기간 안인 것
        (매출 분석과 같은 정의)."""
        self.first_purchases()
        days = int(horizon_days)
        until = f"strftime('%Y-%m-%dT%H:%M:%SZ', substr(f.first_at, 1, 19), '+{days} days')"
        params: dict[str, object] = {"ff": iso(first_from), "ft": iso(first_to), "now": iso(now)}
        cohort = "f.first_at >= :ff AND f.first_at < :ft"
        done = f"{until} <= :now"
        excluded = self._all(
            f"SELECT COUNT(*) AS n FROM seg_first f WHERE {cohort} AND NOT ({done})", params
        )[0]["n"]
        rows = self._all(
            "WITH h AS (SELECT f.buyer_id, f.src, COUNT(*) AS orders, "
            "SUM(o.items_amount - o.coupon_discount - o.points_used) AS net FROM seg_first f "
            "CROSS JOIN sales_orders o INDEXED BY idx_sales_orders_buyer ON o.tenant_id = :t "
            "AND o.buyer_id = f.buyer_id AND o.created_at >= f.first_at "
            f"AND o.created_at < {until} "
            f"WHERE {cohort} AND {done} AND {self.paid_where} GROUP BY f.buyer_id) "
            "SELECT src, COUNT(*) AS n, SUM(net) AS net, SUM(orders) AS orders, "
            "SUM(CASE WHEN orders >= 2 THEN 1 ELSE 0 END) AS rep FROM h GROUP BY src",
            params,
        )
        refunds = self._all(
            "SELECT f.src AS src, SUM(r.amount) AS amount FROM seg_first f "
            "CROSS JOIN sales_orders o INDEXED BY idx_sales_orders_buyer ON o.tenant_id = :t "
            "AND o.buyer_id = f.buyer_id CROSS JOIN sales_refunds r ON r.tenant_id = :t "
            "AND r.order_id = o.order_id AND r.refunded_at >= f.first_at "
            f"AND r.refunded_at < {until} "
            f"WHERE {cohort} AND {done} AND {self.paid_where} GROUP BY f.src",
            params,
        )
        return (
            [(r["src"], int(r["n"]), int(r["net"]), int(r["orders"]), int(r["rep"])) for r in rows],
            {r["src"]: int(r["amount"]) for r in refunds},
            int(excluded),
        )
