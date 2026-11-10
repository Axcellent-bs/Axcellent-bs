"""판매 사이트 저장소(SQLite). 테이블은 복수형 snake_case이고 모두 `tenant_id`·`context_id`를 둔다.

`SHOP_DB_PATH`(기본 `:memory:`)로 파일을 지정한다. 연결 하나를 잠금으로 보호한다(데모 규모).
시간은 UTC ISO8601 문자열, 금액은 정수 원이다.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from ax_contracts.schemas.common import TENANT_ID_DEFAULT

SCHEMA = """
CREATE TABLE IF NOT EXISTS sellers (
  seller_id TEXT PRIMARY KEY, name TEXT NOT NULL, category_codes TEXT NOT NULL,
  tenant_id TEXT NOT NULL, context_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
  product_id TEXT PRIMARY KEY, seller_id TEXT NOT NULL REFERENCES sellers(seller_id),
  name TEXT NOT NULL, category_code TEXT NOT NULL, price INTEGER NOT NULL CHECK (price >= 0),
  stock INTEGER NOT NULL CHECK (stock >= 0), active INTEGER NOT NULL DEFAULT 1,
  tenant_id TEXT NOT NULL, context_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS buyers (
  buyer_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, context_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY, buyer_id TEXT NOT NULL REFERENCES buyers(buyer_id),
  status TEXT NOT NULL, total_amount INTEGER NOT NULL, tenant_id TEXT NOT NULL,
  context_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  shipping_fee INTEGER NOT NULL DEFAULT 0, points_used INTEGER NOT NULL DEFAULT 0,
  delivered_at TEXT, promised_delivery_at TEXT, shipped_at TEXT, idempotency_key TEXT,
  coupon_id TEXT, coupon_units INTEGER NOT NULL DEFAULT 0,
  coupon_discount INTEGER NOT NULL DEFAULT 0,
  src_source TEXT, src_medium TEXT, src_campaign TEXT
);
CREATE TABLE IF NOT EXISTS order_items (
  order_id TEXT NOT NULL REFERENCES orders(order_id), product_id TEXT NOT NULL,
  seller_id TEXT NOT NULL, qty INTEGER NOT NULL CHECK (qty > 0),
  unit_price INTEGER NOT NULL, tenant_id TEXT NOT NULL, context_id TEXT,
  title TEXT, points_share INTEGER NOT NULL DEFAULT 0, coupon_share INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payments (
  payment_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(order_id),
  amount INTEGER NOT NULL, method_code TEXT NOT NULL, status TEXT NOT NULL,
  failure_code TEXT, tenant_id TEXT NOT NULL, context_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox_events (
  event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, tenant_id TEXT NOT NULL,
  context_id TEXT, created_at TEXT NOT NULL, sent_at TEXT
);
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS banners (
  notice_id TEXT PRIMARY KEY, text TEXT NOT NULL, status TEXT NOT NULL,
  valid_from TEXT, valid_to TEXT, tenant_id TEXT NOT NULL, context_id TEXT,
  created_at TEXT NOT NULL, ended_at TEXT
);
CREATE TABLE IF NOT EXISTS refunds (
  refund_id TEXT PRIMARY KEY, payment_id TEXT NOT NULL REFERENCES payments(payment_id),
  order_id TEXT NOT NULL REFERENCES orders(order_id), amount INTEGER NOT NULL CHECK (amount > 0),
  idempotency_key TEXT NOT NULL UNIQUE, tenant_id TEXT NOT NULL, context_id TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS returns (
  return_id TEXT PRIMARY KEY, claim_id INTEGER NOT NULL UNIQUE,
  order_id TEXT NOT NULL REFERENCES orders(order_id), inspection TEXT,
  received_at TEXT NOT NULL, tenant_id TEXT NOT NULL, context_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coupons (
  coupon_id TEXT PRIMARY KEY, buyer_id TEXT NOT NULL REFERENCES buyers(buyer_id),
  purpose_code TEXT NOT NULL, campaign_id TEXT, count INTEGER NOT NULL CHECK (count > 0),
  unit_value INTEGER NOT NULL CHECK (unit_value > 0), total_value INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, tenant_id TEXT NOT NULL, context_id TEXT,
  created_at TEXT NOT NULL, expires_at TEXT
);
-- 토스 결제 시도(시도마다 새 toss_order_id). 금액은 준비할 때의 주문 합계이고 승인 요청 금액과
-- 비교한다. payment_id는 승인 결과가 남긴 결제 행(끝난 시도를 다시 승인하면 그 결과를 돌려준다).
-- order_id에 외래키를 걸지 않는다: 보유기간 삭제(retention)가 주문을 지울 때 막히지 않게
CREATE TABLE IF NOT EXISTS payment_attempts (
  toss_order_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, amount INTEGER NOT NULL,
  status TEXT NOT NULL, payment_id TEXT, tenant_id TEXT NOT NULL, context_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retention_daily_stats (
  stat_date TEXT NOT NULL, kind TEXT NOT NULL, rows_count INTEGER NOT NULL,
  amount_sum INTEGER, tenant_id TEXT NOT NULL,
  PRIMARY KEY (stat_date, kind, tenant_id)
);
-- 주문 목록·상세가 주문마다 품목·결제·환불을 order_id로 찾는다. 인덱스가 없으면 주문 수의
-- 제곱만큼 느려져(13,000건에서 약 18초) 한 요청이 DB 잠금을 오래 쥔다.
-- 이미 만들어진 DB 파일에도 시작할 때 붙는다.
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id);
CREATE INDEX IF NOT EXISTS idx_refunds_order_id ON refunds(order_id);
-- 주문 목록 페이지: 기간(since·until)과 구매자별 조회
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_buyer_id ON orders(buyer_id);
"""

# 이미 만들어진 DB 파일에 뒤늦게 붙은 열(`CREATE TABLE IF NOT EXISTS`는 열을 더하지 않는다)
_ADDED_COLUMNS = {
    "orders": (
        ("shipping_fee", "INTEGER NOT NULL DEFAULT 0"),
        ("points_used", "INTEGER NOT NULL DEFAULT 0"),
        ("delivered_at", "TEXT"),
        ("promised_delivery_at", "TEXT"),
        ("shipped_at", "TEXT"),
        ("idempotency_key", "TEXT"),
        ("coupon_id", "TEXT"),
        ("coupon_units", "INTEGER NOT NULL DEFAULT 0"),
        ("coupon_discount", "INTEGER NOT NULL DEFAULT 0"),
        # 유입 출처(utm). 없으면 NULL이고 옛 주문도 그대로 NULL이다
        ("src_source", "TEXT"),
        ("src_medium", "TEXT"),
        ("src_campaign", "TEXT"),
    ),
    "order_items": (
        ("title", "TEXT"),
        ("points_share", "INTEGER NOT NULL DEFAULT 0"),
        ("coupon_share", "INTEGER NOT NULL DEFAULT 0"),
    ),
    "coupons": (("expires_at", "TEXT"),),
    # 결제사(fake·toss_test), 토스 paymentKey, 결제사 오류 코드. 옛 결제 행은 provider=fake
    "payments": (
        ("provider", "TEXT NOT NULL DEFAULT 'fake'"),
        ("pg_payment_key", "TEXT"),
        ("failure_detail", "TEXT"),
    ),
}

# 새 열에 거는 인덱스는 열이 붙은 뒤에 만든다(옛 DB 파일에서는 `SCHEMA`를 돌릴 때 열이 아직 없다)
_ADDED_INDEXES = (
    # 같은 구매자의 같은 멱등 키는 주문 하나다. 키가 없는 주문은 서로 겹쳐도 된다
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_buyer_idempotency ON orders(buyer_id, "
    "idempotency_key) WHERE idempotency_key IS NOT NULL",
    # 쿠폰별 사용 장수를 합산할 때 쓴다
    "CREATE INDEX IF NOT EXISTS idx_orders_coupon_id ON orders(coupon_id) "
    "WHERE coupon_id IS NOT NULL",
    # 캠페인별 유입 주문을 찾을 때 쓴다
    "CREATE INDEX IF NOT EXISTS idx_orders_src_campaign ON orders(src_campaign) "
    "WHERE src_campaign IS NOT NULL",
)

TENANT_ID = TENANT_ID_DEFAULT


class Db:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.getenv("SHOP_DB_PATH", ":memory:")
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        for table, columns in _ADDED_COLUMNS.items():
            have = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns:
                if name not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        for statement in _ADDED_INDEXES:
            self._conn.execute(statement)
        self._conn.commit()
        self._lock = threading.RLock()

    @property
    def is_file(self) -> bool:
        """DB가 디스크 파일이면 True(`:memory:`·빈 경로·메모리 URI는 False)."""
        return self.path not in ("", ":memory:") and ":memory:" not in self.path

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """한 트랜잭션. 예외가 나면 되돌린다(주문·결제·outbox가 함께 저장되거나 함께 취소된다)."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def next_id(self, conn: sqlite3.Connection, name: str, prefix: str) -> str:
        """`prefix + 4자리 이상 순번`. 접두사 규칙은 CLAUDE.md ID 표를 따른다."""
        conn.execute(
            "INSERT INTO counters(name, value) VALUES (?, 1) "
            "ON CONFLICT(name) DO UPDATE SET value = value + 1",
            (name,),
        )
        value = conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()[0]
        return f"{prefix}{value:04d}"
