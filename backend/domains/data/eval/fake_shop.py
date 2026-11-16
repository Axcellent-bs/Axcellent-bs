"""데이터분석 평가용 가짜 판매 서버. 판매 서버 코드(`ax_shop_api`)를 import하지 않는다.

판매 서버는 `Catbooks-demo/catbooks-demo` 레포로 나갔다(레포 분리 마지막 단계). data 평가 생성기
(`eval_world.py`·`eval_sellers.py`)와 시험은 그 코드를 직접 부르지 않고, 이 파일이 판매 서버가
하는 일 중 평가에 필요한 것만 **같은 규칙**으로 흉내 낸다.

재무에도 가짜 판매 서버(`domains/finance/eval/fake_shop.py`)가 있다. 겹치는 규칙이 있어도 부서끼리
코드를 나눠 쓰지 않는다(`domains`끼리 import하지 않는다, 부서 독립). data 쪽은 평가 생성기가 SQL로
판매 서버 표를 직접 읽으므로(`world.db.tx()`) 표를 메모리 sqlite에 판매 서버와 같은 표·열로 둔다.

- 저장소(`FakeShopDb`): 판매 서버 `db.py`의 표 중 쓰는 것만(sellers·products·buyers·orders·
  order_items·payments·refunds·coupons·counters), 같은 열 이름·순서. `tx()`는 한 트랜잭션이다.
- 쓰기(`FakeShop`)는 판매 서버와 같은 규칙이다:
  - ID: `접두사 + 4자리 이상 순번`(counters 표). 시드 상품이 8번까지 써서 새 상품은 prd_0009부터
  - 주문: 결제 금액 = 품목 합계 − 쿠폰 할인 − 포인트 + 배송비(품목 합계 5만 원 미만이면 3,000원).
    쿠폰 할인 = min(장수 × 단가, 품목 합계)이고 품목 금액 비율로 나눈다(내림, 남는 몫은 뒤
    품목부터 1원씩). 포인트는 쿠폰을 뺀 품목 금액 비율로 같은 방식으로 나눈다. 재고를 줄인다.
    유입 표시(`src_*`)는 영문·숫자·`.`·`_`·`-` 64자까지만 남기고 어긋나면 비운다
  - 쿠폰: 본인 쿠폰만, 만료 전만, 남은 장수 안에서만. 쓴 장수는 주문에서 센다
    (취소·전액 환불 주문 제외)
  - 결제: 장애 주입(`inject_fault("failure")`) 중이면 `PG_UNAVAILABLE`, 카드 거절
    (`simulate="failure"`)이면 `PG_DECLINED`로 실패하고 주문은 `payment_failed`. 성공이면 `paid`이고
    약속 배송일 = 결제 시각 + 3일. 결제할 수 있는 주문은 결제 대기·결제 실패뿐이다
  - 시각은 모두 주입한 시계(`now`)로 정하고 `YYYY-MM-DDTHH:MM:SSZ`(UTC, 초 단위)로 적는다.
    판매 서버는 같은 일을 벽시계로 한다(시드 시각·약속 배송일). 평가는 시나리오 시계 하나만 쓴다
- 읽기(`FakeShopTransport`)는 data가 판매 서버에 보내는 GET에 같은 `{data, meta}` 모양으로 답한다:
  `/failed-orders`·`/metric-series`·`/orders`(order_id 순 페이지, `meta.next_cursor`)·
  `/orders/attributed`·`/products`·`/coupons`·`/sellers/{id}/profile`·`/sellers/{id}/prices`.
  계산 규칙은 판매 서버 조회 코드(`lookups.py`·`service.py`·`promotions.py`)와 같다.
  `check_tokens=True`면 판매 서버 `_caller`처럼 토큰을 검사한다(401·403).
- 모양이 판매 서버 계약과 맞는지는 고정본 `shop_api_openapi.json`(`OPENAPI_SOURCE`)으로 시험이
  잰다(`tests/test_eval_fake_shop.py`). 판매 서버 계약이 바뀌면 고정본을 새 커밋에서 다시 받는다:
  `cd backend && python scripts/sync_shop_openapi.py` (어긋났는지만: `--check`).
"""

from __future__ import annotations

import hmac
import json
import math
import os
import re
import sqlite3
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from ax_agent_template.server import verify_token
from ax_agent_template.services import department_of
from ax_contracts.data_access.shop_failed_orders import (
    FailedOrder,
    ShopFailedOrdersQuery,
    ShopFailedOrdersResult,
)
from ax_contracts.data_access.shop_metric_series import (
    MetricPoint,
    ShopMetricSeriesQuery,
    ShopMetricSeriesResult,
)
from ax_contracts.data_access.shop_seller_prices import (
    SellerProductPrice,
    ShopSellerPricesQuery,
    ShopSellerPricesResult,
)
from ax_contracts.data_access.shop_seller_profile import ShopSellerProfile, ShopSellerProfileQuery
from ax_contracts.schemas import IdPrefix
from ax_contracts.schemas.common import TENANT_ID_DEFAULT

OPENAPI_PATH = Path(__file__).resolve().parent / "shop_api_openapi.json"
OPENAPI_SOURCE = (
    "Catbooks-demo/catbooks-demo@bb70ac67014e083b8a4c960803077a9ffdd13568 "
    "backend/contracts/openapi/shop-api.json"
)

TENANT_ID = TENANT_ID_DEFAULT

# 판매 서버 시드(seed.py)와 같은 판매자·상품·구매자
SELLERS = [
    ("sel_0001", "책마루", ["book"]),
    ("sel_0002", "종이나라", ["stationery"]),
    ("sel_0003", "북앤펜", ["book", "stationery"]),
]
# (product_id, seller_id, name, category_code, price, stock)
PRODUCTS = [
    ("prd_0001", "sel_0001", "파이썬 입문서", "book", 18000, 200),
    ("prd_0002", "sel_0001", "데이터 분석 실전", "book", 24000, 150),
    ("prd_0003", "sel_0001", "에세이 모음집", "book", 13000, 300),
    ("prd_0004", "sel_0002", "무지 노트 3권 세트", "stationery", 7500, 500),
    ("prd_0005", "sel_0002", "젤펜 12색", "stationery", 9800, 400),
    ("prd_0006", "sel_0003", "독서대", "stationery", 15000, 120),
    ("prd_0007", "sel_0003", "소설 베스트 3종", "book", 31000, 90),
    ("prd_0008", "sel_0003", "만년필 입문 세트", "stationery", 29000, 60),
]
BUYER_COUNT = 30

