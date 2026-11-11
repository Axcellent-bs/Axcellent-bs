"""holdout 평가용 가짜 판매 서버. 판매 서버 코드(`ax_shop_api`)를 import하지 않는다.

판매 서버는 `Catbooks-demo/catbooks-demo` 레포로 나갔다(레포 분리 4단계). 재무 holdout은 그 코드를
직접 부르지 않고, 이 파일이 판매 서버가 하는 일 중 평가에 필요한 것만 같은 규칙으로 흉내 낸다.

- 쓰기(주문·결제·배송 완료·환불·쿠폰 발급)는 판매 서버 코드와 같은 규칙이다: 쿠폰 할인은 품목
  금액 비율로 나누고, 5만 원 미만이면 배송비 3,000원, 성공한 결제만 환불하고 환불 합계는 결제
  금액을 넘지 않는다, 결제된 주문만 배송 완료, ID는 `접두사 + 4자리 순번`.
- 읽기는 HTTP다. `FakeShopTransport`가 재무가 실서버에 보내는 `GET /api/shop/orders`·`/products`·
  `/sellers`·`/coupons`에 판매 서버와 같은 `{data, meta}` 모양으로 답한다(주문은 `order_id` 순
  페이지, `meta.next_cursor`).
- 모양이 판매 서버 계약과 맞는지는 고정본 `shop_api_openapi.json`(`OPENAPI_SOURCE`)으로 시험이
  잰다(`tests/test_holdout_fake_shop.py`). 판매 서버 계약이 바뀌면 고정본을 새 커밋에서 다시 받는다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

OPENAPI_PATH = Path(__file__).resolve().parent / "shop_api_openapi.json"
OPENAPI_SOURCE = (
    "Catbooks-demo/catbooks-demo@4e75f59d7f9ebd70531cf29a3e5b973f6b2517ae "
    "backend/contracts/openapi/shop-api.json"
)

# 판매 서버 시드(seed.py)와 같은 판매자·상품·구매자
SELLERS = [
    ("sel_0001", "책마루", ["book"]),
    ("sel_0002", "종이나라", ["stationery"]),
    ("sel_0003", "북앤펜", ["book", "stationery"]),
]
PRODUCTS = [
    ("sel_0001", "파이썬 입문서", "book", 18000),
    ("sel_0001", "데이터 분석 실전", "book", 24000),
    ("sel_0001", "에세이 모음집", "book", 13000),
    ("sel_0002", "무지 노트 3권 세트", "stationery", 7500),
    ("sel_0002", "젤펜 12색", "stationery", 9800),
    ("sel_0003", "독서대", "stationery", 15000),
    ("sel_0003", "소설 베스트 3종", "book", 31000),
    ("sel_0003", "만년필 입문 세트", "stationery", 29000),
]
BUYER_COUNT = 30
SHIPPING_FEE = 3000
FREE_SHIPPING_FROM = 50000
PROMISED_DELIVERY_DAYS = 3
ORDER_PAGE_DEFAULT, ORDER_PAGE_MAX = 100, 1000


class FakeShopError(Exception):
    """판매 서버가 4xx로 거절할 요청. 평가 코드가 잘못 부르면 바로 드러나게 한다."""


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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


class FakeShop:
    """쇼핑몰 하나의 표(주문·품목·결제·환불·쿠폰·상품). 시각은 `now()`가 정한다."""

    def __init__(self, now: Callable[[], datetime]) -> None:
        self.now = now
        self.counters: dict[str, int] = {}
        self.sellers = [
            {"seller_id": s, "name": n, "category_codes": list(c)} for s, n, c in SELLERS
        ]
        self.products: list[dict[str, Any]] = []
        for seller_id, name, category, price in PRODUCTS:
            self.create_product(seller_id, name, category, price, 1_000_000)
        self.buyers = [f"buy_{n:04d}" for n in range(1, BUYER_COUNT + 1)]
        self.orders: dict[str, dict[str, Any]] = {}
        self.items: list[dict[str, Any]] = []
        self.payments: list[dict[str, Any]] = []
        self.refunds: list[dict[str, Any]] = []
        self.coupons: list[dict[str, Any]] = []
        self.fault: str | None = None

    def _next_id(self, name: str, prefix: str) -> str:
        self.counters[name] = self.counters.get(name, 0) + 1
        return f"{prefix}{self.counters[name]:04d}"

    # ---- 쓰기
    def create_product(
        self, seller_id: str, name: str, category_code: str, price: int, stock: int
    ) -> dict[str, Any]:
        if not any(s["seller_id"] == seller_id for s in self.sellers):
            raise FakeShopError("SELLER_NOT_FOUND")
        product = {
            "product_id": self._next_id("product", "prd_"),
            "seller_id": seller_id,
            "name": name,
            "category_code": category_code,
            "price": price,
            "stock": stock,
            "active": True,
        }
        self.products.append(product)
        return dict(product)

    def issue_coupon(
        self, buyer_id: str, count: int, unit_value: int, campaign_id: str
    ) -> dict[str, Any]:
        if buyer_id not in self.buyers:
            raise FakeShopError("BUYER_NOT_FOUND")
        coupon = {
            "coupon_id": self._next_id("coupon", "cpn_"),
            "buyer_id": buyer_id,
            "purpose_code": "campaign",
            "campaign_id": campaign_id,
            "count": count,
            "unit_value": unit_value,
            "total_value": count * unit_value,
            "context_id": None,
            "created_at": iso(self.now()),
            "expires_at": None,
        }
        self.coupons.append(coupon)
        return self.coupon_view(coupon)

    def place_order(
        self, buyer_id: str, lines: list[tuple[str, int]], coupon_id: str | None = None
    ) -> dict[str, Any]:
        """주문을 만든다. 결제 금액 = 품목 합계 − 쿠폰 할인 + 배송비(5만 원 미만)."""
        if not lines:
            raise FakeShopError("EMPTY_ORDER")
        if buyer_id not in self.buyers:
            raise FakeShopError("BUYER_NOT_FOUND")
        by_id = {p["product_id"]: p for p in self.products}
        rows = []
        for product_id, qty in lines:
            product = by_id.get(product_id)
            if product is None or product["stock"] < qty:
                raise FakeShopError("PRODUCT_NOT_AVAILABLE")
            product["stock"] -= qty
            rows.append((product, qty))
        subtotal = sum(p["price"] * q for p, q in rows)
        discount = 0
        if coupon_id is not None:
            coupon = next((c for c in self.coupons if c["coupon_id"] == coupon_id), None)
            if coupon is None or coupon["buyer_id"] != buyer_id:
                raise FakeShopError("COUPON_NOT_USABLE")
            if self.coupon_used_units(coupon_id) >= coupon["count"]:
                raise FakeShopError("COUPON_UNITS_EXCEEDED")
            discount = min(coupon["unit_value"], subtotal)  # 한 번에 1장
        amounts = [p["price"] * q for p, q in rows]
        coupon_shares = split_by_amount(discount, amounts)
        shipping = 0 if subtotal >= FREE_SHIPPING_FROM else SHIPPING_FEE
        order_id = self._next_id("order", "ord_")
        self.orders[order_id] = {
            "order_id": order_id,
            "buyer_id": buyer_id,
            "status": "pending_payment",
            "total_amount": subtotal - discount + shipping,
            "shipping_fee": shipping,
            "points_used": 0,
            "coupon_id": coupon_id,
            "coupon_units": 1 if coupon_id else 0,
            "coupon_discount": discount,
            "promised_delivery_at": None,
            "shipped_at": None,
            "delivered_at": None,
            "created_at": iso(self.now()),
        }
        for (product, qty), c_share in zip(rows, coupon_shares, strict=True):
            self.items.append(
                {
                    "rid": len(self.items) + 1,
                    "order_id": order_id,
                    "product_id": product["product_id"],
                    "seller_id": product["seller_id"],
                    "title": product["name"],
                    "qty": qty,
                    "unit_price": product["price"],
                    "points_share": 0,
                    "coupon_share": c_share,
                    "category_code": product["category_code"],
                }
            )
        return self.order_view(order_id)

    def inject_fault(self, mode: str) -> None:
        self.fault = mode

    def release_fault(self) -> None:
        self.fault = None

    def pay(self, order_id: str, decline: bool = False) -> dict[str, Any]:
        """결제 시도 하나. 장애 중이면 PG_UNAVAILABLE, 거절이면 PG_DECLINED로 실패한다."""
        order = self.orders.get(order_id)
        if order is None or order["status"] not in ("pending_payment", "payment_failed"):
            raise FakeShopError("ORDER_NOT_PAYABLE")
        failure = "PG_UNAVAILABLE" if self.fault else "PG_DECLINED" if decline else None
        now = self.now()
        self.payments.append(
            {
                "payment_id": self._next_id("payment", "pay_"),
                "order_id": order_id,
                "amount": order["total_amount"],
                "method_code": "card",
                "status": "failed" if failure else "succeeded",
                "failure_code": failure,
                "created_at": iso(now),
            }
        )
        order["status"] = "payment_failed" if failure else "paid"
        if not failure:
            order["promised_delivery_at"] = iso(now + timedelta(days=PROMISED_DELIVERY_DAYS))
        return self.order_view(order_id)

    def mark_delivered(self, order_id: str, delivered_at: datetime) -> dict[str, Any]:
        order = self.orders.get(order_id)
        if order is None or order["status"] != "paid":
            raise FakeShopError("ORDER_NOT_PAID")
        if order["delivered_at"] is None:
            order["delivered_at"] = iso(delivered_at)
        return self.order_view(order_id)

    def refund(self, payment_id: str, amount: int) -> dict[str, Any]:
        payment = next((p for p in self.payments if p["payment_id"] == payment_id), None)
        if payment is None or payment["status"] != "succeeded":
            raise FakeShopError("PAYMENT_NOT_REFUNDABLE")
        done = sum(r["amount"] for r in self.refunds if r["payment_id"] == payment_id)
        if amount > payment["amount"] - done:
            raise FakeShopError("REFUND_EXCEEDS_PAYMENT")
        refund = {
            "refund_id": self._next_id("refund", "rfd_"),
            "payment_id": payment_id,
            "order_id": payment["order_id"],
            "amount": amount,
            "created_at": iso(self.now()),
        }
        self.refunds.append(refund)
        return dict(refund)

    def succeeded_payment(self, order_id: str) -> dict[str, Any] | None:
        return next(
            (p for p in self.payments if p["order_id"] == order_id and p["status"] == "succeeded"),
            None,
        )

    # ---- 판매 서버 응답 모양
    def coupon_used_units(self, coupon_id: str) -> int:
        """취소·전액 환불된 주문은 장수를 돌려준다(판매 서버 `coupon_used_units`)."""
        used = 0
        for o in self.orders.values():
            if o["coupon_id"] != coupon_id or o["status"] == "cancelled":
                continue
            refunded = sum(r["amount"] for r in self.refunds if r["order_id"] == o["order_id"])
            if o["total_amount"] > 0 and refunded >= o["total_amount"]:
                continue
            used += o["coupon_units"]
        return used

    def coupon_view(self, coupon: dict[str, Any]) -> dict[str, Any]:
        used = self.coupon_used_units(coupon["coupon_id"])
        return {
            **coupon,
            "used_units": used,
            "remaining_units": coupon["count"] - used,
            "expired": False,
        }

    def order_view(self, order_id: str) -> dict[str, Any]:
        order = self.orders[order_id]
        refunds = sorted(
            (r for r in self.refunds if r["order_id"] == order_id), key=lambda r: r["created_at"]
        )
        return {
            "order_id": order_id,
            "buyer_id": order["buyer_id"],
            "status": order["status"],
            "total_amount": order["total_amount"],
            "refunded_amount": sum(r["amount"] for r in refunds),
            "refunds": [
                {k: r[k] for k in ("refund_id", "payment_id", "amount", "created_at")}
                for r in refunds
            ],
            **{
                k: order[k]
                for k in (
                    "shipping_fee",
                    "points_used",
                    "coupon_id",
                    "coupon_units",
                    "coupon_discount",
                    "promised_delivery_at",
                    "shipped_at",
                    "delivered_at",
                    "created_at",
                )
            },  # fmt: skip
            "items": [
                {
                    "order_item_id": f"oit_{i['rid']:04d}",
                    **{
                        k: i[k]
                        for k in (
                            "product_id",
                            "seller_id",
                            "title",
                            "qty",
                            "unit_price",
                            "points_share",
                            "coupon_share",
                        )
                    },  # fmt: skip
                    "paid_amount": i["qty"] * i["unit_price"]
                    - i["points_share"]
                    - i["coupon_share"],
                }
                for i in self.items
                if i["order_id"] == order_id
            ],
            "payments": [
                {
                    k: p[k]
                    for k in (
                        "payment_id",
                        "amount",
                        "method_code",
                        "status",
                        "failure_code",
                        "created_at",
                    )
                }  # fmt: skip
                for p in self.payments
                if p["order_id"] == order_id
            ],
        }

    def list_orders(
        self, since: str | None, until: str | None, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        rows = sorted(
            o
            for o, row in self.orders.items()
            if (since is None or row["created_at"] >= since)
            and (until is None or row["created_at"] < until)
            and (cursor is None or o > cursor)
        )
        page = rows[:limit]
        return [self.order_view(o) for o in page], (page[-1] if len(rows) > limit else None)

    def tables(self) -> dict[str, Any]:
        """정답 계산용 표. 판매 서버 DB 표와 같은 열 이름."""
        return {
            "orders": {o: dict(row) for o, row in self.orders.items()},
            "items": [dict(i) for i in self.items],
            "payments": [dict(p) for p in self.payments],
            "refunds": [dict(r) for r in self.refunds],
            "coupons": [dict(c) for c in self.coupons],
        }


def _period(value: str) -> str:
    """판매 서버 `_period_bound`처럼 시각을 UTC `...Z` 문자열로 맞춘다."""
    return iso(datetime.fromisoformat(value.replace("Z", "+00:00")))


class FakeShopTransport(httpx.AsyncBaseTransport):
    """재무가 읽는 판매 서버 GET 4개와 safety 실행 기록을 답한다. 보낸 요청은 `seen`에 남긴다."""

    def __init__(self, shop: FakeShop) -> None:
        self.shop = shop
        self.seen: list[tuple[str, str, list[str], bool]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path, query = request.url.path, dict(request.url.params)
        authorized = request.headers.get("authorization", "").startswith("Bearer ")
        self.seen.append((request.method, path, sorted(query), authorized))
        if request.method != "GET":
            return _error(405, "METHOD_NOT_ALLOWED")
        if path.startswith("/api/safety/"):
            return _ok([])
        if path == "/api/shop/sellers":
            sellers = self.shop.sellers
            return _ok([dict(s, category_codes=list(s["category_codes"])) for s in sellers])
        if path == "/api/shop/products":
            return _ok([dict(p) for p in self.shop.products if p["active"]])
        if path == "/api/shop/coupons":
            if not authorized:
                return _error(401, "UNAUTHORIZED")
            return _ok([self.shop.coupon_view(c) for c in self.shop.coupons])
        if path == "/api/shop/orders":
            try:
                limit = int(query.get("limit", ORDER_PAGE_DEFAULT))
            except ValueError:
                return _error(422, "INVALID_QUERY")
            if not 1 <= limit <= ORDER_PAGE_MAX:
                return _error(422, "INVALID_QUERY")
            since, until = query.get("since"), query.get("until")
            orders, cursor = self.shop.list_orders(
                _period(since) if since else None,
                _period(until) if until else None,
                limit,
                query.get("cursor"),
            )
            return _ok(orders, next_cursor=cursor)
        return _error(404, "NOT_FOUND")


def _ok(data: Any, **meta: Any) -> httpx.Response:
    return httpx.Response(200, json={"data": data, "meta": meta})


def _error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code}})


def openapi() -> dict[str, Any]:
    return json.loads(OPENAPI_PATH.read_text("utf-8"))
