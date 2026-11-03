"""상품·주문·가짜 결제 업무 규칙. 웹 프레임워크에 의존하지 않는다.

- 가격·총액은 DB 값으로 코드가 계산한다(요청 값을 믿지 않는다).
- 주문·결제·outbox 이벤트는 한 트랜잭션에 저장한다.
- 가짜 결제는 `Gateway`가 성공·실패를 정한다. 장애 주입(SHOP-06)은 이 자리에 붙는다.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_contracts.schemas import EventType, PaymentFailedData
from ax_shop_api import outbox
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.monitor import Monitor

CANCELLABLE = ("pending_payment", "payment_failed")


class ShopError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _period_bound(value: str) -> str:
    """기간 경계(ISO 8601)를 저장 형식(UTC, 초 단위)으로 맞춘다. 시간대가 없으면 UTC로 본다."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ShopError("INVALID_PERIOD", "기간은 ISO 8601 시각이어야 한다", 400) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def coupon_used_units(conn: sqlite3.Connection, coupon_id: str) -> int:
    """쿠폰이 이미 쓴 장수. 취소됐거나 전액 환불된 주문은 장수를 돌려주므로 세지 않는다.

    남은 장수를 따로 저장하지 않고 주문에서 계산하므로 취소·환불 코드는 쿠폰을 몰라도 된다.
    부분 환불은 장수를 돌려주지 않는다. 전액 환불은 `refunds` 합계가 결제 금액과 같을 때다.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(o.coupon_units), 0) AS used FROM orders o "
        "WHERE o.coupon_id = ? AND o.status != 'cancelled' AND NOT (o.total_amount > 0 AND "
        "COALESCE((SELECT SUM(r.amount) FROM refunds r WHERE r.order_id = o.order_id), 0) "
        ">= o.total_amount)",
        (coupon_id,),
    ).fetchone()
    return int(row["used"])


def use_coupon(
    conn: sqlite3.Connection, buyer_id: str, coupon_id: str, units: int, subtotal: int
) -> int:
    """주문에 쓸 쿠폰을 검증하고 할인액(원)을 돌려준다. 할인은 상품 합계를 넘지 않는다.

    본인의 쿠폰만 쓸 수 있고(`campaign`·`compensation` 모두), 만료됐거나 남은 장수보다 많이
    쓰려 하면 거부한다. 배송비는 할인 대상이 아니다.
    """
    row = conn.execute("SELECT * FROM coupons WHERE coupon_id = ?", (coupon_id,)).fetchone()
    if row is None:
        raise ShopError("COUPON_NOT_FOUND", "쿠폰을 찾을 수 없다", 404)
    if row["buyer_id"] != buyer_id:
        raise ShopError("COUPON_NOT_YOURS", "본인의 쿠폰만 쓸 수 있다", 403)
    if row["expires_at"] is not None and row["expires_at"] <= _now():
        raise ShopError("COUPON_EXPIRED", "만료된 쿠폰이다", 409)
    left = row["count"] - coupon_used_units(conn, coupon_id)
    if units > left:
        raise ShopError("COUPON_UNITS_EXCEEDED", f"남은 쿠폰이 {left}장이다", 409)
    return min(units * row["unit_value"], subtotal)


@dataclass
class PaymentOutcome:
    ok: bool
    failure_code: str | None = None
    latency_ms: float = 0.0
    server_error: bool = False  # 카드 거절(PG_DECLINED)은 5xx가 아니다


# 배송비: 품목 합계(포인트 차감 전)가 기준 미만이면 부과한다(시연용 값, 고객지원 시드와 같다)
SHIPPING_FEE = 3000
FREE_SHIPPING_FROM = 50000

# 약속 배송일: 결제가 성공한 시각 + 이 일수(시연용 값). 고객에게 약속한 도착 기한이다
PROMISED_DELIVERY_DAYS = 3

BASELINE_LATENCY_MS = 180.0  # 평소 p95 기준(시연용)


class Gateway:
    """가짜 결제 서버. 요청한 대로(`simulate`) 성공·실패를 돌려준다.

    장애 주입(SHOP-06): `latency`는 지연(ms)을 더하고, `failure`는 모든 결제를 5xx로 실패시킨다.
    지연은 값만 기록한다(실제로 기다리지 않는다).
    """

    def __init__(self) -> None:
        self.delay_ms = 0
        self.failing = False

    def inject(self, kind: str, delay_ms: int = 0) -> None:
        if kind == "latency":
            self.delay_ms = delay_ms
        elif kind == "failure":
            self.failing = True

    def release(self) -> None:
        self.delay_ms, self.failing = 0, False

    @property
    def active(self) -> bool:
        return bool(self.delay_ms or self.failing)

    def attempt(self, simulate: str | None) -> PaymentOutcome:
        latency = BASELINE_LATENCY_MS + self.delay_ms
        if self.failing:
            return PaymentOutcome(False, "PG_UNAVAILABLE", latency, server_error=True)
        if simulate == "failure":
            return PaymentOutcome(False, "PG_DECLINED", latency)
        return PaymentOutcome(True, None, latency)


class Shop:
    def __init__(
        self,
        db: Db,
        gateway: Gateway | None = None,
        monitor: Monitor | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db = db
        self.gateway = gateway or Gateway()
        self.monitor = monitor or Monitor(db)
        self.clock = clock

    # --- 조회 -------------------------------------------------------------
    def list_sellers(self) -> list[dict[str, Any]]:
        with self.db.tx() as conn:
            rows = conn.execute("SELECT * FROM sellers ORDER BY seller_id").fetchall()
        return [
            {
                "seller_id": r["seller_id"],
                "name": r["name"],
                "category_codes": r["category_codes"].split(","),
            }
            for r in rows
        ]

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

    def get_product(self, product_id: str) -> dict[str, Any]:
        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE product_id = ?", (product_id,)
            ).fetchone()
        if row is None:
            raise ShopError("PRODUCT_NOT_FOUND", "상품을 찾을 수 없다", 404)
        return self._product(row)

    @staticmethod
    def _product(row: sqlite3.Row) -> dict[str, Any]:
        keys = ("product_id", "seller_id", "name", "category_code", "price", "stock")
        return {key: row[key] for key in keys} | {"active": bool(row["active"])}

    def get_order(self, order_id: str) -> dict[str, Any]:
        with self.db.tx() as conn:
            return self._order(conn, order_id)

    def list_orders(
        self, buyer_id: str | None = None, since: str | None = None, until: str | None = None
    ) -> list[dict[str, Any]]:
        """접수 시각(created_at)이 `since` 이상, `until` 미만인 주문. 둘 다 없으면 전체."""
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
        with self.db.tx() as conn:
            sql = "SELECT order_id FROM orders" + (" WHERE " + " AND ".join(where) if where else "")
            ids = [r["order_id"] for r in conn.execute(sql + " ORDER BY order_id", args)]
            return [self._order(conn, order_id) for order_id in ids]

    def _order(self, conn: sqlite3.Connection, order_id: str) -> dict[str, Any]:
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if order is None:
            raise ShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
        items = conn.execute(
            "SELECT i.rowid AS item_rowid, i.product_id, i.seller_id, i.qty, i.unit_price, "
            "i.points_share, i.coupon_share, COALESCE(i.title, p.name) AS title FROM order_items i "
            "LEFT JOIN products p ON p.product_id = i.product_id "
            "WHERE i.order_id = ? ORDER BY i.rowid",
            (order_id,),
        ).fetchall()
        payments = conn.execute(
            "SELECT payment_id, amount, method_code, status, failure_code, created_at "
            "FROM payments WHERE order_id = ? ORDER BY rowid",
            (order_id,),
        ).fetchall()
        refunded = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM refunds WHERE order_id = ?",
            (order_id,),
        ).fetchone()["total"]
        return {
            "order_id": order["order_id"],
            "buyer_id": order["buyer_id"],
            "status": order["status"],
            "total_amount": order["total_amount"],
            "refunded_amount": refunded,
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
                    "paid_amount": i["qty"] * i["unit_price"]
                    - i["points_share"]
                    - i["coupon_share"],
                }
                for i in items
            ],
            "payments": [dict(p) for p in payments],
        }

    # --- 판매자 입점 --------------------------------------------------------
    def register_seller(
        self,
        name: str,
        category_codes: list[str],
        documents_verified: bool,
        products: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """판매자와 처음 올릴 상품을 한 트랜잭션에 저장하고 `seller.registered`를 outbox에 넣는다.

        판매자 이름은 사람이 쓴 문장이라 이벤트에 넣지 않는다. 입점을 받을지는 판매자를 등록하는
        관리자가 정한다(재무 AI는 마켓운영을 하지 않는다, 2026-10-30).
        """
        if not category_codes:
            raise ShopError("INVALID_SELLER", "판매 분류가 하나 이상 필요하다")
        with self.db.tx() as conn:
            count = conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0]
            number = count + 1
            while conn.execute(
                "SELECT 1 FROM sellers WHERE seller_id = ?", (f"sel_{number:04d}",)
            ).fetchone():
                number += 1
            seller_id = f"sel_{number:04d}"
            now = _now()
            conn.execute(
                "INSERT INTO sellers VALUES (?, ?, ?, ?, NULL, ?)",
                (seller_id, name, ",".join(category_codes), TENANT_ID, now),
            )
            for item in products:
                product_id = self.db.next_id(conn, "product", "prd_")
                conn.execute(
                    "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL, ?, ?)",
                    (product_id, seller_id, item["name"], item["category_code"],
                     item["price"], item["stock"], TENANT_ID, now, now),
                )  # fmt: skip
            outbox.enqueue(
                conn,
                EventType.SELLER_REGISTERED,
                {
                    "seller_id": seller_id,
                    "category_codes": category_codes,
                    "documents_verified": documents_verified,
                },
                f"seller.registered:{seller_id}",
            )
        return {
            "seller_id": seller_id,
            "name": name,
            "category_codes": category_codes,
            "documents_verified": documents_verified,
            "product_count": len(products),
        }

    # --- 상품 관리 ----------------------------------------------------------
    def create_product(
        self, seller_id: str, name: str, category_code: str, price: int, stock: int
    ) -> dict[str, Any]:
        with self.db.tx() as conn:
            if not conn.execute(
                "SELECT 1 FROM sellers WHERE seller_id = ?", (seller_id,)
            ).fetchone():
                raise ShopError("SELLER_NOT_FOUND", "판매자를 찾을 수 없다", 404)
            product_id = self.db.next_id(conn, "product", "prd_")
            now = _now()
            conn.execute(
                "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL, ?, ?)",
                (product_id, seller_id, name, category_code, price, stock, TENANT_ID, now, now),
            )
        return self.get_product(product_id)

    def update_product(self, product_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {"name", "price", "stock", "active"}
        fields = {k: (int(v) if k == "active" else v) for k, v in changes.items() if k in allowed}
        self.get_product(product_id)
        if fields:
            assignments = ", ".join(f"{k} = ?" for k in fields)
            with self.db.tx() as conn:
                conn.execute(
                    f"UPDATE products SET {assignments}, updated_at = ? WHERE product_id = ?",
                    [*fields.values(), _now(), product_id],
                )
        return self.get_product(product_id)

    # --- 주문·결제 ----------------------------------------------------------
    @staticmethod
    def _split_points(points_used: int, amounts: list[int]) -> list[int]:
        """포인트를 품목 금액 비율로 나눈다. 반올림으로 남는 몫은 뒤 품목부터 1원씩 붙인다."""
        subtotal = sum(amounts)
        if points_used == 0 or subtotal == 0:
            return [0] * len(amounts)
        shares = [points_used * amount // subtotal for amount in amounts]
        left = points_used - sum(shares)
        for index in reversed(range(len(shares))):
            if left == 0:
                break
            if shares[index] < amounts[index]:
                shares[index] += 1
                left -= 1
        return shares

    def create_order(
        self, buyer_id: str, lines: list[tuple[str, int]], points_used: int = 0
    ) -> dict[str, Any]:
        """주문을 만든다. 결제(현금) 총액 = 품목 합계 − 포인트 + 배송비를 코드가 계산한다."""
        return self.place_order(buyer_id, lines, points_used)[0]

    def place_order(
        self,
        buyer_id: str,
        lines: list[tuple[str, int]],
        points_used: int = 0,
        idempotency_key: str | None = None,
        coupon_id: str | None = None,
        coupon_units: int = 1,
    ) -> tuple[dict[str, Any], bool]:
        """`create_order`에 멱등 키를 더한 것. `(주문, 재전송 여부)`를 돌려준다.

        같은 구매자의 같은 `idempotency_key`가 다시 오면 재고를 확인하거나 차감하기 전에 처음
        주문을 그대로 돌려준다(`replayed=True`). 내용(상품·수량·포인트·쿠폰)이 다르면 409다.
        키가 없으면 예전처럼 매번 새 주문이다.
        """
        if not lines:
            raise ShopError("EMPTY_ORDER", "주문 상품이 없다")
        with self.db.tx() as conn:
            if not conn.execute("SELECT 1 FROM buyers WHERE buyer_id = ?", (buyer_id,)).fetchone():
                raise ShopError("BUYER_NOT_FOUND", "구매자를 찾을 수 없다", 404)
            if idempotency_key is not None:
                existing = conn.execute(
                    "SELECT * FROM orders WHERE buyer_id = ? AND idempotency_key = ?",
                    (buyer_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    stored = sorted(
                        (r["product_id"], r["qty"])
                        for r in conn.execute(
                            "SELECT product_id, qty FROM order_items WHERE order_id = ?",
                            (existing["order_id"],),
                        )
                    )
                    same = (
                        stored,
                        existing["points_used"],
                        existing["coupon_id"],
                        existing["coupon_units"],
                    ) == (sorted(lines), points_used, coupon_id, coupon_units if coupon_id else 0)
                    if not same:
                        raise ShopError(
                            "IDEMPOTENCY_MISMATCH", "같은 키로 다른 내용을 요청할 수 없다", 409
                        )
                    return self._order(conn, existing["order_id"]), True
            items, titles, total = [], [], 0
            for product_id, qty in lines:
                product = conn.execute(
                    "SELECT * FROM products WHERE product_id = ? AND active = 1", (product_id,)
                ).fetchone()
                if product is None:
                    raise ShopError("PRODUCT_NOT_FOUND", f"상품 {product_id}을 찾을 수 없다", 404)
                if product["stock"] < qty:
                    raise ShopError("OUT_OF_STOCK", f"상품 {product_id}의 재고가 부족하다", 409)
                items.append(
                    {
                        "product_id": product_id,
                        "seller_id": product["seller_id"],
                        "qty": qty,
                        "unit_price": product["price"],
                    }
                )
                titles.append(product["name"])
                total += qty * product["price"]  # 계산은 코드로
                conn.execute(
                    "UPDATE products SET stock = stock - ? WHERE product_id = ?", (qty, product_id)
                )
            discount = (
                use_coupon(conn, buyer_id, coupon_id, coupon_units, total) if coupon_id else 0
            )
            if points_used > total - discount:
                raise ShopError("POINTS_EXCEED_ITEMS", "포인트는 품목 합계를 넘을 수 없다")
            shipping_fee = 0 if total >= FREE_SHIPPING_FROM else SHIPPING_FEE
            amounts = [item["qty"] * item["unit_price"] for item in items]
            coupon_shares = self._split_points(
                discount, amounts
            )  # 쿠폰을 먼저, 포인트는 남은 금액에
            shares = self._split_points(
                points_used, [a - c for a, c in zip(amounts, coupon_shares, strict=True)]
            )
            total = total - discount - points_used + shipping_fee  # 실제 결제(현금) 금액
            order_id = self.db.next_id(conn, "order", "ord_")
            now = _now()
            conn.execute(
                "INSERT INTO orders (order_id, buyer_id, status, total_amount, tenant_id, "
                "context_id, created_at, updated_at, shipping_fee, points_used, idempotency_key, "
                "coupon_id, coupon_units, coupon_discount) "
                "VALUES (?, ?, 'pending_payment', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    order_id,
                    buyer_id,
                    total,
                    TENANT_ID,
                    now,
                    now,
                    shipping_fee,
                    points_used,
                    idempotency_key,
                    coupon_id,
                    coupon_units if coupon_id else 0,
                    discount,
                ),
            )
            for item, title, share, c_share in zip(
                items, titles, shares, coupon_shares, strict=True
            ):
                conn.execute(
                    "INSERT INTO order_items (order_id, product_id, seller_id, qty, unit_price, "
                    "tenant_id, context_id, title, points_share, coupon_share) "
                    "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                    (order_id, item["product_id"], item["seller_id"], item["qty"],
                     item["unit_price"], TENANT_ID, title, share, c_share),
                )  # fmt: skip
            outbox.enqueue(
                conn,
                EventType.ORDER_CREATED,
                {"order_id": order_id, "buyer_id": buyer_id, "items": items, "total_amount": total},
                f"order.created:{order_id}",
            )
        return self.get_order(order_id), False

    def pay(self, order_id: str, method_code: str, simulate: str | None) -> dict[str, Any]:
        with self.db.tx() as conn:
            order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if order is None:
                raise ShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
            if order["status"] not in ("pending_payment", "payment_failed"):
                raise ShopError("ORDER_NOT_PAYABLE", "결제할 수 없는 주문 상태다", 409)
            outcome = self.gateway.attempt(simulate)
            self.monitor.record(self.clock(), outcome.latency_ms, outcome.server_error)
            payment_id = self.db.next_id(conn, "payment", "pay_")
            now = _now()
            amount = order["total_amount"]
            status = "succeeded" if outcome.ok else "failed"
            conn.execute(
                "INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (payment_id, order_id, amount, method_code, status, outcome.failure_code,
                 TENANT_ID, now),
            )  # fmt: skip
            conn.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?",
                ("paid" if outcome.ok else "payment_failed", now, order_id),
            )
            if outcome.ok:
                promised = datetime.now(UTC) + timedelta(days=PROMISED_DELIVERY_DAYS)
                conn.execute(
                    "UPDATE orders SET promised_delivery_at = ? WHERE order_id = ?",
                    (promised.strftime("%Y-%m-%dT%H:%M:%SZ"), order_id),
                )
                data = {
                    "payment_id": payment_id,
                    "order_id": order_id,
                    "amount": amount,
                    "method_code": method_code,
                }
                outbox.enqueue(
                    conn, EventType.PAYMENT_SUCCEEDED, data, f"payment.succeeded:{payment_id}"
                )
            else:
                failed = PaymentFailedData(
                    payment_id=payment_id,
                    order_id=order_id,
                    amount=amount,
                    failure_code=outcome.failure_code or "UNKNOWN",
                )
                outbox.enqueue(
                    conn,
                    EventType.PAYMENT_FAILED,
                    failed.model_dump(mode="json"),
                    f"payment.failed:{payment_id}",
                )
        return self.get_order(order_id)

    def mark_delivered(self, order_id: str, delivered_at: datetime | None = None) -> dict[str, Any]:
        """배송 완료 시각을 적는다(시연·시드용 관리 API). 상태는 바꾸지 않고 이벤트도 내지 않는다.

        결제된 주문만 가능하고, 이미 적혀 있으면 그대로 돌려준다.
        """
        with self.db.tx() as conn:
            order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if order is None:
                raise ShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
            if order["status"] != "paid":
                raise ShopError("ORDER_NOT_PAID", "결제된 주문만 배송 완료로 표시할 수 있다", 409)
            if order["delivered_at"] is None:
                moment = (delivered_at or datetime.now(UTC)).astimezone(UTC)
                stamp = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
                if order["shipped_at"] is not None and stamp < order["shipped_at"]:
                    raise ShopError("INVALID_TIME", "도착 시각은 발송 시각보다 빠를 수 없다", 409)
                conn.execute(
                    "UPDATE orders SET delivered_at = ? WHERE order_id = ?", (stamp, order_id)
                )
        return self.get_order(order_id)

    def mark_shipped(self, order_id: str, shipped_at: datetime | None = None) -> dict[str, Any]:
        """발송 시각을 적는다(시연·시드용 관리 API). 상태는 바꾸지 않고 이벤트도 내지 않는다.

        결제된 주문만 가능하고, 이미 적혀 있으면 그대로 돌려준다. 이미 도착한 주문이면 도착 시각보다
        늦게 발송할 수 없다.
        """
        with self.db.tx() as conn:
            order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if order is None:
                raise ShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
            if order["status"] != "paid":
                raise ShopError("ORDER_NOT_PAID", "결제된 주문만 발송으로 표시할 수 있다", 409)
            if order["shipped_at"] is None:
                moment = (shipped_at or datetime.now(UTC)).astimezone(UTC)
                stamp = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
                if order["delivered_at"] is not None and stamp > order["delivered_at"]:
                    raise ShopError("INVALID_TIME", "발송 시각은 도착 시각보다 늦을 수 없다", 409)
                conn.execute(
                    "UPDATE orders SET shipped_at = ? WHERE order_id = ?", (stamp, order_id)
                )
        return self.get_order(order_id)

    def cancel(self, order_id: str, reason_code: str) -> dict[str, Any]:
        with self.db.tx() as conn:
            order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if order is None:
                raise ShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
            if order["status"] not in CANCELLABLE:
                raise ShopError(
                    "ORDER_NOT_CANCELLABLE",
                    "결제된 주문의 취소·환불은 환불 API(SHOP-12, 2차)로만 한다",
                    409,
                )
            for item in conn.execute(
                "SELECT product_id, qty FROM order_items WHERE order_id = ?", (order_id,)
            ):
                conn.execute(
                    "UPDATE products SET stock = stock + ? WHERE product_id = ?",
                    (item["qty"], item["product_id"]),
                )
            conn.execute(
                "UPDATE orders SET status = 'cancelled', updated_at = ? WHERE order_id = ?",
                (_now(), order_id),
            )
            outbox.enqueue(
                conn,
                EventType.ORDER_CANCELLED,
                {
                    "order_id": order_id,
                    "reason_code": reason_code,
                    "cancelled_amount": order["total_amount"],
                },
                f"order.cancelled:{order_id}",
            )
        return self.get_order(order_id)

    # --- 장애 주입·측정 (SHOP-06) -------------------------------------------
    def fault_state(self) -> dict[str, Any]:
        return {
            "active": self.gateway.active,
            "delay_ms": self.gateway.delay_ms,
            "failing": self.gateway.failing,
            "degraded": self.monitor.degraded,
        }

    def inject_fault(self, kind: str, delay_ms: int = 0) -> dict[str, Any]:
        self.gateway.inject(kind, delay_ms)
        return self.fault_state()

    def release_fault(self) -> dict[str, Any]:
        """장애를 해제한다. 장애 상태였다면 `shop.recovered`가 outbox에 들어간다."""
        self.gateway.release()
        self.monitor.reset()
        return self.fault_state()

    def evaluate_monitor(self) -> None:
        self.monitor.evaluate(self.clock())