SHIPPING_FEE = 3000
FREE_SHIPPING_FROM = 50000
PROMISED_DELIVERY_DAYS = 3
ORDER_PAGE_DEFAULT = 200
ORDER_PAGE_MAX = 1000
PAYABLE = ("pending_payment", "payment_failed")
_PAYMENT_KEYS = ("payment_id", "amount", "method_code", "status", "failure_code", "created_at")
_REFUND_CHUNK = 500  # IN (...) 한 번에 넣는 주문 수(SQLite 변수 상한 안쪽)
_SOURCE_TAG = re.compile(r"[A-Za-z0-9._-]{1,64}")

# 조회 규칙 (판매 서버 lookups.py와 같은 값)
BUCKET = timedelta(minutes=5)
LISTING_WINDOW = timedelta(hours=24)
MAX_ATTRIBUTION_DAYS = 31
MAX_POINTS = 2016
MAX_SERIES_SPAN = timedelta(hours=24)
_COUPON_KEPT = (
    "o.coupon_id IS NOT NULL AND o.status != 'cancelled' AND NOT (o.total_amount > 0 AND "
    "COALESCE((SELECT SUM(r.amount) FROM refunds r WHERE r.order_id = o.order_id), 0) "
    ">= o.total_amount)"
)
ORDER_VALUE_FILTER = {
    "order_amount": "o.status NOT IN ('pending_payment', 'payment_failed', 'cancelled')",
    "coupon_order_count": _COUPON_KEPT,
    "coupon_discount_amount": _COUPON_KEPT,
}

# 판매 서버 `_caller`가 받는 요청자 (main.py)
DATA_READERS = frozenset({"data", "admin"})
COUPON_READERS = frozenset({"marketing", "support", "finance", "orchestrator", "data"})
ATTRIBUTION_READERS = frozenset({"data"})
VIEWER_TOKEN_ENV = "SHOP_VIEWER_TOKEN"
VIEWER_TOKEN_MIN_LENGTH = 24

# 판매 서버 db.py의 표 중 평가가 쓰는 것. 열 이름·순서는 판매 서버와 같다
# (payments의 뒤 네 열은 판매 서버에서 나중에 붙은 열이라 맨 뒤에 있다)
SCHEMA = """
CREATE TABLE sellers (
  seller_id TEXT PRIMARY KEY, name TEXT NOT NULL, category_codes TEXT NOT NULL,
  tenant_id TEXT NOT NULL, context_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE products (
  product_id TEXT PRIMARY KEY, seller_id TEXT NOT NULL REFERENCES sellers(seller_id),
  name TEXT NOT NULL, category_code TEXT NOT NULL, price INTEGER NOT NULL CHECK (price >= 0),
  stock INTEGER NOT NULL CHECK (stock >= 0), active INTEGER NOT NULL DEFAULT 1,
  tenant_id TEXT NOT NULL, context_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE buyers (
  buyer_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, context_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE orders (
  order_id TEXT PRIMARY KEY, buyer_id TEXT NOT NULL REFERENCES buyers(buyer_id),
  status TEXT NOT NULL, total_amount INTEGER NOT NULL, tenant_id TEXT NOT NULL,
  context_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  shipping_fee INTEGER NOT NULL DEFAULT 0, points_used INTEGER NOT NULL DEFAULT 0,
  delivered_at TEXT, promised_delivery_at TEXT, shipped_at TEXT, idempotency_key TEXT,
  coupon_id TEXT, coupon_units INTEGER NOT NULL DEFAULT 0,
  coupon_discount INTEGER NOT NULL DEFAULT 0,
  src_source TEXT, src_medium TEXT, src_campaign TEXT
);
CREATE TABLE order_items (
  order_id TEXT NOT NULL REFERENCES orders(order_id), product_id TEXT NOT NULL,
  seller_id TEXT NOT NULL, qty INTEGER NOT NULL CHECK (qty > 0),
  unit_price INTEGER NOT NULL, tenant_id TEXT NOT NULL, context_id TEXT,
  title TEXT, points_share INTEGER NOT NULL DEFAULT 0, coupon_share INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE payments (
  payment_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(order_id),
  amount INTEGER NOT NULL, method_code TEXT NOT NULL, status TEXT NOT NULL,
  failure_code TEXT, tenant_id TEXT NOT NULL, context_id TEXT, created_at TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'fake', pg_payment_key TEXT, failure_detail TEXT,
  resolved_at TEXT
);
CREATE TABLE refunds (
  refund_id TEXT PRIMARY KEY, payment_id TEXT NOT NULL REFERENCES payments(payment_id),
  order_id TEXT NOT NULL REFERENCES orders(order_id), amount INTEGER NOT NULL CHECK (amount > 0),
  idempotency_key TEXT NOT NULL UNIQUE, tenant_id TEXT NOT NULL, context_id TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE coupons (
  coupon_id TEXT PRIMARY KEY, buyer_id TEXT NOT NULL REFERENCES buyers(buyer_id),
  purpose_code TEXT NOT NULL, campaign_id TEXT, count INTEGER NOT NULL CHECK (count > 0),
  unit_value INTEGER NOT NULL CHECK (unit_value > 0), total_value INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, tenant_id TEXT NOT NULL, context_id TEXT,
  created_at TEXT NOT NULL, expires_at TEXT
);
CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_payments_order_id ON payments(order_id);
CREATE INDEX idx_refunds_order_id ON refunds(order_id);
CREATE INDEX idx_orders_created_at ON orders(created_at);
CREATE INDEX idx_orders_buyer_id ON orders(buyer_id);
CREATE INDEX idx_payments_created_at ON payments(created_at);
CREATE INDEX idx_payments_status_created_at ON payments(status, created_at);
CREATE INDEX idx_orders_coupon_id ON orders(coupon_id) WHERE coupon_id IS NOT NULL;
CREATE INDEX idx_orders_src_campaign ON orders(src_campaign) WHERE src_campaign IS NOT NULL;
"""


