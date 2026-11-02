"""쇼핑몰 화면용 읽기 API 업무 규칙: 데모 사용자·카테고리·장바구니 가격 미리보기.

웹 프레임워크에 의존하지 않는다. 가격은 DB 값으로 코드가 계산한다(요청 값을 믿지 않는다).
사용자는 가명 ID(`buy_xxxx`)와 표시용 데모 이름뿐이다(개인정보 없음, 비밀번호·토큰 없음).
"""

from __future__ import annotations

from typing import Any

from ax_shop_api.db import Db
from ax_shop_api.service import FREE_SHIPPING_FROM, SHIPPING_FEE, ShopError, use_coupon

# 카테고리 코드 → 화면 표시 이름(시드의 도서·문구). 모르는 코드는 이름을 지어내지 않고 None이다
CATEGORY_NAMES = {"book": "도서", "stationery": "문구"}


class Storefront:
    def __init__(self, db: Db) -> None:
        self.db = db

    # --- 데모 사용자 ----------------------------------------------------------
    @staticmethod
    def _user(buyer_id: str) -> dict[str, Any]:
        number = buyer_id.rsplit("_", 1)[-1]
        return {"buyer_id": buyer_id, "display_name": f"데모 구매자 {number}"}

    def list_users(self) -> list[dict[str, Any]]:
        with self.db.tx() as conn:
            rows = conn.execute("SELECT buyer_id FROM buyers ORDER BY buyer_id").fetchall()
        return [self._user(r["buyer_id"]) for r in rows]

    def get_user(self, buyer_id: str) -> dict[str, Any]:
        with self.db.tx() as conn:
            found = conn.execute(
                "SELECT buyer_id FROM buyers WHERE buyer_id = ?", (buyer_id,)
            ).fetchone()
        if found is None:
            raise ShopError("BUYER_NOT_FOUND", "구매자를 찾을 수 없다", 404)
        return self._user(found["buyer_id"])

    # --- 카테고리 -------------------------------------------------------------
    def list_categories(self) -> list[dict[str, Any]]:
        with self.db.tx() as conn:
            rows = conn.execute(
                "SELECT category_code, COUNT(*) AS n FROM products WHERE active = 1 "
                "GROUP BY category_code ORDER BY category_code"
            ).fetchall()
        return [
            {
                "category_code": r["category_code"],
                "name": CATEGORY_NAMES.get(r["category_code"]),
                "product_count": r["n"],
            }
            for r in rows
        ]

    # --- 장바구니 가격 미리보기 ---------------------------------------------------
    def price_cart(
        self,
        lines: list[tuple[str, int]],
        points_used: int = 0,
        coupon_id: str | None = None,
        coupon_units: int = 1,
        buyer_id: str | None = None,
    ) -> dict[str, Any]:
        """저장하지 않는 계산. 순서는 상품 합계 → 배송비 → (쿠폰) → 적립금이다.

        배송비·쿠폰·적립금은 `create_order`와 같은 규칙이다. 쿠폰은 `coupon_id`와 `buyer_id`가
        함께 있을 때만 검증해 할인액을 계산한다(없으면 0원). 재고·쿠폰 장수는 차감하지 않는다.
        """
        if not lines:
            raise ShopError("EMPTY_CART", "장바구니가 비어 있다")
        items, subtotal = [], 0
        with self.db.tx() as conn:
            for product_id, qty in lines:
                product = conn.execute(
                    "SELECT * FROM products WHERE product_id = ? AND active = 1", (product_id,)
                ).fetchone()
                if product is None:
                    raise ShopError("PRODUCT_NOT_FOUND", f"상품 {product_id}을 찾을 수 없다", 404)
                if product["stock"] < qty:
                    raise ShopError("OUT_OF_STOCK", f"상품 {product_id}의 재고가 부족하다", 409)
                line_amount = qty * product["price"]
                subtotal += line_amount
                items.append(
                    {
                        "product_id": product_id,
                        "title": product["name"],
                        "qty": qty,
                        "unit_price": product["price"],
                        "line_amount": line_amount,
                    }
                )
        discount = 0
        if coupon_id:
            if not buyer_id:
                raise ShopError("BUYER_REQUIRED", "쿠폰을 계산하려면 buyer_id가 필요하다")
            with self.db.tx() as conn:
                discount = use_coupon(conn, buyer_id, coupon_id, coupon_units, subtotal)
        if points_used > subtotal - discount:
            raise ShopError("POINTS_EXCEED_ITEMS", "포인트는 품목 합계를 넘을 수 없다")
        shipping_fee = 0 if subtotal >= FREE_SHIPPING_FROM else SHIPPING_FEE
        return {
            "items": items,
            "subtotal": subtotal,
            "shipping_fee": shipping_fee,
            "free_shipping_from": FREE_SHIPPING_FROM,
            "coupon_id": coupon_id,
            "coupon_units": coupon_units if coupon_id else 0,
            "coupon_discount": discount,
            "coupon_unknown_reason": None,
            "points_used": points_used,
            "payable_amount": subtotal + shipping_fee - discount - points_used,
        }