class FakeShopError(Exception):
    """판매 서버 `ShopError`와 같은 코드·상태로 거절한다."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _floor(moment: datetime) -> datetime:
    seconds = int(BUCKET.total_seconds())
    return datetime.fromtimestamp(int(moment.timestamp()) // seconds * seconds, UTC)


def seed_buyer_id(number: int) -> str:
    return f"{IdPrefix.BUYER}{number:04d}"


def clean_source_tag(value: str | None) -> str | None:
    """유입 출처 값. 규칙에 어긋나거나 비어 있으면 None(저장 안 함)."""
    if value is None:
        return None
    value = value.strip()
    return value if _SOURCE_TAG.fullmatch(value) else None


def split_by_amount(total: int, amounts: list[int]) -> list[int]:
    """금액 비율로 나누고(내림) 남는 몫은 뒤 품목부터 1원씩 붙인다(판매 서버 `_split_points`)."""
    subtotal = sum(amounts)
    if total == 0 or subtotal == 0:
        return [0] * len(amounts)
    shares = [total * amount // subtotal for amount in amounts]
    left = total - sum(shares)
    for index in reversed(range(len(shares))):
        if left == 0:
            break
        if shares[index] < amounts[index]:
            shares[index] += 1
            left -= 1
    return shares


def median_price(prices: list[int]) -> int:
    """정수 원 중앙값. 짝수 개면 가운데 두 값의 평균을 반올림한다(0.5는 올림)."""
    ordered = sorted(prices)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return math.floor((ordered[middle - 1] + ordered[middle]) / 2 + 0.5)


def _period_bound(value: str) -> str:
    """기간 경계(ISO 8601)를 저장 형식(UTC, 초 단위)으로 맞춘다. 시간대가 없으면 UTC로 본다."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise FakeShopError("INVALID_PERIOD", "기간은 ISO 8601 시각이어야 한다", 400) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return iso(parsed)


class FakeShopDb:
    """판매 서버 표를 담은 sqlite. 연결 하나를 잠금으로 보호한다(판매 서버 `Db`와 같은 방식)."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._lock = threading.RLock()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """한 트랜잭션. 예외가 나면 되돌린다."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    @staticmethod
    def next_id(conn: sqlite3.Connection, name: str, prefix: str) -> str:
        """`prefix + 4자리 이상 순번`."""
        conn.execute(
            "INSERT INTO counters(name, value) VALUES (?, 1) "
            "ON CONFLICT(name) DO UPDATE SET value = value + 1",
            (name,),
        )
        value = conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()[0]
        return f"{prefix}{value:04d}"


def _coupon_used_units(conn: sqlite3.Connection, coupon_id: str) -> int:
    """쿠폰이 이미 쓴 장수. 취소됐거나 전액 환불된 주문은 장수를 돌려주므로 세지 않는다."""
    row = conn.execute(
        "SELECT COALESCE(SUM(o.coupon_units), 0) AS used FROM orders o "
        "WHERE o.coupon_id = ? AND o.status != 'cancelled' AND NOT (o.total_amount > 0 AND "
        "COALESCE((SELECT SUM(r.amount) FROM refunds r WHERE r.order_id = o.order_id), 0) "
        ">= o.total_amount)",
        (coupon_id,),
    ).fetchone()
    return int(row["used"])


class FakeShop:
    """쇼핑몰 하나. 쓰기 시각은 `now()`가 정한다(주입한 시계)."""

    def __init__(self, db: FakeShopDb, now: Callable[[], datetime]) -> None:
        self.db = db
        self.now = now
        self.failing = False  # 장애 주입(failure) 스위치

    # ---- 시드
    def seed(self) -> None:
        """판매 서버 시드와 같은 판매자·상품·구매자. 시각은 지금(`now()`)이다."""
        stamp = iso(self.now())
        with self.db.tx() as conn:
            for seller_id, name, categories in SELLERS:
                conn.execute(
                    "INSERT INTO sellers VALUES (?, ?, ?, ?, NULL, ?)",
                    (seller_id, name, ",".join(categories), TENANT_ID, stamp),
                )
            for product_id, seller_id, name, category, price, stock in PRODUCTS:
                conn.execute(
                    "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL, ?, ?)",
                    (product_id, seller_id, name, category, price, stock, TENANT_ID, stamp, stamp),
                )
            # 시드가 쓴 번호 다음부터 새 ID를 발급한다
            conn.execute("INSERT INTO counters VALUES ('product', ?)", (len(PRODUCTS),))
            for number in range(1, BUYER_COUNT + 1):
                conn.execute(
                    "INSERT INTO buyers VALUES (?, ?, NULL, ?)",
                    (seed_buyer_id(number), TENANT_ID, stamp),
                )

    # ---- 상품
    def create_product(
        self, seller_id: str, name: str, category_code: str, price: int, stock: int
    ) -> dict[str, Any]:
        with self.db.tx() as conn:
            if not conn.execute(
                "SELECT 1 FROM sellers WHERE seller_id = ?", (seller_id,)
            ).fetchone():
                raise FakeShopError("SELLER_NOT_FOUND", "판매자를 찾을 수 없다", 404)
            product_id = self.db.next_id(conn, "product", "prd_")
            stamp = iso(self.now())
            conn.execute(
                "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL, ?, ?)",
                (product_id, seller_id, name, category_code, price, stock, TENANT_ID, stamp,
                 stamp),
            )  # fmt: skip
            row = conn.execute(
                "SELECT * FROM products WHERE product_id = ?", (product_id,)
            ).fetchone()
        return self._product(row)

    @staticmethod
    def _product(row: sqlite3.Row) -> dict[str, Any]:
        keys = ("product_id", "seller_id", "name", "category_code", "price", "stock")
        return {key: row[key] for key in keys} | {"active": bool(row["active"])}

    def list_products(
        self, seller_id: str | None = None, category_code: str | None = None
    ) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM products WHERE active = 1", []
        for column, value in (("seller_id", seller_id), ("category_code", category_code)):
            if value:
                sql += f" AND {column} = ?"
                args.append(value)
        with self.db.tx() as conn:
            rows = conn.execute(sql + " ORDER BY product_id", args).fetchall()
        return [self._product(r) for r in rows]

    # ---- 주문
    def create_order(
        self, buyer_id: str, lines: list[tuple[str, int]], points_used: int = 0
    ) -> dict[str, Any]:
        return self.place_order(buyer_id, lines, points_used=points_used)

    def place_order(
        self,
        buyer_id: str,
        lines: list[tuple[str, int]],
        points_used: int = 0,
        coupon_id: str | None = None,
        coupon_units: int = 1,
        src_source: str | None = None,
        src_medium: str | None = None,
        src_campaign: str | None = None,
    ) -> dict[str, Any]:
        """주문을 만들고 주문 응답을 돌려준다. 결제 금액은 코드가 계산한다.

        멱등 키(`idempotency_key`)는 평가가 쓰지 않아 흉내 내지 않는다.
        """
        if not lines:
            raise FakeShopError("EMPTY_ORDER", "주문 상품이 없다")
        with self.db.tx() as conn:
            if not conn.execute("SELECT 1 FROM buyers WHERE buyer_id = ?", (buyer_id,)).fetchone():
                raise FakeShopError("BUYER_NOT_FOUND", "구매자를 찾을 수 없다", 404)
            items, titles, total = [], [], 0
            for product_id, qty in lines:
                product = conn.execute(
                    "SELECT * FROM products WHERE product_id = ? AND active = 1", (product_id,)
                ).fetchone()
                if product is None:
                    raise FakeShopError(
                        "PRODUCT_NOT_FOUND", f"상품 {product_id}을 찾을 수 없다", 404
                    )
                if product["stock"] < qty:
                    raise FakeShopError("OUT_OF_STOCK", f"상품 {product_id}의 재고가 부족하다", 409)
                items.append((product_id, product["seller_id"], qty, product["price"]))
                titles.append(product["name"])
                total += qty * product["price"]
                conn.execute(
                    "UPDATE products SET stock = stock - ? WHERE product_id = ?", (qty, product_id)
                )
            discount = self._use_coupon(conn, buyer_id, coupon_id, coupon_units, total)
            if points_used > total - discount:
                raise FakeShopError("POINTS_EXCEED_ITEMS", "포인트는 품목 합계를 넘을 수 없다")
            shipping_fee = 0 if total >= FREE_SHIPPING_FROM else SHIPPING_FEE
            amounts = [qty * price for _, _, qty, price in items]
            coupon_shares = split_by_amount(discount, amounts)  # 쿠폰을 먼저, 포인트는 남은 금액에
            shares = split_by_amount(
                points_used, [a - c for a, c in zip(amounts, coupon_shares, strict=True)]
            )
            total = total - discount - points_used + shipping_fee  # 실제 결제(현금) 금액
            order_id = self.db.next_id(conn, "order", "ord_")
            stamp = iso(self.now())
            conn.execute(
                "INSERT INTO orders (order_id, buyer_id, status, total_amount, tenant_id, "
                "context_id, created_at, updated_at, shipping_fee, points_used, idempotency_key, "
                "coupon_id, coupon_units, coupon_discount, src_source, src_medium, src_campaign) "
                "VALUES (?, ?, 'pending_payment', ?, ?, NULL, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                (order_id, buyer_id, total, TENANT_ID, stamp, stamp, shipping_fee, points_used,
                 coupon_id, coupon_units if coupon_id else 0, discount,
                 clean_source_tag(src_source), clean_source_tag(src_medium),
                 clean_source_tag(src_campaign)),
            )  # fmt: skip
            for (product_id, seller_id, qty, price), title, share, c_share in zip(
                items, titles, shares, coupon_shares, strict=True
            ):
                conn.execute(
                    "INSERT INTO order_items (order_id, product_id, seller_id, qty, unit_price, "
                    "tenant_id, context_id, title, points_share, coupon_share) "
                    "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                    (order_id, product_id, seller_id, qty, price, TENANT_ID, title, share,
                     c_share),
                )  # fmt: skip
            return self._order(conn, order_id)

    def _use_coupon(
        self,
        conn: sqlite3.Connection,
        buyer_id: str,
        coupon_id: str | None,
        units: int,
        subtotal: int,
    ) -> int:
        """쿠폰 할인액(원). 본인 쿠폰·만료 전·남은 장수 안에서만. 상품 합계를 넘지 않는다."""
        if not coupon_id:
            return 0
        row = conn.execute("SELECT * FROM coupons WHERE coupon_id = ?", (coupon_id,)).fetchone()
        if row is None:
            raise FakeShopError("COUPON_NOT_FOUND", "쿠폰을 찾을 수 없다", 404)
        if row["buyer_id"] != buyer_id:
            raise FakeShopError("COUPON_NOT_YOURS", "본인의 쿠폰만 쓸 수 있다", 403)
        if row["expires_at"] is not None and row["expires_at"] <= iso(self.now()):
            raise FakeShopError("COUPON_EXPIRED", "만료된 쿠폰이다", 409)
        left = row["count"] - _coupon_used_units(conn, coupon_id)
        if units > left:
            raise FakeShopError("COUPON_UNITS_EXCEEDED", f"남은 쿠폰이 {left}장이다", 409)
        return min(units * row["unit_value"], subtotal)

    # ---- 결제
    def inject_fault(self, kind: str) -> None:
        """장애 주입. 평가가 쓰는 `failure`(모든 결제를 PG_UNAVAILABLE로 실패)만 흉내 낸다."""
        if kind != "failure":
            raise FakeShopError("FAULT_NOT_IMITATED", f"가짜 판매 서버에 없는 장애: {kind}")
        self.failing = True

    def release_fault(self) -> None:
        self.failing = False

    def pay(self, order_id: str, method_code: str, simulate: str | None) -> dict[str, Any]:
        """결제 시도 하나. 장애 중이면 PG_UNAVAILABLE, `simulate="failure"`면 PG_DECLINED."""
        with self.db.tx() as conn:
            order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if order is None:
                raise FakeShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
            if order["status"] not in PAYABLE:
                raise FakeShopError("ORDER_NOT_PAYABLE", "결제할 수 없는 주문 상태다", 409)
            failure = (
                "PG_UNAVAILABLE" if self.failing
                else "PG_DECLINED" if simulate == "failure"
                else None
            )  # fmt: skip
            payment_id = self.db.next_id(conn, "payment", "pay_")
            moment = self.now()
            stamp = iso(moment)
            conn.execute(
                "INSERT INTO payments (payment_id, order_id, amount, method_code, status, "
                "failure_code, tenant_id, context_id, created_at, provider, pg_payment_key, "
                "failure_detail) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'fake', NULL, NULL)",
                (payment_id, order_id, order["total_amount"], method_code,
                 "failed" if failure else "succeeded", failure, TENANT_ID, stamp),
            )  # fmt: skip
            conn.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?",
                ("payment_failed" if failure else "paid", stamp, order_id),
            )
            if not failure:
                promised = moment + timedelta(days=PROMISED_DELIVERY_DAYS)
                conn.execute(
                    "UPDATE orders SET promised_delivery_at = ? WHERE order_id = ?",
                    (iso(promised), order_id),
                )
            return self._order(conn, order_id)

    # ---- 주문 응답 (판매 서버 service.py `_orders_view`와 같은 모양)
    def _order(self, conn: sqlite3.Connection, order_id: str) -> dict[str, Any]:
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if order is None:
            raise FakeShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
        return self._orders_view(conn, [order])[0]

    def get_order(self, order_id: str) -> dict[str, Any]:
        with self.db.tx() as conn:
            return self._order(conn, order_id)

    def list_orders(
        self,
        buyer_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = ORDER_PAGE_DEFAULT,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """접수 시각이 `since` 이상 `until` 미만인 주문 한 페이지(order_id 순)와 다음 cursor."""
        limit = max(1, min(limit, ORDER_PAGE_MAX))
        where, args = [], []
        if buyer_id:
            where.append("buyer_id = ?")
            args.append(buyer_id)
        if since:
            where.append("created_at >= ?")
            args.append(_period_bound(since))
        if until:
            where.append("created_at < ?")
            args.append(_period_bound(until))
        if cursor:
            where.append("order_id > ?")
            args.append(cursor)
        sql = "SELECT * FROM orders" + (" WHERE " + " AND ".join(where) if where else "")
        with self.db.tx() as conn:
            rows = conn.execute(sql + " ORDER BY order_id LIMIT ?", [*args, limit + 1]).fetchall()
            page = rows[:limit]
            orders = self._orders_view(conn, page)
        next_cursor = page[-1]["order_id"] if len(rows) > limit else None
        return orders, next_cursor

    def _orders_view(
        self, conn: sqlite3.Connection, orders: list[sqlite3.Row]
    ) -> list[dict[str, Any]]:
        ids = [o["order_id"] for o in orders]
        items = _by_order(
            conn,
            ids,
            "SELECT i.order_id, i.rowid AS item_rowid, i.product_id, i.seller_id, i.qty, "
            "i.unit_price, i.points_share, i.coupon_share, COALESCE(i.title, p.name) AS title "
            "FROM order_items i LEFT JOIN products p ON p.product_id = i.product_id "
            "WHERE i.order_id IN ({marks}) ORDER BY i.rowid",
        )
        payments = _by_order(
            conn,
            ids,
            "SELECT order_id, payment_id, amount, method_code, status, failure_code, created_at "
            "FROM payments WHERE order_id IN ({marks}) ORDER BY rowid",
        )
        refunds = _by_order(
            conn,
            ids,
            "SELECT order_id, refund_id, payment_id, amount, created_at FROM refunds "
            "WHERE order_id IN ({marks}) ORDER BY created_at, rowid",
        )
        return [
            _order_view(
                o,
                items.get(o["order_id"], []),
                payments.get(o["order_id"], []),
                [
                    {k: r[k] for k in ("refund_id", "payment_id", "amount", "created_at")}
                    for r in refunds.get(o["order_id"], [])
                ],
            )
            for o in orders
        ]

    # ---- 쿠폰 (판매 서버 promotions.py)
    def issue_coupon(
        self,
        idempotency_key: str,
        purpose_code: str,
        buyer_id: str,
        count: int,
        unit_value: int,
        campaign_id: str | None,
        context_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """쿠폰을 발급한다. 같은 멱등 키의 재요청은 이전 쿠폰을 돌려준다(`replayed=True`)."""
        if purpose_code == "campaign" and not campaign_id:
            raise FakeShopError("INVALID_COUPON", "캠페인 쿠폰에는 campaign_id가 필요하다")
        if purpose_code == "compensation" and campaign_id:
            raise FakeShopError("INVALID_COUPON", "보상 쿠폰에는 campaign_id가 없어야 한다")
        if expires_at is not None and expires_at <= self.now():
            raise FakeShopError("INVALID_COUPON", "만료일은 지금보다 뒤여야 한다")
        expires = iso(expires_at) if expires_at is not None else None
        with self.db.tx() as conn:
            existing = conn.execute(
                "SELECT * FROM coupons WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                same = (
                    existing["purpose_code"],
                    existing["buyer_id"],
                    existing["count"],
                    existing["unit_value"],
                    existing["campaign_id"],
                    existing["expires_at"],
                ) == (purpose_code, buyer_id, count, unit_value, campaign_id, expires)
                if not same:
                    raise FakeShopError(
                        "IDEMPOTENCY_MISMATCH", "같은 키로 다른 내용을 요청할 수 없다", 409
                    )
                return self._coupon_view(conn, existing, self.now()), True
            if not conn.execute("SELECT 1 FROM buyers WHERE buyer_id = ?", (buyer_id,)).fetchone():
                raise FakeShopError("BUYER_NOT_FOUND", "구매자를 찾을 수 없다", 404)
            coupon_id = self.db.next_id(conn, "coupon", "cpn_")
            conn.execute(
                "INSERT INTO coupons (coupon_id, buyer_id, purpose_code, campaign_id, count, "
                "unit_value, total_value, idempotency_key, tenant_id, context_id, created_at, "
                "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (coupon_id, buyer_id, purpose_code, campaign_id, count, unit_value,
                 count * unit_value, idempotency_key, TENANT_ID, context_id, iso(self.now()),
                 expires),
            )  # fmt: skip
            row = conn.execute("SELECT * FROM coupons WHERE coupon_id = ?", (coupon_id,)).fetchone()
            return self._coupon_view(conn, row, self.now()), False

    def list_coupons(
        self,
        buyer_id: str | None = None,
        purpose_code: str | None = None,
        campaign_id: str | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM coupons WHERE 1 = 1", []
        for column, value in (
            ("buyer_id", buyer_id),
            ("purpose_code", purpose_code),
            ("campaign_id", campaign_id),
        ):
            if value:
                sql += f" AND {column} = ?"
                args.append(value)
        moment = now or self.now()
        with self.db.tx() as conn:
            rows = conn.execute(sql + " ORDER BY coupon_id", args).fetchall()
            return [self._coupon_view(conn, row, moment) for row in rows]

    @staticmethod
    def _coupon_view(conn: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> dict[str, Any]:
        keys = (
            "coupon_id",
            "buyer_id",
            "purpose_code",
            "campaign_id",
            "count",
            "unit_value",
            "total_value",
            "context_id",
            "created_at",
            "expires_at",
        )
        out = {key: row[key] for key in keys}
        used = _coupon_used_units(conn, row["coupon_id"])
        out["used_units"] = used
        out["remaining_units"] = row["count"] - used
        out["expired"] = row["expires_at"] is not None and row["expires_at"] <= iso(now)
        return out

    # ---- 데이터 조회 (판매 서버 lookups.py). `now`는 응답의 조회 시각·최근 등록 기준이다
    def failed_orders(self, query: ShopFailedOrdersQuery, now: datetime) -> ShopFailedOrdersResult:
        if query.to <= query.from_:
            raise FakeShopError("INVALID_RANGE", "to는 from보다 뒤여야 한다")
        sql = (
            "SELECT p.order_id, o.buyer_id, p.amount, p.failure_code, p.created_at, "
            "  (SELECT pr.category_code FROM order_items i JOIN products pr "
            "   ON pr.product_id = i.product_id WHERE i.order_id = p.order_id "
            "   ORDER BY i.rowid LIMIT 1) AS category "
            "FROM payments p JOIN orders o ON o.order_id = p.order_id "
            "WHERE p.status = 'failed' AND p.tenant_id = ? "
            "AND p.created_at >= ? AND p.created_at < ?"
        )
        args: list[Any] = [query.tenant_id, iso(query.from_), iso(query.to)]
        if query.category:
            sql += " AND category = ?"
            args.append(query.category)
        sql += " ORDER BY p.created_at, p.payment_id LIMIT ?"
        args.append(query.limit + 1)  # 하나 더 읽어 잘림을 안다
        with self.db.tx() as conn:
            rows = conn.execute(sql, args).fetchall()
        orders = [
            FailedOrder(
                order_id=row["order_id"],
                buyer_id=row["buyer_id"],
                amount=row["amount"],
                category=row["category"] or "unknown",
                failed_at=_parse(row["created_at"]),
                reason=row["failure_code"] or "UNKNOWN",
                tenant_id=query.tenant_id,
            )
            for row in rows[: query.limit]
        ]
        return ShopFailedOrdersResult(
            orders=orders, collected_at=now, truncated=len(rows) > query.limit
        )

    def attributed_orders(
        self,
        since: datetime,
        until: datetime,
        campaign_id: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """유입 출처(`src_*`)가 하나라도 있는 주문(취소·전액 환불 제외). 구매자 id는 주지 않는다."""
        if until <= since:
            raise FakeShopError("INVALID_RANGE", "to는 from보다 뒤여야 한다")
        if until - since > timedelta(days=MAX_ATTRIBUTION_DAYS):
            raise FakeShopError(
                "RANGE_TOO_LONG", f"기간은 {MAX_ATTRIBUTION_DAYS}일을 넘을 수 없다", 400
            )
        sql = (
            "SELECT o.order_id, o.created_at, o.total_amount, o.status, o.src_source, "
            "o.src_medium, o.src_campaign FROM orders o "
            "WHERE o.tenant_id = ? AND o.created_at >= ? AND o.created_at < ? "
            "AND (o.src_source IS NOT NULL OR o.src_medium IS NOT NULL "
            "OR o.src_campaign IS NOT NULL) AND o.status != 'cancelled' "
            "AND NOT (o.total_amount > 0 AND COALESCE((SELECT SUM(r.amount) FROM refunds r "
            "WHERE r.order_id = o.order_id), 0) >= o.total_amount)"
        )
        args: list[Any] = [TENANT_ID, iso(since), iso(until)]
        if campaign_id:
            sql += " AND o.src_campaign = ?"
            args.append(campaign_id)
        if source:
            sql += " AND o.src_source = ?"
            args.append(source)
        sql += " ORDER BY o.created_at, o.order_id LIMIT ?"
        args.append(limit + 1)
        with self.db.tx() as conn:
            rows = conn.execute(sql, args).fetchall()
            truncated = len(rows) > limit
            rows = rows[:limit]
            items: dict[str, list[dict[str, Any]]] = defaultdict(list)
            ids = [r["order_id"] for r in rows]
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ",".join("?" * len(chunk))
                for item in conn.execute(
                    f"SELECT order_id, product_id, qty, unit_price FROM order_items "
                    f"WHERE order_id IN ({marks}) ORDER BY rowid",
                    chunk,
                ):
                    items[item["order_id"]].append(
                        {
                            "product_id": item["product_id"],
                            "qty": item["qty"],
                            "unit_price": item["unit_price"],
                        }
                    )
        orders = [
            {
                "order_id": r["order_id"],
                "created_at": r["created_at"],
                "total_amount": r["total_amount"],
                "status": r["status"],
                "items": items[r["order_id"]],
                "src_source": r["src_source"],
                "src_medium": r["src_medium"],
                "src_campaign": r["src_campaign"],
            }
            for r in rows
        ]
        return {
            "orders": orders,
            "order_count": len(orders),
            "total_amount": sum(o["total_amount"] for o in orders),
            "truncated": truncated,
        }

    def metric_series(self, query: ShopMetricSeriesQuery, now: datetime) -> ShopMetricSeriesResult:
        """5분 버킷 지표. 건수 지표는 빈 버킷을 0으로, 실패율은 결제가 없는 버킷을 뺀다."""
        if query.to <= query.from_:
            raise FakeShopError("INVALID_RANGE", "to는 from보다 뒤여야 한다")
        if query.to - query.from_ > MAX_SERIES_SPAN:
            raise FakeShopError(
                "RANGE_TOO_LONG", "기간은 24시간을 넘을 수 없다(하루씩 나눠 부른다)", 422
            )
        start = _floor(query.from_)
        with self.db.tx() as conn:
            if query.metric == "order_count":
                rows = conn.execute(
                    "SELECT created_at, 1 AS ok FROM orders "
                    "WHERE tenant_id = ? AND created_at >= ? AND created_at < ?",
                    (query.tenant_id, iso(start), iso(query.to)),
                ).fetchall()
            elif query.metric in ORDER_VALUE_FILTER:
                rows = conn.execute(
                    "SELECT created_at, total_amount, coupon_discount, status, 1 AS ok "
                    "FROM orders o "
                    "WHERE tenant_id = ? AND created_at >= ? AND created_at < ? AND "
                    + ORDER_VALUE_FILTER[query.metric],
                    (query.tenant_id, iso(start), iso(query.to)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT created_at, status = 'succeeded' AS ok, order_id FROM payments "
                    "WHERE tenant_id = ? AND created_at >= ? AND created_at < ?",
                    (query.tenant_id, iso(start), iso(query.to)),
                ).fetchall()
        buckets: dict[datetime, list[Any]] = defaultdict(list)
        for row in rows:
            buckets[_floor(_parse(row["created_at"]))].append(row)
        points: list[MetricPoint] = []
        truncated = False
        moment = start
        while moment < query.to:
            if len(points) >= MAX_POINTS:
                truncated = True
                break
            value = _metric_value(query.metric, buckets.get(moment, []))
            if value is not None:
                points.append(MetricPoint(ts=moment, value=value))
            moment += BUCKET
        return ShopMetricSeriesResult(
            metric=query.metric,
            bucket=query.bucket,
            points=points,
            collected_at=now,
            truncated=truncated,
            tenant_id=query.tenant_id,
        )

    def _seller(self, conn: sqlite3.Connection, seller_id: str, tenant_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM sellers WHERE seller_id = ? AND tenant_id = ?", (seller_id, tenant_id)
        ).fetchone()
        if row is None:
            raise FakeShopError("SELLER_NOT_FOUND", "판매자를 찾을 수 없다", 404)
        return row

    def seller_profile(self, query: ShopSellerProfileQuery, now: datetime) -> ShopSellerProfile:
        """가입 시각·판매 중 상품 수·최근 24시간 등록 수(판매 중지 포함).

        사업자 해시는 판매 서버 표에 없어 null이다.
        """
        with self.db.tx() as conn:
            seller = self._seller(conn, query.seller_id, query.tenant_id)
            active = conn.execute(
                "SELECT COUNT(*) FROM products "
                "WHERE seller_id = ? AND tenant_id = ? AND active = 1",
                (query.seller_id, query.tenant_id),
            ).fetchone()[0]
            recent = conn.execute(
                "SELECT COUNT(*) FROM products WHERE seller_id = ? AND tenant_id = ? "
                "AND created_at >= ? AND created_at <= ?",
                (query.seller_id, query.tenant_id, iso(now - LISTING_WINDOW), iso(now)),
            ).fetchone()[0]
        return ShopSellerProfile(
            seller_id=seller["seller_id"],
            joined_at=_parse(seller["created_at"]),
            business_hash=None,
            business_hash_duplicated=None,
            product_count=active,
            listings_last_24h=recent,
            tenant_id=query.tenant_id,
        )

    def seller_prices(self, query: ShopSellerPricesQuery) -> ShopSellerPricesResult:
        """판매 중 상품마다 같은 카테고리 판매 중 상품(모든 판매자) 가격의 중앙값."""
        with self.db.tx() as conn:
            self._seller(conn, query.seller_id, query.tenant_id)
            own = conn.execute(
                "SELECT product_id, category_code, price FROM products "
                "WHERE seller_id = ? AND tenant_id = ? AND active = 1 ORDER BY product_id",
                (query.seller_id, query.tenant_id),
            ).fetchall()
            categories = sorted({row["category_code"] for row in own})
            prices: dict[str, list[int]] = defaultdict(list)
            if categories:
                marks = ",".join("?" * len(categories))
                for row in conn.execute(
                    "SELECT category_code, price FROM products WHERE tenant_id = ? AND active = 1 "
                    f"AND category_code IN ({marks})",
                    (query.tenant_id, *categories),
                ).fetchall():
                    prices[row["category_code"]].append(row["price"])
        medians = {category: median_price(values) for category, values in prices.items()}
        return ShopSellerPricesResult(
            items=[
                SellerProductPrice(
                    product_id=row["product_id"],
                    category=row["category_code"],
                    price=row["price"],
                    category_median_price=medians[row["category_code"]],
                )
                for row in own
            ]
        )


def _by_order(
    conn: sqlite3.Connection, order_ids: list[str], sql: str
) -> dict[str, list[sqlite3.Row]]:
    """`sql`(`{marks}` 자리에 IN 목록)을 주문 id 묶음으로 돌려 order_id별로 모은다."""
    found: dict[str, list[sqlite3.Row]] = {}
    for start in range(0, len(order_ids), _REFUND_CHUNK):
        chunk = order_ids[start : start + _REFUND_CHUNK]
        for r in conn.execute(sql.format(marks=",".join("?" * len(chunk))), chunk):
            found.setdefault(r["order_id"], []).append(r)
    return found


def _order_view(
    order: sqlite3.Row,
    items: list[sqlite3.Row],
    payments: list[sqlite3.Row],
    refunds: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "order_id": order["order_id"],
        "buyer_id": order["buyer_id"],
        "status": order["status"],
        "total_amount": order["total_amount"],
        "refunded_amount": sum(r["amount"] for r in refunds),
        "refunds": refunds,
        "shipping_fee": order["shipping_fee"],
        "points_used": order["points_used"],
        "coupon_id": order["coupon_id"],
        "coupon_units": order["coupon_units"],
        "coupon_discount": order["coupon_discount"],
        "promised_delivery_at": order["promised_delivery_at"],
        "shipped_at": order["shipped_at"],
        "delivered_at": order["delivered_at"],
        "created_at": order["created_at"],
        "items": [
            {
                "order_item_id": f"oit_{i['item_rowid']:04d}",
                "product_id": i["product_id"],
                "seller_id": i["seller_id"],
                "title": i["title"],
                "qty": i["qty"],
                "unit_price": i["unit_price"],
                "points_share": i["points_share"],
                "coupon_share": i["coupon_share"],
                "paid_amount": i["qty"] * i["unit_price"] - i["points_share"] - i["coupon_share"],
            }
            for i in items
        ],
        "payments": [{key: p[key] for key in _PAYMENT_KEYS} for p in payments],
    }


def _metric_value(metric: str, rows: list[Any]) -> float | None:
    failed = [row for row in rows if not row["ok"]] if metric != "order_count" else []
    if metric in ("order_count", "coupon_order_count", "payment_count"):
        return float(len(rows))
    if metric == "order_amount":
        return float(sum(row["total_amount"] for row in rows))
    if metric == "coupon_discount_amount":
        return float(sum(row["coupon_discount"] for row in rows))
    if metric == "order_fail_count":
        return float(len({row["order_id"] for row in failed}))
    if not rows:  # payment_fail_rate: 결제 시도가 없으면 값이 없다
        return None
    return len(failed) / len(rows)


def _caller(authorization: str | None, allowed: frozenset[str]) -> str:
    """판매 서버 `_caller`와 같은 토큰 검사. 통과하면 요청자 이름, 아니면 401·403."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise FakeShopError("UNAUTHORIZED", "토큰이 필요하다", 401)
    viewer = os.getenv(VIEWER_TOKEN_ENV, "").strip()
    if len(viewer) >= VIEWER_TOKEN_MIN_LENGTH and hmac.compare_digest(viewer, token):
        raise FakeShopError("FORBIDDEN", "읽기 전용 토큰으로는 호출할 수 없는 API다", 403)
    try:
        requester = verify_token(token)
    except PermissionError as error:
        for item in os.getenv("SAFETY_HUMAN_TOKENS", "").split(","):
            name, sep, known = item.strip().partition(":")
            if sep and name and known and hmac.compare_digest(known, token):
                raise FakeShopError("FORBIDDEN", "에이전트 토큰이 필요하다", 403) from error
        raise FakeShopError("UNAUTHORIZED", "토큰을 검증할 수 없다", 401) from error
    if requester not in allowed and department_of(requester) not in allowed:
        raise FakeShopError("FORBIDDEN", "이 API를 호출할 수 없는 요청자다", 403)
    return requester


def _when(params: httpx.QueryParams, name: str) -> datetime:
    return datetime.fromisoformat(params[name].replace("Z", "+00:00")).astimezone(UTC)


class FakeShopTransport(httpx.MockTransport):
    """data가 판매 서버에 보내는 GET에 판매 서버와 같은 `{data, meta}` 모양으로 답한다.

    `now`: 응답의 조회 시각(`collected_at`)·쿠폰 만료·최근 등록 기준(판매 서버의 지금 시각).
    `check_tokens`: 판매 서버 `_caller`처럼 토큰을 검사한다. 평가 생성기의 응답은 예전처럼 검사하지
    않는다(평가 클라이언트 토큰은 `eval`). 보낸 요청은 `seen`에 남긴다.
    """

    def __init__(
        self,
        shop: FakeShop,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        check_tokens: bool = False,
    ) -> None:
        self.shop = shop
        self.clock = now
        self.check_tokens = check_tokens
        self.seen: list[tuple[str, str, list[str]]] = []
        super().__init__(self.respond)

    def _allow(self, request: httpx.Request, allowed: frozenset[str]) -> None:
        if self.check_tokens:
            _caller(request.headers.get("authorization"), allowed)

    def respond(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        self.seen.append((request.method, path, sorted(params)))
        if request.method != "GET":
            return _error(405, "METHOD_NOT_ALLOWED")  # GET만 흉내 낸다
        meta: dict[str, object] = {}
        try:
            data = self._read(request, path, params, meta)
        except FakeShopError as error:
            return _error(error.status, error.code)
        except (KeyError, ValueError, ValidationError):  # 빠진·틀린 쿼리
            return _error(422, "INVALID_QUERY")
        if data is None:
            return _error(404, "NOT_FOUND")
        return httpx.Response(200, content=json.dumps({"data": data, "meta": meta}))

    def _read(
        self, request: httpx.Request, path: str, params: httpx.QueryParams, meta: dict[str, object]
    ) -> Any:
        shop, now = self.shop, self.clock()
        if path.endswith("/failed-orders"):
            self._allow(request, DATA_READERS)
            query = ShopFailedOrdersQuery(
                from_=_when(params, "from"),
                to=_when(params, "to"),
                category=params.get("category"),
                limit=int(params.get("limit", 1000)),
            )
            return shop.failed_orders(query, now).model_dump(mode="json")
        if path.endswith("/metric-series"):
            self._allow(request, DATA_READERS)
            series = ShopMetricSeriesQuery(
                metric=params["metric"],
                from_=_when(params, "from"),
                to=_when(params, "to"),
                bucket=params.get("bucket", "5m"),
            )
            return shop.metric_series(series, now).model_dump(mode="json")
        if path.endswith("/coupons"):
            self._allow(request, COUPON_READERS)
            return shop.list_coupons(
                params.get("buyer_id"), params.get("purpose_code"), params.get("campaign_id"), now
            )
        if path.endswith("/orders"):  # 판매 서버도 주문 목록은 토큰을 보지 않는다
            orders, meta["next_cursor"] = shop.list_orders(
                params.get("buyer_id"),
                params.get("since"),
                params.get("until"),
                int(params.get("limit", ORDER_PAGE_DEFAULT)),
                params.get("cursor"),
            )
            return orders
        if path.endswith("/orders/attributed"):
            self._allow(request, ATTRIBUTION_READERS)
            return shop.attributed_orders(
                _when(params, "from"),
                _when(params, "to"),
                params.get("campaign_id"),
                params.get("source"),
                int(params.get("limit", 500)),
            )
        if path.endswith("/products"):  # 판매 서버도 상품 목록은 토큰을 보지 않는다
            return shop.list_products(params.get("seller_id"), params.get("category_code"))
        parts = path.split("/")  # ['', 'api', 'shop', 'sellers', id, kind]
        if len(parts) == 6 and parts[3] == "sellers" and parts[5] in ("profile", "prices"):
            self._allow(request, DATA_READERS)
            if parts[5] == "profile":
                profile = ShopSellerProfileQuery(seller_id=parts[4])
                return shop.seller_profile(profile, now).model_dump(mode="json")
            prices = ShopSellerPricesQuery(seller_id=parts[4])
            return shop.seller_prices(prices).model_dump(mode="json")
        return None


def _error(status: int, code: str) -> httpx.Response:
    """오류 응답. data는 `error.code`만 읽으므로 코드만 싣는다(재무 가짜 판매 서버와 같은 모양)."""
    return httpx.Response(status, json={"error": {"code": code}})


def openapi() -> dict[str, Any]:
    return json.loads(OPENAPI_PATH.read_text("utf-8"))
